import csv
import io
import time
import shutil
import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import RetryAfter, BadRequest
from database.connection import db, firebase_init_error
from database.operations import (
    all_user_ids, get_active_users_48h, stat_inc_sync, load_stats_sync, 
    get_latest_users, get_setting, get_all_users_data, optimize_db, 
    set_setting, register_user_sync, ban_user_db
)
from utils.helpers import is_admin, _cleanup_old_downloads_sync, _force_cleanup_all_sync, format_size, esc, clean_title, alert_admins_live
from utils.keyboards import admin_main_keyboard, admin_broadcast_menu, admin_broadcast_cancel_keyboard, admin_users_menu, admin_security_menu
from locales.language import _t
from core.config import BASE_DOWNLOAD_DIR, COOKIES_FILE, DB_FILE, EXECUTOR
from core.security import BANNED_USERS_CACHE
import os
import subprocess

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    register_user_sync(update.effective_user)
    context.user_data.pop("bc_active", None)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(_t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")

async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        uid = int(context.args[0])
        doc_ref = db.collection('users').document(str(uid))
        u_doc = doc_ref.get()
        if u_doc.exists:
            u = u_doc.to_dict()
            status = "🔴 Banned" if uid in BANNED_USERS_CACHE else "🟢 Active"
            text = f"👤 <b>معلومات المستخدم:</b>\n\n• ID: <code>{u.get('id', uid)}</code>\n• الاسم: {esc(u.get('first_name'))} {esc(u.get('last_name'))}\n• المعرف: @{u.get('username')}\n• الحالة: {status}"
            await update.message.reply_text(text, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود بقاعدة البيانات.")
    except Exception as e:
        logger.error(f"Error checking user info on Firebase: {e}")
        await update.message.reply_text("طريقة الاستخدام: /user ID")

async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    msg = await update.message.reply_text("جاري تحديث مكتبة التحميل...")
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text("✅ تم تحديث yt-dlp لآخر إصدار بنجاح.")
    except Exception as e:
        await msg.edit_text(f"❌ فشل التحديث: {e}")

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    if not update.message.document:
        return await update.message.reply_text("الرجاء إرسال ملف cookies.txt كملف وثيقة مباشرة مع الأمر.")
    new_file = await context.bot.get_file(update.message.document.file_id)
    await new_file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم استقبال وتحديث ملف الكوكيز بنجاح.")

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    try:
        with open(DB_FILE, "rb") as f:
            await update.message.reply_document(document=f, filename="bot_database.db", caption="نسخة احتياطية لقاعدة البيانات المحلية.")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذر النسخ الاحتياطي: {e}")

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

async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_active"] = False
    lang = context.user_data.get("lang", "ar")
    target = context.user_data.get("bc_target", "all")
    users = all_user_ids() if target == "all" else get_active_users_48h()
    if not users: return await update.message.reply_text(_t("msg_adm_no_users", lang))
    status = await update.message.reply_text(_t("msg_adm_bc_start", lang))
    sent, fail = 0, 0
    total = len(users)
    for i, user_id in enumerate(users):
        try:
            await update.message.copy(chat_id=user_id)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e: 
            await asyncio.sleep(int(e.retry_after) + 1)
            try:
                await update.message.copy(chat_id=user_id)
                sent += 1
            except Exception: fail += 1
        except Exception: fail += 1
        if i % 20 == 0 and i > 0:
            try: await status.edit_text(f"⏳ <b>جاري تقدم الإذاعة:</b> {i} / {total}\n✅ نجاح: {sent} | ❌ فشل: {fail}", parse_mode="HTML")
            except Exception: pass
    stat_inc_sync("broadcasts")
    await status.edit_text(_t("msg_adm_bc_done", lang, sent=sent, fail=fail), parse_mode="HTML")

def build_admin_stats_text(lang: str = "ar") -> str:
    if db is None: return f"⚠️ <b>خطأ تشخيص حي في الاتصال بـ Firebase:</b>\n<code>{firebase_init_error}</code>"
    stats = load_stats_sync()
    total_users = len(all_user_ids())
    active_users = len(get_active_users_48h())
    downloaded = format_size(stats.get('bytes', 0), lang)
    return f"📊 <b>إحصائيات البوت الشاملة:</b>\n\n👥 إجمالي المستخدمين: <code>{total_users}</code>\n⚡ النشطين (آخر 48 ساعة): <code>{active_users}</code>\n\n📥 إجمالي الطلبات: <code>{stats.get('requests', 0)}</code>\n✅ الطلبات الناجحة: <code>{stats.get('success', 0)}</code>\n❌ الطلبات الفاشلة: <code>{stats.get('failed', 0)}</code>\n💾 حجم البيانات المحملة: <code>{downloaded}</code>\n📢 عدد الإذاعات: <code>{stats.get('broadcasts', 0)}</code>"

def build_admin_users_text(limit: int, lang: str = "ar") -> str:
    if db is None: return "⚠️ قاعدة البيانات غير متصلة."
    users = get_latest_users(limit)
    if not users: return "📋 لا يوجد مستخدمين بعد."
    text = "📋 <b>أحدث المستخدمين:</b>\n\n"
    for u in users:
        name = esc(f"{u.get('first_name', '')} {u.get('last_name', '')}".strip())
        if not name: name = "بدون اسم"
        text += f"• <code>{u.get('id', '0')}</code> | {name}\n"
    return text

def build_server_status_text(lang: str = "ar") -> str:
    total, used, free = shutil.disk_usage(BASE_DOWNLOAD_DIR)
    return f"📁 <b>حالة السيرفر ومساحة التخزين:</b>\n\n💽 المساحة الكلية: <code>{format_size(total, lang)}</code>\n🟢 المساحة المستخدمة: <code>{format_size(used, lang)}</code>\n⚪ المساحة الحرة: <code>{format_size(free, lang)}</code>\n\n⚙️ مسار التحميل: <code>{BASE_DOWNLOAD_DIR}</code>"

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    lang = context.user_data.get("lang", "ar")
    if data == "adm_main_back":
        await query.answer()
        return await edit_message_smart(query.message, _t("msg_adm_panel", lang), reply_markup=admin_main_keyboard(lang))
    elif data == "adm_bc_menu":
        await query.answer()
        return await edit_message_smart(query.message, "📢 <b>خيارات الإذاعة الشاملة:</b>\nاختر الشريحة المستهدفة:", reply_markup=admin_broadcast_menu(lang))
    elif data.startswith("adm_bc_start:"):
        target = data.split(":")[1]
        context.user_data["bc_active"] = True
        context.user_data["bc_target"] = target
        await query.answer()
        return await edit_message_smart(query.message, _t("msg_adm_bc_ask", lang), reply_markup=admin_broadcast_cancel_keyboard(lang))
    elif data == "adm_cancel_bc":
        context.user_data["bc_active"] = False
        await query.answer("تم إلغاء الإذاعة ❌")
        return await edit_message_smart(query.message, "✅ تم إلغاء وضع الإذاعة بنجاح.", reply_markup=admin_main_keyboard(lang))
    elif data == "adm_users_menu":
        await query.answer()
        return await edit_message_smart(query.message, "👥 <b>إدارة المستخدمين:</b>", reply_markup=admin_users_menu(lang))
    elif data == "adm_export_db":
        await query.answer("جاري سحب البيانات... 📥")
        users = get_all_users_data()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Username", "First Name", "Last Name", "First Seen", "Last Seen"])
        for u in users:
            writer.writerow([u.get('id',''), u.get('username',''), u.get('first_name',''), u.get('last_name',''), u.get('first_seen',''), u.get('last_seen','')])
        output.seek(0)
        file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
        file_bytes.name = f"PlayZone_Users_{int(time.time())}.csv"
        await context.bot.send_document(chat_id=query.message.chat_id, document=file_bytes, caption="📊 نسخة كاملة من بيانات المستخدمين.")
        return
    elif data == "adm_sec_menu":
        await query.answer()
        return await edit_message_smart(query.message, "🛡️ <b>خيارات الصيانة والحماية:</b>", reply_markup=admin_security_menu(lang))
    elif data == "adm_toggle_maint":
        current = get_setting("maintenance", "0")
        new_val = "0" if current == "1" else "1"
        set_setting("maintenance", new_val)
        await query.answer("✅ تم تحديث حالة الصيانة")
        return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        
    # --- تمت إضافة وظائف الأزرار الجديدة هنا ---
    elif data == "adm_update_dlp":
        await query.answer("جاري تحديث محرك التحميل... قد يستغرق الأمر ثواني ⏳")
        try:
            subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True)
            return await edit_message_smart(query.message, "✅ <b>تم تحديث محرك التحميل (yt-dlp) لآخر إصدار بنجاح!</b>", reply_markup=admin_security_menu(lang))
        except Exception as e:
            return await edit_message_smart(query.message, f"❌ <b>حدث خطأ أثناء التحديث:</b>\n<code>{e}</code>", reply_markup=admin_security_menu(lang))

    elif data == "adm_backup_db":
        await query.answer("جاري سحب وتجهيز النسخة الاحتياطية... 💾")
        if DB_FILE.exists():
            await context.bot.send_document(chat_id=query.message.chat_id, document=open(DB_FILE, 'rb'), filename="bot_database.db", caption="✅ النسخة الاحتياطية المحدثة لقاعدة البيانات.")
        return await edit_message_smart(query.message, "✅ تم إرسال النسخة الاحتياطية بنجاح.", reply_markup=admin_security_menu(lang))

    elif data == "adm_cookie_guide":
        await query.answer()
        guide_text = (
            "🍪 <b>طريقة تحديث الكوكيز بدون إيقاف السيرفر:</b>\n\n"
            "1. استخرج ملف <code>cookies.txt</code> جديد من المتصفح.\n"
            "2. قم بإرسال الملف مباشرة هنا في المحادثة كـ (ملف/Document).\n"
            "3. اكتب في وصف الملف (Caption) الأمر المخصص <code>/setcookie</code> وسيقوم النظام بتبديله فوراً."
        )
        return await edit_message_smart(query.message, guide_text, reply_markup=admin_security_menu(lang))
    # ----------------------------------------
        
    elif data == "adm_vacuum_db":
        await query.answer("جاري تحسين القاعدة... 🗜️")
        optimize_db()
        return await edit_message_smart(query.message, "✅ <b>تم ضغط وتحسين قاعدة البيانات بنجاح!</b>", reply_markup=admin_main_keyboard(lang))
    elif data == "adm_close":
        await query.answer("تم الإغلاق ✖️")
        return await safe_delete(query.message)
    elif data == "adm_stats":
        await query.answer()
        return await edit_message_smart(query.message, build_admin_stats_text(lang), reply_markup=admin_users_menu(lang))
    elif data == "adm_users":
        await query.answer()
        return await edit_message_smart(query.message, build_admin_users_text(10, lang), reply_markup=admin_users_menu(lang))
    elif data == "adm_server":
        await query.answer()
        return await edit_message_smart(query.message, build_server_status_text(lang), reply_markup=admin_main_keyboard(lang))
    elif data == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة... 🧹")
        removed = await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)
        return await edit_message_smart(query.message, f"✅ <b>تم التنظيف!</b>\nتم إزالة {removed} ملف مؤقت.", reply_markup=admin_security_menu(lang))
