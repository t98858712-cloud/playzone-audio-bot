"""
بوت PlayZone Enterprise - النسخة الذهبية
يحتوي على: محرك تحميل yt-dlp ذكي، لوحة تحكم إدمن تفاعلية،
دمج Gemini AI (مكتبة جوجل الجديدة)، ونظام حماية من الأخطاء.
"""

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

# إعداد المكتبات الحديثة
warnings.filterwarnings("ignore", category=FutureWarning)
from google import genai
from google.genai import types

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# 1. إعدادات البيئة الأساسية
# ==========================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL") 
PROXY_URL = os.getenv("PROXY_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "bot_database.db"
COOKIES_FILE = Path("cookies.txt")
LOG_FILE = DATA_DIR / "bot.log"

HAS_FFMPEG = shutil.which("ffmpeg") is not None
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO, handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger("PlayZone_Enterprise")

# ==========================================================
# 2. إعداد الذكاء الاصطناعي (أحدث مكتبة Google GenAI)
# ==========================================================
USER_CHATS = {} 

if GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
        ai_model_name = 'gemini-2.0-flash' # أحدث موديل
        ai_config = types.GenerateContentConfig(
            system_instruction="أنت المساعد الذكي لبوت PlayZone. تساعد المستخدمين في التحميل والدردشة."
        )
    except Exception as e:
        logger.error(f"فشل تهيئة Gemini: {e}")
        genai_client = None
else:
    genai_client = None

# ==========================================================
# 3. محرك التحميل الذكي (Smart YTDLP)
# ==========================================================
def get_ydl_options(mode="video"):
    # إذا لم يوجد FFmpeg، نتجنب صيغ الدمج
    fmt = "best[ext=mp4]/best" if not HAS_FFMPEG else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    return {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": fmt if mode == "video" else "bestaudio[ext=m4a]/bestaudio/best",
        "merge_output_format": "mp4" if HAS_FFMPEG else None,
        "http_headers": {"User-Agent": "Mozilla/5.0"}
    }

# ==========================================================
# 4. دوال المساعدة الأصلية لـ PlayZone
# ==========================================================
def esc(text) -> str: return html.escape(str(text or ""), quote=False)
def clean_title(text: str, limit=60) -> str: return (re.sub(r"[\\/:*?\"<>|]+", "", str(text or "Media")))[:limit]
def format_size(size_bytes) -> str:
    try: 
        size_bytes = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0: return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
    except: return "غير معروف"
    return "Unknown"

# ==========================================================
# 5. منطق الإدارة (Admin Panel & Logic)
# ==========================================================
KNOWN_USERS_CACHE = set()
BANNED_USERS_CACHE = set()

async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("🛠 <b>لوحة الإدارة الذكية</b>", reply_markup=admin_main_keyboard(), parse_mode="HTML")

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("🧹 تنظيف", callback_data="adm_clean")],
        [InlineKeyboardButton("🧠 تحليل السيرفر", callback_data="adm_ai_debug"), InlineKeyboardButton("🔄 تحديث", callback_data="adm_update")],
        [InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]
    ])

# ==========================================================
# 6. معالج الرسائل الذكي
# ==========================================================
async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    
    # الدردشة الذكية
    if not any(x in text.lower() for x in ["youtube.com", "youtu.be"]):
        if genai_client:
            await context.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
            try:
                response = await genai_client.aio.models.generate_content(model=ai_model_name, contents=text, config=ai_config)
                await update.message.reply_text(response.text.replace("*", "").replace("_", "")[:4000])
                return
            except Exception as e:
                logger.error(f"AI Error: {e}")
        await update.message.reply_text("أرسل رابط يوتيوب للتحميل، أو أي سؤال للدردشة.")
        return

    # التحميل
    status = await update.message.reply_text("🔍 جاري التحميل...")
    try:
        ydl_opts = get_ydl_options()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(text, download=True)
            file_path = f"./downloads/{info['id']}.mp4"
            await update.message.reply_video(video=open(file_path, 'rb'), caption=info.get('title', ''))
            os.remove(file_path)
            await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ فشل: {str(e)}")

# ==========================================================
# 7. الإقلاع والتهيئة
# ==========================================================
def main():
    if not TOKEN: sys.exit("Error: TOKEN missing")
    
    try:
        app = Application.builder().token(TOKEN).concurrent_updates(True).build()
        
        # ربط المعالجات
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("admin", admin_panel_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
        app.add_handler(CallbackQueryHandler(handle_callbacks))
        
        logger.info("🚀 PlayZone Diamond Edition Ready.")
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error("❌ نسخة أخرى تعمل!")
        sys.exit(1)

if __name__ == "__main__":
    main()
