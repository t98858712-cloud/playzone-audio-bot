import time
import logging
import asyncio
import os

# المكتبات الخاصة بتليجرام
from telegram import (
    Update, 
    BotCommand, 
    BotCommandScopeChat, 
    MenuButtonCommands, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CopyTextButton
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters
)

# الاستدعاءات المحلية (تأكد من وجود هذه الملفات في مشروعك)
from core.config import TOKEN, LOCAL_API_URL
import core.security as sec
from database.connection import init_db
from database.operations import load_banned_users
from utils.helpers import _cleanup_old_downloads_sync, parse_admin_ids
from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel
from handlers.callbacks import handle_callbacks
from services.downloader import youtube_health_monitor

# إعداد سجلات النظام (Logging)
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")

for noisy_logger in ["httpx", "httpcore", "telegram", "telegram.ext"]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)


async def user_id(update: Update, context):
    user = update.effective_user
    username = f"@{user.username}" if user.username else "لا يوجد"

    await update.message.reply_text(
        f"🆔 ID: {user.id}\n👤 Username: {username}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copy ID", copy_text=CopyTextButton(text=str(user.id)))],
            [InlineKeyboardButton("👤 Copy User", copy_text=CopyTextButton(text=username))]
        ])
    )


async def start_health_check_server():
    """سيرفر ويب مدمج خفيف لتلبية فحص الجاهزية (Health Check) لمنصة Railway لضمان استقرار البوت 24 ساعة"""
    port = int(os.environ.get("PORT", 8080))
    
    async def handle_client(reader, writer):
        try:
            data = await reader.read(1024)
            # الرد المباشر بـ نجاح الفحص للحفاظ على استقرار الحاوية في السيرفر
            response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"status\":\"healthy\"}"
            writer.write(response.encode('utf-8'))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", port)
        logger.info(f"🌐 تم تشغيل سيرفر فحص الجاهزية بنجاح على البورت {port}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start HTTP Server: {e}")


async def post_init(app: Application):
    user_commands = [
        BotCommand("start", "بدء / Start"), 
        BotCommand("language", "تغيير اللغة / Toggle Language"), 
        BotCommand("links", "الروابط / Links"),
        BotCommand("id", "المعرف / User ID")
    ]
    admin_commands = user_commands + [BotCommand("admin", "لوحة التحكم / Admin Panel")]

    try:
        # 1. تهيئة أوامر البوت العادية والأدمن
        await app.bot.set_my_commands(user_commands)
        for admin_id in parse_admin_ids():
            try:
                await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(admin_id))
            except Exception:
                pass

        # 2. الحفاظ على زر قائمة السلاش الافتراضي في الزاوية الداخلية دون تداخل
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("✅ تم تثبيت زر السلاش والمينيو الداخلي بنجاح.")

    except Exception as e:
        logger.warning(f"فشل تهيئة الأوامر: {e}")

    # تشغيل المهام الخلفية
    try:
        asyncio.create_task(youtube_health_monitor(app))
        asyncio.create_task(start_health_check_server())
    except Exception as e:
        logger.error(f"فشل تشغيل المهام الخلفية: {e}")


def main():
    if not TOKEN: 
        raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")

    # تهيئة قاعدة البيانات والملفات
    init_db()
    sec.BANNED_USERS_CACHE = load_banned_users()
    _cleanup_old_downloads_sync()

    # بناء البوت
    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL:
        builder.base_url(LOCAL_API_URL)

    app = (
        builder.post_init(post_init)
        .connect_timeout(30)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(30)
        .concurrent_updates(True)
        .build()
    )

    # إضافة الهاندلرز (Handlers)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", toggle_lang_command))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("id", user_id))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("⏳ انتظار 10 ثوانٍ قبل بدء تشغيل البوت...")
    time.sleep(10)
    logger.info("🚀 تم تشغيل البوت بنظام الإدارة المؤسسية (Enterprise Control Center) بنجاح.")
    
    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
