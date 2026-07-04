import uuid
import time
import asyncio
import shutil
import logging
from urllib.parse import quote

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TimedOut, NetworkError

from core.config import BASE_DOWNLOAD_DIR, EXECUTOR, MAX_TELEGRAM_SIZE, BOT_USERNAME
from core.security import ACTIVE_USERS
from core.exceptions import PlayZoneException
from database.operations import stat_inc_sync
from locales.language import _t

from utils.helpers import (
    is_admin, clean_title, format_duration, format_size, esc, 
    ensure_pending_requests, trim_old_pending_requests, get_artist, 
    get_thumbnail, get_largest_estimated_size, alert_admins_live, progress_lock
)
from utils.keyboards import build_preview_keyboard, build_resolution_keyboard

from services.downloader import download_thumbnail_safely, execute_download, run_progress_updates, extract_metadata
from services.media import convert_to_mp3_local
from services.orchestrator import TaskOrchestrator, PriorityTask

from handlers.admin import handle_admin_callbacks, safe_delete, edit_message_smart
from handlers.user import render_search_page

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    
    data, uid, lang = query.data or "", query.from_user.id, context.user_data.get("lang", "ar")
    
    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("⛔ هذا الزر مخصص للمدراء فقط.", show_alert=True)
        return await handle_admin_callbacks(query, context)
        
    if data == "cancel_search":
        await query.answer(_t("msg_cancel_done", lang))
        return await safe_delete(query.message)
        
    if data.startswith("page:"):
        parts = data.split(":")
        search_id, page_num = parts[1], int(parts[2])
        context.user_data.setdefault("search_cache", {})[search_id]["page"] = page_num
        await query.answer()
        await render_search_page(query.message, context, search_id, lang)
        return
        
    if data.startswith("selsrc:"):
        video_id = data.split(":")[1]
        url = f"https://www.youtube.com/watch?v={video_id}"
        await query.answer()
        await query.message.edit_text(_t("msg_check_link", lang), reply_markup=None)
        
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(url))
            
            title = clean_title(info.get("title"), lang=lang)
            artist, duration_raw = get_artist(info, lang=lang), info.get("duration") or 0
            est_size, thumb = format_size(get_largest_estimated_size(info), lang=lang), get_thumbnail(info)
            request_id = uuid.uuid4().hex[:10]
            
            ensure_pending_requests(context)[request_id] = {
                "url": url, "title": title, "artist": artist, 
                "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())
            }
            trim_old_pending_requests(context)
            
            caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}"
            await safe_delete(query.message)
            keyboard = build_preview_keyboard(request_id, lang)
            
            if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
                try:
                    await context.bot.send_photo(chat_id=uid, photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
            else:
                await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
                
            stat_inc_sync("requests")
        except Exception as e:
            logger.warning(f"فشل جلب المعاينة من البحث: {e}")
            await context.bot.send_message(chat_id=uid, text=_t("msg_link_error", lang))
        return

    if data.startswith("cancel:"):
        ensure_pending_requests(context).pop(data.split(":")[1], None)
        await query.answer(_t("msg_cancel_done", lang))
        return await safe_delete(query.message)

    if data.startswith("back:"):
        request_id = data.split(":")[1]
        await query.answer(_t("msg_back", lang))
        return await query.message.edit_reply_markup(reply_markup=build_preview_keyboard(request_id, lang))

    if data.startswith("vid:"):
        request_id = data.split(":")[1]
        await query.answer(_t("msg_select_res", lang))
        return await query.message.edit_reply_markup(reply_markup=build_resolution_keyboard(request_id, lang))

    if data.startswith("aud:") or data.startswith("res:"):
        if data.startswith("aud:"):
            mode, resolution, request_id = "audio", "720", data.split(":")[1]
        else:
            mode = "video"
            parts = data.split(":")
            resolution, request_id = parts[1], parts[2]
            
        request = ensure_pending_requests(context).pop(request_id, None)
        trim_old_pending_requests(context)
        
        if not request: return await query.answer(_t("msg_session_expired", lang), show_alert=True)
        
        # 🔒 قفل الحساب الفوري لحظة الضغط لحماية الطابور ومنع الانهيار والسبام التكراري نهائياً
        if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
        ACTIVE_USERS.add(uid)
        
        await query.answer()
        await edit_message_smart(query.message, "⏳ <b>تمت إضافة طلبك بنجاح إلى طابور المعالجة الذكي...</b>\nجاري تحديد أولوية حسابك وبدء التنفيذ الفوري.", reply_markup=None)
        
        task_priority = 0 if is_admin(uid) else 1
        
        async def action_coroutine():
            await start_download_from_callback(query, context, request, mode, resolution, lang)
            
        enterprise_task = PriorityTask(
            priority=task_priority,
            uid=uid,
            action=action_coroutine,
            payload={"url": request.get("url"), "mode": mode}
        )
        await TaskOrchestrator.submit(enterprise_task)


async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str, resolution: str, lang: str):
    uid = query.from_user.id
    url = request.get("url")
    
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    
    progress_data = {"text": _t("msg_wait_progress", lang), "lang": lang}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        with progress_lock: progress_data["text"] = _t("msg_dl_started", lang)
        
        loop = asyncio.get_running_loop()
        local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))
        info_dict = await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data, resolution))
        
        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")
        
        raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)

        if mode == "audio":
            with progress_lock: progress_data["text"] = _t("msg_converting", lang)
            final_mp3_path = job_dir / "playzone_final_audio.mp3"
            success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
            target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file
        else:
            target_file = raw_downloaded_file

        file_size = target_file.stat().st_size
        if file_size > MAX_TELEGRAM_SIZE:
            stop_event.set()
            return await edit_message_smart(query.message, _t("msg_too_large", lang, size=format_size(file_size, lang), limit=format_size(MAX_TELEGRAM_SIZE, lang)), reply_markup=None)

        stop_event.set()
        await edit_message_smart(query.message, _t("msg_uploading", lang), reply_markup=None)

        native_width = info_dict.get("width")
        native_height = info_dict.get("height")
        
        try: native_width = int(native_width) if native_width else None
        except Exception: native_width = None
        try: native_height = int(native_height) if native_height else None
        except Exception: native_height = None

        title, duration = clean_title(request.get("title", _t("txt_media_file", lang)), 80, lang), int(request.get("duration") or 0)
        caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration, lang))}"            
        
        share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(_t('share_text', lang))}"
        media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_share", lang), url=share_link)]])

        with open(target_file, "rb") as f:
            if mode == "audio":
                t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                try:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id, audio=f, title=title, 
                        performer=request.get("artist", _t("txt_unknown", lang)), 
                        duration=duration, caption=caption, thumbnail=t_file, 
                        reply_markup=media_keyboard, parse_mode="HTML", 
                        read_timeout=120, write_timeout=120
                    )
                finally:
                    if t_file: t_file.close()
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id, video=f, caption=caption, 
                    supports_streaming=True, duration=duration, 
                    width=native_width, height=native_height,
                    reply_markup=media_keyboard, parse_mode="HTML", 
                    read_timeout=120, write_timeout=120
                )

        stat_inc_sync("success")
        stat_inc_sync("bytes", file_size)
        await safe_delete(query.message)

    except (TimedOut, NetworkError):
        stat_inc_sync("failed")
        try: await edit_message_smart(query.message, _t("msg_network_error", lang))
        except Exception: pass
    except PlayZoneException as pze:
        stat_inc_sync("failed")
        await alert_admins_live(context.bot, f"🚨 <b>خطأ مؤسسي معزول [{pze.correlation_id}]:</b>\n{pze}")
        try: await edit_message_smart(query.message, _t("msg_dl_failed", lang))
        except Exception: pass
    except Exception as e:
        stat_inc_sync("failed")
        await alert_admins_live(context.bot, f"🚨 <b>فشل تحميل غير متوقع:</b>\nالرابط: {url}\nالخطأ:\n<code>{str(e)[:250]}</code>")
        try: await edit_message_smart(query.message, _t("msg_dl_failed", lang))
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        try: shutil.rmtree(job_dir)
        except Exception: pass
        ACTIVE_USERS.discard(uid)  # إلغاء قفل الحساب نهائياً وفكه للطلبات الجديدة عند الانتهاء أو الانهيار
