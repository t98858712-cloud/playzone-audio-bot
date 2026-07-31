import io
import time
import shutil
import logging
import asyncio
import subprocess
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import RetryAfter, BadRequest

from database.connection import db, firebase_init_error
from database.operations import (
    all_user_ids, get_active_users_48h, stat_inc_sync, load_full_analytics_sync, 
    get_latest_users, get_setting, get_all_users_data, optimize_db, 
    set_setting, register_user_sync, ban_user_db, unban_user_db,
    export_users_csv, export_firebase_backup_json, generate_analytics_txt_report
)
from utils.helpers import is_admin, _force_cleanup_all_sync, format_size, esc
from utils.keyboards import (
    admin_main_keyboard, admin_broadcast_menu, admin_cancel_action_keyboard, 
    admin_users_menu, admin_security_menu, admin_export_menu
)
from locales.language import _t
from core.config import BASE_DOWNLOAD_DIR
from core.security import BANNED_USERS_CACHE

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    register_user_sync(update.effective_user)
    context.user_data.pop("bc_active", None)
    context.user_data.pop("awaiting_user_id", None)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(_t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")

async def process_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    try:
        doc_ref = db.collection('users').document(str(uid))
        u_doc = doc_ref.get()
        if u_doc.exists:
            u = u_doc.to_dict()
            is_banned = uid in BANNED_USERS_CACHE
            status = "🔴 محظور (Banned)" if is_banned else "🟢 نشط (Active)"
            
            toggle_action = "unban" if is_banned else "ban"
            btn_text = "🟢 فك الحظر عن المستخدم" if is_banned else "🚫 حظر المستخدم"
            action_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(btn_text, callback_data=f"adm_user_action:{toggle_action}:{uid}")],
                [InlineKeyboardButton("🔙 رجوع لإدارة المستخدمين", callback_data="adm_users_menu")]
            ])
            
            last_seen_formatted = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(u.get('last_seen', time.time())))
            text = (
                f"👤 <b>بطاقة معلومات المستخدم:</b>\n\n"
                f"• المعرف (ID): <code>{u.get('id', uid)}</code>\n"
                f"• الاسم: {esc(u.get('first_name'))} {esc(u.get('last_name'))}\n"
                f"• اليوزر: @{u.get('username', 'لا يوجد')}\n"
                f"• الحالة: {status}\n"
                f"• آخر ظهور: <code>{last_seen_formatted}</code>"
            )
            await update.message.reply_text(text, reply_markup=action_kb, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود بقاعدة البيانات.")
    except Exception as e:
        logger.error(f"Error checking user info: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء الاستعلام.")

async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def edit_message_smart(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try:
        if getattr(message, "photo", None) or getattr(message, "video", None) or getattr(message, "document", None):
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.debug(f"تخطي تحديث الرسالة: {e}")

def make_ascii_bar(val1: int, val2: int, length: int = 8) -> str:
    total = val1 + val2
    if total == 0: return "░" * length
    fill = int((val1 / total) * length)
    return "█" * fill + "░" * (length - fill)

def build_admin_stats_text(lang: str = "ar") -> str:
    if db is None: return f"⚠️ <b>خطأ في الاتصال بقاعدة البيانات:</b>\n<code>{firebase_init_error}</code>"
    analytics = load_full_analytics_sync()
    bot_bytes = format_size(analytics.get('bot_bytes', 0), lang)
    
    bot_dl = analytics.get('bot_success', 0)
    web_dl = analytics.get('web_downloads', 0)
    dist_bar = make_ascii_bar(bot_dl, web_dl, 10)
    
    return (
        f"📊 <b>PlayZone Live Analytics Dashboard:</b>\n"
        f"────────────────────────\n\n"
        f"👥 <b>المستخدمون والنشاط:</b>\n"
        f"• إجمالي المستخدمين: <code>{analytics.get('total_users', 0)}</code>\n"
        f"• النشطون (آخر 48 ساعة): <code>{analytics.get('active_48h', 0)}</code>\n\n"
        f"🤖 <b>أداء بوت تيليجرام:</b>\n"
        f"• طلبات المعاينة والبحث: <code>{analytics.get('bot_requests', 0)}</code>\n"
        f"• التحميلات الناجحة: <code>{analytics.get('bot_success', 0)}</code>\n"
        f"• نسبة النجاح التشغيلية: <code>{analytics.get('bot_success_rate', 100.0)}%</code>\n"
        f"• البيانات المرسلة: <code>{bot_bytes}</code>\n\n"
        f"🌐 <b>أداء الموقع (Web App):</b>\n"
        f"• زيارات وطلبات البحث: <code>{analytics.get('web_requests', 0)}</code>\n"
        f"• التحميلات المباشرة: <code>{analytics.get('web_downloads', 0)}</code>\n\n"
        f"📈 <b>توزيع التحميلات [بوت : موقع]:</b>\n"
        f"<code>[{dist_bar}]</code> (Bot: {bot_dl} | Web: {web_dl})\n\n"
        f"💰 <b>أداء إعلانات Adsterra:</b>\n"
        f"• الجلسات والنقرات: <code>{analytics.get('adsterra_clicks', 0)}</code>\n"
        f"• التحققات الناجحة: <code>{analytics.get('adsterra_verified', 0)}</code>\n"
        f"────────────────────────"
    )

def build_admin_users_text(limit: int, lang: str = "ar") -> str:
    if db is None: return "⚠️ قاعدة البيانات غير متصلة."
    users = get_latest_users(limit)
    if not users: return "📋 لا يوجد مستخدمين بعد."
    text = "📋 <b>أحدث المنضمين للنظام:</b>\n\n"
    for u in users:
        name = esc(f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()) or "بدون اسم"
        text += f"• <code>{u.get('id', '0')}</code> | {name}\n"
    return text

def build_server_status_text(lang: str = "ar") -> str:
    total, used, free = shutil.disk_usage(BASE_DOWNLOAD_DIR)
    return (
        f"📁 <b>حالة السيرفر ومساحة التخزين:</b>\n\n"
        f"💽 المساحة الكلية: <code>{format_size(total, lang)}</code>\n"
        f"🟢 المساحة المستخدمة: <code>{format_size(used, lang)}</code>\n"
        f"⚪ المساحة الحرة: <code>{format_size(free, lang)}</code>\n\n"
        f"⚙️ مسار التخزين: <code>{BASE_DOWNLOAD_DIR}</code>"
    )

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    lang = context.user_data.get("lang", "ar")
    
    if data == "adm_main_back":
        await query.answer()
        context.user_data.pop("awaiting_user_id", None)
        return await edit_message_smart(query.message, _t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang))
        
    elif data in ["adm_cancel_bc", "adm_cancel_action"]:
        context.user_data.pop("bc_active", None)
        context.user_data.pop("awaiting_user_id", None)
        await query.answer("تم الإلغاء ❌")
        return await edit_message_smart(query.message, "✅ تم إلغاء الإجراء بنجاح.", reply_markup=admin_main_keyboard(lang))

    elif data == "adm_bc_menu":
        await query.answer()
        return await edit_message_smart(query.message, "📢 <b>خيارات الإذاعة الشاملة:</b>\nاختر الشريحة المستهدفة:", reply_markup=admin_broadcast_menu(lang))
        
    elif data.startswith("adm_bc_start:"):
        target = data.split(":")[1]
        context.user_data["bc_active"] = True
        context.user_data["bc_target"] = target
        await query.answer()
        return await edit_message_smart(query.message, _t("msg_adm_bc_ask", lang), reply_markup=admin_cancel_action_keyboard(lang))
        
    elif data == "adm_users_menu":
        await query.answer()
        return await edit_message_smart(query.message, "👥 <b>قسم إدارة المستخدمين والتصدير:</b>", reply_markup=admin_users_menu(lang))
        
    elif data == "adm_user_info_prompt":
        context.user_data["awaiting_user_id"] = True
        await query.answer()
        return await edit_message_smart(query.message, "✍️ <b>استعلام أو حظر مستخدم:</b>\n\nأرسل ID المستخدم أرقام فقط:", reply_markup=admin_cancel_action_keyboard(lang))

    elif data.startswith("adm_user_action:"):
        parts = data.split(":")
        act, target_uid = parts[1], int(parts[2])
        if act == "ban":
            ban_user_db(target_uid)
            await query.answer("🚫 تم حظر المستخدم")
        else:
            unban_user_db(target_uid)
            await query.answer("🟢 تم فك حظر المستخدم")
        return await process_user_info(query.message, context, target_uid)

    elif data == "adm_export_menu":
        await query.answer()
        return await edit_message_smart(query.message, "📂 <b>قائمة تصدير البيانات الشاملة:</b>\nاختر الصيغة المطلوبة:", reply_markup=admin_export_menu(lang))

    elif data == "adm_export_csv":
        await query.answer("جاري استخراج ملف CSV... 📊")
        csv_data = export_users_csv()
        file_bytes = io.BytesIO(csv_data.encode('utf-8'))
        file_bytes.name = f"PlayZone_Users_{int(time.time())}.csv"
        await context.bot.send_document(
            chat_id=query.message.chat_id, 
            document=file_bytes, 
            caption="📊 <b>سجل المستخدمين الكامل (صيغة CSV Excel)</b>",
            parse_mode="HTML"
        )
        return

    elif data == "adm_export_json":
        await query.answer("جاري تجهيز نسخة JSON... 📦")
        backup_json = export_firebase_backup_json()
        file_bytes = io.BytesIO(backup_json.encode('utf-8'))
        file_bytes.name = f"PlayZone_Backup_{int(time.time())}.json"
        await context.bot.send_document(
            chat_id=query.message.chat_id, 
            document=file_bytes, 
            caption="📦 <b>نسخة احتياطية كاملة لقاعدة البيانات (صيغة JSON)</b>",
            parse_mode="HTML"
        )
        return

    elif data == "adm_export_txt":
        await query.answer("جاري استخراج التقرير النصي... 📝")
        txt_report = generate_analytics_txt_report()
        file_bytes = io.BytesIO(txt_report.encode('utf-8'))
        file_bytes.name = f"PlayZone_Analytics_{int(time.time())}.txt"
        await context.bot.send_document(
            chat_id=query.message.chat_id, 
            document=file_bytes, 
            caption="📝 <b>تقرير الأداء التشخيصي الشامل (صيغة TXT)</b>",
            parse_mode="HTML"
        )
        return

    elif data == "adm_sec_menu":
        await query.answer()
        return await edit_message_smart(query.message, "🛡️ <b>قسم الصيانة وإعلانات Adsterra:</b>", reply_markup=admin_security_menu(lang))

    elif data == "adm_toggle_maint":
        current = get_setting("maintenance", "0")
        new_val = "0" if current == "1" else "1"
        set_setting("maintenance", new_val)
        await query.answer("✅ تم تحديث وضع الصيانة")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except BadRequest: pass

    elif data == "adm_toggle_adsterra":
        current = get_setting("adsterra_status", "1")
        new_val = "0" if current == "1" else "1"
        set_setting("adsterra_status", new_val)
        await query.answer("✅ تم تحديث حالة Adsterra")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except BadRequest: pass

    elif data == "adm_update_dlp":
        await query.answer("جاري تحديث محرك التحميل... ⏳")
        try:
            subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True)
            return await edit_message_smart(query.message, "✅ <b>تم تحديث محرك (yt-dlp) بنجاح!</b>", reply_markup=admin_security_menu(lang))
        except Exception:
            return await edit_message_smart(query.message, "❌ <b>تعذر استكمال التحديث في الوقت الحالي.</b>", reply_markup=admin_security_menu(lang))

    elif data == "adm_cookie_guide":
        await query.answer()
        guide_text = "🍪 <b>تحديث كوكيز يوتيوب:</b>\n\nأرسل ملف <code>cookies.txt</code> مباشرة هنا في المحادثة."
        return await edit_message_smart(query.message, guide_text, reply_markup=admin_security_menu(lang))
        
    elif data == "adm_vacuum_db":
        await query.answer("جاري تحسين الفهرسة... 🗜️")
        optimize_db()
        return await edit_message_smart(query.message, "✅ <b>تم تحسين الفهرسة بنجاح!</b>", reply_markup=admin_security_menu(lang))
        
    elif data == "adm_close":
        await query.answer("تم الإغلاق ✖️")
        return await safe_delete(query.message)
        
    elif data == "adm_stats":
        await query.answer()
        return await edit_message_smart(query.message, build_admin_stats_text(lang), reply_markup=admin_main_keyboard(lang))
        
    elif data == "adm_users":
        await query.answer()
        return await edit_message_smart(query.message, build_admin_users_text(10, lang), reply_markup=admin_users_menu(lang))
        
    elif data == "adm_server":
        await query.answer()
        return await edit_message_smart(query.message, build_server_status_text(lang), reply_markup=admin_main_keyboard(lang))
        
    elif data == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة... 🧹")
        removed = await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)
        return await edit_message_smart(query.message, f"✅ <b>تم التنظيف!</b>\nتم إزالة {removed} ملف.", reply_markup=admin_security_menu(lang))
