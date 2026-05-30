import os
import re
import json
import asyncio
import shutil
import time
import logging
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================================
# الإعدادات الأساسية - تم الحفاظ عليها
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_TELEGRAM_SIZE = 50 * 1024 * 1024
COOKIES_FILE = "cookies.txt"

# ==========================================================
# روابط الاشتراك المطلوبة - تم الحفاظ عليها
# ==========================================================

INSTAGRAM_REQUIRED_URL = os.getenv(
    "INSTAGRAM_REQUIRED_URL",
    "https://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg=="
)

TELEGRAM_REQUIRED_BOT_URL = os.getenv(
    "TELEGRAM_REQUIRED_BOT_URL",
    "https://t.me/P1ay_Z0ne_Bot"
)

TELEGRAM_REQUIRED_CHAT = os.getenv("TELEGRAM_REQUIRED_CHAT", "").strip()
FORCE_SUBSCRIPTION = os.getenv("FORCE_SUBSCRIPTION", "true").lower() == "true"

# ==========================================================
# إعدادات احترافية إضافية
# ==========================================================

BOT_PUBLIC_USERNAME = os.getenv("BOT_PUBLIC_USERNAME", "").strip().replace("@", "")
PROJECT_NAME = os.getenv("PROJECT_NAME", "بوت تنزيل تحميل أغاني من يوتيوب")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@P1ay_Z0ne_Bot")

DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

STATS_FILE = DATA_DIR / "stats.json"
BANNED_FILE = DATA_DIR / "banned_users.json"
VERIFIED_FILE = DATA_DIR / "verified_users.json"

JOB_EXPIRE_SECONDS = 10 * 60
OLD_FILES_EXPIRE_SECONDS = 60 * 60

MAX_ERROR_LENGTH = 900
MAX_TITLE_LENGTH = 80

PROGRESS_UPDATE_INTERVAL = 2.5

USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "8"))
MAX_ACTIVE_DOWNLOADS = int(os.getenv("MAX_ACTIVE_DOWNLOADS", "2"))

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()

for item in ADMIN_IDS_RAW.split(","):
    item = item.strip()
    if item.isdigit():
        ADMIN_IDS.add(int(item))

ACTIVE_USERS = {}
USER_LAST_REQUEST = {}
USER_LAST_ACTION = {}
USER_MODE = {}

BOT_STARTED_AT = time.time()

# ==========================================================
# Logging
# ==========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("PlayZoneDownloaderBot")


# ==========================================================
# تخزين الإحصائيات والحظر والتحقق
# ==========================================================

DEFAULT_STATS = {
    "total_requests": 0,
    "total_success": 0,
    "total_failed": 0,
    "total_audio": 0,
    "total_video": 0,
    "total_size_sent": 0,
    "total_verified_users": 0,
    "users": {},
    "last_error": "",
}


def load_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"تعذر حفظ الملف {path}: {e}")


def load_stats():
    data = load_json_file(STATS_FILE, DEFAULT_STATS.copy())
    for key, value in DEFAULT_STATS.items():
        data.setdefault(key, value)
    return data


def save_stats(stats):
    save_json_file(STATS_FILE, stats)


def load_banned_users():
    data = load_json_file(BANNED_FILE, [])
    try:
        return set(int(x) for x in data)
    except Exception:
        return set()


def save_banned_users(users):
    save_json_file(BANNED_FILE, sorted(list(users)))


def load_verified_users():
    data = load_json_file(VERIFIED_FILE, {})
    if isinstance(data, dict):
        return data
    return {}


def save_verified_users(data):
    save_json_file(VERIFIED_FILE, data)


def set_user_verified(user_id: int):
    data = load_verified_users()
    uid = str(user_id)

    if uid not in data:
        stats = load_stats()
        stats["total_verified_users"] = stats.get("total_verified_users", 0) + 1
        save_stats(stats)

    data[uid] = {
        "verified_at": int(time.time()),
        "instagram_url": INSTAGRAM_REQUIRED_URL,
        "telegram_url": TELEGRAM_REQUIRED_BOT_URL,
        "telegram_required_chat": TELEGRAM_REQUIRED_CHAT,
    }
    save_verified_users(data)


def is_user_marked_verified(user_id: int) -> bool:
    data = load_verified_users()
    return str(user_id) in data


def remove_user_verified(user_id: int):
    data = load_verified_users()
    data.pop(str(user_id), None)
    save_verified_users(data)


def update_user_stat(user_id: int, key: str, inc: int = 1):
    stats = load_stats()
    uid = str(user_id)

    stats.setdefault("users", {})
    stats["users"].setdefault(uid, {
        "requests": 0,
        "success": 0,
        "failed": 0,
        "audio": 0,
        "video": 0,
        "bytes": 0,
        "last_seen": 0,
    })

    stats["users"][uid][key] = stats["users"][uid].get(key, 0) + inc
    stats["users"][uid]["last_seen"] = int(time.time())

    save_stats(stats)


def get_user_stats(user_id: int) -> dict:
    stats = load_stats()
    return stats.get("users", {}).get(str(user_id), {
        "requests": 0,
        "success": 0,
        "failed": 0,
        "audio": 0,
        "video": 0,
        "bytes": 0,
        "last_seen": 0,
    })


def inc_stat(key: str, inc: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + inc
    save_stats(stats)


def set_last_error(text: str):
    stats = load_stats()
    stats["last_error"] = safe_text(text, 600)
    save_stats(stats)


# ==========================================================
# أدوات عامة
# ==========================================================

def now_ts() -> int:
    return int(time.time())


def safe_text(text: str, limit: int = 3500) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def short_error(e: Exception) -> str:
    msg = str(e)
    msg = re.sub(r"\s+", " ", msg).strip()

    if len(msg) > MAX_ERROR_LENGTH:
        msg = msg[:MAX_ERROR_LENGTH] + "..."

    return msg


def safe_title(text: str, limit: int = 80) -> str:
    if not text:
        return "ملف"

    text = str(text)
    text = re.sub(r"[\\/:*?\"<>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > limit:
        text = text[:limit].strip()

    return text or "ملف"


def format_size(size_bytes) -> str:
    try:
        size_bytes = int(size_bytes)
    except Exception:
        return "غير معروف"

    if size_bytes <= 0:
        return "0 KB"

    kb = size_bytes / 1024
    mb = kb / 1024
    gb = mb / 1024

    if gb >= 1:
        return f"{gb:.2f} GB"

    if mb >= 1:
        return f"{mb:.2f} MB"

    return f"{kb:.2f} KB"


def format_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except Exception:
        return "غير معروف"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def format_uptime() -> str:
    seconds = int(time.time() - BOT_STARTED_AT)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if minutes:
        parts.append(f"{minutes} دقيقة")

    return "، ".join(parts) if parts else "أقل من دقيقة"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_banned(user_id: int) -> bool:
    return user_id in load_banned_users()


def parse_url_host(url: str) -> str:
    try:
        return urlparse(url.strip()).netloc.lower()
    except Exception:
        return ""


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        host = parsed.netloc.lower()

        if scheme not in ["http", "https"]:
            return False

        allowed_hosts = [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
            "www.youtu.be",
        ]

        if host in allowed_hosts:
            return True

        if host.endswith(".youtube.com"):
            return True

        return False

    except Exception:
        return False


def is_instagram_url(url: str) -> bool:
    host = parse_url_host(url)
    return host in ["instagram.com", "www.instagram.com", "m.instagram.com"] or host.endswith(".instagram.com")


def is_snapchat_url(url: str) -> bool:
    host = parse_url_host(url)
    return host in ["snapchat.com", "www.snapchat.com", "story.snapchat.com"] or host.endswith(".snapchat.com")


def has_cookies_file() -> bool:
    path = Path(COOKIES_FILE)
    return path.exists() and path.is_file() and path.stat().st_size > 0


def make_job_dir(user_id: int) -> Path:
    job_dir = BASE_DOWNLOAD_DIR / f"{user_id}_{now_ts()}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def clean_job_dir(job_dir: Path):
    try:
        if job_dir and job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception as e:
        logger.warning(f"خطأ أثناء تنظيف الملفات: {e}")


def cleanup_old_downloads():
    try:
        current_time = time.time()

        for item in BASE_DOWNLOAD_DIR.iterdir():
            if not item.exists():
                continue

            age = current_time - item.stat().st_mtime

            if age >= OLD_FILES_EXPIRE_SECONDS:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)

    except Exception as e:
        logger.warning(f"تعذر تنظيف الملفات القديمة: {e}")


def find_downloaded_file(job_dir: Path):
    try:
        files = [p for p in job_dir.iterdir() if p.is_file()]

        if not files:
            return None

        valid_files = [
            p for p in files
            if not p.name.endswith(".part")
            and not p.name.endswith(".ytdl")
            and not p.name.endswith(".temp")
            and not p.name.endswith(".tmp")
        ]

        if not valid_files:
            return None

        return max(valid_files, key=lambda p: p.stat().st_mtime)

    except Exception as e:
        logger.warning(f"تعذر إيجاد الملف المحمل: {e}")
        return None


def get_best_thumbnail_url(info: dict) -> str:
    try:
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            # اختيار أكبر صورة متاحة غالباً تعطي أفضل نتيجة بعد التصغير
            best = sorted(
                thumbnails,
                key=lambda x: (x.get("width") or 0) * (x.get("height") or 0),
                reverse=True,
            )[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception:
        return ""


def prepare_audio_thumbnail_sync(info: dict, job_dir: Path):
    """
    تجهيز صورة مصغرة للصوت حتى يظهر مشغل تيليجرام بشكل أجمل.
    تيليجرام يفضل JPG صغيرة، لذلك نحاول تنزيل الصورة وتحويلها عبر ffmpeg.
    إذا فشل التحويل يرجع None بدون تعطيل التحميل.
    """
    try:
        url = get_best_thumbnail_url(info)
        if not url:
            return None

        raw_path = job_dir / "raw_thumbnail"
        jpg_path = job_dir / "thumb.jpg"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            data = response.read(5 * 1024 * 1024)

        if not data:
            return None

        raw_path.write_bytes(data)

        # تحويل وتصغير الصورة حتى تناسب Telegram audio thumbnail
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(raw_path),
            "-vf", "scale='min(320,iw)':-2",
            "-frames:v", "1",
            "-q:v", "7",
            str(jpg_path),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if result.returncode != 0 or not jpg_path.exists():
            return None

        # إذا بقيت كبيرة جداً نحاول ضغطها أكثر
        if jpg_path.stat().st_size > 200 * 1024:
            cmd = [
                "ffmpeg",
                "-y",
                "-i", str(raw_path),
                "-vf", "scale='min(240,iw)':-2",
                "-frames:v", "1",
                "-q:v", "12",
                str(jpg_path),
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        if jpg_path.exists() and 0 < jpg_path.stat().st_size <= 300 * 1024:
            return jpg_path

        return None

    except Exception as e:
        logger.warning(f"تعذر تجهيز صورة الصوت: {e}")
        return None


def check_cooldown(user_id: int):
    if is_admin(user_id):
        return 0

    last = USER_LAST_ACTION.get(user_id, 0)
    remaining = USER_COOLDOWN_SECONDS - (time.time() - last)

    if remaining > 0:
        return int(remaining) + 1

    USER_LAST_ACTION[user_id] = time.time()
    return 0


async def safe_edit_message(message, text: str, reply_markup=None):
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except RetryAfter as e:
        await asyncio.sleep(int(e.retry_after) + 1)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"فشل تعديل الرسالة: {e}")
    except Exception as e:
        logger.warning(f"فشل تعديل الرسالة: {e}")


async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass


# ==========================================================
# واجهة شبيهة بالصورة
# ==========================================================

def main_reply_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("🎬 اليوتيوب"), KeyboardButton("🎵 صوت مباشر")],
        [KeyboardButton("📹 فيديو مباشر"), KeyboardButton("📊 إحصائياتي")],
        [KeyboardButton("📸 الانستكرام"), KeyboardButton("👻 سناب جات")],
        [KeyboardButton("➕ أضف البوت لمجموعتك")],
        [KeyboardButton("🔒 الاشتراك"), KeyboardButton("❓ المساعدة")],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="اختر نوع التحميل أو أرسل رابط يوتيوب",
    )


def youtube_section_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🎵 صوت MP3 مباشر", callback_data="mode:youtube_audio"),
            InlineKeyboardButton("🎬 فيديو MP4 مباشر", callback_data="mode:youtube_video"),
        ],
        [
            InlineKeyboardButton("ℹ️ معلومات الرابط", callback_data="download:info"),
            InlineKeyboardButton("❌ إلغاء", callback_data="download:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def add_bot_keyboard() -> InlineKeyboardMarkup:
    if BOT_PUBLIC_USERNAME:
        add_url = f"https://t.me/{BOT_PUBLIC_USERNAME}?startgroup=true"
    else:
        add_url = TELEGRAM_REQUIRED_BOT_URL

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ أضف البوت لمجموعتك", url=add_url)],
        [InlineKeyboardButton("🤖 بوت PlayZone", url=TELEGRAM_REQUIRED_BOT_URL)],
    ])


def welcome_text(first_name: str = "عزيزي") -> str:
    return (
        f"⚠️ أهلاً عذراً {first_name} .\n"
        "🔰 عليك الاشتراك بقناة/حسابات البوت لتتمكن من استخدامه.\n\n"
        "بعد الاشتراك اضغط زر التحقق.\n\n"
        "✅ بعد التفعيل تستطيع تحميل:\n"
        "• من اليوتيوب بجودة عالية.\n"
        "• ملفات صوتية وفيديو MP4.\n"
        "• إرسال رابط الفيديو مباشرة أو اختيار القسم من القائمة."
    )


def youtube_mode_text() -> str:
    return (
        "🎬 قسم اليوتيوب\n\n"
        "اختر طريقة التحميل ثم أرسل الرابط:\n\n"
        "🎵 صوت مباشر: يحمّل الرابط القادم كـ MP3 مباشرة.\n"
        "📹 فيديو مباشر: يحمّل الرابط القادم كـ MP4 مباشرة.\n\n"
        "أو أرسل رابط يوتيوب مباشرة وسيظهر لك اختيار الصيغة.\n\n"
        "✅ مثال:\n"
        "https://youtu.be/VIDEO_ID"
    )


def coming_soon_text(name: str) -> str:
    return (
        f"🚧 قسم {name}\n\n"
        "تمت إضافة القسم إلى الواجهة مثل المثال.\n"
        "حالياً التحميل الأساسي المفعّل هو اليوتيوب.\n\n"
        "أرسل رابط يوتيوب للتحميل أو اختر قسم اليوتيوب."
    )


# ==========================================================
# شرط الاشتراك
# ==========================================================

def subscription_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📸 متابعة صفحة إنستغرام", url=INSTAGRAM_REQUIRED_URL),
        ],
        [
            InlineKeyboardButton("🤖 متابعة بوت تيليجرام", url=TELEGRAM_REQUIRED_BOT_URL),
        ],
        [
            InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="subscription:check"),
        ],
        [
            InlineKeyboardButton("ℹ️ لماذا يظهر هذا الشرط؟", callback_data="subscription:why"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def subscription_text() -> str:
    return (
        "🔒 شرط استخدام البوت\n\n"
        "لاستخدام البوت يجب تنفيذ الخطوتين:\n\n"
        "1️⃣ متابعة صفحة PlayZone على إنستغرام.\n"
        "2️⃣ متابعة/تشغيل بوت PlayZone على تيليجرام.\n\n"
        "بعد تنفيذ الخطوتين اضغط:\n"
        "✅ تحققت من الاشتراك"
    )


async def check_telegram_chat_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    if not TELEGRAM_REQUIRED_CHAT:
        return True, "لا يوجد تحقق قناة/مجموعة مفعّل."

    try:
        member = await context.bot.get_chat_member(TELEGRAM_REQUIRED_CHAT, user_id)
        status = member.status

        if status in ["member", "administrator", "creator"]:
            return True, "مشترك في قناة/مجموعة تيليجرام."

        return False, "غير مشترك في قناة/مجموعة تيليجرام."

    except Exception as e:
        logger.warning(f"تعذر التحقق من عضوية تيليجرام: {e}")
        return False, (
            "تعذر التحقق من عضوية تيليجرام.\n"
            "تأكد أن البوت أدمن داخل القناة/المجموعة أو اترك TELEGRAM_REQUIRED_CHAT فارغاً."
        )


async def ensure_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not FORCE_SUBSCRIPTION:
        return True

    user = update.effective_user
    if not user:
        return False

    user_id = user.id

    if is_admin(user_id):
        return True

    if is_user_marked_verified(user_id):
        if TELEGRAM_REQUIRED_CHAT:
            ok, _ = await check_telegram_chat_membership(user_id, context)
            if not ok:
                remove_user_verified(user_id)
                await send_subscription_gate(update)
                return False
        return True

    await send_subscription_gate(update)
    return False


async def send_subscription_gate(update: Update):
    user = update.effective_user
    name = user.first_name if user and user.first_name else "عزيزي"
    text = welcome_text(name) + "\n\n" + subscription_text()

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )


async def handle_subscription_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    user_id = query.from_user.id
    data = query.data or ""

    if data == "subscription:why":
        await query.edit_message_text(
            "ℹ️ هذا الشرط يساعد على دعم PlayZone ويضمن أن المستخدم يتابع الحسابات الرسمية.\n\n"
            "اضغط على الروابط، ثم ارجع واضغط زر التحقق.",
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data != "subscription:check":
        await query.edit_message_text(
            subscription_text(),
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )
        return

    telegram_ok, reason = await check_telegram_chat_membership(user_id, context)

    if not telegram_ok:
        await query.edit_message_text(
            "❌ لم يكتمل شرط الاشتراك في تيليجرام.\n\n"
            f"{reason}\n\n"
            "اشترك أولاً ثم اضغط التحقق مرة أخرى.",
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )
        return

    set_user_verified(user_id)

    await query.message.reply_text(
        "✅ تم تفعيل استخدام البوت بنجاح.\n\n"
        "اختر من القائمة أو أرسل رابط يوتيوب الآن.",
        reply_markup=main_reply_keyboard(),
    )

    try:
        await query.delete_message()
    except Exception:
        pass


# ==========================================================
# لوحات الأزرار
# ==========================================================

def main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🎵 صوت MP3", callback_data="download:mp3"),
            InlineKeyboardButton("🎬 فيديو MP4", callback_data="download:mp4"),
        ],
        [
            InlineKeyboardButton("ℹ️ معلومات الرابط", callback_data="download:info"),
            InlineKeyboardButton("❌ إلغاء", callback_data="download:cancel"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def after_done_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🔁 تحميل رابط آخر", callback_data="download:new"),
        ],
        [
            InlineKeyboardButton("🎵 تحميل صوت", callback_data="mode:youtube_audio"),
            InlineKeyboardButton("🎬 تحميل فيديو", callback_data="mode:youtube_video"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def admin_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"),
            InlineKeyboardButton("📥 النشط الآن", callback_data="admin:active"),
        ],
        [
            InlineKeyboardButton("🧹 تنظيف الملفات", callback_data="admin:clean"),
            InlineKeyboardButton("🍪 فحص الكوكيز", callback_data="admin:cookies"),
        ],
        [
            InlineKeyboardButton("🔒 حالة الاشتراك", callback_data="admin:subscription"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Progress Hook
# ==========================================================

def create_progress_hook(progress_data: dict):
    def hook(d):
        try:
            status = d.get("status")

            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta")

                if total:
                    percent = downloaded / total * 100
                    bars_count = int(percent // 10)
                    bar = "█" * bars_count + "░" * (10 - bars_count)

                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"[{bar}] {percent:.1f}%\n\n"
                        f"📦 الحجم: {format_size(downloaded)} / {format_size(total)}\n"
                        f"⚡ السرعة: {format_size(speed)}/s\n"
                        f"⏱️ المتبقي: {eta if eta else 'غير معروف'} ثانية"
                    )
                else:
                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"📦 تم تحميل: {format_size(downloaded)}\n"
                        f"⚡ السرعة: {format_size(speed)}/s"
                    )

            elif status == "finished":
                progress_data["text"] = (
                    "✅ انتهى التحميل.\n"
                    "⚙️ جاري تجهيز الملف للرفع..."
                )

            elif status == "error":
                progress_data["text"] = "❌ حدث خطأ أثناء التحميل."

        except Exception:
            pass

    return hook


async def progress_message_updater(status_message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""

    while not stop_event.is_set():
        text = progress_data.get("text", "")

        if text and text != last_text:
            await safe_edit_message(status_message, text)
            last_text = text

        await asyncio.sleep(PROGRESS_UPDATE_INTERVAL)


# ==========================================================
# yt-dlp Logic
# ==========================================================

def build_base_ydl_options(job_dir: Path, progress_data: dict):
    out_tmpl = str(job_dir / f"%(title).{MAX_TITLE_LENGTH}s.%(ext)s")

    base_ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "socket_timeout": 30,
        "windowsfilenames": True,
        "restrictfilenames": False,
        "progress_hooks": [create_progress_hook(progress_data)],
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android"],
                "skip": ["webpage"],
            }
        },
    }

    if has_cookies_file():
        base_ydl_opts["cookiefile"] = COOKIES_FILE
    else:
        raise FileNotFoundError(
            "لم يتم العثور على ملف cookies.txt بجانب ملف البوت."
        )

    return base_ydl_opts


def build_ydl_options(choice: str, job_dir: Path, progress_data: dict):
    base_ydl_opts = build_base_ydl_options(job_dir, progress_data)

    if choice == "mp3":
        ydl_opts = {
            **base_ydl_opts,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

    elif choice == "mp4":
        ydl_opts = {
            **base_ydl_opts,
            "format": (
                "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/"
                "best[ext=mp4][height<=480]/"
                "best[ext=mp4]/best"
            ),
            "merge_output_format": "mp4",
        }

    else:
        raise ValueError("اختيار غير معروف.")

    return ydl_opts


def extract_info_sync(url: str):
    progress_data = {}
    temp_dir = BASE_DOWNLOAD_DIR / f"info_{now_ts()}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        opts = build_base_ydl_options(temp_dir, progress_data)
        opts["skip_download"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return info

    finally:
        clean_job_dir(temp_dir)


def download_file_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    ydl_opts = build_ydl_options(choice, job_dir, progress_data)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return info


# ==========================================================
# أوامر المستخدم
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()

    if not await ensure_subscription(update, context):
        return

    user = update.effective_user
    first_name = user.first_name if user and user.first_name else "عزيزي"

    text = (
        f"مرحباً {first_name} 👋\n\n"
        "✅ البوت جاهز للتحميل من يوتيوب.\n\n"
        "اختر من القائمة:\n"
        "🎵 صوت مباشر: بعدها أرسل الرابط وسيبدأ MP3 فوراً.\n"
        "📹 فيديو مباشر: بعدها أرسل الرابط وسيبدأ MP4 فوراً.\n"
        "🎬 اليوتيوب: يعرض لك كل الخيارات.\n\n"
        "أو أرسل رابط يوتيوب مباشرة."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_reply_keyboard(),
        disable_web_page_preview=True,
    )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_gate(update)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    text = (
        "📘 شرح الاستخدام:\n\n"
        "1. اختر اليوتيوب من القائمة أو أرسل رابط يوتيوب مباشرة.\n"
        "2. اختر 🎵 صوت أو 🎬 فيديو.\n"
        "3. انتظر حتى ينتهي التحميل والرفع.\n\n"
        "⚙️ معلومات مهمة:\n"
        f"• الحد الأعلى لحجم الملف: {format_size(MAX_TELEGRAM_SIZE)}\n"
        "• يجب وجود cookies.txt بجانب ملف البوت.\n"
        "• يتم حذف الملفات المؤقتة تلقائياً بعد الإرسال.\n"
        "• إذا ظهر خطأ حجم الملف، استخدم فيديو أقصر.\n\n"
        "🧭 الأوامر:\n"
        "/start - البداية\n"
        "/status - حالة البوت\n"
        "/cancel - إلغاء الطلب الحالي\n"
        "/subscribe - روابط الاشتراك"
    )

    await update.message.reply_text(text, reply_markup=main_reply_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    cookies_status = "موجود ✅" if has_cookies_file() else "غير موجود ❌"
    active_count = len(ACTIVE_USERS)
    verified_status = "مفعّل ✅" if is_user_marked_verified(update.effective_user.id) else "غير مفعّل ❌"

    text = (
        "📊 حالة البوت:\n\n"
        f"🔒 تحقق الاشتراك: {verified_status}\n"
        f"🍪 ملف cookies.txt: {cookies_status}\n"
        f"📥 عمليات التحميل النشطة: {active_count}/{MAX_ACTIVE_DOWNLOADS}\n"
        f"📁 مجلد التحميل: {BASE_DOWNLOAD_DIR}\n"
        f"📦 حد تيليجرام: {format_size(MAX_TELEGRAM_SIZE)}\n"
        f"⏱️ مدة التشغيل: {format_uptime()}"
    )

    await update.message.reply_text(text, reply_markup=main_reply_keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    context.user_data.clear()
    USER_LAST_REQUEST.pop(user_id, None)
    USER_MODE.pop(user_id, None)

    await update.message.reply_text("✅ تم إلغاء الطلب الحالي.", reply_markup=main_reply_keyboard())


async def my_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    await send_my_stats(update.message, update.effective_user.id)


async def send_my_stats(message, user_id: int):
    st = get_user_stats(user_id)

    text = (
        "📊 إحصائياتي\n\n"
        f"📩 طلباتي: {st.get('requests', 0)}\n"
        f"✅ الناجحة: {st.get('success', 0)}\n"
        f"❌ الفاشلة: {st.get('failed', 0)}\n"
        f"🎵 الصوت: {st.get('audio', 0)}\n"
        f"🎬 الفيديو: {st.get('video', 0)}\n"
        f"📦 الحجم المستلم: {format_size(st.get('bytes', 0))}"
    )

    await message.reply_text(text, reply_markup=main_reply_keyboard())


# ==========================================================
# أوامر الأدمن
# ==========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    await update.message.reply_text(
        "🛠️ لوحة الأدمن\n\nاختر العملية:",
        reply_markup=admin_keyboard(),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    await send_stats(update.message)


async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    await send_active(update.message)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    cleanup_old_downloads()
    await update.message.reply_text("✅ تم تنظيف الملفات القديمة.", reply_markup=main_reply_keyboard())


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم الأمر هكذا:\n/ban 123456789")
        return

    target_id = int(context.args[0])
    banned = load_banned_users()
    banned.add(target_id)
    save_banned_users(banned)

    await update.message.reply_text(f"✅ تم حظر المستخدم: {target_id}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم الأمر هكذا:\n/unban 123456789")
        return

    target_id = int(context.args[0])
    banned = load_banned_users()
    banned.discard(target_id)
    save_banned_users(banned)

    await update.message.reply_text(f"✅ تم فك الحظر عن المستخدم: {target_id}")


async def reset_verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.", reply_markup=main_reply_keyboard())
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم الأمر هكذا:\n/resetverify 123456789")
        return

    target_id = int(context.args[0])
    remove_user_verified(target_id)

    await update.message.reply_text(f"✅ تم تصفير تحقق الاشتراك للمستخدم: {target_id}")


async def send_stats(message):
    stats = load_stats()
    users_count = len(stats.get("users", {}))
    verified_count = len(load_verified_users())

    text = (
        "📊 إحصائيات البوت:\n\n"
        f"📩 إجمالي الطلبات: {stats.get('total_requests', 0)}\n"
        f"✅ الناجحة: {stats.get('total_success', 0)}\n"
        f"❌ الفاشلة: {stats.get('total_failed', 0)}\n"
        f"🎵 الصوت: {stats.get('total_audio', 0)}\n"
        f"🎬 الفيديو: {stats.get('total_video', 0)}\n"
        f"📦 الحجم المرسل: {format_size(stats.get('total_size_sent', 0))}\n"
        f"👥 عدد المستخدمين: {users_count}\n"
        f"🔒 المستخدمون المتحققون: {verified_count}\n"
        f"⏱️ مدة التشغيل: {format_uptime()}\n\n"
        f"آخر خطأ:\n{stats.get('last_error') or 'لا يوجد'}"
    )

    await message.reply_text(text)


async def send_active(message):
    if not ACTIVE_USERS:
        await message.reply_text("✅ لا توجد عمليات تحميل نشطة حالياً.")
        return

    lines = ["📥 العمليات النشطة الآن:\n"]

    for uid, data in ACTIVE_USERS.items():
        started = data.get("started_at", time.time())
        choice = data.get("choice", "غير معروف")
        age = int(time.time() - started)

        lines.append(
            f"• المستخدم: {uid}\n"
            f"  الصيغة: {choice}\n"
            f"  منذ: {age} ثانية"
        )

    await message.reply_text("\n".join(lines))


# ==========================================================
# استقبال الرسائل والقائمة
# ==========================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    cleanup_old_downloads()

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if is_banned(user_id):
        await update.message.reply_text("❌ لا يمكنك استخدام البوت حالياً.")
        return

    # أوامر القائمة الظاهرة مثل الصورة
    if text in ["🔒 الاشتراك", "الاشتراك"]:
        await send_subscription_gate(update)
        return

    if text in ["❓ المساعدة", "المساعدة"]:
        await help_command(update, context)
        return

    if not await ensure_subscription(update, context):
        return

    if text in ["اليوتيوب", "🎬 اليوتيوب"]:
        USER_MODE[user_id] = "youtube"
        await update.message.reply_text(
            youtube_mode_text(),
            reply_markup=youtube_section_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if text in ["🎵 صوت مباشر", "صوت مباشر"]:
        USER_MODE[user_id] = "youtube_audio"
        await update.message.reply_text(
            "🎵 وضع الصوت المباشر مفعّل.\n\nأرسل رابط يوتيوب الآن وسيبدأ تحميل MP3 مباشرة.",
            reply_markup=main_reply_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if text in ["📹 فيديو مباشر", "فيديو مباشر"]:
        USER_MODE[user_id] = "youtube_video"
        await update.message.reply_text(
            "📹 وضع الفيديو المباشر مفعّل.\n\nأرسل رابط يوتيوب الآن وسيبدأ تحميل MP4 مباشرة.",
            reply_markup=main_reply_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if text in ["الانستكرام", "📸 الانستكرام"]:
        USER_MODE[user_id] = "instagram"
        await update.message.reply_text(
            coming_soon_text("الانستكرام"),
            reply_markup=main_reply_keyboard(),
        )
        return

    if text in ["سناب جات", "👻 سناب جات"]:
        USER_MODE[user_id] = "snapchat"
        await update.message.reply_text(
            coming_soon_text("سناب جات"),
            reply_markup=main_reply_keyboard(),
        )
        return

    if text == "📊 إحصائياتي":
        await send_my_stats(update.message, user_id)
        return

    if text == "➕ أضف البوت لمجموعتك":
        await update.message.reply_text(
            "➕ أضف البوت لمجموعتك من الزر التالي:",
            reply_markup=add_bot_keyboard(),
            disable_web_page_preview=True,
        )
        return

    cooldown = check_cooldown(user_id)
    if cooldown > 0:
        await update.message.reply_text(f"⏳ انتظر {cooldown} ثانية ثم أرسل مرة أخرى.")
        return

    if user_id in ACTIVE_USERS:
        await update.message.reply_text(
            "⏳ لديك عملية تحميل تعمل حالياً.\n"
            "انتظر حتى تنتهي، أو استخدم /cancel."
        )
        return

    if len(ACTIVE_USERS) >= MAX_ACTIVE_DOWNLOADS and not is_admin(user_id):
        await update.message.reply_text(
            "⚠️ السيرفر مشغول حالياً.\n"
            "جرّب بعد قليل."
        )
        return

    url = text

    if not is_youtube_url(url):
        # دعم كتابة اسم الفيديو: نرسل رابط بحث يوتيوب بدلاً من التخمين
        if "http://" not in text and "https://" not in text:
            search_url = f"https://www.youtube.com/results?search_query={quote_plus(text)}"
            await update.message.reply_text(
                "🔎 يبدو أنك أرسلت اسم فيديو وليس رابطاً.\n\n"
                "افتح بحث يوتيوب من الزر، ثم انسخ رابط الفيديو وأرسله للبوت.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔎 بحث في يوتيوب", url=search_url)],
                    [InlineKeyboardButton("🎬 قسم اليوتيوب", callback_data="mode:youtube")]
                ]),
                disable_web_page_preview=True,
            )
            return

        await update.message.reply_text(
            "❌ هذا لا يبدو كرابط يوتيوب صحيح.\n\n"
            "أرسل رابط مثل:\n"
            "https://youtu.be/xxxx\n"
            "أو:\n"
            "https://www.youtube.com/watch?v=xxxx",
            reply_markup=main_reply_keyboard(),
        )
        return

    context.user_data["current_url"] = url
    context.user_data["created_at"] = time.time()

    USER_LAST_REQUEST[user_id] = {
        "url": url,
        "created_at": time.time(),
    }

    inc_stat("total_requests", 1)
    update_user_stat(user_id, "requests", 1)

    mode = USER_MODE.get(user_id)

    if mode == "youtube_audio":
        await update.message.reply_text("✅ تم استلام الرابط.\n🎵 سيتم تحميله كصوت MP3 مباشرة.")
        await process_download_from_message(update, context, "mp3", url)
        return

    if mode == "youtube_video":
        await update.message.reply_text("✅ تم استلام الرابط.\n🎬 سيتم تحميله كفيديو MP4 مباشرة.")
        await process_download_from_message(update, context, "mp4", url)
        return

    await update.message.reply_text(
        "✅ تم استلام الرابط.\n\n"
        "اختر الصيغة المطلوبة:",
        reply_markup=main_keyboard(),
    )


# ==========================================================
# ضغط الأزرار
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data.startswith("subscription:"):
        await handle_subscription_button(update, context)
        return

    if data.startswith("admin:"):
        await handle_admin_button(update, context)
        return

    if is_banned(user_id):
        await query.edit_message_text("❌ لا يمكنك استخدام البوت حالياً.")
        return

    if not await ensure_subscription(update, context):
        return

    if data == "mode:youtube":
        USER_MODE[user_id] = "youtube"
        await query.message.reply_text(
            youtube_mode_text(),
            reply_markup=main_reply_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "mode:youtube_audio":
        USER_MODE[user_id] = "youtube_audio"
        url = await get_current_url_for_user(user_id, context)
        if url:
            await process_download(update, context, "mp3")
        else:
            await query.message.reply_text("🎵 أرسل رابط يوتيوب لتحميله كصوت MP3.", reply_markup=main_reply_keyboard())
        return

    if data == "mode:youtube_video":
        USER_MODE[user_id] = "youtube_video"
        url = await get_current_url_for_user(user_id, context)
        if url:
            await process_download(update, context, "mp4")
        else:
            await query.message.reply_text("🎬 أرسل رابط يوتيوب لتحميله كفيديو MP4.", reply_markup=main_reply_keyboard())
        return

    if data == "download:cancel":
        context.user_data.clear()
        USER_LAST_REQUEST.pop(user_id, None)
        USER_MODE.pop(user_id, None)
        await query.edit_message_text("✅ تم إلغاء الطلب.")
        return

    if data == "download:new":
        context.user_data.clear()
        USER_LAST_REQUEST.pop(user_id, None)
        USER_MODE.pop(user_id, None)
        await query.message.reply_text("📩 أرسل رابط يوتيوب جديد للبدء.", reply_markup=main_reply_keyboard())
        try:
            await query.delete_message()
        except Exception:
            pass
        return

    if data == "download:info":
        await handle_link_info(update, context)
        return

    if not data.startswith("download:"):
        await query.edit_message_text("❌ خيار غير معروف.")
        return

    choice = data.split(":", 1)[1]

    await process_download(update, context, choice)


async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data or ""

    if not is_admin(user_id):
        await query.edit_message_text("❌ هذا الخيار للأدمن فقط.")
        return

    if data == "admin:stats":
        await query.edit_message_text("📊 يتم تجهيز الإحصائيات...")
        await send_stats(query.message)
        return

    if data == "admin:active":
        await query.edit_message_text("📥 يتم فحص العمليات النشطة...")
        await send_active(query.message)
        return

    if data == "admin:clean":
        cleanup_old_downloads()
        await query.edit_message_text("✅ تم تنظيف الملفات القديمة.")
        return

    if data == "admin:cookies":
        status = "موجود ✅" if has_cookies_file() else "غير موجود ❌"
        await query.edit_message_text(f"🍪 حالة cookies.txt: {status}")
        return

    if data == "admin:subscription":
        verified_count = len(load_verified_users())
        text = (
            "🔒 حالة شرط الاشتراك:\n\n"
            f"مفعل: {'نعم ✅' if FORCE_SUBSCRIPTION else 'لا ❌'}\n"
            f"إنستغرام:\n{INSTAGRAM_REQUIRED_URL}\n\n"
            f"بوت تيليجرام:\n{TELEGRAM_REQUIRED_BOT_URL}\n\n"
            f"تحقق قناة/مجموعة تيليجرام:\n{TELEGRAM_REQUIRED_CHAT or 'غير مفعّل'}\n\n"
            f"عدد المستخدمين المتحققين: {verified_count}"
        )
        await query.edit_message_text(text, disable_web_page_preview=True)
        return

    await query.edit_message_text("❌ خيار أدمن غير معروف.")


async def get_current_url_for_user(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    url = context.user_data.get("current_url")

    if url:
        return url

    saved = USER_LAST_REQUEST.get(user_id)
    if saved:
        age = time.time() - saved.get("created_at", 0)
        if age <= JOB_EXPIRE_SECONDS:
            return saved.get("url")

    return None


async def handle_link_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    url = await get_current_url_for_user(user_id, context)

    if not url:
        await query.message.reply_text(
            "❌ انتهت صلاحية الطلب.\n"
            "أرسل الرابط مرة أخرى.",
            reply_markup=main_reply_keyboard(),
        )
        return

    await safe_edit_message(query.message, "🔍 جاري جلب معلومات الرابط...")

    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_info_sync(url))

        title = safe_title(info.get("title", "ملف"), 100)
        uploader = safe_title(info.get("uploader", "غير معروف"), 80)
        duration = format_duration(info.get("duration"))
        view_count = info.get("view_count")

        text = (
            "ℹ️ معلومات الرابط:\n\n"
            f"📌 العنوان: {title}\n"
            f"👤 القناة: {uploader}\n"
            f"⏱️ المدة: {duration}\n"
        )

        if view_count:
            text += f"👁️ المشاهدات: {view_count}\n"

        text += "\nاختر الصيغة المطلوبة:"
        await safe_edit_message(query.message, text, reply_markup=main_keyboard())

    except Exception as e:
        await safe_edit_message(
            query.message,
            "❌ تعذر جلب معلومات الرابط.\n\n"
            f"التفاصيل:\n{short_error(e)}",
            reply_markup=main_keyboard(),
        )



async def process_download_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str, url: str):
    """
    تحميل مباشر عند اختيار المستخدم وضع صوت/فيديو ثم إرسال الرابط.
    هذا يجعل الاستخدام أسهل من الضغط على أزرار كثيرة.
    """
    user_id = update.effective_user.id
    message = update.message

    if not message:
        return

    if user_id in ACTIVE_USERS:
        await message.reply_text(
            "⏳ لديك عملية تحميل تعمل حالياً.\nانتظر حتى تنتهي.",
            reply_markup=main_reply_keyboard(),
        )
        return

    if len(ACTIVE_USERS) >= MAX_ACTIVE_DOWNLOADS and not is_admin(user_id):
        await message.reply_text(
            "⚠️ السيرفر مشغول حالياً.\nجرّب بعد قليل.",
            reply_markup=main_reply_keyboard(),
        )
        return

    ACTIVE_USERS[user_id] = {
        "started_at": time.time(),
        "choice": choice,
    }

    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = None

    try:
        job_dir = make_job_dir(user_id)

        status_message = await message.reply_text(
            "⏳ جاري تجهيز الطلب...\n🔍 يتم فحص الرابط والكوكيز الآن."
        )

        if not has_cookies_file():
            await safe_edit_message(
                status_message,
                "⚠️ لم يتم العثور على ملف cookies.txt.\n\n"
                "ارفع الملف بجانب ملف البوت، ثم أعد تشغيل السيرفر.\n\n"
                "مثال ترتيب الملفات:\n"
                "main.py\nrequirements.txt\nnixpacks.toml\ncookies.txt"
            )
            return

        progress_data = {
            "text": "🔍 جاري فحص الرابط وتجهيز التحميل..."
        }

        updater_task = asyncio.create_task(
            progress_message_updater(status_message, progress_data, stop_event)
        )

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.TYPING,
        )

        loop = asyncio.get_running_loop()

        info = await loop.run_in_executor(
            None,
            lambda: download_file_sync(url, choice, job_dir, progress_data)
        )

        title = "ملف"
        duration = None
        uploader = None

        if isinstance(info, dict):
            title = safe_title(info.get("title", "ملف"), 80)
            duration = info.get("duration")
            uploader = info.get("uploader")

        file_path = find_downloaded_file(job_dir)

        if not file_path or not file_path.exists():
            raise RuntimeError("فشل العثور على الملف بعد التحميل.")

        file_size = file_path.stat().st_size

        if file_size <= 0:
            raise RuntimeError("الملف الناتج فارغ أو غير صالح.")

        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit_message(
                status_message,
                "❌ حجم الملف أكبر من حد تيليجرام للبوتات العادية.\n\n"
                f"📦 حجم الملف: {format_size(file_size)}\n"
                f"📌 الحد المسموح: {format_size(MAX_TELEGRAM_SIZE)}\n\n"
                "جرّب فيديو أقصر أو جودة أقل."
            )
            inc_stat("total_failed", 1)
            update_user_stat(user_id, "failed", 1)
            return

        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        await safe_edit_message(status_message, "📤 جاري رفع الملف إلى تيليجرام...")

        caption_lines = [
            f"✅ {PROJECT_NAME}",
            "",
            f"📌 {title}",
            f"💾 {format_size(file_size)}",
        ]

        if duration:
            caption_lines.append(f"⏱️ {format_duration(duration)}")

        if uploader:
            caption_lines.append(f"👤 {safe_title(uploader, 60)}")

        caption = "\n".join(caption_lines)

        thumb_path = None
        if choice == "mp3" and isinstance(info, dict):
            await safe_edit_message(status_message, "🖼️ جاري تجهيز صورة وبيانات مشغل الصوت...")
            thumb_path = await loop.run_in_executor(
                None,
                lambda: prepare_audio_thumbnail_sync(info, job_dir)
            )

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )

        with open(file_path, "rb") as f:
            if choice == "mp3":
                audio_kwargs = {
                    "audio": f,
                    "title": title,
                    "caption": caption,
                }

                if uploader:
                    audio_kwargs["performer"] = safe_title(uploader, 64)

                if duration:
                    try:
                        audio_kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                thumb_file = None
                try:
                    if thumb_path and thumb_path.exists():
                        thumb_file = open(thumb_path, "rb")
                        audio_kwargs["thumbnail"] = thumb_file

                    await message.reply_audio(**audio_kwargs)

                finally:
                    if thumb_file:
                        thumb_file.close()

                inc_stat("total_audio", 1)
                update_user_stat(user_id, "audio", 1)

            elif choice == "mp4":
                video_kwargs = {
                    "video": f,
                    "caption": caption,
                    "supports_streaming": True,
                }

                if duration:
                    try:
                        video_kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                await message.reply_video(**video_kwargs)

                inc_stat("total_video", 1)
                update_user_stat(user_id, "video", 1)

            else:
                await message.reply_document(document=f, caption=caption)

        inc_stat("total_success", 1)
        inc_stat("total_size_sent", file_size)
        update_user_stat(user_id, "success", 1)
        update_user_stat(user_id, "bytes", file_size)

        await safe_edit_message(
            status_message,
            "✅ اكتملت العملية وتم إرسال الملف.",
            reply_markup=after_done_keyboard(),
        )

        await message.reply_text("القائمة الرئيسية:", reply_markup=main_reply_keyboard())

    except FileNotFoundError as e:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))
        if status_message:
            await safe_edit_message(status_message, f"⚠️ {short_error(e)}")
        else:
            await message.reply_text(f"⚠️ {short_error(e)}", reply_markup=main_reply_keyboard())

    except yt_dlp.utils.DownloadError as e:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))
        await safe_edit_message(
            status_message,
            "❌ فشل التحميل من المصدر.\n\n"
            f"السبب:\n{short_error(e)}\n\n"
            "💡 جرّب تحديث cookies.txt أو استخدم رابطاً آخر."
        )

    except TimedOut:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error("Telegram TimedOut")
        await safe_edit_message(status_message, "❌ انتهت مهلة الاتصال مع تيليجرام.\nجرّب مرة أخرى.")

    except NetworkError:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error("Telegram NetworkError")
        await safe_edit_message(status_message, "❌ حدثت مشكلة في الاتصال بالشبكة.\nجرّب مرة أخرى بعد قليل.")

    except Exception as e:
        logger.exception("حدث خطأ غير متوقع")
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))

        if status_message:
            await safe_edit_message(
                status_message,
                "❌ حدث خطأ أثناء المعالجة.\n\n"
                f"التفاصيل:\n{short_error(e)}"
            )
        else:
            await message.reply_text(
                "❌ حدث خطأ أثناء المعالجة.\n\n"
                f"التفاصيل:\n{short_error(e)}",
                reply_markup=main_reply_keyboard(),
            )

    finally:
        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        if job_dir:
            clean_job_dir(job_dir)

        ACTIVE_USERS.pop(user_id, None)
        context.user_data.pop("current_url", None)
        context.user_data.pop("created_at", None)

async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str):
    query = update.callback_query
    user_id = query.from_user.id

    url = await get_current_url_for_user(user_id, context)

    if not url:
        await query.message.reply_text(
            "❌ انتهت صلاحية الطلب.\n"
            "أرسل الرابط مرة أخرى.",
            reply_markup=main_reply_keyboard(),
        )
        return

    if user_id in ACTIVE_USERS:
        await query.message.reply_text(
            "⏳ لديك عملية تحميل تعمل حالياً.\n"
            "انتظر حتى تنتهي.",
            reply_markup=main_reply_keyboard(),
        )
        return

    if len(ACTIVE_USERS) >= MAX_ACTIVE_DOWNLOADS and not is_admin(user_id):
        await query.message.reply_text(
            "⚠️ السيرفر مشغول حالياً.\n"
            "جرّب بعد قليل.",
            reply_markup=main_reply_keyboard(),
        )
        return

    ACTIVE_USERS[user_id] = {
        "started_at": time.time(),
        "choice": choice,
    }

    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = query.message

    try:
        job_dir = make_job_dir(user_id)

        await safe_edit_message(
            status_message,
            "⏳ جاري تجهيز الطلب...\n"
            "🔍 يتم فحص الرابط والكوكيز الآن."
        )

        if not has_cookies_file():
            await safe_edit_message(
                status_message,
                "⚠️ لم يتم العثور على ملف cookies.txt.\n\n"
                "ارفع الملف بجانب ملف البوت، ثم أعد تشغيل السيرفر.\n\n"
                "مثال ترتيب الملفات:\n"
                "main.py\n"
                "requirements.txt\n"
                "nixpacks.toml\n"
                "cookies.txt"
            )
            return

        progress_data = {
            "text": "🔍 جاري فحص الرابط وتجهيز التحميل..."
        }

        updater_task = asyncio.create_task(
            progress_message_updater(status_message, progress_data, stop_event)
        )

        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.TYPING,
        )

        loop = asyncio.get_running_loop()

        info = await loop.run_in_executor(
            None,
            lambda: download_file_sync(url, choice, job_dir, progress_data)
        )

        title = "ملف"
        duration = None
        uploader = None

        if isinstance(info, dict):
            title = safe_title(info.get("title", "ملف"), 80)
            duration = info.get("duration")
            uploader = info.get("uploader")

        file_path = find_downloaded_file(job_dir)

        if not file_path or not file_path.exists():
            raise RuntimeError("فشل العثور على الملف بعد التحميل.")

        file_size = file_path.stat().st_size

        if file_size <= 0:
            raise RuntimeError("الملف الناتج فارغ أو غير صالح.")

        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit_message(
                status_message,
                "❌ حجم الملف أكبر من حد تيليجرام للبوتات العادية.\n\n"
                f"📦 حجم الملف: {format_size(file_size)}\n"
                f"📌 الحد المسموح: {format_size(MAX_TELEGRAM_SIZE)}\n\n"
                "جرّب فيديو أقصر أو جودة أقل."
            )
            inc_stat("total_failed", 1)
            update_user_stat(user_id, "failed", 1)
            return

        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        await safe_edit_message(
            status_message,
            "📤 جاري رفع الملف إلى تيليجرام..."
        )

        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )

        caption_lines = [
            f"✅ {PROJECT_NAME}",
            "",
            f"📌 {title}",
            f"💾 {format_size(file_size)}",
        ]

        if duration:
            caption_lines.append(f"⏱️ {format_duration(duration)}")

        if uploader:
            caption_lines.append(f"👤 {safe_title(uploader, 60)}")

        caption = "\n".join(caption_lines)

        thumb_path = None
        if choice == "mp3" and isinstance(info, dict):
            await safe_edit_message(
                status_message,
                "🖼️ جاري تجهيز صورة وبيانات مشغل الصوت..."
            )
            thumb_path = await loop.run_in_executor(
                None,
                lambda: prepare_audio_thumbnail_sync(info, job_dir)
            )

        with open(file_path, "rb") as f:
            if choice == "mp3":
                audio_kwargs = {
                    "audio": f,
                    "title": title,
                    "caption": caption,
                }

                if uploader:
                    audio_kwargs["performer"] = safe_title(uploader, 64)

                if duration:
                    try:
                        audio_kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                thumb_file = None
                try:
                    if thumb_path and thumb_path.exists():
                        thumb_file = open(thumb_path, "rb")
                        audio_kwargs["thumbnail"] = thumb_file

                    await query.message.reply_audio(**audio_kwargs)

                finally:
                    if thumb_file:
                        thumb_file.close()

                inc_stat("total_audio", 1)
                update_user_stat(user_id, "audio", 1)

            elif choice == "mp4":
                video_kwargs = {
                    "video": f,
                    "caption": caption,
                    "supports_streaming": True,
                }

                if duration:
                    try:
                        video_kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                await query.message.reply_video(**video_kwargs)

                inc_stat("total_video", 1)
                update_user_stat(user_id, "video", 1)

            else:
                await query.message.reply_document(
                    document=f,
                    caption=caption,
                )

        inc_stat("total_success", 1)
        inc_stat("total_size_sent", file_size)
        update_user_stat(user_id, "success", 1)
        update_user_stat(user_id, "bytes", file_size)

        await safe_edit_message(
            status_message,
            "✅ اكتملت العملية وتم إرسال الملف.",
            reply_markup=after_done_keyboard(),
        )

        try:
            await query.message.reply_text("القائمة الرئيسية:", reply_markup=main_reply_keyboard())
        except Exception:
            pass

    except FileNotFoundError as e:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))

        await safe_edit_message(
            status_message,
            f"⚠️ {short_error(e)}"
        )

    except yt_dlp.utils.DownloadError as e:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))

        await safe_edit_message(
            status_message,
            "❌ فشل التحميل من المصدر.\n\n"
            f"السبب:\n{short_error(e)}\n\n"
            "💡 جرّب تحديث cookies.txt أو استخدم رابطاً آخر."
        )

    except TimedOut:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error("Telegram TimedOut")

        await safe_edit_message(
            status_message,
            "❌ انتهت مهلة الاتصال مع تيليجرام.\n"
            "جرّب مرة أخرى."
        )

    except NetworkError:
        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error("Telegram NetworkError")

        await safe_edit_message(
            status_message,
            "❌ حدثت مشكلة في الاتصال بالشبكة.\n"
            "جرّب مرة أخرى بعد قليل."
        )

    except Exception as e:
        logger.exception("حدث خطأ غير متوقع")

        inc_stat("total_failed", 1)
        update_user_stat(user_id, "failed", 1)
        set_last_error(short_error(e))

        await safe_edit_message(
            status_message,
            "❌ حدث خطأ أثناء المعالجة.\n\n"
            f"التفاصيل:\n{short_error(e)}"
        )

    finally:
        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        if job_dir:
            clean_job_dir(job_dir)

        ACTIVE_USERS.pop(user_id, None)

        context.user_data.pop("current_url", None)
        context.user_data.pop("created_at", None)


# ==========================================================
# أوامر غير معروفة + أخطاء عامة
# ==========================================================

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ أمر غير معروف.\n"
        "استخدم /help لعرض الأوامر.",
        reply_markup=main_reply_keyboard(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error:", exc_info=context.error)


# ==========================================================
# تشغيل البوت
# ==========================================================

def main():
    if not TOKEN:
        raise RuntimeError(
            "لم يتم العثور على TELEGRAM_TOKEN.\n"
            "ضع التوكن في Railway Variables باسم TELEGRAM_TOKEN."
        )

    cleanup_old_downloads()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("mystats", my_stats_command))

    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("active", active_command))
    app.add_handler(CommandHandler("clean", clean_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("resetverify", reset_verify_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.add_error_handler(error_handler)

    logger.info("✅ البوت يعمل الآن...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
