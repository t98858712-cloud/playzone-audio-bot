import os
import re
import uuid
import shutil
import asyncio
import logging
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, date

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import yt_dlp

# =========================================================
# PlayZone Audio Bot - Safe/Legal Edition
# مجاني للمستخدمين بشرط متابعة منصات PlayZone
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. ضع BOT_TOKEN في Railway Variables.")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
APP_NAME = os.getenv("APP_NAME", "PlayZone Audio Bot")

INSTAGRAM_URL = os.getenv(
    "INSTAGRAM_URL",
    "https://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg=="
)

TELEGRAM_URL = os.getenv(
    "TELEGRAM_URL",
    "https://t.me/P1ay_Z0ne_Bot"
)

# ضع هنا قناة/مجموعة PlayZone إذا أردت تحقق تيليجرام حقيقي مثل: @PlayZone_Channel
# يجب إضافة هذا البوت Admin داخل القناة/المجموعة
TELEGRAM_REQUIRED_CHAT = os.getenv("TELEGRAM_REQUIRED_CHAT", "").strip()
VERIFY_TELEGRAM = os.getenv("VERIFY_TELEGRAM", "off").lower()

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DB_PATH = BASE_DIR / "bot_data.sqlite3"
DOWNLOADS_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(APP_NAME)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
ACTIVE_JOBS: dict[int, str] = {}

# منصات محمية/غير مسموحة افتراضياً لتجنب مشاكل الحقوق وشروط الاستخدام
BLOCKED_PLATFORM_KEYWORDS = [
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "fb.watch",
    "x.com", "twitter.com", "soundcloud.com", "spotify.com", "deezer.com", "music.apple.com",
]

# مواقع مسموحة افتراضياً: محتوى عام/مرخص أو نماذج ملفات
DEFAULT_ALLOWED_DOMAINS = [
    "archive.org", "www.archive.org", "commons.wikimedia.org", "upload.wikimedia.org",
    "freemusicarchive.org", "www.freemusicarchive.org", "pixabay.com", "www.pixabay.com",
    "pexels.com", "www.pexels.com", "samplelib.com", "filesamples.com",
]

DIRECT_MEDIA_EXTENSIONS = (
    ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".mp4", ".mov", ".webm", ".mkv", ".avi"
)

# =========================================================
# Database
# =========================================================

def db_connect():
    return sqlite3.connect(DB_PATH)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return date.today().strftime("%Y-%m-%d")


def db_init():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            total_requests INTEGER DEFAULT 0,
            total_success INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            follow_confirmed INTEGER DEFAULT 0,
            follow_confirmed_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT,
            source TEXT,
            title TEXT,
            status TEXT,
            size_mb REAL DEFAULT 0,
            created_at TEXT,
            finished_at TEXT,
            error TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_domains (
            domain TEXT PRIMARY KEY,
            added_at TEXT
        )
        """)
        con.commit()
    seed_defaults()


def seed_defaults():
    defaults = {
        "max_file_mb": os.getenv("MAX_FILE_MB", "45"),
        "daily_limit": os.getenv("DAILY_LIMIT", "20"),
        "maintenance": "off",
        "allow_direct_links": "on",
        "force_follow": "on",
    }
    with db_connect() as con:
        cur = con.cursor()
        for key, value in defaults.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        for domain in DEFAULT_ALLOWED_DOMAINS:
            cur.execute("INSERT OR IGNORE INTO allowed_domains (domain, added_at) VALUES (?, ?)", (domain, now_text()))
        con.commit()


def get_setting(key: str, default: str = "") -> str:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        con.commit()


def get_max_file_mb() -> int:
    try:
        return max(1, int(get_setting("max_file_mb", "45")))
    except Exception:
        return 45


def get_daily_limit() -> int:
    try:
        return max(1, int(get_setting("daily_limit", "20")))
    except Exception:
        return 20


def is_maintenance() -> bool:
    return get_setting("maintenance", "off") == "on"


def is_force_follow() -> bool:
    return get_setting("force_follow", "on") == "on"


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID and user_id == ADMIN_ID)


def register_user(message: Message):
    if not message.from_user:
        return
    user = message.from_user
    username = user.username or ""
    full_name = user.full_name or ""
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE users
                SET username = ?, full_name = ?, last_seen = ?, total_requests = total_requests + 1
                WHERE user_id = ?
            """, (username, full_name, now_text(), user.id))
        else:
            cur.execute("""
                INSERT INTO users (user_id, username, full_name, first_seen, last_seen, total_requests)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (user.id, username, full_name, now_text(), now_text()))
        con.commit()


def is_user_blocked(user_id: int) -> bool:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return bool(row and row[0] == 1)


def set_user_block(user_id: int, blocked: bool):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen, is_blocked)
            VALUES (?, '', '', ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_blocked = excluded.is_blocked
        """, (user_id, now_text(), now_text(), 1 if blocked else 0))
        con.commit()


def is_follow_confirmed(user_id: int) -> bool:
    if not is_force_follow() or is_admin(user_id):
        return True
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT follow_confirmed FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return bool(row and row[0] == 1)


def set_follow_confirmed(user_id: int):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET follow_confirmed = 1, follow_confirmed_at = ? WHERE user_id = ?", (now_text(), user_id))
        con.commit()


def add_download(user_id: int, kind: str, source: str, title: str = "") -> int:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO downloads (user_id, kind, source, title, status, created_at)
            VALUES (?, ?, ?, ?, 'processing', ?)
        """, (user_id, kind, source, title, now_text()))
        con.commit()
        return cur.lastrowid


def finish_download(download_id: int, user_id: int, status: str, size_mb: float = 0, error: str = ""):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE downloads SET status = ?, size_mb = ?, finished_at = ?, error = ? WHERE id = ?
        """, (status, size_mb, now_text(), error[:400], download_id))
        if status == "success":
            cur.execute("UPDATE users SET total_success = total_success + 1 WHERE user_id = ?", (user_id,))
        else:
            cur.execute("UPDATE users SET total_failed = total_failed + 1 WHERE user_id = ?", (user_id,))
        con.commit()


def user_downloads_today(user_id: int) -> int:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM downloads WHERE user_id = ? AND substr(created_at, 1, 10) = ?", (user_id, today_text()))
        return cur.fetchone()[0]


def get_allowed_domains():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT domain FROM allowed_domains ORDER BY domain ASC")
        return [r[0] for r in cur.fetchall()]


def add_allowed_domain(domain: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO allowed_domains (domain, added_at) VALUES (?, ?)", (domain, now_text()))
        con.commit()


def remove_allowed_domain(domain: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM allowed_domains WHERE domain = ?", (domain,))
        con.commit()

# =========================================================
# UI
# =========================================================

def main_menu(user_id: int):
    rows = [
        [InlineKeyboardButton(text="🎵 تحويل ملف إلى MP3", callback_data="help_file")],
        [InlineKeyboardButton(text="🔗 الروابط المسموحة", callback_data="help_links"), InlineKeyboardButton(text="📊 إحصائياتي", callback_data="my_stats")],
        [InlineKeyboardButton(text="⚖️ سياسة الاستخدام", callback_data="policy"), InlineKeyboardButton(text="❓ مساعدة", callback_data="help")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🛠 لوحة الإدارة", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def follow_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 تابع PlayZone على Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton(text="💬 افتح Telegram PlayZone", url=TELEGRAM_URL)],
        [InlineKeyboardButton(text="✅ تحققت من المتابعة", callback_data="check_follow")],
    ])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="home")]])


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 إحصائيات", callback_data="admin_stats"), InlineKeyboardButton(text="📅 شهرياً", callback_data="admin_monthly")],
        [InlineKeyboardButton(text="🌐 المواقع", callback_data="admin_domains"), InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="admin_settings")],
        [InlineKeyboardButton(text="🔐 شرط المتابعة", callback_data="admin_follow_toggle"), InlineKeyboardButton(text="🟢/🔴 الصيانة", callback_data="admin_maintenance")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="home")],
    ])

# =========================================================
# Helpers
# =========================================================

def clean_domain(domain: str) -> str:
    domain = domain.strip().lower().replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].replace(":443", "").replace(":80", "")
    return domain


def get_domain(url: str) -> str:
    return clean_domain(urlparse(url).netloc)


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip(), re.I))


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w\s\-.ء-ي]", "", name, flags=re.UNICODE).strip()
    return name[:80] if name else "audio"


def file_size_mb(path: Path) -> float:
    if not path.exists():
        return 0
    return round(path.stat().st_size / (1024 * 1024), 2)


def progress_bar(percent: float, width: int = 12) -> str:
    percent = max(0, min(100, percent))
    filled = int(width * percent / 100)
    return "▰" * filled + "▱" * (width - filled)


def monthly_bar(value: int, max_value: int, width: int = 10) -> str:
    if max_value <= 0:
        return "▱" * width
    filled = int(width * value / max_value)
    return "▰" * filled + "▱" * (width - filled)


def format_bytes(num):
    if not num:
        return "غير معروف"
    try:
        num = float(num)
        for unit in ["B", "KB", "MB", "GB"]:
            if num < 1024:
                return f"{num:.1f}{unit}"
            num /= 1024
        return f"{num:.1f}TB"
    except Exception:
        return "غير معروف"


def format_eta(seconds):
    if seconds is None:
        return "غير معروف"
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}س {m}د"
        if m:
            return f"{m}د {s}ث"
        return f"{s}ث"
    except Exception:
        return "غير معروف"


def is_blocked_platform(url: str) -> bool:
    low = url.lower()
    return any(x in low for x in BLOCKED_PLATFORM_KEYWORDS)


def is_allowed_url(url: str) -> tuple[bool, str]:
    domain = get_domain(url)
    path = urlparse(url).path.lower()
    if is_blocked_platform(url):
        return False, "الرابط من منصة غير مسموحة داخل هذا البوت."
    if domain in set(get_allowed_domains()):
        return True, "domain"
    if get_setting("allow_direct_links", "on") == "on" and path.endswith(DIRECT_MEDIA_EXTENSIONS):
        return True, "direct"
    return False, "الموقع غير موجود في قائمة المواقع المسموحة."


async def cleanup_later(folder: Path, delay: int = 10):
    await asyncio.sleep(delay)
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except Exception as e:
        logger.warning(f"cleanup failed: {e}")


async def notify_admin(text: str):
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, text)
        except Exception:
            pass


def run_ffmpeg_to_mp3(input_path: Path, output_path: Path):
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(output_path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        logger.error(result.stderr[-1200:])
        raise RuntimeError("فشل التحويل بواسطة ffmpeg")


async def send_final_audio(message: Message, path: Path, title: str, caption: str):
    size_mb = file_size_mb(path)
    if size_mb <= 48:
        await message.answer_audio(audio=FSInputFile(path), title=title[:64], caption=caption)
    else:
        await message.answer_document(document=FSInputFile(path), caption=caption + "\n\n📦 تم إرساله كملف بسبب الحجم.")

# =========================================================
# Follow Check
# =========================================================

async def check_telegram_membership(user_id: int) -> bool:
    if VERIFY_TELEGRAM != "on" or not TELEGRAM_REQUIRED_CHAT:
        return True
    try:
        member = await bot.get_chat_member(TELEGRAM_REQUIRED_CHAT, user_id)
        return member.status in ["member", "administrator", "creator"]
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception as e:
        logger.warning(f"membership check failed: {e}")
        return False


async def require_follow_message(message: Message) -> bool:
    user_id = message.from_user.id
    if is_follow_confirmed(user_id):
        return True
    await message.answer(
        "🔐 لاستخدام البوت مجاناً، تابع منصات PlayZone أولاً.\n\n"
        "1️⃣ تابع حساب Instagram.\n"
        "2️⃣ افتح Telegram PlayZone.\n"
        "3️⃣ اضغط زر التحقق.\n\n"
        "بعدها تستطيع استخدام البوت مجاناً.",
        reply_markup=follow_menu()
    )
    return False


async def require_follow_callback(call: CallbackQuery) -> bool:
    user_id = call.from_user.id
    if is_follow_confirmed(user_id):
        return True
    await call.message.edit_text(
        "🔐 لاستخدام البوت مجاناً، تابع منصات PlayZone أولاً.\n\n"
        "1️⃣ تابع حساب Instagram.\n2️⃣ افتح Telegram PlayZone.\n3️⃣ اضغط زر التحقق.",
        reply_markup=follow_menu()
    )
    await call.answer()
    return False

# =========================================================
# Stats
# =========================================================

def admin_stats_text() -> str:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
        blocked_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE follow_confirmed = 1")
        followers_confirmed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM downloads")
        total_requests = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM downloads WHERE status = 'success'")
        success = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM downloads WHERE status != 'success'")
        failed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE substr(first_seen, 1, 7) = strftime('%Y-%m', 'now')")
        new_users_month = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM downloads WHERE substr(created_at, 1, 7) = strftime('%Y-%m', 'now')")
        requests_month = cur.fetchone()[0]
        cur.execute("SELECT IFNULL(SUM(size_mb), 0) FROM downloads WHERE status = 'success'")
        total_mb = cur.fetchone()[0]
    rate = round((success / total_requests) * 100, 1) if total_requests else 0
    return (
        "🛠 لوحة الإدارة\n\n📊 الإحصائيات العامة\n"
        f"👥 إجمالي المستخدمين: {total_users}\n🔐 أكدوا المتابعة: {followers_confirmed}\n🚫 المحظورين: {blocked_users}\n"
        f"🆕 مستخدمون جدد هذا الشهر: {new_users_month}\n⬇️ إجمالي الطلبات: {total_requests}\n📅 طلبات هذا الشهر: {requests_month}\n"
        f"✅ ناجحة: {success}\n❌ فاشلة: {failed}\n📈 نسبة النجاح: {rate}%\n💾 الحجم المرسل تقريباً: {round(total_mb, 2)} MB\n"
    )


def monthly_stats_text() -> str:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT substr(created_at, 1, 7) AS month, COUNT(*) AS requests,
                   COUNT(DISTINCT user_id) AS active_users,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success
            FROM downloads GROUP BY month ORDER BY month DESC LIMIT 12
        """)
        rows = cur.fetchall()
    if not rows:
        return "📅 لا توجد بيانات شهرية بعد."
    max_requests = max([r[1] for r in rows] or [1])
    max_users = max([r[2] for r in rows] or [1])
    text = "📅 المستخدمين والتحميلات شهرياً\n\n"
    for month, requests, active_users, success in rows:
        text += f"🗓 {month}\n⬇️ {monthly_bar(requests, max_requests)} {requests} طلب\n👥 {monthly_bar(active_users, max_users)} {active_users} مستخدم\n✅ ناجح: {success or 0}\n\n"
    return text


def user_stats_text(user_id: int) -> str:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT total_requests, total_success, total_failed, first_seen, last_seen, follow_confirmed FROM users WHERE user_id = ?", (user_id,))
        user_row = cur.fetchone()
        cur.execute("SELECT COUNT(*), IFNULL(SUM(size_mb), 0) FROM downloads WHERE user_id = ? AND status = 'success'", (user_id,))
        d_row = cur.fetchone()
    if not user_row:
        return "📊 لا توجد إحصائيات لك بعد."
    total_requests, total_success, total_failed, first_seen, last_seen, follow_confirmed = user_row
    _, total_mb = d_row
    return (
        "📊 إحصائياتك\n\n"
        f"🔐 حالة المتابعة: {'مؤكد' if follow_confirmed else 'غير مؤكد'}\n📨 عدد الطلبات: {total_requests}\n"
        f"✅ الناجحة: {total_success}\n❌ الفاشلة: {total_failed}\n💾 الحجم المرسل تقريباً: {round(total_mb, 2)} MB\n"
        f"🕒 أول استخدام: {first_seen}\n🕘 آخر استخدام: {last_seen}\n"
    )


def settings_text() -> str:
    return (
        "⚙️ إعدادات البوت\n\n"
        f"📦 حد حجم الملف: {get_max_file_mb()}MB\n📅 الحد اليومي لكل مستخدم: {get_daily_limit()} طلب\n"
        f"🟢 الصيانة: {get_setting('maintenance', 'off')}\n🔐 شرط المتابعة: {get_setting('force_follow', 'on')}\n"
        f"🔗 قبول روابط الملفات المباشرة: {get_setting('allow_direct_links', 'on')}\n\n"
        "أوامر الإدارة:\n/setmax 45\n/setlimit 20\n/directlinks on أو off\n/maintenance on أو off\n/forcefollow on أو off\n"
    )


def domains_text() -> str:
    domains = get_allowed_domains()
    if not domains:
        return "🌐 لا توجد مواقع مسموحة."
    text = "🌐 المواقع المسموحة\n\n"
    for d in domains[:80]:
        text += f"• {d}\n"
    text += "\nأوامر الإدارة:\n/adddomain example.com\n/removedomain example.com"
    return text

# =========================================================
# Guards
# =========================================================

async def guard_message(message: Message, need_follow: bool = True) -> bool:
    register_user(message)
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer("🚫 حسابك محظور من استخدام البوت.")
        return False
    if is_maintenance() and not is_admin(user_id):
        await message.answer("🛠 البوت في وضع الصيانة حالياً.")
        return False
    if need_follow and not await require_follow_message(message):
        return False
    return True


def can_start_job(user_id: int) -> tuple[bool, str]:
    if user_id in ACTIVE_JOBS:
        return False, "⏳ لديك طلب يعمل الآن. انتظر حتى يكتمل قبل إرسال طلب جديد."
    daily_limit = get_daily_limit()
    used_today = user_downloads_today(user_id)
    if used_today >= daily_limit and not is_admin(user_id):
        return False, f"📅 وصلت للحد اليومي المجاني: {daily_limit} طلب."
    return True, ""

# =========================================================
# User Commands
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):
    if not await guard_message(message, need_follow=False):
        return
    if not await require_follow_message(message):
        return
    await message.answer(
        f"🎧 أهلاً بك في {APP_NAME}\n\n"
        "الاستخدام بسيط جداً:\n"
        "أرسل ملف فيديو أو صوت، وسأحوله إلى MP3 جاهز للتشغيل.\n\n"
        "أو أرسل رابط ملف مباشر مسموح.\n\n"
        "⚠️ إذا كان الرابط من منصة لا تسمح بالتحميل، أرسل الملف نفسه للبوت وسأحوله لك.",
        reply_markup=main_menu(message.from_user.id)
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if not await guard_message(message):
        return
    await message.answer(
        "❓ طريقة الاستخدام\n\n1. تابع منصات PlayZone.\n2. أرسل فيديو أو ملف صوتي.\n3. انتظر مستوى التحويل.\n4. استلم MP3.\n\n/start - القائمة\n/stats - إحصائياتك",
        reply_markup=main_menu(message.from_user.id)
    )


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not await guard_message(message):
        return
    await message.answer(user_stats_text(message.from_user.id))

# =========================================================
# Admin Commands
# =========================================================

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not await guard_message(message, need_follow=False):
        return
    if not is_admin(message.from_user.id):
        await message.answer("❌ هذا الأمر للمدير فقط.")
        return
    await message.answer("🛠 لوحة الإدارة", reply_markup=admin_menu())


@dp.message(Command("setmax"))
async def setmax_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("استخدم:\n/setmax 45")
        return
    value = max(1, int(parts[1]))
    set_setting("max_file_mb", str(value))
    await message.answer(f"✅ تم ضبط حد حجم الملف إلى {value}MB")


@dp.message(Command("setlimit"))
async def setlimit_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("استخدم:\n/setlimit 20")
        return
    value = max(1, int(parts[1]))
    set_setting("daily_limit", str(value))
    await message.answer(f"✅ تم ضبط الحد اليومي إلى {value} طلب")


@dp.message(Command("maintenance"))
async def maintenance_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or parts[1].lower() not in ["on", "off"]:
        await message.answer("استخدم:\n/maintenance on\n/maintenance off")
        return
    mode = parts[1].lower()
    set_setting("maintenance", mode)
    await message.answer(f"✅ وضع الصيانة الآن: {mode}")


@dp.message(Command("forcefollow"))
async def forcefollow_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or parts[1].lower() not in ["on", "off"]:
        await message.answer("استخدم:\n/forcefollow on\n/forcefollow off")
        return
    mode = parts[1].lower()
    set_setting("force_follow", mode)
    await message.answer(f"✅ شرط المتابعة الآن: {mode}")


@dp.message(Command("directlinks"))
async def directlinks_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or parts[1].lower() not in ["on", "off"]:
        await message.answer("استخدم:\n/directlinks on\n/directlinks off")
        return
    mode = parts[1].lower()
    set_setting("allow_direct_links", mode)
    await message.answer(f"✅ قبول روابط الملفات المباشرة: {mode}")


@dp.message(Command("adddomain"))
async def adddomain_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("استخدم:\n/adddomain example.com")
        return
    domain = clean_domain(parts[1])
    if not domain or "." not in domain:
        await message.answer("❌ الدومين غير صحيح.")
        return
    add_allowed_domain(domain)
    await message.answer(f"✅ تم إضافة الموقع:\n{domain}")


@dp.message(Command("removedomain"))
async def removedomain_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("استخدم:\n/removedomain example.com")
        return
    domain = clean_domain(parts[1])
    remove_allowed_domain(domain)
    await message.answer(f"✅ تم حذف الموقع:\n{domain}")


@dp.message(Command("block"))
async def block_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("استخدم:\n/block 123456789")
        return
    target_id = int(parts[1])
    set_user_block(target_id, True)
    await message.answer(f"🚫 تم حظر المستخدم: {target_id}")


@dp.message(Command("unblock"))
async def unblock_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("استخدم:\n/unblock 123456789")
        return
    target_id = int(parts[1])
    set_user_block(target_id, False)
    await message.answer(f"✅ تم فك حظر المستخدم: {target_id}")


@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message):
    if not await guard_message(message, need_follow=False) or not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("استخدم:\n/broadcast نص الرسالة")
        return
    text = parts[1]
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users WHERE is_blocked = 0")
        users = [r[0] for r in cur.fetchall()]
    sent = failed = 0
    status = await message.answer("📢 جاري الإرسال...")
    for uid in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await status.edit_text(f"📢 انتهى الإرسال\n\n✅ وصل: {sent}\n❌ فشل: {failed}")

# =========================================================
# Callbacks
# =========================================================

@dp.callback_query(F.data == "check_follow")
async def cb_check_follow(call: CallbackQuery):
    register_user(call.message)
    telegram_ok = await check_telegram_membership(call.from_user.id)
    if not telegram_ok:
        await call.answer("لم يتم العثور على عضويتك في قناة/مجموعة تيليجرام.", show_alert=True)
        return
    set_follow_confirmed(call.from_user.id)
    await call.message.edit_text(
        "✅ تم تفعيل استخدام البوت مجاناً.\n\nشكراً لدعمك PlayZone.\nيمكنك الآن إرسال ملف فيديو/صوت أو رابط مسموح.",
        reply_markup=main_menu(call.from_user.id)
    )
    await call.answer("تم التحقق")


@dp.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery):
    if not await require_follow_callback(call):
        return
    await call.message.edit_text(f"🎧 {APP_NAME}\n\nاختر من القائمة:", reply_markup=main_menu(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "help_file")
async def cb_help_file(call: CallbackQuery):
    if not await require_follow_callback(call):
        return
    await call.message.edit_text(
        "🎵 تحويل ملف إلى MP3\n\nأرسل ملف صوتي أو فيديو مثل:\nMP4 / MOV / WEBM / M4A / WAV / OGG / FLAC\n\nسيظهر لك مستوى التقدم، ثم يصلك ملف MP3.",
        reply_markup=back_menu()
    )
    await call.answer()


@dp.callback_query(F.data == "help_links")
async def cb_help_links(call: CallbackQuery):
    if not await require_follow_callback(call):
        return
    await call.message.edit_text(
        "🔗 الروابط المسموحة\n\nيدعم البوت المواقع التي يضيفها المدير وروابط الملفات المباشرة إذا كانت مفعلة.\nلا يتم قبول روابط المنصات المحمية أو غير المسموحة.",
        reply_markup=back_menu()
    )
    await call.answer()


@dp.callback_query(F.data == "policy")
async def cb_policy(call: CallbackQuery):
    await call.message.edit_text(
        "⚖️ سياسة الاستخدام\n\nالبوت مجاني لمتابعي PlayZone.\n\nاستخدمه فقط مع ملفاتك الشخصية أو محتوى تملك حق استخدامه أو محتوى عام/مرخّص.\nالبوت ليس مخصصاً لتجاوز حقوق النشر أو قيود المنصات.",
        reply_markup=back_menu()
    )
    await call.answer()


@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    if not await require_follow_callback(call):
        return
    await call.message.edit_text("❓ المساعدة\n\n• تابع منصات PlayZone أولاً.\n• أرسل ملف فيديو أو صوت.\n• انتظر التحويل.\n• استلم MP3.", reply_markup=back_menu())
    await call.answer()


@dp.callback_query(F.data == "my_stats")
async def cb_my_stats(call: CallbackQuery):
    if not await require_follow_callback(call):
        return
    await call.message.edit_text(user_stats_text(call.from_user.id), reply_markup=back_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_home")
async def cb_admin_home(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    await call.message.edit_text("🛠 لوحة الإدارة", reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    await call.message.edit_text(admin_stats_text(), reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_monthly")
async def cb_admin_monthly(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    await call.message.edit_text(monthly_stats_text(), reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_domains")
async def cb_admin_domains(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    await call.message.edit_text(domains_text(), reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_settings")
async def cb_admin_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    await call.message.edit_text(settings_text(), reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_maintenance")
async def cb_admin_maintenance(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    current = get_setting("maintenance", "off")
    new_value = "off" if current == "on" else "on"
    set_setting("maintenance", new_value)
    await call.message.edit_text(f"✅ تم تغيير وضع الصيانة إلى: {new_value}", reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "admin_follow_toggle")
async def cb_admin_follow_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("غير مسموح", show_alert=True)
        return
    current = get_setting("force_follow", "on")
    new_value = "off" if current == "on" else "on"
    set_setting("force_follow", new_value)
    await call.message.edit_text(f"✅ شرط المتابعة الآن: {new_value}", reply_markup=admin_menu())
    await call.answer()

# =========================================================
# Media Handling
# =========================================================

@dp.message(F.video | F.audio | F.voice | F.document)
async def handle_media(message: Message):
    if not await guard_message(message):
        return
    user_id = message.from_user.id
    ok, reason = can_start_job(user_id)
    if not ok:
        await message.answer(reason)
        return

    job_id = str(uuid.uuid4())
    ACTIVE_JOBS[user_id] = job_id
    workdir = DOWNLOADS_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    download_id = add_download(user_id, "telegram_file", "telegram")
    status = await message.answer("⏳ تم استلام الطلب\n\n▰▱▱▱▱▱▱▱▱▱▱▱ 5%\n📥 جاري فحص الملف...")

    try:
        max_file_mb = get_max_file_mb()
        file_obj = None
        original_name = "audio"
        if message.video:
            file_obj = message.video
            original_name = message.video.file_name or "video.mp4"
        elif message.audio:
            file_obj = message.audio
            original_name = message.audio.file_name or message.audio.title or "audio.mp3"
        elif message.voice:
            file_obj = message.voice
            original_name = "voice.ogg"
        elif message.document:
            file_obj = message.document
            original_name = message.document.file_name or "document"

        incoming_mb = (file_obj.file_size or 0) / (1024 * 1024)
        if incoming_mb > max_file_mb:
            await status.edit_text(f"❌ الملف كبير جداً.\n\n📦 حجم الملف: {round(incoming_mb, 2)}MB\n🔒 الحد الحالي: {max_file_mb}MB")
            finish_download(download_id, user_id, "failed", 0, "file too large")
            return

        await status.edit_text("📥 جاري تنزيل الملف من تيليجرام\n\n▰▰▱▱▱▱▱▱▱▱▱▱ 15%")
        input_path = workdir / safe_name(original_name)
        output_path = workdir / f"{safe_name(Path(original_name).stem)}.mp3"
        await bot.download(file_obj, destination=input_path)

        await status.edit_text("🎛 جاري تحويل الملف إلى MP3\n\n▰▰▰▰▰▰▱▱▱▱▱▱ 55%")
        await asyncio.to_thread(run_ffmpeg_to_mp3, input_path, output_path)
        if not output_path.exists():
            raise RuntimeError("لم يتم إنشاء ملف MP3")

        output_mb = file_size_mb(output_path)
        if output_mb > max_file_mb:
            await status.edit_text(f"❌ الملف الناتج كبير جداً.\n\n📦 الحجم: {output_mb}MB\n🔒 الحد الحالي: {max_file_mb}MB")
            finish_download(download_id, user_id, "failed", output_mb, "output too large")
            return

        await status.edit_text("📤 جاري إرسال الملف\n\n▰▰▰▰▰▰▰▰▰▰▱▱ 90%")
        await send_final_audio(message, output_path, safe_name(Path(original_name).stem), f"✅ تم التحويل بنجاح\n\n🎵 الصيغة: MP3\n💾 الحجم: {output_mb}MB\n🎮 PlayZone")
        await status.edit_text("✅ اكتمل التحويل\n\n▰▰▰▰▰▰▰▰▰▰▰▰ 100%")
        finish_download(download_id, user_id, "success", output_mb)
        await notify_admin(f"✅ تحويل ملف ناجح\n\n👤 المستخدم: {user_id}\n💾 الحجم: {output_mb}MB")
    except Exception as e:
        logger.exception(e)
        await status.edit_text("❌ حدث خطأ أثناء التحويل. جرّب ملفاً آخر.")
        finish_download(download_id, user_id, "failed", 0, str(e))
    finally:
        ACTIVE_JOBS.pop(user_id, None)
        asyncio.create_task(cleanup_later(workdir))

# =========================================================
# URL Handling
# =========================================================

@dp.message(F.text)
async def handle_text(message: Message):
    if not await guard_message(message):
        return
    user_id = message.from_user.id
    text = message.text.strip()
    if text.startswith("/"):
        return
    if not is_url(text):
        await message.answer("أرسل ملف فيديو/صوت، أو رابط صحيح يبدأ بـ http أو https.", reply_markup=main_menu(user_id))
        return
    ok, reason = can_start_job(user_id)
    if not ok:
        await message.answer(reason)
        return
    allowed, why = is_allowed_url(text)
    if not allowed:
        await message.answer(
            "⚠️ لا يمكن تحميل هذا الرابط مباشرة.\n\n"
            "التحميل المباشر متاح فقط للروابط المسموحة أو روابط الملفات المباشرة.\n\n"
            "✅ الحل الأسهل: أرسل ملف الفيديو أو الصوت هنا، وسأحوله لك إلى MP3 فوراً.",
            reply_markup=main_menu(user_id)
        )
        return

    job_id = str(uuid.uuid4())
    ACTIVE_JOBS[user_id] = job_id
    workdir = DOWNLOADS_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    download_id = add_download(user_id, "url", text)
    status_msg = await message.answer("⏳ جاري تجهيز التحميل\n\n▱▱▱▱▱▱▱▱▱▱▱▱ 0%")

    progress = {"percent": 0.0, "speed": None, "eta": None, "downloaded": None, "total": None, "phase": "starting"}
    stop_monitor = asyncio.Event()

    async def monitor():
        last = ""
        while not stop_monitor.is_set():
            p = progress.get("percent", 0.0)
            if progress.get("phase") == "extracting":
                msg = "🎛 اكتمل التحميل\n\n▰▰▰▰▰▰▰▰▰▰▰▰ 100%\n🎧 جاري استخراج الصوت..."
            else:
                msg = (
                    "⬇️ جاري التحميل\n\n"
                    f"{progress_bar(p)} {p:.1f}%\n"
                    f"📦 الحجم: {format_bytes(progress.get('downloaded'))} / {format_bytes(progress.get('total'))}\n"
                    f"⚡ السرعة: {format_bytes(progress.get('speed'))}/s\n"
                    f"⏳ المتبقي: {format_eta(progress.get('eta'))}"
                )
            if msg != last:
                try:
                    await status_msg.edit_text(msg)
                    last = msg
                except Exception:
                    pass
            await asyncio.sleep(2)

    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = (downloaded / total) * 100 if total else 0.0
            progress.update({"percent": min(percent, 99.0), "speed": d.get("speed"), "eta": d.get("eta"), "downloaded": downloaded, "total": total, "phase": "downloading"})
        elif status == "finished":
            progress.update({"percent": 100.0, "phase": "extracting"})

    monitor_task = asyncio.create_task(monitor())
    try:
        max_file_mb = get_max_file_mb()
        output_template = str(workdir / "%(title).80s.%(ext)s")
        ydl_opts = {
            "outtmpl": output_template,
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "restrictfilenames": True,
            "progress_hooks": [hook],
            "max_filesize": max_file_mb * 1024 * 1024,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
        def download_job():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(text, download=True)
        info = await asyncio.to_thread(download_job)
        title = safe_name(info.get("title") or "audio")
        mp3_files = list(workdir.glob("*.mp3"))
        if not mp3_files:
            raise RuntimeError("لم يتم العثور على ملف MP3 الناتج")
        output_path = mp3_files[0]
        output_mb = file_size_mb(output_path)
        if output_mb > max_file_mb:
            await status_msg.edit_text(f"❌ الملف الناتج كبير جداً.\n\n📦 الحجم: {output_mb}MB\n🔒 الحد الحالي: {max_file_mb}MB")
            finish_download(download_id, user_id, "failed", output_mb, "output too large")
            return
        stop_monitor.set()
        if not monitor_task.done():
            try:
                await monitor_task
            except Exception:
                pass
        await status_msg.edit_text("📤 جاري إرسال الملف\n\n▰▰▰▰▰▰▰▰▰▰▰▱ 95%")
        await send_final_audio(message, output_path, title, f"✅ تم استخراج الصوت بنجاح\n\n🎵 الاسم: {title}\n💾 الحجم: {output_mb}MB\n🎮 PlayZone")
        await status_msg.edit_text("✅ اكتمل التحميل والتحويل\n\n▰▰▰▰▰▰▰▰▰▰▰▰ 100%")
        finish_download(download_id, user_id, "success", output_mb)
        await notify_admin(f"✅ رابط تم تحويله بنجاح\n\n👤 المستخدم: {user_id}\n🌐 المصدر: {get_domain(text)}\n💾 الحجم: {output_mb}MB")
    except yt_dlp.utils.DownloadError as e:
        stop_monitor.set()
        await status_msg.edit_text("❌ فشل تحميل الرابط.\n\nقد يكون الرابط غير متاح، أو الملف أكبر من الحد، أو الموقع غير مدعوم.")
        finish_download(download_id, user_id, "failed", 0, str(e))
    except Exception as e:
        stop_monitor.set()
        logger.exception(e)
        await status_msg.edit_text("❌ حدث خطأ أثناء معالجة الرابط.")
        finish_download(download_id, user_id, "failed", 0, str(e))
    finally:
        stop_monitor.set()
        if not monitor_task.done():
            monitor_task.cancel()
        ACTIVE_JOBS.pop(user_id, None)
        asyncio.create_task(cleanup_later(workdir))

# =========================================================
# Run
# =========================================================

async def main():
    db_init()
    logger.info("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
