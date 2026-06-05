import os
import sys
import time
import html
import uuid
import asyncio
import shutil
import logging
import warnings
import urllib.request
import ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
import aiosqlite
warnings.filterwarnings("ignore", category=FutureWarning)

from google import genai
from google.genai import types

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# 1. إعدادات البوت والبيئة
# ==========================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "bot_database.db"
COOKIES_FILE = Path("cookies.txt")
LOG_FILE = DATA_DIR / "bot.log"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO, handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger("PlayZone_Enterprise")

# ==========================================================
# 2. نظام الذكاء الاصطناعي الحديث
# ==========================================================
if GEMINI_API_KEY:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    ai_model_name = 'gemini-2.0-flash'
    ai_config = types.GenerateContentConfig(system_instruction="أنت المساعد الذكي لبوت PlayZone. تساعد المستخدمين في التحميل والدردشة بأسلوب ودود.")
else:
    genai_client = None

# ==========================================================
# 3. دوال PlayZone الأصلية
# ==========================================================
def esc(text) -> str: return html.escape(str(text or ""), quote=False)
def clean_title(text: str) -> str: return re.sub(r"[\\/:*?\"<>|]+", "", str(text or "Media"))[:60]

def build_start_text(name: str) -> str:
    return f"أهلاً {esc(name)} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة للتحميل.\n\n💚 دعمك يصنع الفرق..."

# ==========================================================
# 4. المعالجات (Handlers) - تم إصلاح مشكلة NameError هنا
# ==========================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        build_start_text(update.effective_user.first_name or ""),
        reply_markup=ReplyKeyboardMarkup([["📘 دليل الاستخدام"], ["🔗 روابط PlayZone"]], resize_keyboard=True),
        parse_mode="HTML"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    
    # التحقق من الروابط
    if "youtube.com" in text or "youtu.be" in text:
        status = await update.message.reply_text("🔍 جاري التحميل...")
        try:
            ydl_opts = {"format": "best[ext=mp4]/best", "outtmpl": "./downloads/%(id)s.%(ext)s"}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(text, download=True)
                path = f"./downloads/{info['id']}.mp4"
                await update.message.reply_video(video=open(path, 'rb'), caption=info.get('title', ''))
                os.remove(path)
                await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ فشل التحميل: {e}")
            
    # الذكاء الاصطناعي
    elif genai_client:
        await context.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
        try:
            response = await genai_client.aio.models.generate_content(model=ai_model_name, contents=text, config=ai_config)
            await update.message.reply_text(response.text.replace("*", "").replace("_", "")[:4000])
        except Exception:
            await update.message.reply_text("عذراً، الخوادم مشغولة.")

# ==========================================================
# 5. التهيئة والتشغيل (Main)
# ==========================================================
async def post_init(app: Application):
    try: await app.bot.set_my_commands([BotCommand("start", "البدء"), BotCommand("admin", "لوحة التحكم")])
    except: pass

def main():
    if not TOKEN: sys.exit("Error: TOKEN missing")
    
    try:
        app = Application.builder().token(TOKEN).concurrent_updates(True).post_init(post_init).build()
        
        # ربط المعالجات بشكل صحيح
        app.add_handler(CommandHandler("start", start_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        # (يمكنك إضافة باقي المعالجات هنا بنفس النمط)

        logger.info("🚀 PlayZone Diamond Edition: نظام التشغيل يعمل بنجاح.")
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error("❌ نسخة أخرى تعمل! توقف.")
        sys.exit(1)

if __name__ == "__main__":
    main()
