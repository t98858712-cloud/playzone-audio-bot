import logging
import asyncio
from telegram import Update, BotCommand, BotCommandScopeChat, MenuButtonCommands
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# استدعاء الإعدادات
from core.config import TOKEN, LOCAL_API_URL
import core.security as sec

# استدعاء قواعد البيانات
from database.connection import init_db
from database.operations import load_banned_users

# استدعاء المساعدات (المسار الصحيح الذي تم تصحيحه)
from utils.helpers import _cleanup_old_downloads_sync, parse_admin_ids

# استدعاء معالجات المستخدم والإدارة
from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel, user_info_command, update_ytdlp_command, set_cookie_command, backup_db_command
from handlers.callbacks import handle_callbacks

# استدعاء الخدمات
from services.downloader import youtube_health_monitor

# إعداد السجلات
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")

for noisy_logger in ["httpx", "httpcore", "telegram", "telegram.ext"]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

async def post_init(app: Application):
    user_commands = [
        BotCommand("start", "بدء / Start"), 
        BotCommand("language", "تغيير اللغة / Toggle Language"), 
        BotCommand("links", "الروابط / Links")
    ]
    
    admin_commands = user_commands + [
        BotCommand("admin", "لوحة التحكم / Admin Panel"),
        BotCommand("user", "معلومات مستخدم / User Info"),
        BotCommand("update_dlp", "تحديث مكتبات / Update"),
        BotCommand("setcookie", "تحديث الكوكيز / Set Cookie"),
        BotCommand("backup", "نسخة احتياطية / Backup DB")
    ]

    try:
        await app.bot.set_my_commands(user_commands)
        
        for admin_id in parse_admin_ids():
            try:
                await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(admin_id))
            except Exception as e:
                logger.warning(f"فشل تعيين أوامر الإدارة للمدير {admin_id}: {e}")

        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        asyncio.create_task(youtube_health_monitor(app))
    except Exception as e:
        logger.warning(f"فشل تهيئة الأوامر: {e}")

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")

    # تهيئة قاعدة البيانات والكاش
    init_db()
    sec.BANNED_USERS_CACHE = load_banned_users()
    
    # تنظيف الملفات القديمة
    _cleanup_old_downloads_sync()

    # بناء التطبيق
    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL:
        builder.base_url(LOCAL_API_URL)

    app = (
        builder.post_init(post_init)
        .connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30)
        .concurrent_updates(True)
        .build()
    )

    # تسجيل مسارات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", toggle_lang_command))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("user", user_info_command))
    app.add_handler(CommandHandler("update_dlp", update_ytdlp_command))
    app.add_handler(CommandHandler("setcookie", set_cookie_command))
    app.add_handler(CommandHandler("backup", backup_db_command))
    
    # تسجيل مسار الرسائل النصية
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_text))
    
    # تسجيل مسار الأزرار
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بنظام الإدارة المؤسسية (Enterprise Control Center) بعد التقسيم بنجاح.")
    
    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
