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
# إعدادات PlayZone / Railway (الأصلية كما هي)
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL") 

BASE_DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(exist_ok=True)

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
EXECUTOR = ThreadPoolExecutor(max_workers=max(2, MAX_WORKERS))

ACTIVE_USERS = set()
CANCEL_FLAGS = set() # تتبع عمليات التحميل الملغية

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{BOT_USERNAME.replace('@', '')}"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PlayZoneEnterpriseBot")

for noisy_logger in ["httpx", "httpcore", "telegram", "telegram.ext"]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

progress_lock = threading.Lock()

# ==========================================================
# إدارة قاعدة البيانات (تمت إضافة الجداول الجديدة)
# ==========================================================

def init_db():
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen INTEGER,
                    last_seen INTEGER,
                    lang TEXT DEFAULT 'ar'
                )
            """)
            # إضافة عمود اللغة للمستخدمين القدامى إن لم يكن موجوداً
            try: conn.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'ar'")
            except: pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )
            """)
            conn.execute("CREATE TABLE IF NOT EXISTS banned_users (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, url TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS ratings (user_id INTEGER PRIMARY KEY, rating INTEGER)")
            
            for k in ["requests", "success", "failed", "bytes", "broadcasts"]:
                conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (k,))

def register_user_sync(user):
    if not user: return
    now = int(time.time())
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT first_seen, lang FROM users WHERE id = ?", (user.id,))
            row = cur.fetchone()
            first_seen = row[0] if row else now
            lang = row[1] if row else 'ar'
            conn.execute("""
                INSERT OR REPLACE INTO users (id, username, first_name, last_name, first_seen, last_seen, lang)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user.id, user.username or "", user.first_name or "", user.last_name or "", first_seen, now, lang))

def stat_inc_sync(key: str, value: int = 1):
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))
            conn.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))

def load_stats_sync() -> dict:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            rows = conn.execute("SELECT key, value FROM stats").fetchall()
            return {k: v for k, v in rows}

def is_banned(user_id: int) -> bool:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            return bool(conn.execute("SELECT 1 FROM banned_users WHERE id = ?", (user_id,)).fetchone())

def get_user_lang(user_id: int) -> str:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute("SELECT lang FROM users WHERE id = ?", (user_id,)).fetchone()
            return row[0] if row else 'ar'

def all_user_ids() -> list:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            rows = conn.execute("SELECT id FROM users").fetchall()
            return [row[0] for row in rows]

def get_latest_users(limit: int = 10) -> list:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

# ==========================================================
# أدوات الفحص والتنسيق الأصلية
# ==========================================================

def parse_admin_ids():
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return {int(item.strip()) for item in admin_ids_raw.split(",") if item.strip().isdigit()}

def is_admin(user_id: int) -> bool:
    return user_id in parse_admin_ids()

def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60) -> str:
    if not text: return "ملف ميديا"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except Exception: return "غير معروف"
    if seconds <= 0: return "00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def is_public_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}: return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)
    except ValueError: return True

def is_valid_url(text: str) -> bool:
    try:
        text = (text or "").strip()
        if len(text) > 2000: return False
        parsed = urlparse(text)
        if parsed.scheme not in ["http", "https"] or not parsed.netloc: return False
        if parsed.username or parsed.password: return False
        return is_public_host(parsed.hostname or "")
    except Exception: return False

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception: return ""

def get_artist(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator"]:
        val = info.get(key)
        if val: return clean_title(val, 35)
    return "غير معروف"

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def get_largest_estimated_size(info: dict) -> int:
    sizes = []
    for f in info.get("formats", []) or []:
        try: sizes.append(int(f.get("filesize") or f.get("filesize_approx") or 0))
        except Exception: pass
    return max(sizes) if sizes else 0

def ensure_pending_requests(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("pending_requests", {})

def trim_old_pending_requests(context: ContextTypes.DEFAULT_TYPE, max_items: int = 8):
    pending = ensure_pending_requests(context)
    now = int(time.time())
    for rid, item in list(pending.items()):
        if now - int(item.get("created_at", 0)) > REQUEST_EXPIRE_SECONDS:
            pending.pop(rid, None)
    if len(pending) > max_items:
        items = sorted(pending.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)
        context.user_data["pending_requests"] = dict(items[:max_items])

def cookie_file_is_usable(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0: return False
        now = int(time.time())
        has_youtube = False
        has_valid_cookie = False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split("\t")
                if len(parts) < 7: continue
                domain, _, _, _, expires, name, value = parts[:7]
                if "youtube.com" in domain: has_youtube = True
                try: exp = int(expires)
                except Exception: exp = 0
                if value.strip() and (exp == 0 or exp > now): has_valid_cookie = True
        return has_youtube and has_valid_cookie
    except Exception: return False

def _cleanup_old_downloads_sync():
    now = time.time()
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            try:
                if now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            except Exception: pass
    except Exception: pass

def _force_cleanup_all_sync() -> int:
    removed = 0
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            try:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
                removed += 1
            except Exception: pass
    except Exception: pass
    return removed

# ==========================================================
# الواجهات والأزرار والنصوص المترجمة ديناميكياً
# ==========================================================

def user_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط هنا..."
    )

def build_preview_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{request_id}"),
         InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton("🤍 إضافة للمفضلة", callback_data=f"addfav:{request_id}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{request_id}")],
    ])

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

def build_start_text(first_name: str, lang: str = 'ar') -> str:
    if lang == 'en':
        return (f"Welcome {esc(first_name)} 👋\n\nSend a media link to preview and download, or type a song name to search directly.\n\n"
                "💚 Your support makes a difference. Follow our PlayZone links!")
    return (
        f"أهلاً {esc(first_name)} 👋\n\n"
        "أرسل رابط فيديو أو صوت، أو اكتب اسم أغنية للبحث عنها، وسأعرض لك معاينة قبل التحميل.\n\n"
        "💚 دعمك يصنع الفرق\n\n"
        "تابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\n"
        "كل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.\n\n"
        "ابدأ بإرسال الرابط أو اسم الأغنية مباشرة."
    )

def build_guide_text(lang: str = 'ar') -> str:
    if lang == 'en':
        return "📘 Usage Guide\n1) Copy the link.\n2) Send it to the bot.\n3) Wait for preview.\n4) Choose Audio/Video."
    return (
        "📘 طريقة الاستخدام\n\n"
        "1) انسخ رابط المقطع.\n"
        "2) أرسله هنا في البوت، أو ابحث باسم الأغنية مباشرة.\n"
        "3) انتظر ظهور المعاينة.\n"
        "4) اختر التحميل صوت أو فيديو."
    )

def build_preview_caption(title: str, artist: str, duration: str, est_size: str) -> str:
    return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"

def build_admin_stats_text() -> str:
    stats = load_stats_sync()
    users_count = len(all_user_ids())
    
    platforms = [k for k in stats.keys() if k.startswith("plat_")]
    plat_text = "\n".join([f"• {p.replace('plat_', '')}: {stats[p]}" for p in platforms]) if platforms else "لا توجد بيانات منصات بعد"

    return (
        "📊 <b>إحصائيات البوت المتقدمة</b>\n\n"
        f"• الطلبات الكلية: {stats.get('requests', 0)}\n"
        f"• التحميلات الناجحة: {stats.get('success', 0)}\n"
        f"• العمليات الفاشلة: {stats.get('failed', 0)}\n"
        f"• عدد المستخدمين: {users_count}\n"
        f"• حجم الملفات المرسلة: {format_size(stats.get('bytes', 0))}\n"
        f"• عدد الإذاعات: {stats.get('broadcasts', 0)}\n\n"
        f"🌐 <b>أكثر المنصات استخداماً:</b>\n{plat_text}"
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

# ==========================================================
# الرسائل الآمنة
# ==========================================================

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

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
        try:
            return await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        except Exception: pass
    return await update.message.reply_text(text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# yt-dlp و FFmpeg للتحميل والجودة والضغط
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", req_id: str = None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive"
        },
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "webpage_safari"], "skip": ["webpage"]}},
    }

    if mode == "audio":
        opts["format"] = "bestaudio/best"
    else:
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        opts["format"] = f"bestvideo[height<=720][filesize<{max_fs}]+bestaudio/best[height<=720][filesize<{max_fs}]/best"
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]

    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)

    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data, req_id)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_hook(progress_data: dict, req_id: str = None):
    def hook(d):
        if req_id and req_id in CANCEL_FLAGS:
            raise ValueError("USER_CANCELLED")
            
        with progress_lock:
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                if total:
                    percent = downloaded / total * 100
                    progress_data["text"] = (
                        "📥 <b>جاري تحميل الملف...</b>\n\n"
                        f"{make_progress_bar(percent)}  {percent:.1f}%\n"
                        f"📦 الحجم: {format_size(downloaded)} / {format_size(total)}\n"
                        f"🚀 السرعة: {format_size(speed)}/ث"
                    )
                else:
                    progress_data["text"] = f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(downloaded)}"
            elif d.get("status") == "finished":
                progress_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event, req_id: str):
    last_text = ""
    # زر إيقاف التحميل النشط
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إيقاف التحميل", callback_data=f"stop_dl:{req_id}")]])
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=kb)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict, req_id: str):
    opts = get_ydl_options(job_dir, progress_data, mode, req_id)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

def download_thumbnail_safely(thumb_url: str, output_path: Path) -> Path | None:
    try:
        if not thumb_url or not is_public_host(urlparse(thumb_url).hostname or ""): return None
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = response.read(MAX_THUMBNAIL_BYTES + 1)
        if len(data) > MAX_THUMBNAIL_BYTES: return None
        output_path.write_bytes(data)
        return output_path if output_path.exists() else None
    except Exception: return None

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)]
        if local_thumb and local_thumb.exists():
            cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else:
            cmd.extend(["-vn"])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")
        return False

def compress_video_local(input_file: Path, output_file: Path) -> bool:
    try:
        # ضغط الفيديو باستخدام CRF 28 للحفاظ على الجودة وتقليل الحجم
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
               "-vcodec", "libx264", "-crf", "28", "-preset", "faster",
               "-acodec", "aac", "-b:a", "128k", str(output_file)]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=600)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل الضغط التلقائي: {e}")
        return False

# ==========================================================
# أوامر الإدارة الديناميكية
# ==========================================================

async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("🔄 جاري تحديث محرك التحميل...")
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text("✅ تم تحديث محرك `yt-dlp` بنجاح إلى أحدث إصدار.")
    except Exception as e:
        await msg.edit_text(f"❌ فشل التحديث: {e}")

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.document:
        return await update.message.reply_text("📥 أرسل ملف `cookies.txt` كـ Document مع هذا الأمر لتخطي قيود يوتيوب.")
    
    file_id = update.message.document.file_id
    new_file = await context.bot.get_file(file_id)
    await new_file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم استلام وتركيب ملف الكوكيز بنجاح!")

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        with open(DB_FILE, "rb") as f:
            await update.message.reply_document(document=f, filename="bot_database.db", caption="📦 نسخة احتياطية من قاعدة البيانات.")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذر سحب النسخة: {e}")

async def msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2: return await update.message.reply_text("الاستخدام: /msg <id> <الرسالة>")
    try:
        await context.bot.send_message(chat_id=context.args[0], text=" ".join(context.args[1:]))
        await update.message.reply_text("✅ تم إرسال الرسالة بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    user_id = int(context.args[0])
    with sqlite3.connect(DB_FILE) as conn: conn.execute("INSERT OR IGNORE INTO banned_users (id) VALUES (?)", (user_id,))
    await update.message.reply_text(f"✅ تم حظر المستخدم {user_id}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    user_id = int(context.args[0])
    with sqlite3.connect(DB_FILE) as conn: conn.execute("DELETE FROM banned_users WHERE id = ?", (user_id,))
    await update.message.reply_text(f"✅ تم فك الحظر عن المستخدم {user_id}")

# ==========================================================
# أوامر المستخدم (اللغة، التقييم، المفضلة)
# ==========================================================

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    current_lang = get_user_lang(uid)
    new_lang = 'en' if current_lang == 'ar' else 'ar'
    with sqlite3.connect(DB_FILE) as conn: conn.execute("UPDATE users SET lang = ? WHERE id = ?", (new_lang, uid))
    
    msg = "✅ Language has been changed to English." if new_lang == 'en' else "✅ تم تغيير اللغة إلى العربية."
    await update.message.reply_text(msg)

async def rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or not context.args[0].isdigit() or not (1 <= int(context.args[0]) <= 5):
        msg = "⭐ للتقييم، أرسل مثلاً:\n`/rate 5`" if get_user_lang(uid) == 'ar' else "⭐ To rate, send for example:\n`/rate 5`"
        return await update.message.reply_text(msg, parse_mode="Markdown")
    
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO ratings (user_id, rating) VALUES (?, ?)", (uid, int(context.args[0])))
    
    msg = "✅ شكراً لتقييمك و لدعمك!" if get_user_lang(uid) == 'ar' else "✅ Thank you for your rating and support!"
    await update.message.reply_text(msg)

async def fav_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT title, url FROM favorites WHERE user_id = ?", (uid,)).fetchall()
    
    if not rows:
        msg = "❌ مفضلتك فارغة." if lang == 'ar' else "❌ Your favorites list is empty."
        return await update.message.reply_text(msg)
    
    title_text = "🤍 قائمة مفضلتك:\n\n" if lang == 'ar' else "🤍 Your Favorites:\n\n"
    text = title_text + "\n".join([f"- <a href='{url}'>{clean_title(t, 30)}</a>" for t, url in rows])
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


# ==========================================================
# أحداث المستخدم والروابط الموحدة
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    uid = update.effective_user.id
    if is_banned(uid): return
    
    lang = get_user_lang(uid)
    await update.message.reply_text(
        build_start_text(update.effective_user.first_name or "", lang),
        reply_markup=user_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    await update.message.reply_text(
        "🛠 <b>لوحة الإدارة المتقدمة</b>\n\n"
        "أوامر إضافية للمدير:\n"
        "/update_dlp - لتحديث المحرك\n/setcookie - لتجديد الكوكيز\n/backup - لسحب قاعدة البيانات\n"
        "/msg id text - لمراسلة مستخدم\n/ban id - لحظر مستخدم\n/unban id - لفك الحظر",
        reply_markup=admin_main_keyboard(), parse_mode="HTML"
    )

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(
        build_playzone_links_text(),
        reply_markup=build_playzone_links_keyboard(),
        disable_web_page_preview=True
    )

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
            except Exception: fail += 1
        except Exception: fail += 1
    
    stat_inc_sync("broadcasts")
    await status.edit_text(f"✅ تم إرسال الإذاعة.\n\n• تم الإرسال: {sent}\n• فشل الإرسال: {fail}")

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    uid = update.effective_user.id
    register_user_sync(update.effective_user)
    
    if is_banned(uid): return
    lang = get_user_lang(uid)
    text = update.message.text.strip()

    if text in ["🔗 روابط PlayZone", "/links", "\\links"]:
        return await show_playzone_links(update, context)
    if text == "📘 دليل الاستخدام":
        return await update.message.reply_text(build_guide_text(lang), disable_web_page_preview=True)
    
    if is_admin(uid) and context.user_data.get("bc_active"):
        return await handle_broadcast_text(update, context, text)
    
    if uid in ACTIVE_USERS:
        msg = "⏳ لديك تحميل قيد التنفيذ.\n\nانتظر حتى يكتمل، أو قم بإيقافه أولاً." if lang == 'ar' else "⏳ You have an active download. Please wait."
        return await update.message.reply_text(msg)

    # إذا كان الرابط صحيحاً (تحميل مباشر)
    if is_valid_url(text):
        try:
            domain = urlparse(text).netloc.replace("www.", "")
            stat_inc_sync(f"plat_{domain}")
        except: pass
        
        status_msg = "🔍 جاري فحص الرابط وتجهيز المعاينة..." if lang == 'ar' else "🔍 Analyzing link..."
        status = await update.message.reply_text(status_msg)
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))

            title = clean_title(info.get("title"))
            artist = get_artist(info)
            duration_raw = info.get("duration") or 0
            est_size = format_size(get_largest_estimated_size(info))
            thumb = get_thumbnail(info)
            request_id = uuid.uuid4().hex[:10]

            ensure_pending_requests(context)[request_id] = {
                "url": text, "title": title, "artist": artist,
                "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())
            }
            trim_old_pending_requests(context)

            caption = build_preview_caption(title, artist, format_duration(duration_raw), est_size)
            await safe_delete(status)
            await send_preview(update, thumb, caption, build_preview_keyboard(request_id))
            stat_inc_sync("requests")
        except Exception as e:
            logger.warning(f"فشل جلب المعاينة: {e}")
            err_msg = "❌ تعذر قراءة الرابط.\n\nتأكد أن المقطع متاح للعامة." if lang == 'ar' else "❌ Could not read the link."
            await status.edit_text(err_msg)
            
    else:
        # البحث من داخل البوت عبر اسم الأغنية (بدون رابط)
        status_msg = "🔍 جاري البحث في يوتيوب..." if lang == 'ar' else "🔍 Searching YouTube..."
        status = await update.message.reply_text(status_msg)
        try:
            loop = asyncio.get_running_loop()
            opts = {"quiet": True, "extract_flat": True, "playlist_items": "1-5"}
            info = await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(f"ytsearch5:{text}", download=False))
            entries = info.get("entries", [])
            
            if not entries:
                return await status.edit_text("❌ لم يتم العثور على نتائج." if lang == 'ar' else "❌ No results found.")
            
            kb = []
            for e in entries:
                v_url = e.get("url")
                if v_url:
                    title = clean_title(e.get("title"), 40)
                    kb.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"searchdl:{v_url[-15:]}")])
                    context.user_data[f"searchdl:{v_url[-15:]}"] = v_url
            
            res_msg = "🔍 اختر المقطع المطلوب:" if lang == 'ar' else "🔍 Choose a result:"
            await status.edit_text(res_msg, reply_markup=InlineKeyboardMarkup(kb))
            stat_inc_sync("bot_searches")
        except Exception as e:
            await status.edit_text("❌ حدث خطأ أثناء البحث." if lang == 'ar' else "❌ Search error.")

# ==========================================================
# الأزرار ونظام الطابور الذكي
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
        return await query.message.edit_text(f"🧹 تم تنظيف الملفات المؤقتة.\n\nالعناصر المحذوفة: {removed}", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_bc":
        context.user_data["bc_active"] = True
        await query.answer()
        return await query.message.edit_text("📢 أرسل نص الرسالة التي تريد إرسالها لجميع المستخدمين:", reply_markup=admin_broadcast_keyboard(), parse_mode="HTML")
    elif data == "adm_cancel_bc":
        context.user_data["bc_active"] = False
        await query.answer("تم إلغاء الإذاعة")
        return await query.message.edit_text("تم إلغاء العملية.", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    data = query.data or ""
    uid = query.from_user.id
    lang = get_user_lang(uid)

    if is_banned(uid): return await query.answer("محظور" if lang == 'ar' else "Banned", show_alert=True)

    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("صلاحية إدارة فقط.", show_alert=True)
        return await handle_admin_callbacks(query, context)

    # زر البحث (المحاكاة كأن المستخدم أرسل الرابط)
    if data.startswith("searchdl:"):
        url = context.user_data.get(data)
        if not url: return await query.answer("انتهت صلاحية البحث" if lang == 'ar' else "Expired", show_alert=True)
        update.message = query.message
        update.message.text = url
        await query.message.delete()
        return await handle_incoming_text(update, context)

    # الإضافة للمفضلة
    if data.startswith("addfav:"):
        request_id = data.split(":")[1]
        request = ensure_pending_requests(context).get(request_id)
        if request:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("INSERT OR IGNORE INTO favorites (user_id, title, url) VALUES (?, ?, ?)", (uid, request['title'], request['url']))
            await query.answer("✅ تمت الإضافة للمفضلة" if lang == 'ar' else "✅ Added to favorites", show_alert=True)
        return

    # زر الإلغاء العام للمعانية
    if data.startswith("cancel:"):
        ensure_pending_requests(context).pop(data.split(":")[1], None)
        await query.answer("تم الإلغاء")
        return await safe_delete(query.message)

    # زر إيقاف التحميل النشط 
    if data.startswith("stop_dl:"):
        req_id = data.split(":")[1]
        CANCEL_FLAGS.add(req_id)
        return await query.answer("⚠️ جاري إيقاف التحميل..." if lang == 'ar' else "⚠️ Stopping...", show_alert=True)

    if data.startswith("aud:") or data.startswith("vid:"):
        mode = "audio" if data.startswith("aud:") else "video"
        request_id = data.split(":")[1]
        request = ensure_pending_requests(context).pop(request_id, None)
        trim_old_pending_requests(context)
        
        if not request: return await query.answer("انتهت جلسة الطلب، أرسل الرابط مجدداً." if lang == 'ar' else "Session expired.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ حالياً." if lang == 'ar' else "Active download running.", show_alert=True)
        
        await start_download_from_callback(query, context, request, mode, request_id)

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str, request_id: str):
    uid = query.from_user.id
    url = request.get("url")
    lang = get_user_lang(uid)
    
    ACTIVE_USERS.add(uid)
    CANCEL_FLAGS.discard(request_id)

    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    
    progress_data = {"text": "⏳ يرجى الانتظار..." if lang == 'ar' else "⏳ Please wait..."}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event, request_id))

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

        async with DOWNLOAD_SEMAPHORE:
            with progress_lock: progress_data["text"] = "🚀 بدأ التحميل... يرجى الانتظار ⏬"
            
            loop = asyncio.get_running_loop()
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))
            
            await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data, request_id))
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
            if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي")

            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with progress_lock: progress_data["text"] = "🎵 جاري تحويل الصوت بدقة عالية (MP3 320k) ودمج الغلاف..."
                final_mp3_path = job_dir / "playzone_final_audio.mp3"
                success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
                target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file
            else:
                target_file = raw_downloaded_file

            file_size = target_file.stat().st_size
            
            # --- الضغط التلقائي للفيديو إذا تجاوز الحد المسموح ---
            if file_size > MAX_TELEGRAM_SIZE and mode == "video":
                with progress_lock: progress_data["text"] = "⚙️ حجم الملف كبير.. جاري الضغط التلقائي للحفاظ على الجودة وإرساله..."
                compressed_path = job_dir / "playzone_compressed.mp4"
                success_comp = await loop.run_in_executor(EXECUTOR, lambda: compress_video_local(target_file, compressed_path))
                if success_comp:
                    target_file = compressed_path
                    file_size = target_file.stat().st_size
            
            if file_size > MAX_TELEGRAM_SIZE:
                stop_event.set()
                err = f"❌ حجم الملف لا يزال يتجاوز الحد.\n\nالحجم: {format_size(file_size)}\nالحد: {format_size(MAX_TELEGRAM_SIZE)}"
                return await edit_message_smart(query.message, err, reply_markup=None)

            stop_event.set()
            await edit_message_smart(query.message, "📤 تم تجهيز الملف، جاري الإرسال...", reply_markup=None)

            title = clean_title(request.get("title", "ملف ميديا"), 80)
            duration = int(request.get("duration") or 0)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration))}"
            share_link = f"https://t.me/share/url?url={quote(url)}&text={quote('🎬 ' + title)}"
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=share_link)]])

            with open(target_file, "rb") as f:
                if mode == "audio":
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                    try:
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id, audio=f, title=title,
                            performer=request.get("artist", "غير معروف"), duration=duration,
                            caption=caption, thumbnail=t_file, reply_markup=media_keyboard, parse_mode="HTML",
                            read_timeout=120, write_timeout=120
                        )
                    finally:
                        if t_file: t_file.close()
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id, video=f, caption=caption,
                        supports_streaming=True,  
                        duration=duration, reply_markup=media_keyboard, parse_mode="HTML",
                        read_timeout=120, write_timeout=120
                    )

            stat_inc_sync("success")
            stat_inc_sync("bytes", file_size)
            await safe_delete(query.message)

    except ValueError as e:
        if "USER_CANCELLED" in str(e):
            try: await edit_message_smart(query.message, "❌ تم إيقاف التحميل." if lang == 'ar' else "❌ Cancelled.")
            except: pass
    except (TimedOut, NetworkError) as e:
        stat_inc_sync("failed")
        logger.error(f"فشل اتصال تيليجرام: {e}")
        try: await edit_message_smart(query.message, "❌ تعذر إرسال الملف بسبب ضعف الاتصال.")
        except Exception: pass
    except Exception as e:
        stat_inc_sync("failed")
        logger.error(f"فشل المعالجة: {e}")
        try: await edit_message_smart(query.message, "❌ فشل تحميل المقطع.")
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        try: shutil.rmtree(job_dir)
        except Exception: pass
        ACTIVE_USERS.discard(uid)
        CANCEL_FLAGS.discard(request_id)

# ==========================================================
# التشغيل
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
    except Exception as e:
        logger.warning(f"فشل تهيئة الأوامر: {e}")

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")

    init_db()
    _cleanup_old_downloads_sync()

    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL:
        builder.base_url(LOCAL_API_URL)

    app = (
        builder.post_init(post_init)
        .connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("lang", lang_command))
    app.add_handler(CommandHandler("fav", fav_command))
    app.add_handler(CommandHandler("rate", rate_command))
    
    # أوامر المشرف
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("update_dlp", update_ytdlp_command))
    app.add_handler(CommandHandler("setcookie", set_cookie_command))
    app.add_handler(CommandHandler("backup", backup_db_command))
    app.add_handler(CommandHandler("msg", msg_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_command))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بالنسخة النهائية المتكاملة (Smart Queue & DB & Search & Compress).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
