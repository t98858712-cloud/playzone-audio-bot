import random  # ✅ استيراد مكتبة العشوائية للمداورة بين الروابط[span_1](start_span)[span_1](end_span)
import uuid
import time
import asyncio
import shutil
import logging
import os
from urllib.parse import quote

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TimedOut, NetworkError, BadRequest

# ✅ استيراد روابط الإعلانات والتهيئة الخاصة بالسيرفر[span_2](start_span)[span_2](end_span)
from core.config import BASE_DOWNLOAD_DIR, DOWNLOAD_SEMAPHORE, EXECUTOR, MAX_TELEGRAM_SIZE, BOT_USERNAME, HILLTOPADS_LINK, ADSTERRA_LINK[span_3](start_span)[span_3](end_span)
from core.security import ACTIVE_USERS[span_4](start_span)[span_4](end_span)
from database.operations import stat_inc_sync[span_5](start_span)[span_5](end_span)
from locales.language import _t[span_6](start_span)[span_6](end_span)

from utils.helpers import (
    is_admin, clean_title, format_duration, format_size, esc, 
    ensure_pending_requests, trim_old_pending_requests, get_artist, 
    get_thumbnail, get_largest_estimated_size, alert_admins_live, progress_lock
)[span_7](start_span)[span_7](end_span)
from utils.keyboards import build_preview_keyboard, build_resolution_keyboard[span_8](start_span)[span_8](end_span)

from services.downloader import (
    download_thumbnail_safely, execute_download, run_progress_updates, extract_metadata
)[span_9](start_span)[span_9](end_span)
from services.media import convert_to_mp3_local[span_10](start_span)[span_10](end_span)

from handlers.admin import handle_admin_callbacks, safe_delete, edit_message_smart[span_11](start_span)[span_11](end_span)
from handlers.user import render_search_page[span_12](start_span)[span_12](end_span)

logger = logging.getLogger("PlayZoneEnterpriseBot")[span_13](start_span)[span_13](end_span)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    
    data, uid, lang = query.data or "", query.from_user.id, context.user_data.get("lang", "ar")[span_14](start_span)[span_14](end_span)
    
    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("⛔ هذا الزر مخصص للمدراء فقط.", show_alert=True)[span_15](start_span)[span_15](end_span)
        return await handle_admin_callbacks(query, context)[span_16](start_span)[span_16](end_span)
        
    if data == "cancel_search":
        await query.answer(_t("msg_cancel_done", lang))[span_17](start_span)[span_17](end_span)
        return await safe_delete(query.message)[span_18](start_span)[span_18](end_span)
        
    if data.startswith("page:"):
        parts = data.split(":")
        search_id, page_num = parts[1], int(parts[2])[span_19](start_span)[span_19](end_span)
        context.user_data.setdefault("search_cache", {})[search_id]["page"] = page_num[span_20](start_span)[span_20](end_span)
        await query.answer()[span_21](start_span)[span_21](end_span)
        await render_search_page(query.message, context, search_id, lang)[span_22](start_span)[span_22](end_span)
        return
        
    if data.startswith("selsrc:"):
        video_id = data.split(":")[1][span_23](start_span)[span_23](end_span)
        url = f"https://www.youtube.com/watch?v={video_id}[span_24](start_span)"[span_24](end_span)
        
        if context.user_data.get("loading_preview"):
            return await query.answer("⏳ جاري فحص خيارات الرابط بالفعل، يرجى الانتظار...", show_alert=True)[span_25](start_span)[span_25](end_span)
        
        context.user_data["loading_preview"] = True[span_26](start_span)[span_26](end_span)
        await query.answer()[span_27](start_span)[span_27](end_span)
        
        try:
            await query.message.edit_text(_t("msg_check_link", lang), reply_markup=None)[span_28](start_span)[span_28](end_span)
        except BadRequest as e:
            if "Message is not modified" in str(e) or "There is no text" in str(e):[span_29](start_span)[span_29](end_span)
                context.user_data.pop("loading_preview", None)[span_30](start_span)[span_30](end_span)
                return
            logger.warning(f"تنبيه أثناء تعديل نص البحث: {e}")[span_31](start_span)[span_31](end_span)
        
        try:
            loop = asyncio.get_running_loop()[span_32](start_span)[span_32](end_span)
            info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(url))[span_33](start_span)[span_33](end_span)
            
            title = clean_title(info.get("title"), lang=lang)[span_34](start_span)[span_34](end_span)
            artist, duration_raw = get_artist(info, lang=lang), info.get("duration") or 0[span_35](start_span)[span_35](end_span)
            est_size, thumb = format_size(get_largest_estimated_size(info), lang=lang), get_thumbnail(info)[span_36](start_span)[span_36](end_span)
            request_id = uuid.uuid4().hex[:10][span_37](start_span)[span_37](end_span)
            
            ensure_pending_requests(context)[request_id] = {
                "url": url, "title": title, "artist": artist, 
                "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())
            }[span_38](start_span)[span_38](end_span)
            trim_old_pending_requests(context)[span_39](start_span)[span_39](end_span)
            
            caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}[span_40](start_span)"[span_40](end_span)
            await safe_delete(query.message)[span_41](start_span)[span_41](end_span)
            keyboard = build_preview_keyboard(request_id, lang)[span_42](start_span)[span_42](end_span)
            
            if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):[span_43](start_span)[span_43](end_span)
                try:
                    await context.bot.send_photo(chat_id=uid, photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")[span_44](start_span)[span_44](end_span)
                except Exception:
                    await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)[span_45](start_span)[span_45](end_span)
            else:
                await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)[span_46](start_span)[span_46](end_span)
                
            stat_inc_sync("requests")[span_47](start_span)[span_47](end_span)
        except Exception as e:
            logger.warning(f"فشل جلب المعاينة من البحث: {e}")[span_48](start_span)[span_48](end_span)
            await context.bot.send_message(chat_id=uid, text=_t("msg_link_error", lang))[span_49](start_span)[span_49](end_span)
        finally:
            context.user_data.pop("loading_preview", None)[span_50](start_span)[span_50](end_span)
        return

    if data.startswith("cancel:"):
        ensure_pending_requests(context).pop(data.split(":")[1], None)[span_51](start_span)[span_51](end_span)
        await query.answer(_t("msg_cancel_done", lang))[span_52](start_span)[span_52](end_span)
        return await safe_delete(query.message)[span_53](start_span)[span_53](end_span)

    if data.startswith("back:"):
        request_id = data.split(":")[1][span_54](start_span)[span_54](end_span)
        await query.answer(_t("msg_back", lang))[span_55](start_span)[span_55](end_span)
        return await query.message.edit_reply_markup(reply_markup=build_preview_keyboard(request_id, lang))[span_56](start_span)[span_56](end_span)

    if data.startswith("vid:"):
        request_id = data.split(":")[1][span_57](start_span)[span_57](end_span)
        await query.answer(_t("msg_select_res", lang))[span_58](start_span)[span_58](end_span)
        return await query.message.edit_reply_markup(reply_markup=build_resolution_keyboard(request_id, lang))[span_59](start_span)[span_59](end_span)

    if data.startswith("aud:") or data.startswith("res:") or data.startswith("v_ad:"):
        if data.startswith("v_ad:"):
            parts = data.split(":")
            mode, resolution, request_id = parts[1], parts[2], parts[3][span_60](start_span)[span_60](end_span)
        elif data.startswith("aud:"):
            mode, resolution, request_id = "audio", "720", data.split(":")[1][span_61](start_span)[span_61](end_span)
        else:
            mode = "video[span_62](start_span)"[span_62](end_span)
            parts = data.split(":")
            resolution, request_id = parts[1], parts[2][span_63](start_span)[span_63](end_span)

        # ✅ قراءة حالة كل منصة بشكل مستقل من قاعدة البيانات[span_64](start_span)[span_64](end_span)
        from database.operations import check_ad_verified_status, get_setting[span_65](start_span)[span_65](end_span)[span_66](start_span)[span_66](end_span)
        hilltop_status = get_setting("hilltop_status", "1")[span_67](start_span)[span_67](end_span)
        adsterra_status = get_setting("adsterra_status", "1")[span_68](start_span)[span_68](end_span)
        
        # ✅ إذا كان العضو مشرفاً، أو أكمل الإعلان، أو تم إيقاف كلا الإعلانات من الإدارة -> يبدأ التحميل مباشرة[span_69](start_span)[span_69](end_span)[span_70](start_span)[span_70](end_span)
        if is_admin(uid) or (hilltop_status == "0" and adsterra_status == "0") or check_ad_verified_status(uid):[span_71](start_span)[span_71](end_span)[span_72](start_span)[span_72](end_span)
            if data.startswith("v_ad:"):
                await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)[span_73](start_span)[span_73](end_span)
            else:
                if mode == "audio": await query.answer(_t("msg_prep_audio", lang))[span_74](start_span)[span_74](end_span)
                else: await query.answer(_t("msg_prep_video", lang))[span_75](start_span)[span_75](end_span)

            request = ensure_pending_requests(context).pop(request_id, None)[span_76](start_span)[span_76](end_span)
            trim_old_pending_requests(context)[span_77](start_span)[span_77](end_span)
            
            if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))[span_78](start_span)[span_78](end_span)
            if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)[span_79](start_span)[span_79](end_span)
            
            await start_download_from_callback(query, context, request, mode, resolution, lang)[span_80](start_span)[span_80](end_span)
        else:
            if data.startswith("v_ad:"):
                ad_start = context.user_data.get(f"ad_start_{request_id}", 0)[span_81](start_span)[span_81](end_span)
                if time.time() - ad_start >= 12:[span_82](start_span)[span_82](end_span)
                    from database.operations import verify_user_ad_completion[span_83](start_span)[span_83](end_span)
                    verify_user_ad_completion(uid)[span_84](start_span)[span_84](end_span)
                    
                    await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)[span_85](start_span)[span_85](end_span)
                    request = ensure_pending_requests(context).pop(request_id, None)[span_86](start_span)[span_86](end_span)
                    trim_old_pending_requests(context)[span_87](start_span)[span_87](end_span)
                    if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))[span_88](start_span)[span_88](end_span)
                    if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)[span_89](start_span)[span_89](end_span)
                    await start_download_from_callback(query, context, request, mode, resolution, lang)[span_90](start_span)[span_90](end_span)
                else:
                    return await query.answer("❌ لم تنتهِ من مشاهدة الإعلان بالكامل. يرجى الانتظار والمحاولة.", show_alert=True)[span_91](start_span)[span_91](end_span)
            else:
                await query.answer()[span_92](start_span)[span_92](end_span)
                context.user_data[f"ad_start_{request_id}"] = time.time()[span_93](start_span)[span_93](end_span)
                
                # ✅ المداورة التلقائية الذكية بين المنصات النشطة فقط[span_94](start_span)[span_94](end_span)
                available_links = []
                if hilltop_status == "1":
                    available_links.append(HILLTOPADS_LINK)[span_95](start_span)[span_95](end_span)
                if adsterra_status == "1":
                    available_links.append(ADSTERRA_LINK)[span_96](start_span)[span_96](end_span)
                
                # صمام أمان في حال وجود أي عطل بقراءة البيانات
                if not available_links:
                    ad_direct_url = HILLTOPADS_LINK[span_97](start_span)[span_97](end_span)
                else:
                    ad_direct_url = random.choice(available_links)[span_98](start_span)[span_98](end_span)
                
                btn_watch = "📺 مشاهدة الإعلان " if lang == "ar" else "📺 Watch Ad[span_99](start_span)"[span_99](end_span)
                btn_verify = "🔄 التحقق من اكتمال المشاهدة" if lang == "ar" else "🔄 Verify Ad Completion[span_100](start_span)"[span_100](end_span)
                
                ad_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(btn_watch, url=ad_direct_url)],
                    [InlineKeyboardButton(btn_verify, callback_data=f"v_ad:{mode}:{resolution}:{request_id}")]
                ])[span_101](start_span)[span_101](end_span)
                
                msg_text = (
                    "📥 <b>لفك قفل التحميل:</b>\n\n"
                    "1️⃣ اضغط على زر الإعلان.\n"
                    "2️⃣ افتح الرابط ثم أغلقه.\n"
                    "3️⃣ اضغط على زر التحقق، وسيبدأ التحميل مباشرة. ❤️"
                    if lang == "ar" else
                    "📥 <b>To unlock your download:</b>\n\n"
                    "1️⃣ Tap the ad button.\n"
                    "2️⃣ Open the link, then close it.\n"
                    "3️⃣ Tap Verify to start your download instantly. ❤️"
                )[span_102](start_span)[span_102](end_span)
                await edit_message_smart(query.message, msg_text, reply_markup=ad_keyboard)[span_103](start_span)[span_103](end_span)
        return

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str, resolution: str, lang: str):
    uid = query.from_user.id[span_104](start_span)[span_104](end_span)
    url = request.get("url")[span_105](start_span)[span_105](end_span)
    ACTIVE_USERS.add(uid)[span_106](start_span)[span_106](end_span)
    
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}[span_107](start_span)"[span_107](end_span)
    job_dir.mkdir(parents=True, exist_ok=True)[span_108](start_span)[span_108](end_span)
    stop_event = asyncio.Event()[span_109](start_span)[span_109](end_span)
    
    progress_data = {"text": _t("msg_wait_progress", lang), "lang": lang}[span_110](start_span)[span_110](end_span)
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))[span_111](start_span)[span_111](end_span)

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)[span_112](start_span)[span_112](end_span)
        except Exception: pass

        async with DOWNLOAD_SEMAPHORE:[span_113](start_span)[span_113](end_span)
            with progress_lock: progress_data["text"] = _t("msg_dl_started", lang)[span_114](start_span)[span_114](end_span)
            
            loop = asyncio.get_running_loop()[span_115](start_span)[span_115](end_span)
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))[span_116](start_span)[span_116](end_span)
            
            info_dict = await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data, resolution))[span_117](start_span)[span_117](end_span)
            
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]][span_118](start_span)[span_118](end_span)
            if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")[span_119](start_span)[span_119](end_span)
            
            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)[span_120](start_span)[span_120](end_span)

            if mode == "audio":[span_121](start_span)[span_121](end_span)
                with progress_lock: progress_data["text"] = _t("msg_converting", lang)[span_122](start_span)[span_122](end_span)
                final_mp3_path = job_dir / "playzone_final_audio.mp3[span_123](start_span)"[span_123](end_span)
                success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))[span_124](start_span)[span_124](end_span)
                target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file[span_125](start_span)[span_125](end_span)
            else:
                target_file = raw_downloaded_file[span_126](start_span)[span_126](end_span)

            file_size = target_file.stat().st_size[span_127](start_span)[span_127](end_span)
            if file_size > MAX_TELEGRAM_SIZE:[span_128](start_span)[span_128](end_span)
                stop_event.set()[span_129](start_span)[span_129](end_span)
                return await edit_message_smart(query.message, _t("msg_too_large", lang, size=format_size(file_size, lang), limit=format_size(MAX_TELEGRAM_SIZE, lang)), reply_markup=None)[span_130](start_span)[span_130](end_span)

            stop_event.set()[span_131](start_span)[span_131](end_span)
            await edit_message_smart(query.message, _t("msg_uploading", lang), reply_markup=None)[span_132](start_span)[span_132](end_span)

            native_width = info_dict.get("width")[span_133](start_span)[span_133](end_span)
            native_height = info_dict.get("height")[span_134](start_span)[span_134](end_span)
            
            try: native_width = int(native_width) if native_width else None[span_135](start_span)[span_135](end_span)
            except Exception: native_width = None[span_136](start_span)[span_136](end_span)
            
            try: native_height = int(native_height) if native_height else None[span_137](start_span)[span_137](end_span)
            except Exception: native_height = None[span_138](start_span)[span_138](end_span)

            title, duration = clean_title(request.get("title", _t("txt_media_file", lang)), 80, lang), int(request.get("duration") or 0)[span_139](start_span)[span_139](end_span)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration, lang))}[span_140](start_span)"[span_140](end_span)
            
            share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(_t('share_text', lang))}[span_141](start_span)"[span_141](end_span)
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_share", lang), url=share_link)]])[span_142](start_span)[span_142](end_span)

            with open(target_file, "rb") as f:[span_143](start_span)[span_143](end_span)
                if mode == "audio":[span_144](start_span)[span_144](end_span)
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None[span_145](start_span)[span_145](end_span)
                    try:
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id, audio=f, title=title, 
                            performer=request.get("artist", _t("txt_unknown", lang)), 
                            duration=duration, caption=caption, thumbnail=t_file, 
                            reply_markup=media_keyboard, parse_mode="HTML", 
                            read_timeout=120, write_timeout=120
                        )[span_146](start_span)[span_146](end_span)
                    finally:
                        if t_file: t_file.close()[span_147](start_span)[span_147](end_span)
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id, video=f, caption=caption, 
                        supports_streaming=True, duration=duration, 
                        width=native_width,
                        height=native_height,
                        reply_markup=media_keyboard, parse_mode="HTML", 
                        read_timeout=120, write_timeout=120
                    )[span_148](start_span)[span_148](end_span)

            stat_inc_sync("success")[span_149](start_span)[span_149](end_span)
            stat_inc_sync("bytes", file_size)[span_150](start_span)[span_150](end_span)
            await safe_delete(query.message)[span_151](start_span)[span_151](end_span)

    except (TimedOut, NetworkError) as e:[span_152](start_span)[span_152](end_span)
        stat_inc_sync("failed")[span_153](start_span)[span_153](end_span)
        try: await edit_message_smart(query.message, _t("msg_network_error", lang))[span_154](start_span)[span_154](end_span)
        except Exception: pass
    except Exception as e:[span_155](start_span)[span_155](end_span)
        stat_inc_sync("failed")[span_156](start_span)[span_156](end_span)
        await alert_admins_live(context.bot, f"🚨 <b>فشل تحميل مقطع:</b>\nالرابط: {url}\nالخطأ:\n<code>{str(e)[:300]}</code>")[span_157](start_span)[span_157](end_span)
        try: await edit_message_smart(query.message, _t("msg_dl_failed", lang))[span_158](start_span)[span_158](end_span)
        except Exception: pass
    finally:
        stop_event.set()[span_159](start_span)[span_159](end_span)
        try: await updater_task[span_160](start_span)[span_160](end_span)
        except Exception: pass
        try: shutil.rmtree(job_dir)[span_161](start_span)[span_161](end_span)
        except Exception: pass
        ACTIVE_USERS.discard(uid)[span_162](start_span)[span_162](end_span)
