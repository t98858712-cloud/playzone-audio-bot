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
    get_latest_users, get_setting, get_all_users_data, 
    set_setting, register_user_sync, ban_user_db
)
from utils.helpers import is_admin, _force_cleanup_all_sync, format_size, esc, alert_admins_live
from utils.keyboards import admin_main_keyboard, admin_broadcast_menu, admin_cancel_action_keyboard, admin_users_menu, admin_security_menu
from locales.language import _t
from core.config import BASE_DOWNLOAD_DIR, DB_FILE
from core.security import BANNED_USERS_CACHE

logger = logging.getLogger("PlayZoneEnterpriseBot")

def get_dashboard_text(lang: str = "ar") -> str:
    stats = load_stats_sync()
    total_users = len(all_user_ids())
    maint = get_setting("maintenance", "0")
    status_text = "🟢 متصل ومتاح للجميع" if maint == "0" else "🔴 مغلق لوضع الصيانة"

    return (
        "👑 <b>لوحة القيادة والمراقبة المتقدمة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 <b>حالة البوت:</b> {status_text}\n"
        f"👥 <b>المشتركين:</b> <code>{total_users}</code> عضو\n"
        f"📥 <b>الطلبات الناجحة:</b> <code>{stats.get('success', 0)}</code>\n"
        f"⚠️ <b>الطلبات الفاشلة:</b> <code>{stats.get('failed', 0)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>خيارات القيادة والتحكم السريعة:</b>"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    register_user_sync(update.effective_user)
    context.user_data.pop("bc_active", None)
    context.user_data.pop("awaiting_user_id", None)
    lang = context.user_data.get("lang", "ar")
    
    dashboard_text = get_dashboard_text(lang)
    await update.message.reply_text(dashboard_text, reply_markup=admin_main_keyboard(lang), parse_mode="HTML")

async def process_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    try:
        doc_ref = db.collection('users').document(str(uid))
        u_doc = doc_ref.get()
        if u_doc.exists:
            u = u_doc.to_dict()
            status = "🔴 Banned (محظور)" if uid in BANNED_USERS_CACHE else "🟢 Active (نشط)"
            text = f"👤 <b>معلومات المستخدم:</b>\n\n• ID: <code>{u.get('id', uid)}</code>\n• الاسم: {esc(u.get('first_name'))} {esc(u.get('last_name'))}\n• المعرف: @{u.get('username')}\n• الحالة: {status}"
            await update.message.reply_text(text, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود بقاعدة البيانات.")
    except Exception as e:
        logger.error(f"Error checking user info on Firebase: {e}")
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

async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_active"] = False
    lang = context.user_data.get("lang", "ar")
    target = context.user_data.get("bc_target", "all")
    users = all_user_ids() if target == "all" else get_active_users_48h()
    if not users: return update.message.reply_text(_t("msg_adm_no_users", lang))
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
    if db is None: return f"⚠️ <b>خطأ في الاتصال بـ Firebase:</b>\n<code>{firebase_init_error}</code>"
    stats = load_stats_sync()
    total_users = len(all_user_ids())
    active_users = len(get_active_users_48h())
    downloaded = format_size(stats.get('bytes', 0), lang)
    return f"📊 <b>إحصائيات البوت التفصيلية:</b>\n\n👥 إجمالي الأعضاء: <code>{total_users}</code>\n⚡ النشطين (آخر 48 ساعة): <code>{active_users}</code>\n\n📥 إجمالي الطلبات: <code>{stats.get('requests', 0)}</code>\n✅ الطلبات الناجحة: <code>{stats.get('success', 0)}</code>\n❌ الطلبات الفاشلة: <code>{stats.get('failed', 0)}</code>\n💾 حجم الداتا المحملة: <code>{downloaded}</code>\n📢 عدد الإذاعات المُرسلة: <code>{stats.get('broadcasts', 0)}</code>"

def build_admin_users_text(limit: int, lang: str = "ar") -> str:
    if db is None: return "⚠️ قاعدة البيانات غير متصلة."
    users = get_latest_users(limit)
    if not users: return "📋 لا يوجد مستخدمين بعد."
    text = "📋 <b>أحدث المنضمين للبوت:</b>\n\n"
    for u in users:
        name = esc(f"{u.get('first_name', '')} {u.get('last_name', '')}".strip())
        if not name: name = "بدون اسم"
        text += f"• <code>{u.get('id', '0')}</code> | {name}\n"
    return text

def build_server_status_text(lang: str = "ar") -> str:
    total, used, free = shutil.disk_usage(BASE_DOWNLOAD_DIR)
    return f"💽 <b>حالة مساحة التخزين لخادم الميديا:</b>\n\n📁 المساحة الكلية: <code>{format_size(total, lang)}</code>\n🟢 المساحة المستخدمة: <code>{format_size(used, lang)}</code>\n⚪ المساحة الحرة: <code>{format_size(free, lang)}</code>"

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    lang = context.user_data.get("lang", "ar")
    
    if data == "adm_main_back":
        await query.answer()
        context.user_data.pop("awaiting_user_id", None)
        dashboard_text = get_dashboard_text(lang)
        return await edit_message_smart(query.message, dashboard_text, reply_markup=admin_main_keyboard(lang))
        
    elif data == "adm_cancel_bc" or data == "adm_cancel_action":
        context.user_data.pop("bc_active", None)
        context.user_data.pop("awaiting_user_id", None)
        await query.answer("تم الإلغاء ❌")
        dashboard_text = get_dashboard_text(lang)
        return await edit_message_smart(query.message, dashboard_text, reply_markup=admin_main_keyboard(lang))

    elif data == "adm_bc_menu":
        await query.answer()
        return await edit_message_smart(query.message, "📢 <b>قسم الإذاعة والتواصل:</b>\nيرجى تحديد فئة الإرسال المستهدفة:", reply_markup=admin_broadcast_menu(lang))
    
    elif data.startswith("adm_bc_start:"):
        target = data.split(":")[1]
        context.user_data["bc_active"] = True
        context.user_data["bc_target"] = target
        await query.answer()
        return await edit_message_smart(query.message, _t("msg_adm_bc_ask", lang), reply_markup=admin_cancel_action_keyboard(lang))
        
    elif data == "adm_users_menu":
        await query.answer()
        return await edit_message_smart(query.message, "👥 <b>لوحة التحكم بالمشتركين:</b>", reply_markup=admin_users_menu(lang))
        
    elif data == "adm_user_info_prompt":
        context.user_data["awaiting_user_id"] = True
        await query.answer()
        return await edit_message_smart(query.message, "✍️ <b>فحص مستخدم سريع:</b>\n\nالرجاء إرسال ID المستخدم للاستعلام عن بياناته وحالته حياً:", reply_markup=admin_cancel_action_keyboard(lang))

    elif data == "adm_export_db":
        await query.answer("جاري تصدير تقرير البيانات... 📥")
        users = get_all_users_data()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Username", "First Name", "Last Name", "First Seen", "Last Seen"])
        for u in users:
            writer.writerow([u.get('id',''), u.get('username',''), u.get('first_name',''), u.get('last_name',''), u.get('first_seen',''), u.get('last_seen','')])
        output.seek(0)
        file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
        file_bytes.name = f"PlayZone_Users_{int(time.time())}.csv"
        await context.bot.send_document(chat_id=query.message.chat_id, document=file_bytes, caption="📊 تقرير شامل لكافة بيانات المشتركين.")
        return
        
    elif data == "adm_sec_menu":
        await query.answer()
        return await edit_message_smart(query.message, "🛡️ <b>لوحة التحكم بإعدادات النظام والحماية:</b>", reply_markup=admin_security_menu(lang))

    elif data == "adm_toggle_maint":
        current = get_setting("maintenance", "0")
        new_val = "0" if current == "1" else "1"
        set_setting("maintenance", new_val)
        await query.answer("✅ تم تحديث حالة الصيانة")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except BadRequest: pass

    elif data == "adm_toggle_hilltop":
        current = get_setting("hilltop_status", "1")
        new_val = "0" if current == "1" else "1"
        set_setting("hilltop_status", new_val)
        await query.answer("✅ تم تحديث إعلانات Hilltop")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except BadRequest: pass

    elif data == "adm_toggle_adsterra":
        current = get_setting("adsterra_status", "1")
        new_val = "0" if current == "1" else "1"
        set_setting("adsterra_status", new_val)
        await query.answer("✅ تم تحديث إعلانات AdSterra")
        try:
            return await query.message.edit_reply_markup(reply_markup=admin_security_menu(lang))
        except BadRequest: pass

    elif data == "adm_update_dlp":
        await query.answer("جاري تحديث المحرك... ⏳")
        try:
            process = await asyncio.create_subprocess_exec(
                "pip", "install", "-U", "yt-dlp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return await edit_message_smart(query.message, "✅ <b>تم تحديث محرك التحميل (yt-dlp) بنجاح!</b>", reply_markup=admin_security_menu(lang))
            else:
                raise RuntimeError(stderr.decode().strip())
        except Exception as e:
            return await edit_message_smart(query.message, f"❌ <b>حدث خطأ أثناء التحديث:</b>\n<code>{e}</code>", reply_markup=admin_security_menu(lang))

    elif data == "adm_backup_db":
        await query.answer("جاري تجهيز النسخة الاحتياطية... 💾")
        from database.operations import export_firebase_backup_json
        backup_json = export_firebase_backup_json()
        if backup_json:
            file_bytes = io.BytesIO(backup_json.encode('utf-8'))
            file_bytes.name = f"Firebase_Backup_{int(time.time())}.json"
            await context.bot.send_document(chat_id=query.message.chat_id, document=file_bytes, filename=file_bytes.name, caption="✅ نسخة احتياطية كاملة من خادم Firebase Firestore (JSON).")
            return await edit_message_smart(query.message, "✅ تم إرسال النسخة الاحتياطية بنجاح.", reply_markup=admin_security_menu(lang))
        else:
            if DB_FILE.exists():
                with open(DB_FILE, 'rb') as f:
                    await context.bot.send_document(chat_id=query.message.chat_id, document=f, filename="bot_database.db", caption="✅ النسخة الاحتياطية المحلية (SQLite).")
                return await edit_message_smart(query.message, "✅ تم إرسال النسخة المحلية بنجاح.", reply_markup=admin_security_menu(lang))
            else:
                return await edit_message_smart(query.message, "❌ فشل سحب نسخة احتياطية، الداتا غير متوفرة.", reply_markup=admin_security_menu(lang))

    elif data == "adm_cookie_guide":
        await query.answer()
        guide_text = (
            "🍪 <b>تحديث كوكيز يوتيوب:</b>\n\n"
            "قم باستخراج ملف <code>cookies.txt</code> جديد من متصفحك الشخصي، ثم أرسله مباشرة كملف في هذه المحادثة وسيقوم البوت باستبداله وتفعيله فوراً."
        )
        return await edit_message_smart(query.message, guide_text, reply_markup=admin_security_menu(lang))
        
    elif data == "adm_close":
        await query.answer("تم تسجيل الخروج بنجاح ✖️")
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
        return await edit_message_smart(query.message, f"✅ <b>تم تفريغ مساحة التخزين المؤقتة بنجاح!</b>\nتم مسح {removed} ملف ميديا معلق بنجاح.", reply_markup=admin_security_menu(lang))
