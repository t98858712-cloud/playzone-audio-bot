import uuid
import time
import asyncio
import shutil
import random
from urllib.parse import quote
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TimedOut, NetworkError

from core.config import BASE_DOWNLOAD_DIR, DOWNLOAD_SEMAPHORE, EXECUTOR, MAX_TELEGRAM_SIZE, BOT_USERNAME
from links.urls import HILLTOPADS_LINK, ADSTERRA_LINK
from core.state import ACTIVE_USERS
from database.operations import stat_inc_sync, check_ad_verified_status, get_setting, verify_user_ad_completion
from locales.language import _t
from utils.format import clean_title, format_duration, format_size
from utils.helpers import is_admin, esc, alert_admins_live, ensure_pending_requests, trim_old_pending_requests
from video.downloader import download_thumbnail_safely, execute_download
from processing.progress import run_progress_updates
from audio.processor import convert_to_mp3_local
from admin.panel import edit_message_smart, safe_delete
from user.messages import render_search_page, render_preview_box_from_search

async def handle_download_gateways(query, context: ContextTypes.DEFAULT_TYPE, data: str, uid: int, lang: str):
    if data.startswith("v_ad:"): parts = data.split(":"); mode, resolution, request_id = parts[1], parts[2], parts[3]
    elif data.startswith("aud:"): mode, resolution, request_id = "audio", "720", data.split(":")[1]
    else: mode = "video"; parts = data.split(":"); resolution, request_id = parts[1], parts[2]
    hilltop_status = get_setting("hilltop_status", "1")
    adsterra_status = get_setting("adsterra_status", "1")
    if is_admin(uid) or (hilltop_status == "0" and adsterra_status == "0") or check_ad_verified_status(uid):
        if data.startswith("v_ad:"): await query.answer("✅ تم التحقق بنجاح!", show_alert=True)
        else: await query.answer(_t("msg_prep_audio", lang) if mode == "audio" else _t("msg_prep_video", lang))
        request = ensure_pending_requests(context).pop(request_id, None)
        trim_old_pending_requests(context)
        if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))
        if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
        await start_download_from_callback(query, context, request, mode, resolution, lang)
    else:
        if data.startswith("v_ad:"):
            if time.time() - context.user_data.get(f"ad_start_{request_id}", 0) >= 12:
                verify_user_ad_completion(uid); await query.answer("✅ تم فك القفل بنجاح!", show_alert=True)
                request = ensure_pending_requests(context).pop(request_id, None); trim_old_pending_requests(context)
                if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))
                if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
                await start_download_from_callback(query, context, request, mode, resolution, lang)
            else: return await query.answer("❌ لم تنتهِ من مشاهدة الإعلان بالكامل.", show_alert=True)
        else:
            await query.answer(); context.user_data[f"ad_start_{request_id}"] = time.time(); available_links = []
            if hilltop_status == "1": available_links.append(HILLTOPADS_LINK)
            if adsterra_status == "1": available_links.append(ADSTERRA_LINK)
            ad_direct_url = random.choice(available_links) if available_links else HILLTOPADS_LINK
            ad_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📺 مشاهدة الإعلان", url=ad_direct_url)], [InlineKeyboardButton("🔄 التحقق من اكتمال المشاهدة", callback_data=f"v_ad:{mode}:{resolution}:{request_id}")]])
            await edit_message_smart(query.message, "📥 <b>لفك قفل التحميل:</b>\n\n1️⃣ افتح الإعلان.\n2️⃣ أغلقه واضغط تحقق تلقائياً.", reply_markup=ad_keyboard)

async def start_download_from_callback(query, context, request: dict, mode: str, resolution: str, lang: str):
    uid = query.from_user.id; url = request.get("url"); ACTIVE_USERS.add(uid)
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True); stop_event = asyncio.Event()
    progress_data = {"text": _t("msg_wait_progress", lang), "lang": lang}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))
    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except: pass
        async with DOWNLOAD_SEMAPHORE:
            progress_data["text"] = _t("msg_dl_started", lang); loop = asyncio.get_running_loop()
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))
            info_dict = await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data, resolution))
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
            if not files: raise RuntimeError("خطأ حفظ")
            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)
            if mode == "audio":
                progress_data["text"] = _t("msg_converting", lang); final_mp3_path = job_dir / "playzone_final_audio.mp3"
                success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
                target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file
            else: target_file = raw_downloaded_file
            file_size = target_file.stat().st_size
            if file_size > MAX_TELEGRAM_SIZE: stop_event.set(); return await edit_message_smart(query.message, _t("msg_too_large", lang, size=format_size(file_size, lang), limit=format_size(MAX_TELEGRAM_SIZE, lang)), reply_markup=None)
            stop_event.set(); await edit_message_smart(query.message, _t("msg_uploading", lang), reply_markup=None)
            w, h = info_dict.get("width"), info_dict.get("height")
            title, duration = clean_title(request.get("title", _t("txt_media_file", lang)), 80, lang), int(request.get("duration") or 0)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration, lang))}"
            share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(_t('share_text', lang))}"
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_share", lang), url=share_link)]])
            with open(target_file, "rb") as f:
                if mode == "audio":
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                    try: await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, title=title, performer=request.get("artist", _t("txt_unknown", lang)), duration=duration, caption=caption, thumbnail=t_file, reply_markup=media_keyboard, parse_mode="HTML", read_timeout=120, write_timeout=120)
                    finally:
                        if t_file: t_file.close()
                else: await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=caption, supports_streaming=True, duration=duration, width=int(w) if w else None, height=int(h) if h else None, reply_markup=media_keyboard, parse_mode="HTML", read_timeout=120, write_timeout=120)
            stat_inc_sync("success"); stat_inc_sync("bytes", file_size); await safe_delete(query.message)
    except Exception as e: stat_inc_sync("failed"); await alert_admins_live(context.bot, f"🚨 خطأ: {e}"); try: await edit_message_smart(query.message, _t("msg_dl_failed", lang)) \n except: pass
    finally:
        stop_event.set(); try: await updater_task \n except: pass
        try: shutil.rmtree(job_dir) \n except: pass
        ACTIVE_USERS.discard(uid)
