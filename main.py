import os
import sys
import time
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import Conflict

# إعداد الـ Logging لعرض العمليات وتسهيل المراقبة
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("PlayZoneEnterpriseBot")

# استيراد التوكن الآمن بحماية مرنة لأسماء المتغيرات
try:
    from core.config import TELEGRAM_TOKEN as TOKEN
except ImportError:
    try:
        from core.config import TOKEN
    except ImportError:
        logger.error("❌ لم يتم العثور على توكن البوت في ملف الإعدادات core.config!")
        sys.exit(1)

# استيراد معالجات الحركات واللوحات من المشروع
from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel
from handlers.callbacks import handle_callbacks
from database.operations import load_banned_users
from services.downloader import youtube_health_monitor

# =======================================================
#    سيرفر فحص الجاهزية الذكي لـ Railway (بورت 8080)
# =======================================================

async def handle_health_check(reader, writer):
    """إرسال استجابة HTTP 200 قياسية لمنصة Railway لتأكيد تشغيل الحاوية"""
    response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
    try:
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

async def start_health_check_server():
    """تشغيل خادم ويب خفيف وصامت تماماً في الخلفية لتلبية فحص الجاهزية"""
    try:
        server = await asyncio.start_server(handle_health_check, "0.0.0.0", 8080)
        logger.info("🌐 تم تشغيل سيرفر فحص الجاهزية بنجاح على البورت 8080")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"❌ فشل تشغيل سيرفر فحص الجاهزية: {e}")

# =======================================================
#             إعدادات ما بعد الإقلاع (Post Init)
# =======================================================

async def post_init(application):
    # 1. شحن كاش الحماية للمستخدمين المحظورين من فايربيس فوراً عند التشغيل
    await asyncio.to_thread(load_banned_users)
    
    # 2. تشغيل سيرفر فحص الجاهزية في الخلفية لمنع توقف Railway
    asyncio.create_task(start_health_check_server())
    
    # 3. إطلاق الفحص الدوري الذاتي لصحة اليوتيوب والكوكيز
    asyncio.create_task(youtube_health_monitor(application))
    
    logger.info("🚀 تم تشغيل البوت بنظام الإدارة المؤسسية (Enterprise Control Center) بنجاح.")

# =======================================================
#                    الدالة الأساسية
# =======================================================

def main():
    # ⏳ الخطوة 1: تأخير التشغيل لإعطاء النسخة القديمة في Railway وقتاً لتنطفئ تماماً وتفادي التعارض
    logger.info("⏳ جاري الانتظار 5 ثوانٍ لضمان إغلاق أي جلسات نشطة مسبقاً وتفادي مشاكل التعارض...")
    time.sleep(5)

    # بناء تطبيق التليجرام وربطه بدالة التجهيز
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # تسجيل معالجات الأوامر الرئيسية (Commands)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("lang", toggle_lang_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    # تسجيل معالج الضغطات وتفاعلات الأزرار (Callback Queries)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # تسجيل معالج النصوص والمستندات (مثل استقبال cookies.txt وصيانة الكوكيز)
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_incoming_text))

    # تشغيل البوت مع حماية استثنائية ضد الـ Conflict
    try:
        logger.info("🔌 جاري الاتصال بخوادم Telegram وفتح قنوات الاستقبال...")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Conflict:
        logger.warning("⚠️ تم كشف تعارض (Conflict)! هناك نسخة أخرى تعمل بنفس التوكن حالياً.")
        logger.warning("🚪 سيتم إغلاق هذه الحاوية فوراً لمنع الـ Crash Loop والحفاظ على سلامة التوكن...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ حدث خطأ غير متوقع أثناء تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
