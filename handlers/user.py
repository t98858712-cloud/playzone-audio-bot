import uuid
import time
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from database.operations import register_user_sync, get_setting, ban_user_db, stat_inc_sync
from utils.helpers import is_admin, is_valid_url, esc, clean_title, get_artist, format_size, get_thumbnail, get_largest_estimated_size, format_duration, ensure_pending_requests, trim_old_pending_requests, send_preview, alert_admins_live
from utils.keyboards import user_main_keyboard, build_playzone_links_keyboard, build_preview_keyboard
from services.downloader import search_youtube, extract_metadata, EXECUTOR
from core.security import BANNED_USERS_CACHE, ANTI_SPAM_CACHE, ACTIVE_USERS
from locales.language import _t
import logging

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)[span_177](start_span)[span_177](end_span)
    lang = context.user_data.get("lang", "ar")[span_178](start_span)[span_178](end_span)
    # 🌟 إرسال ترحيب ستارت والتعليمات القديمة مع أزرار روابط الدعم الشفافة الأصلية
    await update.message.reply_text(
        _t("msg_start", lang, first_name=esc(update.effective_user.first_name or "")), 
        reply_markup=build_playzone_links_keyboard(), 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )

async def toggle_lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_lang = context.user_data.get("lang", "ar")[span_179](start_span)[span_179](end_span)
    new_lang = "en" if current_lang == "ar" else "ar[span_180](start_span)"[span_180](end_span)
    context.user_data["lang"] = new_lang[span_181](start_span)[span_181](end_span)
    await update.message.reply_text(_t("msg_lang_changed", new_lang), reply_markup=user_main_keyboard(new_lang))[span_182](start_span)[span_182](end_span)

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "ar")[span_183](start_span)[span_183](end_span)
    await update.message.reply_text(_t("msg_links", lang), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)[span_184](start_span)[span_184](end_span)

async def render_search_page(message, context: ContextTypes.DEFAULT_TYPE, search_id: str, lang: str):
    from handlers.admin import edit_message_smart[span_185](start_span)[span_185](end_span)
    data = context.user_data.get("search_cache", {}).get(search_id)[span_186](start_span)[span_186](end_span)
    if not data: return[span_187](start_span)[span_187](end_span)
    entries, page, query = data["entries"], data["page"], data["query"][span_188](start_span)[span_188](end_span)
    start_idx = page * 5[span_189](start_span)[span_189](end_span)
    end_idx = start_idx + 5[span_190](start_span)[span_190](end_span)
    current_entries = entries[start_idx:end_idx][span_191](start_span)[span_191](end_span)
    results_text = _t("msg_search_results", lang, query=esc(query)) + "\n\n[span_192](start_span)"[span_192](end_span)
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][span_193](start_span)[span_193](end_span)
    btn_rows, current_row = [], [][span_194](start_span)[span_194](end_span)
    for i, entry in enumerate(current_entries):[span_195](start_span)[span_195](end_span)
        if not entry: continue[span_196](start_span)[span_196](end_span)
        title = clean_title(entry.get("title", _t("txt_unknown", lang)), 55, lang)[span_197](start_span)[span_197](end_span)
        video_id, duration = entry.get("id"), format_duration(entry.get("duration") or 0, lang)[span_198](start_span)[span_198](end_span)
        uploader = entry.get("uploader", _t("txt_unknown", lang))[span_199](start_span)[span_199](end_span)
        num_emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}.[span_200](start_span)"[span_200](end_span)
        results_text += f"{num_emoji} <b>{esc(title)}</b>\n   👤 {esc(uploader)} • ⏱ {esc(duration)}\n\n[span_201](start_span)"[span_201](end_span)
        if video_id:[span_202](start_span)[span_202](end_span)
            current_row.append(InlineKeyboardButton(num_emoji, callback_data=f"selsrc:{video_id}"))[span_203](start_span)[span_203](end_span)
            if len(current_row) == 5:[span_204](start_span)[span_204](end_span)
                btn_rows.append(current_row)[span_205](start_span)[span_205](end_span)
                current_row = [][span_206](start_span)[span_206](end_span)
    if current_row: btn_rows.append(current_row)[span_207](start_span)[span_207](end_span)
    nav_row = [][span_208](start_span)[span_208](end_span)
    if page > 0: nav_row.append(InlineKeyboardButton(_t("btn_prev", lang), callback_data=f"page:{search_id}:{page-1}"))[span_209](start_span)[span_209](end_span)
    if end_idx < len(entries): nav_row.append(InlineKeyboardButton(_t("btn_next", lang), callback_data=f"page:{search_id}:{page+1}"))[span_210](start_span)[span_210](end_span)
    if nav_row: btn_rows.append(nav_row)[span_211](start_span)[span_211](end_span)
    btn_rows.append([InlineKeyboardButton(_t("btn_cancel", lang), callback_data="cancel_search")])[span_212](start_span)[span_212](end_span)
    await edit_message_smart(message, results_text, InlineKeyboardMarkup(btn_rows))[span_213](start_span)[span_213](end_span)

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import handle_broadcast_media[span_214](start_span)[span_214](end_span)
    uid = update.effective_user.id[span_215](start_span)[span_215](end_span)
    lang = context.user_data.get("lang", "ar")[span_216](start_span)[span_216](end_span)
    
    if getattr(update.message, "document", None) and is_admin(uid):[span_217](start_span)[span_217](end_span)
        if update.message.document.file_name == "cookies.txt":[span_218](start_span)[span_218](end_span)
            from core.config import COOKIES_FILE[span_219](start_span)[span_219](end_span)
            new_file = await context.bot.get_file(update.message.document.file_id)[span_220](start_span)[span_220](end_span)
            await new_file.download_to_drive(COOKIES_FILE)[span_221](start_span)[span_221](end_span)
            return await update.message.reply_text("✅ تم استلام وتحديث ملف الكوكيز (cookies.txt) بنجاح.")[span_222](start_span)[span_222](end_span)

    if uid in BANNED_USERS_CACHE: return[span_223](start_span)[span_223](end_span)
    maintenance = get_setting("maintenance", "0")[span_224](start_span)[span_224](end_span)
    
    if maintenance == "1" and not is_admin(uid):[span_225](start_span)[span_225](end_span)
        return await update.message.reply_text(_t("msg_maintenance", lang), parse_mode="HTML")[span_226](start_span)[span_226](end_span)
    if getattr(update, "message", None):[span_227](start_span)[span_227](end_span)
        if is_admin(uid) and context.user_data.get("bc_active"):[span_228](start_span)[span_228](end_span)
            return await handle_broadcast_media(update, context)[span_229](start_span)[span_229](end_span)
            
    if not update.message or not update.message.text: return[span_230](start_span)[span_230](end_span)
    register_user_sync(update.effective_user)[span_231](start_span)[span_231](end_span)
    text = update.message.text.strip()[span_232](start_span)[span_232](end_span)
    
    # --- التقاط ID المستخدم بعد الضغط على زر "الاستعلام عن مستخدم" ---
    if is_admin(uid) and context.user_data.get("awaiting_user_id"):[span_233](start_span)[span_233](end_span)
        context.user_data.pop("awaiting_user_id", None)[span_234](start_span)[span_234](end_span)
        try:[span_235](start_span)[span_235](end_span)
            target_uid = int(text)[span_236](start_span)[span_236](end_span)
            from handlers.admin import process_user_info[span_237](start_span)[span_237](end_span)
            return await process_user_info(update, context, target_uid)[span_238](start_span)[span_238](end_span)
        except ValueError:[span_239](start_span)[span_239](end_span)
            return await update.message.reply_text("❌ يرجى إرسال أرقام فقط (ID صالح).")[span_240](start_span)[span_240](end_span)
    # ------------------------------------------------------------------
        
    if not is_admin(uid):[span_241](start_span)[span_241](end_span)
        now = time.time()[span_242](start_span)[span_242](end_span)
        reqs = ANTI_SPAM_CACHE.setdefault(uid, [])[span_243](start_span)[span_243](end_span)
        reqs = [t for t in reqs if now - t < 60][span_244](start_span)[span_244](end_span)
        reqs.append(now)[span_245](start_span)[span_245](end_span)
        ANTI_SPAM_CACHE[uid] = reqs[span_246](start_span)[span_246](end_span)
        if len(reqs) > 12:[span_247](start_span)[span_247](end_span)
            ban_user_db(uid)[span_248](start_span)[span_248](end_span)
            BANNED_USERS_CACHE.add(uid)[span_249](start_span)[span_249](end_span)
            await alert_admins_live(context.bot, f"🚨 <b>نظام الحماية:</b> تم حظر المستخدم <code>{uid}</code> مؤقتاً بسبب السبام.")[span_250](start_span)[span_250](end_span)
            return await update.message.reply_text(_t("msg_spam_blocked", lang), parse_mode="HTML")[span_251](start_span)[span_251](end_span)
            
    if text in [_t("btn_links", "ar"), _t("btn_links", "en"), "/links", "\\links"]:[span_252](start_span)[span_252](end_span)
        return await show_playzone_links(update, context)[span_253](start_span)[span_253](end_span)
    if text in [_t("btn_guide", "ar"), _t("btn_guide", "en")]:[span_254](start_span)[span_254](end_span)
        return await update.message.reply_text(_t("msg_guide", lang), disable_web_page_preview=True)[span_255](start_span)[span_255](end_span)
    if text in [_t("btn_add_group", "ar"), _t("btn_add_group", "en")]:[span_256](start_span)[span_256](end_span)
        add_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_add_group_url", lang), url=f"https://t.me/{context.bot.username}?startgroup=true")]])[span_257](start_span)[span_257](end_span)
        return await update.message.reply_text(_t("msg_add_group", lang), reply_markup=add_keyboard)[span_258](start_span)[span_258](end_span)
    if uid in ACTIVE_USERS:[span_259](start_span)[span_259](end_span)
        return await update.message.reply_text(_t("msg_wait_current", lang))[span_260](start_span)[span_260](end_span)
        
    if not is_valid_url(text):[span_261](start_span)[span_261](end_span)
        status = await update.message.reply_text(_t("msg_searching", lang, query=esc(text)), parse_mode="HTML")[span_262](start_span)[span_262](end_span)
        try:[span_263](start_span)[span_263](end_span)
            loop = asyncio.get_running_loop()[span_264](start_span)[span_264](end_span)
            search_info = await loop.run_in_executor(EXECUTOR, lambda: search_youtube(text, limit=30))[span_265](start_span)[span_265](end_span)
            entries = (search_info.get("entries", []) if search_info else [])[:25][span_266](start_span)[span_266](end_span)
            if not entries: return await status.edit_text(_t("msg_no_results", lang, query=esc(text)), parse_mode="HTML")[span_267](start_span)[span_267](end_span)
            search_id = uuid.uuid4().hex[:8][span_268](start_span)[span_268](end_span)
            context.user_data.setdefault("search_cache", {})[search_id] = {"query": text, "entries": entries, "page": 0}[span_269](start_span)[span_269](end_span)
            if len(context.user_data["search_cache"]) > 5:[span_270](start_span)[span_270](end_span)
                context.user_data["search_cache"].pop(next(iter(context.user_data["search_cache"])), None)[span_271](start_span)[span_271](end_span)
            await render_search_page(status, context, search_id, lang)[span_272](start_span)[span_272](end_span)
            return[span_273](start_span)[span_273](end_span)
        except Exception as e:
            logger.warning(f"فشل البحث: {e}")[span_274](start_span)[span_274](end_span)
            await alert_admins_live(context.bot, f"🚨 <b>خطأ في محرك البحث:</b>\n\n<code>{e}</code>")[span_275](start_span)[span_275](end_span)
            return await status.edit_text(_t("msg_link_error", lang))[span_276](start_span)[span_276](end_span)

    status = await update.message.reply_text(_t("msg_check_link", lang))[span_277](start_span)[span_277](end_span)
    try:[span_278](start_span)[span_278](end_span)
        loop = asyncio.get_running_loop()[span_279](start_span)[span_279](end_span)
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))[span_280](start_span)[span_280](end_span)
        title = clean_title(info.get("title"), lang=lang)[span_281](start_span)[span_281](end_span)
        artist = get_artist(info, lang=lang)[span_282](start_span)[span_282](end_span)
        duration_raw = info.get("duration") or 0[span_283](start_span)[span_283](end_span)
        est_size = format_size(get_largest_estimated_size(info), lang=lang)[span_284](start_span)[span_284](end_span)
        thumb = get_thumbnail(info)[span_285](start_span)[span_285](end_span)
        request_id = uuid.uuid4().hex[:10][span_286](start_span)[span_286](end_span)
        ensure_pending_requests(context)[request_id] = {"url": text, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())}[span_287](start_span)[span_287](end_span)
        trim_old_pending_requests(context)[span_288](start_span)[span_288](end_span)
        caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}[span_289](start_span)"[span_289](end_span)
        from handlers.admin import safe_delete[span_290](start_span)[span_290](end_span)
        await safe_delete(status)[span_291](start_span)[span_291](end_span)
        await send_preview(update, thumb, caption, build_preview_keyboard(request_id, lang))[span_292](start_span)[span_292](end_span)
        stat_inc_sync("requests")[span_293](start_span)[span_293](end_span)
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة: {e}")[span_294](start_span)[span_294](end_span)
        await status.edit_text(_t("msg_link_error", lang))[span_295](start_span)[span_295](end_span)
