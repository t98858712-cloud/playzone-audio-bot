import random[span_298](start_span)[span_298](end_span)
import uuid[span_299](start_span)[span_299](end_span)
import time[span_300](start_span)[span_300](end_span)
import asyncio[span_301](start_span)[span_301](end_span)
import shutil[span_302](start_span)[span_302](end_span)
import logging[span_303](start_span)[span_303](end_span)
import os[span_304](start_span)[span_304](end_span)
from urllib.parse import quote[span_305](start_span)[span_305](end_span)

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton[span_306](start_span)[span_306](end_span)
from telegram.ext import ContextTypes[span_307](start_span)[span_307](end_span)
from telegram.error import TimedOut, NetworkError, BadRequest[span_308](start_span)[span_308](end_span)

from core.config import BASE_DOWNLOAD_DIR, DOWNLOAD_SEMAPHORE, EXECUTOR, MAX_TELEGRAM_SIZE, BOT_USERNAME, HILLTOPADS_LINK, ADSTERRA_LINK[span_309](start_span)[span_309](end_span)
from core.security import ACTIVE_USERS[span_310](start_span)[span_310](end_span)
from database.operations import stat_inc_sync[span_311](start_span)[span_311](end_span)
from locales.language import _t[span_312](start_span)[span_312](end_span)

from utils.helpers import (
    is_admin, clean_title, format_duration, format_size, esc, 
    ensure_pending_requests, trim_old_pending_requests, get_artist, 
    get_thumbnail, get_largest_estimated_size, alert_admins_live, progress_lock[span_313](start_span)[span_313](end_span)
)
from utils.keyboards import build_preview_keyboard, build_resolution_keyboard[span_314](start_span)[span_314](end_span)

from services.downloader import (
    download_thumbnail_safely, execute_download, run_progress_updates, extract_metadata[span_315](start_span)[span_315](end_span)
)
# 🌟 استدعاء دالة الهندسة الصوتية مع دالة التحويل لـ MP3
from services.media import convert_to_mp3_local, normalize_audio_local

from handlers.admin import handle_admin_callbacks, safe_delete, edit_message_smart[span_316](start_span)[span_316](end_span)
from handlers.user import render_search_page[span_317](start_span)[span_317](end_span)

logger = logging.getLogger("PlayZoneEnterpriseBot")[span_318](start_span)[span_318](end_span)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query[span_319](start_span)[span_319](end_span)
    if not query: return[span_320](start_span)[span_320](end_span)
    
    data, uid, lang = query.data or "", query.from_user.id, context.user_data.get("lang", "ar")[span_321](start_span)[span_321](end_span)
    
    if data.startswith("adm_"):[span_322](start_span)[span_322](end_span)
        if not is_admin(uid): return await query.answer("⛔ هذا الزر مخصص للمدراء فقط.", show_alert=True)[span_323](start_span)[span_323](end_span)
        return await handle_admin_callbacks(query, context)[span_324](start_span)[span_324](end_span)
        
    if data == "cancel_search":[span_325](start_span)[span_325](end_span)
        await query.answer(_t("msg_cancel_done", lang))[span_326](start_span)[span_326](end_span)
        return await safe_delete(query.message)[span_327](start_span)[span_327](end_span)
        
    if data.startswith("page:"):[span_328](start_span)[span_328](end_span)
        parts = data.split(":")[span_329](start_span)[span_329](end_span)
        search_id, page_num = parts[1], int(parts[2])[span_330](start_span)[span_330](end_span)
        context.user_data.setdefault("search_cache", {})[search_id]["page"] = page_num[span_331](start_span)[span_331](end_span)
        await query.answer()[span_332](start_span)[span_332](end_span)
        await render_search_page(query.message, context, search_id, lang)[span_333](start_span)[span_333](end_span)
        return[span_334](start_span)[span_334](end_span)
        
    if data.startswith("selsrc:"):[span_335](start_span)[span_335](end_span)
        video_id = data.split(":")[1][span_336](start_span)[span_336](end_span)
        url = f"https://www.youtube.com/watch?v={video_id}[span_337](start_span)"[span_337](end_span)
        
        if context.user_data.get("loading_preview"):[span_338](start_span)[span_338](end_span)
            return await query.answer("⏳ جاري فحص خيارات الرابط بالفعل، يرجى الانتظار...", show_alert=True)[span_339](start_span)[span_339](end_span)
        
        context.user_data["loading_preview"] = True[span_340](start_span)[span_340](end_span)
        await query.answer()[span_341](start_span)[span_341](end_span)
        
        try:[span_342](start_span)[span_342](end_span)
            await query.message.edit_text(_t("msg_check_link", lang), reply_markup=None)[span_343](start_span)[span_343](end_span)
        except BadRequest as e:[span_344](start_span)[span_344](end_span)
            if "Message is not modified" in str(e) or "There is no text" in str(e):[span_345](start_span)[span_345](end_span)
                context.user_data.pop("loading_preview", None)[span_346](start_span)[span_346](end_span)
                return[span_347](start_span)[span_347](end_span)
            logger.warning(f"تنبيه أثناء تعديل نص البحث: {e}")[span_348](start_span)[span_348](end_span)
        
        try:[span_349](start_span)[span_349](end_span)
            loop = asyncio.get_running_loop()[span_350](start_span)[span_350](end_span)
            info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(url))[span_351](start_span)[span_351](end_span)
            
            title = clean_title(info.get("title"), lang=lang)[span_352](start_span)[span_352](end_span)
            artist, duration_raw = get_artist(info, lang=lang), info.get("duration") or 0[span_353](start_span)[span_353](end_span)
            est_size, thumb = format_size(get_largest_estimated_size(info), lang=lang), get_thumbnail(info)[span_354](start_span)[span_354](end_span)
            request_id = uuid.uuid4().hex[:10][span_355](start_span)[span_355](end_span)
            
            ensure_pending_requests(context)[request_id] = {
                "url": url, "title": title, "artist": artist, 
                "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())[span_356](start_span)[span_356](end_span)
            }
            trim_old_pending_requests(context)[span_357](start_span)[span_357](end_span)
            
            caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}[span_358](start_span)"[span_358](end_span)
            await safe_delete(query.message)[span_359](start_span)[span_359](end_span)
            keyboard = build_preview_keyboard(request_id, lang)[span_360](start_span)[span_360](end_span)
            
            if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):[span_361](start_span)[span_361](end_span)
                try:[span_362](start_span)[span_362](end_span)
                    await context.bot.send_photo(chat_id=uid, photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")[span_363](start_span)[span_363](end_span)
                except Exception:[span_364](start_span)[span_364](end_span)
                    await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)[span_365](start_span)[span_365](end_span)
            else:[span_366](start_span)[span_366](end_span)
                await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)[span_367](start_span)[span_367](end_span)
                
            stat_inc_sync("requests")[span_368](start_span)[span_368](end_span)
        except Exception as e:[span_369](start_span)[span_369](end_span)
            logger.warning(f"فشل جلب المعاينة من البحث: {e}")[span_370](start_span)[span_370](end_span)
            await context.bot.send_message(chat_id=uid, text=_t("msg_link_error", lang))[span_371](start_span)[span_371](end_span)
        finally:[span_372](start_span)[span_372](end_span)
            context.user_data.pop("loading_preview", None)[span_373](start_span)[span_373](end_span)
        return[span_374](start_span)[span_374](end_span)

    if data.startswith("cancel:"):[span_375](start_span)[span_375](end_span)
        ensure_pending_requests(context).pop(data.split(":")[1], None)[span_376](start_span)[span_376](end_span)
        await query.answer(_t("msg_cancel_done", lang))[span_377](start_span)[span_377](end_span)
        return await safe_delete(query.message)[span_378](start_span)[span_378](end_span)

    if data.startswith("back:"):[span_379](start_span)[span_379](end_span)
        request_id = data.split(":")[1][span_380](start_span)[span_380](end_span)
        await query.answer(_t("msg_back", lang))[span_381](start_span)[span_381](end_span)
        return await query.message.edit_reply_markup(reply_markup=build_preview_keyboard(request_id, lang))[span_382](start_span)[span_382](end_span)

    if data.startswith("vid:"):[span_383](start_span)[span_383](end_span)
        request_id = data.split(":")[1][span_384](start_span)[span_384](end_span)
        await query.answer(_t("msg_select_res", lang))[span_385](start_span)[span_385](end_span)
        return await query.message.edit_reply_markup(reply_markup=build_resolution_keyboard(request_id, lang))[span_386](start_span)[span_386](end_span)

    # 🌟 استقبال أوضاع التحميل متضمنة وضع الهندسة الصوتية (norm)
    if data.startswith("aud:") or data.startswith("res:") or data.startswith("v_ad:") or data.startswith("norm:"):[span_387](start_span)[span_387](end_span)
        if data.startswith("v_ad:"):[span_388](start_span)[span_388](end_span)
            parts = data.split(":")[span_389](start_span)[span_389](end_span)
            mode, resolution, request_id = parts[1], parts[2], parts[3][span_390](start_span)[span_390](end_span)
        elif data.startswith("aud:"):[span_391](start_span)[span_391](end_span)
            mode, resolution, request_id = "audio", "720", data.split(":")[1][span_392](start_span)[span_392](end_span)
        elif data.startswith("norm:"):
            mode, resolution, request_id = "norm", "720", data.split(":")[1]
        else:[span_393](start_span)[span_393](end_span)
            mode = "video[span_394](start_span)"[span_394](end_span)
            parts = data.split(":")[span_395](start_span)[span_395](end_span)
            resolution, request_id = parts[1], parts[2][span_396](start_span)[span_396](end_span)

        from database.operations import check_ad_verified_status, get_setting[span_397](start_span)[span_397](end_span)
        ads_status = get_setting("ads_status", "1")[span_398](start_span)[span_398](end_span)
        
        if is_admin(uid) or ads_status == "0" or check_ad_verified_status(uid):[span_399](start_span)[span_399](end_span)
            if data.startswith("v_ad:"):[span_400](start_span)[span_400](end_span)
                await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)[span_401](start_span)[span_401](end_span)
            else:[span_402](start_span)[span_402](end_span)
                if mode in ["audio", "norm"]: await query.answer(_t("msg_prep_audio", lang))[span_403](start_span)[span_403](end_span)
                else: await query.answer(_t("msg_prep_video", lang))[span_404](start_span)[span_404](end_span)

            request = ensure_pending_requests(context).pop(request_id, None)[span_405](start_span)[span_405](end_span)
            trim_old_pending_requests(context)[span_406](start_span)[span_406](end_span)
            
            if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))[span_407](start_span)[span_407](end_span)
            if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)[span_408](start_span)[span_408](end_span)
            
            await start_download_from_callback(query, context, request, mode, resolution, lang)[span_409](start_span)[span_409](end_span)
        else:[span_410](start_span)[span_410](end_span)
            if data.startswith("v_ad:"):[span_411](start_span)[span_411](end_span)
                ad_start = context.user_data.get(f"ad_start_{request_id}", 0)[span_412](start_span)[span_412](end_span)
                if time.time() - ad_start >= 12:[span_413](start_span)[span_413](end_span)
                    from database.operations import verify_user_ad_completion[span_414](start_span)[span_414](end_span)
                    verify_user_ad_completion(uid)[span_415](start_span)[span_415](end_span)
                    
                    await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)[span_416](start_span)[span_416](end_span)
                    request = ensure_pending_requests(context).pop(request_id, None)[span_417](start_span)[span_417](end_span)
                    trim_old_pending_requests(context)[span_418](start_span)[span_418](end_span)
                    if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))[span_419](start_span)[span_419](end_span)
                    if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)[span_420](start_span)[span_420](end_span)
                    await start_download_from_callback(query, context, request, mode, resolution, lang)[span_421](start_span)[span_421](end_span)
                else:[span_422](start_span)[span_422](end_span)
                    return await query.answer("❌ لم تنتهِ من مشاهدة الإعلان بالكامل. يرجى الانتظار والمحاولة.", show_alert=True)[span_423](start_span)[span_423](end_span)
            else:[span_424](start_span)[span_424](end_span)
                await query.answer()[span_425](start_span)[span_425](end_span)
                context.user_data[f"ad_start_{request_id}"] = time.time()[span_426](start_span)[span_426](end_span)
                
                ad_direct_url = random.choice([HILLTOPADS_LINK, ADSTERRA_LINK])[span_427](start_span)[span_427](end_span)
                
                btn_watch = "📺 مشاهدة الإعلان " if lang == "ar" else "📺 Watch Ad[span_428](start_span)"[span_428](end_span)
                btn_verify = "🔄 التحقق من اكتمال المشاهدة" if lang == "ar" else "🔄 Verify Ad Completion[span_429](start_span)"[span_429](end_span)
                
                ad_keyboard = InlineKeyboardMarkup([[span_430](start_span)[span_430](end_span)
                    [InlineKeyboardButton(btn_watch, url=ad_direct_url)],[span_431](start_span)[span_431](end_span)
                    [InlineKeyboardButton(btn_verify, callback_data=f"v_ad:{mode}:{resolution}:{request_id}")][span_432](start_span)[span_432](end_span)
                ])[span_433](start_span)[span_433](end_span)
                
                msg_text = ([span_434](start_span)[span_434](end_span)
                    "📥 <b>لفك قفل التحميل:</b>\n\n[span_435](start_span)"[span_435](end_span)
                    "1️⃣ اضغط على زر الإعلان.\n[span_436](start_span)"[span_436](end_span)
                    "2️⃣ افتح الرابط ثم أغلقه.\n[span_437](start_span)"[span_437](end_span)
                    "3️⃣ اضغط على زر التحقق، وسيبدأ التحميل مباشرة. ❤️[span_438](start_span)"[span_438](end_span)
                    if lang == "ar" else[span_439](start_span)[span_439](end_span)
                    "📥 <b>To unlock your download:</b>\n\n[span_440](start_span)"[span_440](end_span)
                    "1️⃣ Tap the ad button.\n[span_441](start_span)"[span_441](end_span)
                    "2️⃣ Open the link, then close it.\n[span_442](start_span)"[span_442](end_span)
                    "3️⃣ Tap Verify to start your download instantly. ❤️[span_443](start_span)"[span_443](end_span)
                )[span_444](start_span)[span_444](end_span)
                await edit_message_smart(query.message, msg_text, reply_markup=ad_keyboard)[span_445](start_span)[span_445](end_span)
        return[span_446](start_span)[span_446](end_span)

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str, resolution: str, lang: str):
    uid = query.from_user.id[span_447](start_span)[span_447](end_span)
    url = request.get("url")[span_448](start_span)[span_448](end_span)
    ACTIVE_USERS.add(uid)[span_449](start_span)[span_449](end_span)
    
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}[span_450](start_span)"[span_450](end_span)
    job_dir.mkdir(parents=True, exist_ok=True)[span_451](start_span)[span_451](end_span)
    stop_event = asyncio.Event()[span_452](start_span)[span_452](end_span)
    
    progress_data = {"text": _t("msg_wait_progress", lang), "lang": lang}[span_453](start_span)[span_453](end_span)
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))[span_454](start_span)[span_454](end_span)

    try:[span_455](start_span)[span_455](end_span)
        try: await query.message.edit_reply_markup(reply_markup=None)[span_456](start_span)[span_456](end_span)
        except Exception: pass[span_457](start_span)[span_457](end_span)

        async with DOWNLOAD_SEMAPHORE:[span_458](start_span)[span_458](end_span)
            with progress_lock: progress_data["text"] = _t("msg_dl_started", lang)[span_459](start_span)[span_459](end_span)
            
            loop = asyncio.get_running_loop()[span_460](start_span)[span_460](end_span)
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))[span_461](start_span)[span_461](end_span)
            
            # 🌟 توجيه وضع التحميل الصوتي للمحرك الأساسي سواء كان عادياً أو هندسياً
            download_mode = "audio" if mode in ["audio", "norm"] else "video"
            info_dict = await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, download_mode, job_dir, progress_data, resolution))
            
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]][span_462](start_span)[span_462](end_span)
            if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")[span_463](start_span)[span_463](end_span)
            
            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)[span_464](start_span)[span_464](end_span)

            # 🌟 تطبيق هندسة الماستر وموازنة الصوت في حال تم الضغط على زر Norm
            if mode == "norm":
                with progress_lock: progress_data["text"] = "🎚 جاري معالجة الصوت وهندسة الماستر بمقياس EBU R128..." if lang == "ar" else "🎚 Master normalizing audio..."
                norm_path = job_dir / "playzone_normalized.mp3"
                norm_success = await loop.run_in_executor(EXECUTOR, lambda: normalize_audio_local(raw_downloaded_file, norm_path))
                raw_downloaded_file = norm_path if norm_success and norm_path.exists() else raw_downloaded_file

            if mode in ["audio", "norm"]:[span_465](start_span)[span_465](end_span)
                with progress_lock: progress_data["text"] = _t("msg_converting", lang)[span_466](start_span)[span_466](end_span)
                final_mp3_path = job_dir / "playzone_final_audio.mp3[span_467](start_span)"[span_467](end_span)
                success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))[span_468](start_span)[span_468](end_span)
                target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file[span_469](start_span)[span_469](end_span)
            else:[span_470](start_span)[span_470](end_span)
                target_file = raw_downloaded_file[span_471](start_span)[span_471](end_span)

            file_size = target_file.stat().st_size[span_472](start_span)[span_472](end_span)
            if file_size > MAX_TELEGRAM_SIZE:[span_473](start_span)[span_473](end_span)
                stop_event.set()[span_474](start_span)[span_474](end_span)
                return await edit_message_smart(query.message, _t("msg_too_large", lang, size=format_size(file_size, lang), limit=format_size(MAX_TELEGRAM_SIZE, lang)), reply_markup=None)[span_475](start_span)[span_475](end_span)

            stop_event.set()[span_476](start_span)[span_476](end_span)
            await edit_message_smart(query.message, _t("msg_uploading", lang), reply_markup=None)[span_477](start_span)[span_477](end_span)

            native_width = info_dict.get("width")[span_478](start_span)[span_478](end_span)
            native_height = info_dict.get("height")[span_479](start_span)[span_479](end_span)
            
            try: native_width = int(native_width) if native_width else None[span_480](start_span)[span_480](end_span)
            except Exception: native_width = None[span_481](start_span)[span_481](end_span)
            
            try: native_height = int(native_height) if native_height else None[span_482](start_span)[span_482](end_span)
            except Exception: native_height = None[span_483](start_span)[span_483](end_span)

            title, duration = clean_title(request.get("title", _t("txt_media_file", lang)), 80, lang), int(request.get("duration") or 0)[span_484](start_span)[span_484](end_span)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration, lang))}[span_485](start_span)"[span_485](end_span)
            
            share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(_t('share_text', lang))}[span_486](start_span)"[span_486](end_span)
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_share", lang), url=share_link)]])[span_487](start_span)[span_487](end_span)

            with open(target_file, "rb") as f:[span_488](start_span)[span_488](end_span)
                if mode in ["audio", "norm"]:[span_489](start_span)[span_489](end_span)
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None[span_490](start_span)[span_490](end_span)
                    try:[span_491](start_span)[span_491](end_span)
                        perf_tag = request.get("artist", _t("txt_unknown", lang))
                        if mode == "norm": perf_tag += " (Master HQ)"  # وسم تمييز ملفات الماستر المهندسة
                        await context.bot.send_audio([span_492](start_span)[span_492](end_span)
                            chat_id=query.message.chat_id, audio=f, title=title,[span_493](start_span)[span_493](end_span)
                            performer=perf_tag,[span_494](start_span)[span_494](end_span)
                            duration=duration, caption=caption, thumbnail=t_file,[span_495](start_span)[span_495](end_span)
                            reply_markup=media_keyboard, parse_mode="HTML",[span_496](start_span)[span_496](end_span)
                            read_timeout=120, write_timeout=120[span_497](start_span)[span_497](end_span)
                        )[span_498](start_span)[span_498](end_span)
                    finally:[span_499](start_span)[span_499](end_span)
                        if t_file: t_file.close()[span_500](start_span)[span_500](end_span)
                else:[span_501](start_span)[span_501](end_span)
                    await context.bot.send_video([span_502](start_span)[span_502](end_span)
                        chat_id=query.message.chat_id, video=f, caption=caption,[span_503](start_span)[span_503](end_span)
                        supports_streaming=True, duration=duration,[span_504](start_span)[span_504](end_span)
                        width=native_width,[span_505](start_span)[span_505](end_span)
                        height=native_height,[span_506](start_span)[span_506](end_span)
                        reply_markup=media_keyboard, parse_mode="HTML",[span_507](start_span)[span_507](end_span)
                        read_timeout=120, write_timeout=120[span_508](start_span)[span_508](end_span)
                    )[span_509](start_span)[span_509](end_span)

            stat_inc_sync("success")[span_510](start_span)[span_510](end_span)
            stat_inc_sync("bytes", file_size)[span_511](start_span)[span_511](end_span)
            await safe_delete(query.message)[span_512](start_span)[span_512](end_span)

    except (TimedOut, NetworkError) as e:[span_513](start_span)[span_513](end_span)
        stat_inc_sync("failed")[span_514](start_span)[span_514](end_span)
        try: await edit_message_smart(query.message, _t("msg_network_error", lang))[span_515](start_span)[span_515](end_span)
        except Exception: pass[span_516](start_span)[span_516](end_span)
    except Exception as e:[span_517](start_span)[span_517](end_span)
        stat_inc_sync("failed")[span_518](start_span)[span_518](end_span)
        await alert_admins_live(context.bot, f"🚨 <b>فشل تحميل مقطع:</b>\nالرابط: {url}\nالخطأ:\n<code>{str(e)[:300]}</code>")[span_519](start_span)[span_519](end_span)
        try: await edit_message_smart(query.message, _t("msg_dl_failed", lang))[span_520](start_span)[span_520](end_span)
        except Exception: pass[span_521](start_span)[span_521](end_span)
    finally:[span_522](start_span)[span_522](end_span)
        stop_event.set()[span_523](start_span)[span_523](end_span)
        try: await updater_task[span_524](start_span)[span_524](end_span)
        except Exception: pass[span_525](start_span)[span_525](end_span)
        try: shutil.rmtree(job_dir)[span_526](start_span)[span_526](end_span)
        except Exception: pass[span_527](start_span)[span_527](end_span)
        ACTIVE_USERS.discard(uid)[span_528](start_span)[span_528](end_span)
