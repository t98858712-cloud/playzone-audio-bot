import csv
import io
import time
import shutil
import subprocess
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from database.connection import db, firebase_init_error
from database.operations import all_user_ids, get_active_users_48h, stat_inc_sync, load_stats_sync, get_latest_users, get_setting, get_all_users_data, optimize_db, set_setting, register_user_sync
from core.helpers import is_admin, _force_cleanup_all_sync, esc
from core.format import format_size
from buttons.keyboards import admin_main_keyboard, admin_broadcast_menu, admin_cancel_action_keyboard, admin_users_menu, admin_security_menu
from locales.language import _t
from core.config import BASE_DOWNLOAD_DIR, DB_FILE

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    register_user_sync(update.effective_user)
    context.user_data.pop("bc_active", None)
    context.user_data.pop("awaiting_user_id", None)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(_t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")

async def process_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    try:
        from core.state import BANNED_USERS_CACHE
        u_doc = db.collection('users').document(str(uid)).get()
        if u_doc.exists:
            u = u_doc.to_dict()
            status = "🔴 Banned (محظور)" if uid in BANNED_USERS_CACHE else "🟢 Active (نشط)"
            await update.message.reply_text(f"👤 <b>معلومات المستخدم:</b>\n\n• ID: <code>{u.get('id', uid)}</code>\n• الاسم: {esc(u.get('first_name'))}\n• الحالة: {status}", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود.")
    except Exception:
        await update.message.reply_text("❌ خطأ الاستعلام.")

async def safe_delete(message):
    try:
        await message.delete()
    except:
        pass

async def edit_message_smart(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try:
        if getattr(message, "photo", None) or getattr(message, "video", None):
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise

def build_admin_stats_text(lang: str = "ar") -> str:
    if db is None:
        return f"⚠️ خطأ: {firebase_init_error}"
    stats = load_stats_sync()
    total_users = len(all_user_ids())
    active_users = len(get_active_users_48h())
    return f"📊 <b>إحصائيات البوت:</b>\n\n👥 إجمالي المستخدمين: <code>{total_users}</code>\n⚡ النشطين: <code>{active_users}</code>\n📥 الطلبات: <code>{stats.get('requests', 0)}</code>"

def build_admin_users_text(limit: int, lang: str = "ar") -> str:
    if db is None:
        return "⚠️ غير متصل."
    users = get_latest_users(limit)
    text = "📋 <b>أحدث المستخدمين:</b>\n\n"
    for u in users:
        text += f"• <code>{u.get('id', '0')}</code> | {esc(u.get('first_name', 'بدون اسم'))}\n"
    return text

def build_server_status_text(lang: str = "ar") -> str:
    total, used, free = shutil.disk_usage(BASE_DOWNLOAD_DIR)
    return f"📁 <b>التخزين:</b>\n\n💽 الكلية: <code>{format_size(total, lang)}</code>\n🟢 المستخدمة: <code>{format_size(used, lang)}</code>"

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    lang = context.user_data.get("lang", "ar")
    if data == "adm_main_back":
        context.user_data.pop("awaiting_user_id", None)
        return await edit_message_smart(query.message, _t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang))
    elif data in ["adm_cancel_bc", "adm_cancel_action"]:
        context.user_data.pop("bc_active", None)
        context.user_data.pop("awaiting_user_id", None)
        await query.answer("تم الإلغاء ❌")
        return await edit_message_smart(query.message, "✅ تم إلغاء الإجراء بنجاح.", reply_markup=admin_main_keyboard(lang))
    elif data == "adm_bc_menu":
        return await edit_message_smart(query.message, "📢 <b>قسم الإذاعة الشاملة:</b>", reply_markup=admin_broadcast_menu(lang))
    elif data.startswith("adm_bc_start:"):
        context.user_data["bc_active"] = True
        context.user_data["bc_target"] = data.split(":")[1]
        return await edit_message_smart(query.message, _t("msg_adm_bc_ask", lang), reply_markup=admin_cancel_action_keyboard(lang))
    elif data == "adm_users_menu":
        return await edit_message_smart(query.message, "👥 <b>قسم إدارة المستخدمين:</b>", reply_markup=admin_users_menu(lang))
    elif data == "adm_user_info_prompt":
        context.user_data["awaiting_user_id"] = True
        return await edit_message_smart(query.message, "✍️ أرسل ID المستخدم الآن للاستعلام:", reply_markup=admin_cancel_action_keyboard(lang))
    elif data == "adm_export_db":
        await query.answer("جاري التصدير...")
        users = get_all_users_data()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Username", "First Name"])
        for u in users:
            writer.writerow([u.get('id',''), u.get('username',''), u.get('first_name','')])
        output.seek(0)
        file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
        file_bytes.name = f"Users_{int(time.time())}.csv"
        await context.bot.send_document(chat_id=query.message.chat_id, document=file_bytes, caption="📊 ملف الأعضاء.")
        return
    elif data == "adm_sec_menu":
        return await edit_message_smart(query.message, "🛡️ <b>قسم الصيانة والحماية:</b>", reply_markup=admin_security_menu(lang))
    elif data == "adm_toggle_maint":
        set_setting("maintenance", "0" if get_setting("maintenance", "0") == "1" else "1")
        await query.answer("تحديث وضع الصيانة")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except:
            return
    elif data == "adm_toggle_hilltop":
        set_setting("hilltop_status", "0" if get_setting("hilltop_status", "1") == "1" else "1")
        await query.answer("تحديث HilltopAds")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except:
            return
    elif data == "adm_toggle_adsterra":
        set_setting("adsterra_status", "0" if get_setting("adsterra_status", "1") == "1" else "1")
        await query.answer("تحديث Adsterra")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except:
            return
    elif data == "adm_update_dlp":
        await query.answer("جاري التحديث... ⏳")
        try:
            subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True)
            return await edit_message_smart(query.message, "✅ تم التحديث بنجاح!", reply_markup=admin_security_menu(lang))
        except Exception as e:
            return await edit_message_smart(query.message, f"❌ فشل: {e}", reply_markup=admin_security_menu(lang))
    elif data == "adm_backup_db":
        await query.answer("جاري التجهيز... 💾")
        from database.operations import export_firebase_backup_json
        backup_json = export_firebase_backup_json()
        if backup_json:
            file_bytes = io.BytesIO(backup_json.encode('utf-8'))
            file_bytes.name = f"Backup_{int(time.time())}.json"
            await context.bot.send_document(chat_id=query.message.chat_id, document=file_bytes, caption="✅ نسخة Firestore JSON.")
            return await edit_message_smart(query.message, "✅ تم النسخ.", reply_markup=admin_security_menu(lang))
    elif data == "adm_cookie_guide":
        return await edit_message_smart(query.message, "🍪 أرسل ملف cookies.txt مباشرة هنا للتحديث تلقائياً.", reply_markup=admin_security_menu(lang))
    elif data == "adm_vacuum_db":
        optimize_db()
        await query.answer("🗜️ تم تحسين السيرفر.")
        return await edit_message_smart(query.message, "✅ تم التحسين آلياً.", reply_markup=admin_security_menu(lang))
    elif data == "adm_close":
        await query.answer("تم الإغلاق")
        return await safe_delete(query.message)
    elif data == "adm_stats":
        return await edit_message_smart(query.message, build_admin_stats_text(lang), reply_markup=admin_users_menu(lang))
    elif data == "adm_users":
        return await edit_message_smart(query.message, build_admin_users_text(10, lang), reply_markup=admin_users_menu(lang))
    elif data == "adm_server":
        return await edit_message_smart(query.message, build_server_status_text(lang), reply_markup=admin_main_keyboard(lang))
    elif data == "adm_clean":
        await query.answer("جاري التنظيف... 🧹")
        removed = await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)
        return await edit_message_smart(query.message, f"✅ تم حذف {removed} ملف مؤقت.", reply_markup=admin_security_menu(lang))
