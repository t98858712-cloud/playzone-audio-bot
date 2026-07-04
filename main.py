import logging
import asyncio
from telegram import Update, BotCommand, BotCommandScopeChat, MenuButtonCommands
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# استدعاء الإعدادات وقواعد البيانات والمساعدات
from core.config import TOKEN, LOCAL_API_URL
import core.security as sec
from database.connection import init_db
from database.operations import load_banned_users
from utils.helpers import _cleanup_old_downloads_sync, parse_admin_ids

# استدعاء معالجات المستخدم والإدارة
from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel, handle_admin_inputs
from handlers.callbacks import handle_callbacks
from services.downloader import youtube_health_monitor

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
    
    # أمر الإدارة الوحيد الآن هو /admin فقط
    admin_commands = user_commands + [
        BotCommand("admin", "لوحة التحكم / Admin Panel")
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

# دالة وسيطة لتوجيه الرسائل (نصوص أو ملفات) بشكل صحيح
async def main_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نتحقق أولاً إذا كان الحدث يخص الإدارة (استقبال كوكيز أو ID مستخدم)
    if await handle_admin_inputs(update, context):
        return # إذا تم التعامل معه كمدخل إداري، نتوقف هنا
        
    # إذا لم يكن مدخل إداري، وكان نصاً، نمرره للمستخدم العادي (للبحث والروابط)
    if update.message and update.message.text:
        await handle_incoming_text(update, context)

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")

    init_db()
    sec.BANNED_USERS_CACHE = load_banned_users()
    _cleanup_old_downloads_sync()

    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL: builder.base_url(LOCAL_API_URL)

    app = (
        builder.post_init(post_init)
        .connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30)
        .concurrent_updates(True)
        .build()
    )

    # الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", toggle_lang_command))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("admin", admin_panel)) # الأمر الإداري الوحيد
    
    # التقاط جميع الرسائل (النصوص والمستندات) وتوجيهها للموجه الرئيسي
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL & ~filters.COMMAND, main_message_router))
    
    # الأزرار
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بنظام لوحة التحكم الموحدة (بدون سلاشات) بنجاح.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
