import uuid
import time
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from database.operations import register_user_sync, get_setting, ban_user_db, stat_inc_sync
from utils.helpers import (
    is_admin, is_valid_url, esc, clean_title, get_artist, format_size, 
    get_thumbnail, get_largest_estimated_size, format_duration, 
    ensure_pending_requests, trim_old_pending_requests, send_preview, alert_admins_live
)
from utils.keyboards import user_main_keyboard, build_playzone_links_keyboard, build_preview_keyboard
from services.downloader import search_youtube, extract_metadata, EXECUTOR
from core.security import BANNED_USERS_CACHE, ANTI_SPAM_CACHE, ACTIVE_USERS
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(
        _t("msg_start", lang, first_name=esc(update.effective_user.first_name or "")), 
        reply_markup=user_main_keyboard(lang), 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )

async def toggle_lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_lang = context.user_data.get("lang", "ar")
    new_lang = "en" if current_lang == "ar" else "ar"
    context.user_data["lang"] = new_lang
    await update.message.reply_text(_t("msg_lang_changed", new_lang), reply_markup=user_main_keyboard(new_lang))

from core.config import BOT_USERNAME

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(
        _t("msg_links", lang), 
        reply_markup=build_playzone_links_keyboard(BOT_USERNAME), 
        disable_web_page_preview=True
    )

async def render_search_page(message, context: ContextTypes.DEFAULT_TYPE, search_id: str, lang: str):
    from handlers.admin import edit_message_smart
    data = context.user_data.get("search_cache", {}).get(search_id)
    if not data: return
    entries, page, query = data["entries"], data["page"], data["query"]
    start_idx = page * 5
    end_idx = start_idx + 5
    current_entries = entries[start_idx:end_idx]
    results_text = _t("msg_search_results", lang, query=esc(query)) + "\n\n"
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    btn_rows, current_row = [], []
    for i, entry in enumerate(current_entries):
        if not entry: continue
        title = clean_title(entry.get("title", _t("txt_unknown", lang)), 55, lang)
        video_id, duration = entry.get("id"), format_duration(entry.get("duration") or 0, lang)
        uploader = entry.get("uploader", _t("txt_unknown", lang))
        num_emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        results_text += f"{num_emoji} <b>{esc(title)}</b>\n   👤 {esc(uploader)} • ⏱ {esc(duration)}\n\n"
        if video_id:
            current_row.append(InlineKeyboardButton(num_emoji, callback_data=f"selsrc:{video_id}"))
            if len(current_row) == 5:
                btn_rows.append(current_row)
                current_row = []
    if current_row: btn_rows.append(current_row)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(_t("btn_prev", lang), callback_data=f"page:{search_id}:{page-1}"))
    if end_idx < len(entries): nav_row.append(InlineKeyboardButton(_t("btn_next", lang), callback_data=f"page:{search_id}:{page+1}"))
    if nav_row: btn_rows.append(nav_row)
    btn_rows.append([InlineKeyboardButton(_t("btn_cancel", lang), callback_data="cancel_search")])
    await edit_message_smart(message, results_text, InlineKeyboardMarkup(btn_rows))

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import handle_broadcast_media
    uid = update.effective_user.id
    lang = context.user_data.get("lang", "ar")
    
    if getattr(update.message, "document", None) and is_admin(uid):
        valid_cookie_files = [
            "cookies.txt", "cookies_youtube.txt", "cookies_tiktok.txt", 
            "cookies_instagram.txt", "cookies_facebook.txt", "cookies_x.txt", "cookies_spotify.txt"
        ]
        file_name = update.message.document.file_name
        
        if file_name in valid_cookie_files:
            from core.config import COOKIES_DIR
            from database.operations import save_cookie_to_db
            
            target_path = COOKIES_DIR / file_name
            new_file = await context.bot.get_file(update.message.document.file_id)
            await new_file.download_to_drive(target_path)
            
            # قراءة المحتوى ورفعه للسحابة فوراً
            try:
                with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
                    cookie_content = f.read()
                save_cookie_to_db(file_name, cookie_content)
                return await update.message.reply_text(f"✅ تم استلام وتحديث ملف كوكيز ({file_name}) وحفظه سحابياً لضمان عدم حذفه بنجاح! ☁️🎯")
            except Exception as e:
                return await update.message.reply_text(f"⚠️ تم حفظ الملف محلياً ولكن فشل الرفع السحابي: {e}")

    if uid in BANNED_USERS_CACHE: return
    maintenance = get_setting("maintenance", "0")
    
    if maintenance == "1" and not is_admin(uid):
        return await update.message.reply_text(_t("msg_maintenance", lang), parse_mode="HTML")
    if getattr(update, "message", None):
        if is_admin(uid) and context.user_data.get("bc_active"):
            return await handle_broadcast_media(update, context)
            
    if not update.message or not update.message.text: return
    register_user_sync(update.effective_user)
    text = update.message.text.strip()
    
    if is_admin(uid) and context.user_data.get("awaiting_user_id"):
        context.user_data.pop("awaiting_user_id", None)
        try:
            target_uid = int(text)
            from handlers.admin import process_user_info
            return await process_user_info(update, context, target_uid)
        except ValueError:
            return await update.message.reply_text("❌ يرجى إرسال أرقام فقط (ID صالح).")
        
    if not is_admin(uid):
        now = time.time()
        reqs = ANTI_SPAM_CACHE.setdefault(uid, [])
        reqs = [t for t in reqs if now - t < 60]
        reqs.append(now)
        ANTI_SPAM_CACHE[uid] = reqs
        if len(reqs) > 12:
            ban_user_db(uid)
            BANNED_USERS_CACHE.add(uid)
            await alert_admins_live(context.bot, f"🚨 <b>نظام الحماية:</b> تم حظر المستخدم <code>{uid}</code> مؤقتاً بسبب السبام.")
            return await update.message.reply_text(_t("msg_spam_blocked", lang), parse_mode="HTML")
            
    if text in [_t("btn_links", "ar"), _t("btn_links", "en"), "/links", "\\links"]:
        return await show_playzone_links(update, context)
    if text in [_t("btn_guide", "ar"), _t("btn_guide", "en")]:
        return await update.message.reply_text(_t("msg_guide", lang), disable_web_page_preview=True)
    if text in [_t("btn_add_group", "ar"), _t("btn_add_group", "en")]:
        add_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_add_group_url", lang), url=f"https://t.me/{context.bot.username}?startgroup=true")]])
        return await update.message.reply_text(_t("msg_add_group", lang), reply_markup=add_keyboard)
    if uid in ACTIVE_USERS:
        return await update.message.reply_text(_t("msg_wait_current", lang))
        
    if not is_valid_url(text):
        status = await update.message.reply_text(_t("msg_searching", lang, query=esc(text)), parse_mode="HTML")
        try:
            loop = asyncio.get_running_loop()
            search_info = await loop.run_in_executor(EXECUTOR, lambda: search_youtube(text, limit=30))
            entries = (search_info.get("entries", []) if search_info else [])[:25]
            if not entries: return await status.edit_text(_t("msg_no_results", lang, query=esc(text)), parse_mode="HTML")
            search_id = uuid.uuid4().hex[:8]
            context.user_data.setdefault("search_cache", {})[search_id] = {"query": text, "entries": entries, "page": 0}
            if len(context.user_data["search_cache"]) > 5:
                context.user_data["search_cache"].pop(next(iter(context.user_data["search_cache"])), None)
            await render_search_page(status, context, search_id, lang)
            return
        except Exception as e:
            logger.warning(f"فشل البحث: {e}")
            await alert_admins_live(context.bot, f"🚨 <b>خطأ في محرك البحث:</b>\n\n<code>{e}</code>")
            return await status.edit_text(_t("msg_link_error", lang))

    status = await update.message.reply_text(_t("msg_check_link", lang))
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))
        title = clean_title(info.get("title"), lang=lang)
        artist = get_artist(info, lang=lang)
        duration_raw = info.get("duration") or 0
        est_size = format_size(get_largest_estimated_size(info), lang=lang)
        thumb = get_thumbnail(info)
        request_id = uuid.uuid4().hex[:10]
        ensure_pending_requests(context)[request_id] = {"url": text, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())}
        trim_old_pending_requests(context)
        caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}"
        from handlers.admin import safe_delete
        await safe_delete(status)
        await send_preview(update, thumb, caption, build_preview_keyboard(request_id, lang))
        stat_inc_sync("requests")
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة: {e}")
        await status.edit_text(_t("msg_link_error", lang))
