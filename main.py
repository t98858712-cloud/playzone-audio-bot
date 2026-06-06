import os
import re
import time
import html
import uuid
import asyncio
import shutil
import sqlite3
import logging
import threading
import subprocess
import urllib.request
import ipaddress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    MenuButtonCommands,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================================
# 1. إعدادات PlayZone الأساسية والبيئة
# ==========================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL") 

BASE_DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
BASE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "bot_database.db"
DB_LOCK = threading.Lock()

DEFAULT_MAX_SIZE = (2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024)
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", str(DEFAULT_MAX_SIZE)))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))

PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))
REQUEST_EXPIRE_SECONDS = int(os.getenv("REQUEST_EXPIRE_SECONDS", str(15 * 60)))
OLD_DOWNLOADS_EXPIRE_SECONDS = int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", str(60 * 60)))
MAX_THUMBNAIL_BYTES = int(os.getenv("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2)))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_WORKERS)
EXECUTOR = ThreadPoolExecutor(max_workers=max(4, MAX_WORKERS * 2))

ACTIVE_USERS = set()
CANCEL_FLAGS = set()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")

# --- روابط PlayZone الأصلية ---
WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{BOT_USERNAME.replace('@', '')}"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneBot")
for noisy in ["httpx", "httpcore", "telegram"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

progress_lock = threading.Lock()

# ==========================================================
# 2. اللغات (Localization)
# ==========================================================
LANGS = {
    'ar': {
        'start': "أهلاً بك 👋\n\nأرسل رابط فيديو أو صوت للتحميل، أو اكتب اسم الأغنية للبحث عنها مباشرة.\n\nبدعمك نستمر 💚",
        'guide': "📘 **دليل الاستخدام**\n1. أرسل الرابط هنا.\n2. أو ابحث باسم المقطع.\n3. اختر الصيغة وحمّل.",
        'banned': "❌ عذراً، لا يمكنك استخدام البوت (محظور).",
        'searching': "🔍 جاري البحث وتجهيز المعاينة...",
        'no_results': "❌ لم يتم العثور على نتائج. جرب كلمات أخرى.",
        'in_queue': "🚦 السيرفر مزدحم قليلاً.. أنت في طابور الانتظار، سيبدأ التحميل تلقائياً.",
        'downloading': "📥 جاري التحميل...",
        'compressing': "⚙️ الملف كبير، جاري الضغط التلقائي بأفضل جودة...",
        'converting': "🎵 جاري تحويل الصوت بدقة عالية (MP3 320k) ودمج الغلاف...",
        'uploading': "📤 تم التجهيز، جاري الإرسال...",
        'lang_changed': "✅ تم تغيير اللغة إلى العربية.",
        'fav_added': "✅ تمت الإضافة للمفضلة بنجاح.",
        'fav_exists': "⚠️ المقطع موجود بالفعل في مفضلتك.",
        'fav_empty': "❌ قائمة المفضلة الخاصة بك فارغة.",
        'cancelled': "❌ تم الإلغاء.",
        'error_size': "❌ حجم الملف النهائي يتجاوز الحد المسموح.",
        'error_general': "❌ حدث خطأ غير متوقع أثناء المعالجة.",
        'audio': "🎵 تحميل صوت",
        'video': "🎬 تحميل فيديو",
        'fav_btn': "🤍 إضافة للمفضلة",
        'cancel_btn': "❌ إلغاء"
    },
    'en': {
        'start': "Welcome 👋\n\nSend a media link to download, or type a song name to search directly.\n\nYour support makes a difference 💚",
        'guide': "📘 **Usage Guide**\n1. Send a link.\n2. Or search by name.\n3. Choose format.",
        'banned': "❌ Sorry, you are banned from using this bot.",
        'searching': "🔍 Searching and preparing preview...",
        'no_results': "❌ No results found. Try different keywords.",
        'in_queue': "🚦 Server is busy.. You are in the queue, download will start shortly.",
        'downloading': "📥 Downloading...",
        'compressing': "⚙️ File is large, auto-compressing to optimal quality...",
        'converting': "🎵 Converting to MP3 (320k) and embedding cover...",
        'uploading': "📤 Processed, uploading now...",
        'lang_changed': "✅ Language changed to English.",
        'fav_added': "✅ Added to favorites.",
        'fav_exists': "⚠️ Already in your favorites.",
        'fav_empty': "❌ Your favorites list is empty.",
        'cancelled': "❌ Cancelled.",
        'error_size': "❌ Final file size exceeds the allowed limit.",
        'error_general': "❌ An unexpected error occurred.",
        'audio': "🎵 Download Audio",
        'video': "🎬 Download Video",
        'fav_btn': "🤍 Save to Favorites",
        'cancel_btn': "❌ Cancel"
    }
}

def get_text(user_id: int, key: str) -> str:
    lang = get_user_lang(user_id)
    return LANGS.get(lang, LANGS['ar']).get(key, LANGS['ar'].get(key, ""))

# ==========================================================
# 3. قواعد البيانات والتحقق (نظام WAL)
# ==========================================================
def init_db():
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, 
            first_seen INTEGER, last_seen INTEGER, lang TEXT DEFAULT 'ar')""")
        conn.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS banned_users (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, url TEXT, UNIQUE(user_id, url))")
        conn.execute("CREATE TABLE IF NOT EXISTS ratings (user_id INTEGER PRIMARY KEY, rating INTEGER)")
        for k in ["requests", "success", "failed", "bytes", "broadcasts"]:
            conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (k,))

def register_user_sync(user):
    if not user: return
    now = int(time.time())
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT first_seen, lang FROM users WHERE id = ?", (user.id,))
        row = cur.fetchone()
        first_seen = row[0] if row else now
        lang = row[1] if row else 'ar'
        conn.execute("""
            INSERT OR REPLACE INTO users (id, username, first_name, last_name, first_seen, last_seen, lang)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user.id, user.username or "", user.first_name or "", user.last_name or "", first_seen, now, lang))

def get_user_lang(user_id: int) -> str:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT lang FROM users WHERE id = ?", (user_id,)).fetchone()
        return row[0] if row else 'ar'

def is_banned(user_id: int) -> bool:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        return bool(conn.execute("SELECT 1 FROM banned_users WHERE id = ?", (user_id,)).fetchone())

def stat_inc_sync(key: str, value: int = 1):
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))
        conn.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))

def load_stats_sync() -> dict:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT key, value FROM stats").fetchall()
        return {k: v for k, v in rows}

def all_user_ids() -> list:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM users").fetchall()]

def get_latest_users(limit: int = 10) -> list:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()]

# ==========================================================
# 4. الأدوات والفحص
# ==========================================================
def parse_admin_ids():
    return {int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}

def is_admin(user_id: int) -> bool:
    return user_id in parse_admin_ids()

def esc(text) -> str: return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text or "Media")).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    if not size_bytes or size_bytes <= 0: return "Unknown"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0: return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except: return "00:00"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except: return False

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def cookie_file_is_usable() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

def _force_cleanup_all_sync() -> int:
    removed = 0
    for item in BASE_DOWNLOAD_DIR.iterdir():
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
            removed += 1
        except: pass
    return removed

def garbage_collect_memory(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    to_del = [k for k, v in context.user_data.items() if isinstance(v, dict) and 'timestamp' in v and now - v['timestamp'] > REQUEST_EXPIRE_SECONDS]
    for k in to_del: del context.user_data[k]

# ==========================================================
# 5. الواجهات ولوحات التحكم (الكود الأصلي بالكامل)
# ==========================================================
def user_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط أو ابحث بالاسم..."
    )

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
    ])

def build_playzone_links_text() -> str:
    return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")],
        [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")],
        [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")],
    ])

def admin_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="adm_cancel_bc")]])

def build_admin_stats_text() -> str:
    stats = load_stats_sync()
    users_count = len(all_user_ids())
    return (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"• الطلبات الكلية: {stats.get('requests', 0)}\n"
        f"• التحميلات الناجحة: {stats.get('success', 0)}\n"
        f"• العمليات الفاشلة: {stats.get('failed', 0)}\n"
        f"• عدد المستخدمين: {users_count}\n"
        f"• حجم الملفات المرسلة: {format_size(stats.get('bytes', 0))}\n"
        f"• عدد الإذاعات: {stats.get('broadcasts', 0)}"
    )

def build_admin_users_text(limit: int = 10) -> str:
    users = get_latest_users(limit)
    lines = [f"👥 <b>آخر المستخدمين النشطين:</b>"]
    for u in users:
        name = u.get("first_name") or "بدون اسم"
        username = f"@{u.get('username')}" if u.get("username") else "لا يوجد"
        lines.append(f"• {esc(name)} — {esc(username)} — ID: <code>{u.get('id')}</code>")
    return "\n".join(lines)

def build_server_status_text() -> str:
    total_size = sum(p.stat().st_size for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
    file_count = sum(1 for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
    return (
        "📁 <b>حالة السيرفر</b>\n\n"
        f"• مجلد التحميل: <code>{BASE_DOWNLOAD_DIR}</code>\n"
        f"• الملفات المؤقتة: {file_count}\n"
        f"• حجم الملفات المؤقتة: {format_size(total_size)}\n"
        f"• العمليات النشطة: {len(ACTIVE_USERS)}\n"
        f"• الحد الأقصى المتزامن: {MAX_WORKERS}"
    )

async def safe_delete(message):
    try: await message.delete()
    except: pass

async def safe_edit(message, text: str, reply_markup=None):
    try:
        if getattr(message, "photo", None):
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): logger.warning(f"Edit error: {e}")
    except: pass

# ==========================================================
# 6. محرك التحميل Async FFmpeg
# ==========================================================
def get_ydl_options(job_dir: Path = None, progress_data: dict = None, mode: str = "video", req_id: str = None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30,
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "webpage_safari"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    
    if mode == "audio":
        opts["format"] = "bestaudio/best"
    else:
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        opts["format"] = f"bestvideo[height<=720][filesize<{max_fs}]+bestaudio/best/best"
        opts["merge_output_format"] = "mp4"

    if job_dir: opts["outtmpl"] = str(job_dir / "media.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data, req_id)]
    if cookie_file_is_usable(): opts["cookiefile"] = str(COOKIES_FILE)
    return opts

def download_hook(progress_data: dict, req_id: str):
    def hook(d):
        if req_id and req_id in CANCEL_FLAGS: raise ValueError("USER_CANCELLED")
        with progress_lock:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                down = d.get("downloaded_bytes", 0)
                speed = d.get("speed", 0)
                if total > 0:
                    pct = down / total * 100
                    progress_data["text"] = f"📥 <b>جاري التحميل...</b>\n{make_progress_bar(pct)} {pct:.1f}%\n📦 {format_size(down)} / {format_size(total)}\n🚀 {format_size(speed)}/s"
            elif d.get("status") == "finished":
                progress_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event, req_id: str, uid: int):
    last_text = ""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text(uid, 'cancel_btn'), callback_data=f"stop_dl:{req_id}")]])
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            await safe_edit(message, text, reply_markup=kb)
            last_text = text
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

async def run_ffmpeg_async(cmd: list, cancel_req_id: str = None) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        while process.returncode is None:
            if cancel_req_id and cancel_req_id in CANCEL_FLAGS:
                process.terminate()
                raise ValueError("USER_CANCELLED")
            try: await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError: pass
        return process.returncode == 0
    except ValueError: raise
    except Exception as e:
        logger.error(f"FFmpeg Error: {e}")
        return False

async def convert_to_mp3_async(input_file: Path, output_file: Path, req_id: str) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
           "-vn", "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", str(output_file)]
    return await run_ffmpeg_async(cmd, req_id)

async def compress_video_async(input_file: Path, output_file: Path, duration: int, req_id: str) -> bool:
    target_size = MAX_TELEGRAM_SIZE * 0.95
    total_bitrate = (target_size * 8) / (duration or 1)
    vid_bitrate = max(50000, total_bitrate - 128000)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
           "-c:v", "libx264", "-b:v", str(int(vid_bitrate)), "-preset", "veryfast",
           "-c:a", "aac", "-b:a", "128k", str(output_file)]
    return await run_ffmpeg_async(cmd, req_id)

# ==========================================================
# 7. الأحداث ومعالجة النصوص
# ==========================================================
async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    uid = update.effective_user.id
    register_user_sync(update.effective_user)
    garbage_collect_memory(context)
    
    if is_banned(uid): return await update.message.reply_text(get_text(uid, 'banned'))
    text = update.message.text.strip()
    if text.startswith("/"): return

    if text in ["🔗 روابط PlayZone", "\\links"]:
        return await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)
    if text == "📘 دليل الاستخدام":
        return await update.message.reply_text(get_text(uid, 'guide'), disable_web_page_preview=True)
    
    if is_admin(uid) and context.user_data.get("bc_active"):
        return await handle_broadcast_text(update, context, text)

    if uid in ACTIVE_USERS:
        return await update.message.reply_text("⏳ لديك عملية جارية، يرجى الانتظار أو إلغاؤها أولاً.")

    status = await update.message.reply_text(get_text(uid, 'searching'))
    
    try:
        loop = asyncio.get_running_loop()
        if is_valid_url(text):
            opts = get_ydl_options()
            opts["skip_download"] = True
            info = await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(text, download=False))
            await send_media_preview(update, context, info, status, text, uid)
            stat_inc_sync("requests")
        else:
            opts = {"quiet": True, "extract_flat": True, "playlist_items": "1-5"}
            info = await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(f"ytsearch5:{text}", download=False))
            entries = info.get("entries", [])
            
            if not entries: return await status.edit_text(get_text(uid, 'no_results'))
            
            kb = []
            for e in entries:
                v_url = e.get("url")
                if v_url:
                    title = clean_title(e.get("title"), 40)
                    sid = uuid.uuid4().hex[:8]
                    kb.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"src:{sid}")])
                    context.user_data[f"src_{sid}"] = {"url": v_url, "timestamp": time.time()}
            
            await status.edit_text("🔍 النتائج:", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Search Error: {e}")
        await status.edit_text(get_text(uid, 'error_general'))

async def send_media_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, info: dict, status_msg, url: str, uid: int):
    req_id = uuid.uuid4().hex[:8]
    title = clean_title(info.get("title"))
    dur = info.get("duration", 0)
    artist = info.get("uploader", "Unknown")
    
    context.user_data[f"req_{req_id}"] = {"url": url, "title": title, "duration": dur, "artist": artist, "timestamp": time.time()}
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(uid, 'audio'), callback_data=f"dl:audio:{req_id}"), 
         InlineKeyboardButton(get_text(uid, 'video'), callback_data=f"dl:video:{req_id}")],
        [InlineKeyboardButton(get_text(uid, 'fav_btn'), callback_data=f"fav:{req_id}")],
        [InlineKeyboardButton(get_text(uid, 'cancel_btn'), callback_data=f"rm_msg")]
    ])
    
    est_size = format_size(info.get("filesize_approx") or info.get("filesize") or 0)
    caption = f"🎬 <b>{html.escape(title)}</b>\n👤 {html.escape(artist)}\n⏱ {format_duration(dur)} | 💾 ~{est_size}"
    
    thumb = info.get("thumbnail")
    if thumb:
        try:
            await status_msg.delete()
            return await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=kb, parse_mode="HTML")
        except: pass
    await safe_edit(status_msg, caption, reply_markup=kb)

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    context.user_data["bc_active"] = False
    users = all_user_ids()
    if not users: return await update.message.reply_text("لا يوجد مستخدمون مسجلون.")
    
    status = await update.message.reply_text("📢 جاري إرسال الرسالة للمستخدمين...")
    sent, fail = 0, 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e: 
            await asyncio.sleep(int(e.retry_after) + 1)
            try:
                await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
                sent += 1
            except: fail += 1
        except: fail += 1
    
    stat_inc_sync("broadcasts")
    await status.edit_text(f"✅ تم إرسال الإذاعة.\n\n• تم الإرسال: {sent}\n• فشل الإرسال: {fail}")

# ==========================================================
# 8. الكول باك والأزرار
# ==========================================================
async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    if data == "adm_close":
        await query.answer("تم الإغلاق")
        return await safe_delete(query.message)
    elif data == "adm_stats":
        await query.answer()
        return await query.message.edit_text(build_admin_stats_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_users":
        await query.answer()
        return await query.message.edit_text(build_admin_users_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_server":
        await query.answer()
        return await query.message.edit_text(build_server_status_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة...")
        removed = await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)
        return await query.message.edit_text(f"🧹 تم تنظيف الملفات.\n\nالمحذوفة: {removed}", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_bc":
        context.user_data["bc_active"] = True
        await query.answer()
        return await query.message.edit_text("📢 أرسل نص الرسالة التي تريد إرسالها لجميع المستخدمين:", reply_markup=admin_broadcast_keyboard(), parse_mode="HTML")
    elif data == "adm_cancel_bc":
        context.user_data["bc_active"] = False
        await query.answer("تم الإلغاء")
        return await query.message.edit_text("تم إلغاء العملية.", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    data = query.data

    if is_banned(uid): return await query.answer(get_text(uid, 'banned'), show_alert=True)
    if data == "rm_msg": return await safe_delete(query.message)

    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("صلاحية إدارة فقط.", show_alert=True)
        return await handle_admin_callbacks(query, context)

    if data.startswith("src:"):
        sid = data.split(":")[1]
        req = context.user_data.get(f"src_{sid}")
        if not req: return await query.answer("انتهت صلاحية البحث", show_alert=True)
        update.message = query.message
        update.message.text = req["url"]
        await query.message.delete()
        return await handle_incoming_text(update, context)

    if data.startswith("fav:"):
        req_id = data.split(":")[1]
        req = context.user_data.get(f"req_{req_id}")
        if req:
            with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
                try:
                    conn.execute("INSERT INTO favorites (user_id, title, url) VALUES (?, ?, ?)", (uid, req['title'], req['url']))
                    await query.answer(get_text(uid, 'fav_added'), show_alert=True)
                except sqlite3.IntegrityError:
                    await query.answer(get_text(uid, 'fav_exists'), show_alert=True)
        return

    if data.startswith("stop_dl:"):
        req_id = data.split(":")[1]
        CANCEL_FLAGS.add(req_id)
        return await query.answer("جاري الإلغاء...")

    if data.startswith("dl:"):
        _, mode, req_id = data.split(":")
        req = context.user_data.get(f"req_{req_id}")
        if not req: return await query.answer("انتهت الصلاحية، أرسل الرابط مجدداً", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل نشط", show_alert=True)
        await process_download(query, context, req, mode, req_id)

async def process_download(query, context: ContextTypes.DEFAULT_TYPE, req: dict, mode: str, req_id: str):
    uid = query.from_user.id
    url = req["url"]
    ACTIVE_USERS.add(uid)
    CANCEL_FLAGS.discard(req_id)
    
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{req_id}"
    job_dir.mkdir(exist_ok=True)
    stop_event = asyncio.Event()
    
    prog = {"text": get_text(uid, 'downloading')}
    updater_task = asyncio.create_task(run_progress_updates(query.message, prog, stop_event, req_id, uid))

    try:
        if DOWNLOAD_SEMAPHORE.locked():
            with progress_lock: prog["text"] = get_text(uid, 'in_queue')
            
        async with DOWNLOAD_SEMAPHORE:
            with progress_lock: prog["text"] = get_text(uid, 'downloading')
            
            loop = asyncio.get_running_loop()
            opts = get_ydl_options(job_dir, prog, mode, req_id)
            
            await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=True))
            
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".ytdl"]]
            if not files: raise RuntimeError("No output file")
            target = max(files, key=lambda p: p.stat().st_mtime)
            
            if mode == "audio":
                with progress_lock: prog["text"] = get_text(uid, 'converting')
                mp3_path = job_dir / "final.mp3"
                if await convert_to_mp3_async(target, mp3_path, req_id):
                    target = mp3_path
            else:
                if target.stat().st_size > MAX_TELEGRAM_SIZE:
                    with progress_lock: prog["text"] = get_text(uid, 'compressing')
                    comp_path = job_dir / "comp.mp4"
                    if await compress_video_async(target, comp_path, req.get('duration', 0), req_id):
                        target = comp_path
            
            if target.stat().st_size > MAX_TELEGRAM_SIZE:
                stop_event.set()
                return await safe_edit(query.message, get_text(uid, 'error_size'))

            stop_event.set()
            await safe_edit(query.message, get_text(uid, 'uploading'))
            
            caption_text = f"🎬 {html.escape(req['title'])}\n- @{context.bot.username}"
            with open(target, "rb") as f:
                if mode == "audio":
                    await context.bot.send_audio(chat_id=uid, audio=f, title=req['title'], performer=req['artist'], duration=req.get('duration', 0), caption=caption_text)
                else:
                    await context.bot.send_video(chat_id=uid, video=f, caption=caption_text, supports_streaming=True)
            
            stat_inc_sync("success")
            await query.message.delete()
            
    except ValueError as e:
        if str(e) == "USER_CANCELLED": await safe_edit(query.message, get_text(uid, 'cancelled'))
    except Exception as e:
        stat_inc_sync("failed")
        logger.error(f"Processing Error: {e}")
        await safe_edit(query.message, get_text(uid, 'error_general'))
    finally:
        stop_event.set()
        CANCEL_FLAGS.discard(req_id)
        ACTIVE_USERS.discard(uid)
        shutil.rmtree(job_dir, ignore_errors=True)

# ==========================================================
# 9. الأوامر وأدوات الإدارة (الأساسية والجديدة)
# ==========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    uid = update.effective_user.id
    if is_banned(uid): return
    await update.message.reply_text(get_text(uid, 'start'), reply_markup=user_main_keyboard(), disable_web_page_preview=True)

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    new_lang = 'en' if get_user_lang(uid) == 'ar' else 'ar'
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: conn.execute("UPDATE users SET lang = ? WHERE id = ?", (new_lang, uid))
    await update.message.reply_text(LANGS[new_lang]['lang_changed'])

async def fav_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT title, url FROM favorites WHERE user_id = ?", (uid,)).fetchall()
    if not rows: return await update.message.reply_text(get_text(uid, 'fav_empty'))
    
    lines = [f"- <a href='{url}'>{clean_title(t, 40)}</a>" for t, url in rows]
    await update.message.reply_text("🤍 المفضلة:\n\n" + "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit() or not (1 <= int(context.args[0]) <= 5):
        return await update.message.reply_text("⭐ للتقييم، أرسل:\n`/rate 5`", parse_mode="Markdown")
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO ratings (user_id, rating) VALUES (?, ?)", (update.effective_user.id, int(context.args[0])))
    await update.message.reply_text("✅ شكراً لتقييمك ودعمك!")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    await update.message.reply_text(
        "🛠 <b>لوحة الإدارة المتقدمة</b>\n\n"
        "أوامر إضافية للمدير:\n/update_dlp - تحديث المحرك\n/setcookie - تجديد الكوكيز\n/backup - سحب قاعدة البيانات\n"
        "/msg id text - مراسلة\n/ban id - حظر\n/unban id - فك حظر",
        reply_markup=admin_main_keyboard(), parse_mode="HTML"
    )

async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("🔄 جاري تحديث محرك التحميل...")
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text("✅ تم تحديث محرك `yt-dlp` بنجاح.")
    except Exception as e: await msg.edit_text(f"❌ فشل التحديث: {e}")

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.document:
        return await update.message.reply_text("📥 أرسل ملف `cookies.txt` كـ Document مع هذا الأمر.")
    file_id = update.message.document.file_id
    new_file = await context.bot.get_file(file_id)
    await new_file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم استلام وتركيب ملف الكوكيز بنجاح!")

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        with open(DB_FILE, "rb") as f:
            await update.message.reply_document(document=f, filename="bot_database.db", caption="📦 نسخة احتياطية من قاعدة البيانات.")
    except Exception as e: await update.message.reply_text(f"❌ تعذر سحب النسخة: {e}")

async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 1: return
    cmd = update.message.text.split()[0].lower()
    target_id = context.args[0]
    
    if cmd == "/msg" and len(context.args) > 1:
        try:
            await context.bot.send_message(chat_id=target_id, text=" ".join(context.args[1:]))
            await update.message.reply_text("✅ تم الإرسال.")
        except Exception as e: await update.message.reply_text(f"❌ خطأ: {e}")
    elif cmd in ["/ban", "/unban"]:
        with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
            if cmd == "/ban": conn.execute("INSERT OR IGNORE INTO banned_users (id) VALUES (?)", (target_id,))
            else: conn.execute("DELETE FROM banned_users WHERE id = ?", (target_id,))
        await update.message.reply_text("✅ تم تنفيذ الأمر.")

# ==========================================================
# 10. التشغيل (Main)
# ==========================================================
async def post_init(app: Application):
    commands = [
        BotCommand("start", "بدء استخدام البوت"),
        BotCommand("lang", "تغيير اللغة | Change Language"),
        BotCommand("fav", "عرض قائمة المفضلة الخاصة بك"),
        BotCommand("rate", "تقييم البوت (مثال: /rate 5)"),
        BotCommand("links", "دعم روابط PlayZone")
    ]
    try:
        await app.bot.set_my_commands(commands)
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e: logger.warning(f"فشل تهيئة الأوامر: {e}")

def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN غير متوفر بالسيرفر!")
    init_db()

    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL: builder.base_url(LOCAL_API_URL)
    app = builder.post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(CommandHandler("fav", fav_cmd))
    app.add_handler(CommandHandler("rate", rate_cmd))
    
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("update_dlp", update_ytdlp_command))
    app.add_handler(CommandHandler("setcookie", set_cookie_command))
    app.add_handler(CommandHandler("backup", backup_db_command))
    app.add_handler(CommandHandler(["msg", "ban", "unban"], admin_actions))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 PlayZone Bot Started - Fully Optimized & Original Features Restored")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
