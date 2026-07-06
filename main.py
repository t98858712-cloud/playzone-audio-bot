import logging
import asyncio
from telegram import Update, BotCommand, BotCommandScopeChat, MenuButtonCommands
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from core.config import TOKEN, LOCAL_API_URL
import core.security as sec

from database.connection import init_db
from database.operations import load_banned_users

from utils.helpers import _cleanup_old_downloads_sync, parse_admin_ids

from handlers.user import start, toggle_lang_command, show_playzone_links, handle_incoming_text
from handlers.admin import admin_panel
from handlers.callbacks import handle_callbacks

from services.downloader import youtube_health_monitor

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")

for noisy_logger in ["httpx", "httpcore", "telegram", "telegram.ext"]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

async def start_adsgram_http_server():
    """سيرفر ويب مدمج لخدمة صفحة الإعلانات المصغرة (Mini App) واستقبال الإشعارات"""
    import os
    port = int(os.environ.get("PORT", 8080))
    
    async def handle_client(reader, writer):
        try:
            data = await reader.read(1024)
            request_text = data.decode('utf-8', errors='ignore')
            lines = request_text.split("\r\n")
            if lines:
                request_line = lines[0]
                parts = request_line.split(" ")
                if len(parts) >= 2 and parts[0] == "GET":
                    url_path = parts[1]
                    
                    # مسار معالجة المكافآت الخلفي
                    if "/adsgram_reward" in url_path:
                        user_id = None
                        if "user_id=" in url_path:
                            user_id = url_path.split("user_id=")[1].split("&")[0]
                        if user_id:
                            from database.operations import verify_user_ad_completion
                            verify_user_ad_completion(int(user_id))
                        
                        response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}"
                        writer.write(response.encode('utf-8'))
                        await writer.drain()
                        return
                        
                    # مسار استضافة تطبيق الإعلان المصغر (Mini App)
                    elif "/ad_viewer" in url_path:
                        from core.config import ADSGRAM_BLOCK_ID
                        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://sad.adsgram.ai/js/sad.min.js"></script>
    <style>
        body {{ font-family: Tahoma, sans-serif; text-align: center; padding-top: 40%; background: #1a1a1a; color: #fff; }}
        .loader {{ border: 4px solid #333; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 20px auto; }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div id="status">
        <h2>جاري تحميل الإعلان...</h2>
        <div class="loader"></div>
        <p>يرجى المشاهدة ❤️</p>
    </div>
    <script>
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        
        try {{
            const AdController = window.Adsgram.init({{ blockId: "{ADSGRAM_BLOCK_ID}" }});
            AdController.show().then((result) => {{
                window.Telegram.WebApp.showAlert("✅ اكتملت المشاهدة بنجاح! يرجى الضغط على زر (التحقق) في البوت.", function() {{
                    window.Telegram.WebApp.close();
                }});
            }}).catch((result) => {{
                window.Telegram.WebApp.showAlert("❌ تم الإلغاء. يرجى إكمال مشاهدة الإعلان لفتح التحميل.", function() {{
                    window.Telegram.WebApp.close();
                }});
            }});
        }} catch (e) {{
            document.getElementById("status").innerHTML = "<h2>❌ حدث خطأ</h2><p>" + e.message + "</p>";
        }}
    </script>
</body>
</html>"""
                        response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n{html}"
                        writer.write(response.encode('utf-8'))
                        await writer.drain()
                        return
                        
            response = "HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"
            writer.write(response.encode('utf-8'))
            await writer.drain()
        except Exception as e:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", port)
        logger.info(f"🌐 تم تشغيل نظام الإعلانات المصغرة (Mini App) بنجاح على البورت {port}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start HTTP Server: {e}")

async def post_init(app: Application):
    user_commands = [
        BotCommand("start", "بدء / Start"), 
        BotCommand("language", "تغيير اللغة / Toggle Language"), 
        BotCommand("links", "الروابط / Links")
    ]
    admin_commands = user_commands + [BotCommand("admin", "لوحة التحكم / Admin Panel")]

    try:
        await app.bot.set_my_commands(user_commands)
        for admin_id in parse_admin_ids():
            try:
                await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(admin_id))
            except Exception as e:
                pass

        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        asyncio.create_task(youtube_health_monitor(app))
        
        # تشغيل سيرفر الـ Mini App الخاص بالإعلانات
        asyncio.create_task(start_adsgram_http_server())
    except Exception as e:
        logger.warning(f"فشل تهيئة الأوامر: {e}")

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")

    init_db()
    sec.BANNED_USERS_CACHE = load_banned_users()
    _cleanup_old_downloads_sync()

    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL:
        builder.base_url(LOCAL_API_URL)

    app = builder.post_init(post_init).connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", toggle_lang_command))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بنظام الإدارة المؤسسية (Enterprise Control Center) بنجاح.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
