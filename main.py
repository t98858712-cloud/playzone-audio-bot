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
# إعدادات PlayZone / Railway
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
# نظام اللغات الشامل 100%
# ==========================================================

LANG_DICT = {
    "ar": {
        "btn_guide": "📘 دليل الاستخدام",
        "btn_links": "🔗 روابط PlayZone",
        "btn_add_group": "➕ إضافة البوت للمجموعة",
        "btn_audio": "🎵 تحميل صوت",
        "btn_video": "🎬 تحميل فيديو",
        "btn_cancel": "❌ إلغاء",
        "btn_best_quality": "أفضل جودة",
        "btn_back": "🔙 رجوع",
        "msg_start": "أهلاً {first_name} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.\n\nابدأ بإرسال الرابط مباشرة.",
        "msg_guide": "📘 طريقة الاستخدام\n\n1) انسخ رابط المقطع.\n2) أرسله هنا في البوت.\n3) انتظر ظهور المعاينة.\n4) اختر التحميل صوت أو فيديو.",
        "msg_add_group": "🤖 لإضافة البوت إلى مجموعتك والتمتع بالتحميل المباشر، اضغط على الزر أدناه:",
        "btn_add_group_url": "➕ اضغط هنا لإضافة البوت",
        "msg_links": "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.",
        "msg_check_link": "🔍 جاري فحص الرابط وتجهيز المعاينة...",
        "msg_invalid_link": "❌ الرابط غير صحيح.\n\nأرسل رابط يبدأ بـ:\nhttp:// أو https://",
        "msg_wait_current": "⏳ لديك تحميل قيد التنفيذ.\n\nانتظر حتى يكتمل، ثم أرسل رابطاً جديداً.",
        "msg_link_error": "❌ تعذر قراءة الرابط.\n\nتأكد أن المقطع متاح للعامة وغير محذوف، ثم حاول مرة أخرى.",
        "msg_select_res": "يرجى اختيار الدقة",
        "msg_prep_audio": "جاري تجهيز الصوت...",
        "msg_prep_video": "جاري التجهيز...",
        "msg_session_expired": "انتهت جلسة هذا الطلب، يرجى إعادة إرسال الرابط.",
        "msg_dl_started": "🚀 بدأ التحميل... يرجى الانتظار ⏬",
        "msg_converting": "🎵 جاري تحويل الصوت ودمج الغلاف الخارجي...",
        "msg_too_large": "❌ حجم الملف يتجاوز الحد المسموح.\n\nالحجم: {size}\nالحد: {limit}",
        "msg_uploading": "📤 تم تجهيز الملف، جاري الإرسال...",
        "msg_dl_failed": "❌ فشل تحميل المقطع.\n\nقد يكون الرابط غير متاح أو يتجاوز الحد المسموح به.",
        "msg_network_error": "❌ تعذر إرسال الملف بسبب ضعف الاتصال أو ضغط مؤقت.\n\nحاول مرة أخرى بعد قليل.",
        "msg_cancel_done": "تم إلغاء طلب التحميل",
        "msg_back": "رجوع",
        "msg_lang_changed": "✅ تم تغيير لغة البوت إلى العربية.",
        "txt_unknown": "غير معروف",
        "txt_media_file": "ملف ميديا",
        "txt_placeholder": "أرسل الرابط هنا...",
        "msg_wait_progress": "⏳ يرجى الانتظار...",
        "txt_no_name": "بدون اسم",
        "txt_none": "لا يوجد",
        "msg_dl_progress": "📥 <b>جاري تحميل الملف...</b>\n\n{bar}  {percent}%\n📦 الحجم: {downloaded} / {total}\n🚀 السرعة: {speed}/ث",
        "msg_dl_progress_no_total": "📥 جاري التحميل...\n📦 تم تحميل: {downloaded}",
        "msg_dl_finished": "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي...",
        "share_text": "📥 حمّل أي فيديو أو أغنية MP3 في ثوانٍ!\n⚡ بوت سريع، مجاني وبأعلى جودة.\n👇 جرّبه الآن:",
        "btn_share": "🌟 أعجبك البوت؟ شاركه",
        # الإدارة
        "btn_adm_stats": "📊 الإحصائيات",
        "btn_adm_users": "👥 المستخدمون",
        "btn_adm_bc": "📢 إذاعة",
        "btn_adm_clean": "🧹 تنظيف الكاش",
        "btn_adm_srv": "📁 حالة السيرفر",
        "btn_adm_close": "✖️ إغلاق",
        "btn_adm_cancel_bc": "❌ إلغاء العملية",
        "msg_adm_panel": "🛠 <b>لوحة الإدارة المتقدمة</b>\n\nأوامر إضافية للمدير:\n/update_dlp - لتحديث محرك التحميل\n/setcookie - لتجديد ملف الكوكيز\n/backup - لسحب قاعدة البيانات وحمايتها من الضياع",
        "msg_adm_only": "صلاحية إدارة فقط.",
        "msg_adm_close": "تم الإغلاق",
        "msg_adm_clean": "جاري تنظيف الملفات المؤقتة...",
        "msg_adm_cleaned": "🧹 تم تنظيف الملفات المؤقتة.\n\nالعناصر المحذوفة: {removed}",
        "msg_adm_bc_ask": "📢 أرسل نص الرسالة التي تريد إرسالها لجميع المستخدمين:",
        "msg_adm_bc_cancel": "تم إلغاء الإذاعة",
        "msg_adm_bc_cancelled": "تم إلغاء العملية.",
        "msg_adm_no_users": "لا يوجد مستخدمون مسجلون.",
        "msg_adm_bc_start": "📢 جاري إرسال الرسالة للمستخدمين...",
        "msg_adm_bc_done": "✅ تم إرسال الإذاعة.\n\n• تم الإرسال: {sent}\n• فشل الإرسال: {fail}",
        "msg_adm_stats_text": "📊 <b>إحصائيات البوت</b>\n\n• الطلبات الكلية: {requests}\n• التحميلات الناجحة: {success}\n• العمليات الفاشلة: {failed}\n• عدد المستخدمين: {users}\n• حجم الملفات المرسلة: {bytes}\n• عدد الإذاعات: {broadcasts}",
        "msg_adm_users_title": "👥 <b>آخر المستخدمين النشطين:</b>",
        "msg_adm_srv_text": "📁 <b>حالة السيرفر</b>\n\n• مجلد التحميل: <code>{dl_dir}</code>\n• الملفات المؤقتة: {files}\n• حجم الملفات المؤقتة: {size}\n• العمليات النشطة: {active}\n• الحد الأقصى المتزامن: {max_workers}",
        "msg_adm_update_dlp": "🔄 جاري تحديث محرك التحميل...",
        "msg_adm_update_dlp_ok": "✅ تم تحديث محرك `yt-dlp` بنجاح إلى أحدث إصدار.",
        "msg_adm_update_dlp_fail": "❌ فشل التحديث: {e}",
        "msg_adm_setcookie": "📥 أرسل ملف `cookies.txt` كـ Document مع هذا الأمر لتخطي قيود يوتيوب.",
        "msg_adm_setcookie_ok": "✅ تم استلام وتركيب ملف الكوكيز بنجاح!",
        "msg_adm_backup": "📦 نسخة احتياطية من قاعدة البيانات.",
        "msg_adm_backup_fail": "❌ تعذر سحب النسخة: {e}"
    },
    "en": {
        "btn_guide": "📘 User Guide",
        "btn_links": "🔗 PlayZone Links",
        "btn_add_group": "➕ Add Bot to Group",
        "btn_audio": "🎵 Download Audio",
        "btn_video": "🎬 Download Video",
        "btn_cancel": "❌ Cancel",
        "btn_best_quality": "Best Quality",
        "btn_back": "🔙 Back",
        "msg_start": "Hello {first_name} 👋\n\nSend a video or audio link, and I'll show you a preview before downloading.\n\n💚 Your support makes a difference\n\nFollow official PlayZone links and share them with friends,\nEvery follow helps us grow and provide a better experience.\n\nStart by sending a link directly.",
        "msg_guide": "📘 How to use\n\n1) Copy the media link.\n2) Send it here in the bot.\n3) Wait for the preview.\n4) Choose to download audio or video.",
        "msg_add_group": "🤖 To add the bot to your group and enjoy direct downloading, click the button below:",
        "btn_add_group_url": "➕ Click here to add the bot",
        "msg_links": "💚 Your support makes a difference\n\nFollow official PlayZone links and share them with friends,\nEvery follow helps us grow and provide a better experience.",
        "msg_check_link": "🔍 Checking link and preparing preview...",
        "msg_invalid_link": "❌ Invalid link.\n\nSend a link starting with:\nhttp:// or https://",
        "msg_wait_current": "⏳ You have an ongoing download.\n\nWait until it finishes, then send a new link.",
        "msg_link_error": "❌ Could not read the link.\n\nMake sure the media is public and not deleted, then try again.",
        "msg_select_res": "Please select resolution",
        "msg_prep_audio": "Preparing audio...",
        "msg_prep_video": "Preparing...",
        "msg_session_expired": "Session for this request expired, please send the link again.",
        "msg_dl_started": "🚀 Download started... Please wait ⏬",
        "msg_converting": "🎵 Converting audio and embedding cover...",
        "msg_too_large": "❌ File size exceeds the limit.\n\nSize: {size}\nLimit: {limit}",
        "msg_uploading": "📤 File is ready, uploading...",
        "msg_dl_failed": "❌ Failed to download the media.\n\nLink might be unavailable or exceeds limits.",
        "msg_network_error": "❌ Could not send file due to connection issues.\n\nTry again in a bit.",
        "msg_cancel_done": "Download request canceled",
        "msg_back": "Back",
        "msg_lang_changed": "✅ Bot language changed to English.",
        "txt_unknown": "Unknown",
        "txt_media_file": "Media file",
        "txt_placeholder": "Send the link here...",
        "msg_wait_progress": "⏳ Please wait...",
        "txt_no_name": "No Name",
        "txt_none": "None",
        "msg_dl_progress": "📥 <b>Downloading file...</b>\n\n{bar}  {percent}%\n📦 Size: {downloaded} / {total}\n🚀 Speed: {speed}/s",
        "msg_dl_progress_no_total": "📥 Downloading...\n📦 Downloaded: {downloaded}",
        "msg_dl_finished": "⚙️ Download complete, preparing and compressing...",
        "share_text": "📥 Download any video or MP3 in seconds!\n⚡ Fast, free, and highest quality.\n👇 Try it now:",
        "btn_share": "🌟 Like the bot? Share it",
        # Admin text
        "btn_adm_stats": "📊 Statistics",
        "btn_adm_users": "👥 Users",
        "btn_adm_bc": "📢 Broadcast",
        "btn_adm_clean": "🧹 Clean Cache",
        "btn_adm_srv": "📁 Server Status",
        "btn_adm_close": "✖️ Close",
        "btn_adm_cancel_bc": "❌ Cancel Operation",
        "msg_adm_panel": "🛠 <b>Advanced Admin Panel</b>\n\nAdditional Admin Commands:\n/update_dlp - Update Download Engine\n/setcookie - Update Cookies File\n/backup - Backup Database",
        "msg_adm_only": "Admin privilege only.",
        "msg_adm_close": "Closed",
        "msg_adm_clean": "Cleaning temporary files...",
        "msg_adm_cleaned": "🧹 Temporary files cleaned.\n\nItems removed: {removed}",
        "msg_adm_bc_ask": "📢 Send the message text you want to broadcast to all users:",
        "msg_adm_bc_cancel": "Broadcast canceled",
        "msg_adm_bc_cancelled": "Operation canceled.",
        "msg_adm_no_users": "No registered users.",
        "msg_adm_bc_start": "📢 Sending message to users...",
        "msg_adm_bc_done": "✅ Broadcast sent.\n\n• Sent: {sent}\n• Failed: {fail}",
        "msg_adm_stats_text": "📊 <b>Bot Statistics</b>\n\n• Total Requests: {requests}\n• Successful Downloads: {success}\n• Failed Operations: {failed}\n• Total Users: {users}\n• Total Uploaded Size: {bytes}\n• Total Broadcasts: {broadcasts}",
        "msg_adm_users_title": "👥 <b>Latest Active Users:</b>",
        "msg_adm_srv_text": "📁 <b>Server Status</b>\n\n• Download Dir: <code>{dl_dir}</code>\n• Temp Files: {files}\n• Temp Size: {size}\n• Active Tasks: {active}\n• Max Concurrent: {max_workers}",
        "msg_adm_update_dlp": "🔄 Updating download engine...",
        "msg_adm_update_dlp_ok": "✅ `yt-dlp` engine updated successfully.",
        "msg_adm_update_dlp_fail": "❌ Update failed: {e}",
        "msg_adm_setcookie": "📥 Send the `cookies.txt` as a Document with this command to bypass restrictions.",
        "msg_adm_setcookie_ok": "✅ Cookies file received and updated successfully!",
        "msg_adm_backup": "📦 Database Backup.",
        "msg_adm_backup_fail": "❌ Failed to fetch backup: {e}"
    }
}

def _t(key: str, lang: str = "ar", **kwargs) -> str:
    text = LANG_DICT.get(lang, {}).get(key, LANG_DICT["ar"].get(key, key))
    if kwargs:
        try: return text.format(**kwargs)
        except Exception: return text
    return text

# ==========================================================
# إدارة قاعدة البيانات (نظيفة 100% دون أي تعديل)
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
                    last_seen INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )
            """)
            for k in ["requests", "success", "failed", "bytes", "broadcasts"]:
                conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (k,))

def register_user_sync(user):
    if not user: return
    now = int(time.time())
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT first_seen FROM users WHERE id = ?", (user.id,))
            row = cur.fetchone()
            first_seen = row[0] if row else now
            conn.execute("""
                INSERT OR REPLACE INTO users (id, username, first_name, last_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user.id, user.username or "", user.first_name or "", user.last_name or "", first_seen, now))

def stat_inc_sync(key: str, value: int = 1):
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))

def load_stats_sync() -> dict:
    with DB_LOCK:
        with sqlite3.connect(DB_FILE) as conn:
            rows = conn.execute("SELECT key, value FROM stats").fetchall()
            return {k: v for k, v in rows}

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
# أدوات الفحص والتنسيق
# ==========================================================

def parse_admin_ids():
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return {int(item.strip()) for item in admin_ids_raw.split(",") if item.strip().isdigit()}

def is_admin(user_id: int) -> bool:
    return user_id in parse_admin_ids()

def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60, lang: str = "ar") -> str:
    if not text: return _t("txt_media_file", lang)
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes, lang: str = "ar") -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return _t("txt_unknown", lang)
    if size_bytes <= 0: return _t("txt_unknown", lang)
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds, lang: str = "ar") -> str:
    try: seconds = int(seconds)
    except Exception: return _t("txt_unknown", lang)
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

def get_artist(info: dict, lang: str = "ar") -> str:
    for key in ["artist", "uploader", "channel", "creator"]:
        val = info.get(key)
        if val: return clean_title(val, 35, lang)
    return _t("txt_unknown", lang)

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
# الواجهات والأزرار
# ==========================================================

def user_main_keyboard(lang: str = "ar") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],
            [KeyboardButton(_t("btn_add_group", lang))]
        ],
        resize_keyboard=True, is_persistent=True, input_field_placeholder=_t("txt_placeholder", lang)
    )

def build_preview_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}")],
        [InlineKeyboardButton(_t("btn_video", lang), callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton(_t("btn_cancel", lang), callback_data=f"cancel:{request_id}")],
    ])

def build_resolution_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("360p", callback_data=f"res:360:{request_id}"),
            InlineKeyboardButton("480p", callback_data=f"res:480:{request_id}")
        ],
        [
            InlineKeyboardButton("720p", callback_data=f"res:720:{request_id}"),
            InlineKeyboardButton("1080p", callback_data=f"res:1080:{request_id}")
        ],
        [InlineKeyboardButton(_t("btn_best_quality", lang), callback_data=f"res:best:{request_id}")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data=f"back:{request_id}")]
    ])

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
    ])

def build_playzone_links_text(lang: str = "ar") -> str:
    return _t("msg_links", lang)

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_stats", lang), callback_data="adm_stats"), InlineKeyboardButton(_t("btn_adm_users", lang), callback_data="adm_users")],
        [InlineKeyboardButton(_t("btn_adm_bc", lang), callback_data="adm_bc"), InlineKeyboardButton(_t("btn_adm_clean", lang), callback_data="adm_clean")],
        [InlineKeyboardButton(_t("btn_adm_srv", lang), callback_data="adm_server"), InlineKeyboardButton(_t("btn_adm_close", lang), callback_data="adm_close")],
    ])

def admin_broadcast_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_cancel_bc", lang), callback_data="adm_cancel_bc")]
    ])

def build_start_text(first_name: str, lang: str = "ar") -> str:
    return _t("msg_start", lang, first_name=esc(first_name))

def build_guide_text(lang: str = "ar") -> str:
    return _t("msg_guide", lang)

def build_preview_caption(title: str, artist: str, duration: str, est_size: str) -> str:
    return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"

def build_admin_stats_text(lang: str = "ar") -> str:
    stats = load_stats_sync()
    users_count = len(all_user_ids())
    return _t("msg_adm_stats_text", lang, 
              requests=stats.get('requests', 0), 
              success=stats.get('success', 0), 
              failed=stats.get('failed', 0), 
              users=users_count, 
              bytes=format_size(stats.get('bytes', 0), lang), 
              broadcasts=stats.get('broadcasts', 0))

def build_admin_users_text(limit: int = 10, lang: str = "ar") -> str:
    users = get_latest_users(limit)
    lines = [_t("msg_adm_users_title", lang)]
    for u in users:
        name = u.get("first_name") or _t("txt_no_name", lang)
        username = f"@{u.get('username')}" if u.get("username") else _t("txt_none", lang)
        lines.append(f"• {esc(name)} — {esc(username)} — ID: <code>{u.get('id')}</code>")
    return "\n".join(lines)

def build_server_status_text(lang: str = "ar") -> str:
    total_size = sum(p.stat().st_size for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
    file_count = sum(1 for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
    return _t("msg_adm_srv_text", lang, 
              dl_dir=BASE_DOWNLOAD_DIR, 
              files=file_count, 
              size=format_size(total_size, lang), 
              active=len(ACTIVE_USERS), 
              max_workers=MAX_WORKERS)

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
# yt-dlp و FFmpeg
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
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
        if resolution == "best":
            opts["format"] = f"bestvideo[filesize<{max_fs}]+bestaudio/best[filesize<{max_fs}]/best"
        else:
            opts["format"] = f"bestvideo[height<={resolution}][filesize<{max_fs}]+bestaudio/best[height<={resolution}][filesize<{max_fs}]/best"
            
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]

    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)

    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_hook(progress_data: dict):
    def hook(d):
        lang = progress_data.get("lang", "ar")
        with progress_lock:
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                if total:
                    percent = downloaded / total * 100
                    progress_data["text"] = _t("msg_dl_progress", lang, bar=make_progress_bar(percent), percent=f"{percent:.1f}", downloaded=format_size(downloaded, lang), total=format_size(total, lang), speed=format_size(speed, lang))
                else:
                    progress_data["text"] = _t("msg_dl_progress_no_total", lang, downloaded=format_size(downloaded, lang))
            elif d.get("status") == "finished":
                progress_data["text"] = _t("msg_dl_finished", lang)
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=None)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict, resolution: str = "720"):
    opts = get_ydl_options(job_dir, progress_data, mode, resolution)
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

# ==========================================================
# أوامر الإدارة الديناميكية وتغيير اللغة
# ==========================================================

async def toggle_lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_lang = context.user_data.get("lang", "ar")
    new_lang = "en" if current_lang == "ar" else "ar"
    
    context.user_data["lang"] = new_lang
    msg = _t("msg_lang_changed", new_lang)
    
    await update.message.reply_text(msg, reply_markup=user_main_keyboard(new_lang))

async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    msg = await update.message.reply_text(_t("msg_adm_update_dlp", lang))
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text(_t("msg_adm_update_dlp_ok", lang))
    except Exception as e:
        await msg.edit_text(_t("msg_adm_update_dlp_fail", lang, e=e))

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    if not update.message.document:
        return await update.message.reply_text(_t("msg_adm_setcookie", lang))
    
    file_id = update.message.document.file_id
    new_file = await context.bot.get_file(file_id)
    await new_file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text(_t("msg_adm_setcookie_ok", lang))

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    lang = context.user_data.get("lang", "ar")
    try:
        with open(DB_FILE, "rb") as f:
            await update.message.reply_document(document=f, filename="bot_database.db", caption=_t("msg_adm_backup", lang))
    except Exception as e:
        await update.message.reply_text(_t("msg_adm_backup_fail", lang, e=e))

# ==========================================================
# أحداث المستخدم والروابط الموحدة
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(
        build_start_text(update.effective_user.first_name or "", lang),
        reply_markup=user_main_keyboard(lang), parse_mode="HTML", disable_web_page_preview=True
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(
        _t("msg_adm_panel", lang),
        reply_markup=admin_main_keyboard(lang), parse_mode="HTML"
    )

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(
        build_playzone_links_text(lang),
        reply_markup=build_playzone_links_keyboard(),
        disable_web_page_preview=True
    )

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    context.user_data["bc_active"] = False
    lang = context.user_data.get("lang", "ar")
    users = all_user_ids()
    if not users: return await update.message.reply_text(_t("msg_adm_no_users", lang))
    
    status = await update.message.reply_text(_t("msg_adm_bc_start", lang))
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
    await status.edit_text(_t("msg_adm_bc_done", lang, sent=sent, fail=fail))

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    register_user_sync(update.effective_user)
    uid = update.effective_user.id
    text = update.message.text.strip()
    lang = context.user_data.get("lang", "ar")

    if text in [_t("btn_links", "ar"), _t("btn_links", "en"), "/links", "\\links"]:
        return await show_playzone_links(update, context)
    
    if text in [_t("btn_guide", "ar"), _t("btn_guide", "en")]:
        return await update.message.reply_text(build_guide_text(lang), disable_web_page_preview=True)
    
    if text in [_t("btn_add_group", "ar"), _t("btn_add_group", "en")]:
        bot_username = context.bot.username
        add_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(_t("btn_add_group_url", lang), url=f"https://t.me/{bot_username}?startgroup=true")]
        ])
        return await update.message.reply_text(_t("msg_add_group", lang), reply_markup=add_keyboard)
    
    if is_admin(uid) and context.user_data.get("bc_active"):
        return await handle_broadcast_text(update, context, text)
    
    if uid in ACTIVE_USERS:
        return await update.message.reply_text(_t("msg_wait_current", lang))
    if not is_valid_url(text):
        return await update.message.reply_text(_t("msg_invalid_link", lang))

    status = await update.message.reply_text(_t("msg_check_link", lang))
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))

        title = clean_title(info.get("title"), lang=lang)
        artist = get_artist(info, lang=lang)
        duration_raw = info.get("duration") or 0
        est_size = format_size(get_largest_estimated_size(info), lang=lang)
        thumb = get_thumbnail(info)
        request_id = uuid.uuid4().hex[:10]

        ensure_pending_requests(context)[request_id] = {
            "url": text, "title": title, "artist": artist,
            "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())
        }
        trim_old_pending_requests(context)

        caption = build_preview_caption(title, artist, format_duration(duration_raw, lang), est_size)
        await safe_delete(status)
        await send_preview(update, thumb, caption, build_preview_keyboard(request_id, lang))
        stat_inc_sync("requests")
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة: {e}")
        await status.edit_text(_t("msg_link_error", lang))

# ==========================================================
# الأزرار ونظام الطابور الذكي (Semaphore Queue)
# ==========================================================

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    lang = context.user_data.get("lang", "ar")
    if data == "adm_close":
        await query.answer(_t("msg_adm_close", lang))
        return await safe_delete(query.message)
    elif data == "adm_stats":
        await query.answer()
        return await query.message.edit_text(build_admin_stats_text(lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")
    elif data == "adm_users":
        await query.answer()
        return await query.message.edit_text(build_admin_users_text(10, lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")
    elif data == "adm_server":
        await query.answer()
        return await query.message.edit_text(build_server_status_text(lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")
    elif data == "adm_clean":
        await query.answer(_t("msg_adm_clean", lang))
        removed = await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)
        return await query.message.edit_text(_t("msg_adm_cleaned", lang, removed=removed), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")
    elif data == "adm_bc":
        context.user_data["bc_active"] = True
        await query.answer()
        return await query.message.edit_text(_t("msg_adm_bc_ask", lang), reply_markup=admin_broadcast_keyboard(lang), parse_mode="HTML")
    elif data == "adm_cancel_bc":
        context.user_data["bc_active"] = False
        await query.answer(_t("msg_adm_bc_cancel", lang))
        return await query.message.edit_text(_t("msg_adm_bc_cancelled", lang), reply_markup=admin_main_keyboard(lang), parse_mode="HTML")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    data = query.data or ""
    uid = query.from_user.id
    lang = context.user_data.get("lang", "ar")

    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer(_t("msg_adm_only", lang), show_alert=True)
        return await handle_admin_callbacks(query, context)

    if data.startswith("cancel:"):
        ensure_pending_requests(context).pop(data.split(":")[1], None)
        await query.answer(_t("msg_cancel_done", lang))
        return await safe_delete(query.message)

    if data.startswith("back:"):
        request_id = data.split(":")[1]
        await query.answer(_t("msg_back", lang))
        return await query.message.edit_reply_markup(reply_markup=build_preview_keyboard(request_id, lang))

    if data.startswith("vid:"):
        request_id = data.split(":")[1]
        await query.answer(_t("msg_select_res", lang))
        return await query.message.edit_reply_markup(reply_markup=build_resolution_keyboard(request_id, lang))

    if data.startswith("aud:") or data.startswith("res:"):
        if data.startswith("aud:"):
            mode = "audio"
            resolution = "720"
            request_id = data.split(":")[1]
            await query.answer(_t("msg_prep_audio", lang))
        else:
            mode = "video"
            parts = data.split(":")
            resolution = parts[1]
            request_id = parts[2]
            await query.answer(_t("msg_prep_video", lang))

        request = ensure_pending_requests(context).pop(request_id, None)
        trim_old_pending_requests(context)
        
        if not request: return await query.answer(_t("msg_session_expired", lang), show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
        
        await start_download_from_callback(query, context, request, mode, resolution, lang)

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str, resolution: str, lang: str):
    uid = query.from_user.id
    url = request.get("url")
    ACTIVE_USERS.add(uid)

    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    
    progress_data = {"text": _t("msg_wait_progress", lang), "lang": lang}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

        async with DOWNLOAD_SEMAPHORE:
            with progress_lock: progress_data["text"] = _t("msg_dl_started", lang)
            
            loop = asyncio.get_running_loop()
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))
            
            await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data, resolution))
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
            if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")

            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with progress_lock: progress_data["text"] = _t("msg_converting", lang)
                final_mp3_path = job_dir / "playzone_final_audio.mp3"
                success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
                target_file = final_mp3_path if success and final_mp3_path.exists() else raw_downloaded_file
            else:
                target_file = raw_downloaded_file

            file_size = target_file.stat().st_size
            if file_size > MAX_TELEGRAM_SIZE:
                stop_event.set()
                return await edit_message_smart(query.message, _t("msg_too_large", lang, size=format_size(file_size, lang), limit=format_size(MAX_TELEGRAM_SIZE, lang)), reply_markup=None)

            stop_event.set()
            await edit_message_smart(query.message, _t("msg_uploading", lang), reply_markup=None)

            title = clean_title(request.get("title", _t("txt_media_file", lang)), 80, lang)
            duration = int(request.get("duration") or 0)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration, lang))}"            
            
            share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(_t('share_text', lang))}"
            
            media_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(_t("btn_share", lang), url=share_link)]
            ])

            with open(target_file, "rb") as f:
                if mode == "audio":
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                    try:
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id, audio=f, title=title,
                            performer=request.get("artist", _t("txt_unknown", lang)), duration=duration,
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

    except (TimedOut, NetworkError) as e:
        stat_inc_sync("failed")
        logger.error(f"فشل اتصال تيليجرام: {e}")
        try: await edit_message_smart(query.message, _t("msg_network_error", lang))
        except Exception: pass
    except Exception as e:
        stat_inc_sync("failed")
        logger.error(f"فشل المعالجة: {e}")
        try: await edit_message_smart(query.message, _t("msg_dl_failed", lang))
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        try: shutil.rmtree(job_dir)
        except Exception: pass
        ACTIVE_USERS.discard(uid)

# ==========================================================
# التشغيل
# ==========================================================

async def post_init(app: Application):
    commands = [
        BotCommand("start", "بدء / Start"), 
        BotCommand("language", "تغيير اللغة / Toggle Language"), 
        BotCommand("links", "الروابط / Links")
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
    app.add_handler(CommandHandler("language", toggle_lang_command)) # اسم السلاش الجديد
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("update_dlp", update_ytdlp_command))
    app.add_handler(CommandHandler("setcookie", set_cookie_command))
    app.add_handler(CommandHandler("backup", backup_db_command))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بالنسخة النهائية الشاملة (Toggle Language & Full Translation).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
