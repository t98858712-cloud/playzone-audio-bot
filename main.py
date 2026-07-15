import sys
import time
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import Conflict

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("PlayZoneEnterpriseBot")
logging.getLogger("httpx").setLevel(logging.WARNING)

try:
    from core.config import TOKEN
except ImportError:
    logger.error("❌ لم يتم العثور على توكن البوت في ملف الإعدادات core.config!")
    sys.exit(1)

from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel
from handlers.callbacks import handle_callbacks
from database.operations import load_banned_users
from services.downloader import youtube_health_monitor

async def handle_health_check(reader, writer):
    response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
    try:
        writer.write(response)
        await writer.drain()
    except Exception: pass
    finally:
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass

async def start_health_check_server():
    try:
        server = await asyncio.start_server(handle_health_check, "0.0.0.0", 8080)
        logger.info("🌐 تم تشغيل سيرفر فحص الجاهزية بنجاح على البورت 8080")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"❌ فشل تشغيل سيرفر فحص الجاهزية: {e}")

async def post_init(application):
    await asyncio.to_thread(load_banned_users)
    asyncio.create_task(start_health_check_server())
    asyncio.create_task(youtube_health_monitor(application))
    logger.info("🚀 تم تشغيل البوت بنظام الإدارة المؤسسية بنجاح.")

def main():
    logger.info("⏳ جاري الانتظار 5 ثوانٍ لضمان إغلاق أي جلسات نشطة مسبقاً...")
    time.sleep(5)

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("language", toggle_lang_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_incoming_text))

    try:
        logger.info("🔌 جاري الاتصال بخوادم Telegram...")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Conflict:
        logger.warning("⚠️ تم كشف تعارض (Conflict)! هناك نسخة أخرى تعمل بنفس التوكن حالياً.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ حدث خطأ غير متوقع أثناء تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
