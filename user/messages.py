import uuid
import time
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from database.users import register_user_sync
from database.settings import get_setting, ban_user_db
from database.stats import stat_inc_sync
from utils.format import clean_title, format_duration, format_size, get_largest_estimated_size
from utils.network import is_valid_url
from utils.helpers import esc, get_artist, get_thumbnail, ensure_pending_requests, trim_old_pending_requests, send_preview, alert_admins_live
from buttons.keyboards import build_preview_keyboard
from video.downloader import search_youtube, extract_metadata, EXECUTOR
from core.state import BANNED_USERS_CACHE, ANTI_SPAM_CACHE, ACTIVE_USERS
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def render_search_page(message, context, search_id: str, lang: str):
    from admin.panel import edit_message_smart
    data = context.user_data.get("search_cache", {}).get(search_id); if not data: return
    entries, page, query = data["entries"], data["page"], data["query"]; start_idx = page * 5; end_idx = start_idx + 5; current_entries = entries[start_idx:end_idx]
    results_text = _t("msg_search_results", lang, query=esc(query)) + "\n\n"
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]; btn_rows, current_row = [], []
    for i, entry in enumerate(current_entries):
        if not entry: continue
        title = clean_title(entry.get("title", _t("txt_unknown", lang)), 55, lang)
        video_id, duration = entry.get("id"), format_duration(entry.get("duration") or 0, lang)
        uploader = entry.get("uploader", _t("txt_unknown", lang)); num_emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        results_text += f"{num_emoji} <b>{esc(title)}</b>\n   👤 {esc(uploader)} • ⏱ {esc(duration)}\n\n"
        if video_id:
            current_row.append(InlineKeyboardButton(num_emoji, callback_data=f"selsrc:{video_id}"))
            if len(current_row) == 5: btn_rows.append(current_row); current_row = []
    if current_row: btn_rows.append(current_row)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(_t("btn_prev", lang), callback_data=f"page:{search_id}:{page-1}"))
    if end_idx < len(entries): nav_row.append(InlineKeyboardButton(_t("btn_next", lang), callback_data=f"page:{search_id}:{page+1}"))
    if nav_row: btn_rows.append(nav_row)
    btn_rows.append([InlineKeyboardButton(_t("btn_cancel", lang), callback_data="cancel_search")])
    await edit_message_smart(message, results_text, InlineKeyboardMarkup(btn_rows))

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from admin.broadcast import handle_broadcast_media
    uid = update.effective_user.id; lang = context.user_data.get("lang", "ar")
    if getattr(update.message, "document", None) and is_admin(uid):
        if update.message.document.file_name == "cookies.txt":
            from core.config import COOKIES_FILE
            new_file = await context.bot.get_file(update.message.document.file_id); await new_file.download_to_drive(COOKIES_FILE)
            return await update.message.reply_text("✅ تم تحديث الكوكيز.")
    if uid in BANNED_USERS_CACHE: return
    if get_setting("maintenance", "0") == "1" and not is_admin(uid): return await update.message.reply_text(_t("msg_maintenance", lang), parse_mode="HTML")
    if getattr(update, "message", None) and is_admin(uid) and context.user_data.get("bc_active"): return await handle_broadcast_media(update, context)
    if not update.message or not update.message.text: return
    register_user_sync(update.effective_user); text = update.message.text.strip()
    if is_admin(uid) and context.user_data.get("awaiting_user_id"):
        context.user_data.pop("awaiting_user_id", None)
        try:
            from admin.panel import process_user_info; return await process_user_info(update, context, int(text))
        except: return await update.message.reply_text("❌ رقم ID غير صالح.")
    if not is_admin(uid):
        now = time.time(); reqs = ANTI_SPAM_CACHE.setdefault(uid, []); reqs = [t for t in reqs if now - t < 60]; reqs.append(now); ANTI_SPAM_CACHE[uid] = reqs
        if len(reqs) > 12: ban_user_db(uid); BANNED_USERS_CACHE.add(uid); await alert_admins_live(context.bot, f"🚨 سبام {uid}"); return await update.message.reply_text(_t("msg_spam_blocked", lang), parse_mode="HTML")
    if text in [_t("btn_links", "ar"), _t("btn_links", "en"), "/links", "\\links"]: from user.commands import show_playzone_links; return await show_playzone_links(update, context)
    if text in [_t("btn_guide", "ar"), _t("btn_guide", "en")]: return await update.message.reply_text(_t("msg_guide", lang), disable_web_page_preview=True)
    if text in [_t("btn_add_group", "ar"), _t("btn_add_group", "en")]:
        add_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_t("btn_add_group_url", lang), url=f"https://t.me/{context.bot.username}?startgroup=true")]])
        return await update.message.reply_text(_t("msg_add_group", lang), reply_markup=add_keyboard)
    if uid in ACTIVE_USERS: return await update.message.reply_text(_t("msg_wait_current", lang))
    if not is_valid_url(text):
        status = await update.message.reply_text(_t("msg_searching", lang, query=esc(text)), parse_mode="HTML")
        try:
            loop = asyncio.get_running_loop(); search_info = await loop.run_in_executor(EXECUTOR, lambda: search_youtube(text, limit=30)); entries = (search_info.get("entries", []) if search_info else [])[:25]
            if not entries: return await status.edit_text(_t("msg_no_results", lang, query=esc(text)), parse_mode="HTML")
            search_id = uuid.uuid4().hex[:8]; context.user_data.setdefault("search_cache", {})[search_id] = {"query": text, "entries": entries, "page": 0}
            if len(context.user_data["search_cache"]) > 5: context.user_data["search_cache"].pop(next(iter(context.user_data["search_cache"])), None)
            await render_search_page(status, context, search_id, lang); return
        except Exception: return await status.edit_text(_t("msg_link_error", lang))
    status = await update.message.reply_text(_t("msg_check_link", lang))
    try:
        loop = asyncio.get_running_loop(); info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text)); title = clean_title(info.get("title"), lang=lang); artist = get_artist(info, lang=lang); duration_raw = info.get("duration") or 0; est_size = format_size(get_largest_estimated_size(info), lang=lang); thumb = get_thumbnail(info); request_id = uuid.uuid4().hex[:10]
        ensure_pending_requests(context)[request_id] = {"url": text, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())}; trim_old_pending_requests(context); caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}"
        from admin.panel import safe_delete; await safe_delete(status); await send_preview(update, thumb, caption, build_preview_keyboard(request_id, lang)); stat_inc_sync("requests")
    except: await status.edit_text(_t("msg_link_error", lang))

async def render_preview_box_from_search(query, context, video_id: str, lang: str):
    uid = query.from_user.id; url = f"https://www.youtube.com/watch?v={video_id}"
    if context.user_data.get("loading_preview"): return await query.answer("⏳ جاري التحقق...", show_alert=True)
    context.user_data["loading_preview"] = True; await query.answer()
    try: await query.message.edit_text(_t("msg_check_link", lang), reply_markup=None)
    except: context.user_data.pop("loading_preview", None); return
    try:
        loop = asyncio.get_running_loop(); info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(url)); title = clean_title(info.get("title"), lang=lang); artist = get_artist(info, lang=lang); duration_raw = info.get("duration") or 0; est_size = format_size(get_largest_estimated_size(info), lang=lang); thumb = get_thumbnail(info); request_id = uuid.uuid4().hex[:10]
        ensure_pending_requests(context)[request_id] = { "url": url, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time()) }; trim_old_pending_requests(context); caption = f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(format_duration(duration_raw, lang))} - 💾 {esc(est_size)}"; from admin.panel import safe_delete; await safe_delete(query.message); keyboard = build_preview_keyboard(request_id, lang)
        if thumb and (thumb.startswith("http") or thumb.startswith("https")): await context.bot.send_photo(chat_id=uid, photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        else: await context.bot.send_message(chat_id=uid, text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
        stat_inc_sync("requests")
    except: await context.bot.send_message(chat_id=uid, text=_t("msg_link_error", lang))
    finally: context.user_data.pop("loading_preview", None)
