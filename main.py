import os
import re
import json
import asyncio
import shutil
import time
import logging
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
# روابط الاشتراك المطلوبة
# ==========================================================

INSTAGRAM_REQUIRED_URL = os.getenv(
    "INSTAGRAM_REQUIRED_URL",
    "https://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg=="
)

TELEGRAM_REQUIRED_BOT_URL = os.getenv(
    "TELEGRAM_REQUIRED_BOT_URL",
    "https://t.me/P1ay_Z0ne_Bot"
)

# ملاحظة:
# إنستغرام لا يمكن التحقق منه من داخل بوت تيليجرام بدون API رسمي.
# رابط بوت تيليجرام آخر لا يمكن للبوت الحالي معرفة هل المستخدم شغله أم لا.
# لذلك يوجد تحقق إلزامي بزر "تحققت من الاشتراك".
# إذا أردت تحقق تيليجرام حقيقي لاحقاً، اجعل الاشتراك قناة/مجموعة وضع معرفها هنا:
# مثال:
# TELEGRAM_REQUIRED_CHAT=@YourChannel
TELEGRAM_REQUIRED_CHAT = os.getenv("TELEGRAM_REQUIRED_CHAT", "").strip()

# تفعيل/تعطيل شرط الاشتراك
FORCE_SUBSCRIPTION = os.getenv("FORCE_SUBSCRIPTION", "true").lower() == "true"

# ==========================================================
# إعدادات احترافية إضافية
# ==========================================================

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

BOT_STARTED_AT = time.time()

# ==========================================================
# Logging
# ==========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ProfessionalDownloaderBot")


# ==========================================================
# تخزين بسيط للإحصائيات والحظر والتحقق
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
        return "غير معروف"

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
    if update.message:
        await update.message.reply_text(
            subscription_text(),
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            subscription_text(),
            reply_markup=subscription_keyboard(),
            disable_web_page_preview=True,
        )


async def handle_subscription_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data == "subscription:why":
        await query.edit_message_text(
            "ℹ️ هذا الشرط يساعد على دعم PlayZone ويضمن أن المستخدم يتابع حسابات المنصة الرسمية.\n\n"
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

    await query.edit_message_text(
        "✅ تم تفعيل استخدام البوت بنجاح.\n\n"
        "أرسل رابط يوتيوب الآن للبدء."
    )


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

    text = (
        "👋 أهلاً بك في بوت التحميل.\n\n"
        "📌 طريقة الاستخدام:\n"
        "أرسل رابط يوتيوب، ثم اختر الصيغة المطلوبة.\n\n"
        "الصيغ المتوفرة:\n"
        "🎵 صوت MP3\n"
        "🎬 فيديو MP4\n\n"
        "الأوامر:\n"
        "/help - شرح الاستخدام\n"
        "/cancel - إلغاء الطلب الحالي\n"
        "/status - حالة البوت\n"
        "/subscribe - روابط الاشتراك"
    )

    if is_admin(update.effective_user.id):
        text += "\n/admin - لوحة الأدمن"

    await update.message.reply_text(text)


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_gate(update)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    text = (
        "📘 شرح الاستخدام:\n\n"
        "1. أرسل رابط يوتيوب.\n"
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

    await update.message.reply_text(text)


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

    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    context.user_data.clear()
    USER_LAST_REQUEST.pop(user_id, None)

    await update.message.reply_text("✅ تم إلغاء الطلب الحالي.")


# ==========================================================
# أوامر الأدمن
# ==========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    await update.message.reply_text(
        "🛠️ لوحة الأدمن\n\nاختر العملية:",
        reply_markup=admin_keyboard(),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    await send_stats(update.message)


async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    await send_active(update.message)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    cleanup_old_downloads()
    await update.message.reply_text("✅ تم تنظيف الملفات القديمة.")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
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
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
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
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
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
# استقبال الرابط
# ==========================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    cleanup_old_downloads()

    user_id = update.effective_user.id
    url = update.message.text.strip()

    if is_banned(user_id):
        await update.message.reply_text("❌ لا يمكنك استخدام البوت حالياً.")
        return

    if not await ensure_subscription(update, context):
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

    if not is_youtube_url(url):
        await update.message.reply_text(
            "❌ هذا لا يبدو كرابط يوتيوب صحيح.\n\n"
            "أرسل رابط مثل:\n"
            "https://youtu.be/xxxx\n"
            "أو:\n"
            "https://www.youtube.com/watch?v=xxxx"
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

    if data == "download:cancel":
        context.user_data.clear()
        USER_LAST_REQUEST.pop(user_id, None)
        await query.edit_message_text("✅ تم إلغاء الطلب.")
        return

    if data == "download:new":
        context.user_data.clear()
        USER_LAST_REQUEST.pop(user_id, None)
        await query.edit_message_text("📩 أرسل رابط يوتيوب جديد للبدء.")
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
        await query.edit_message_text(
            "❌ انتهت صلاحية الطلب.\n"
            "أرسل الرابط مرة أخرى."
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


async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str):
    query = update.callback_query
    user_id = query.from_user.id

    url = await get_current_url_for_user(user_id, context)

    if not url:
        await query.edit_message_text(
            "❌ انتهت صلاحية الطلب.\n"
            "أرسل الرابط مرة أخرى."
        )
        return

    if user_id in ACTIVE_USERS:
        await query.edit_message_text(
            "⏳ لديك عملية تحميل تعمل حالياً.\n"
            "انتظر حتى تنتهي."
        )
        return

    if len(ACTIVE_USERS) >= MAX_ACTIVE_DOWNLOADS and not is_admin(user_id):
        await query.edit_message_text(
            "⚠️ السيرفر مشغول حالياً.\n"
            "جرّب بعد قليل."
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
            "✅ تم التحميل بنجاح",
            f"📌 العنوان: {title}",
            f"📦 الحجم: {format_size(file_size)}",
        ]

        if uploader:
            caption_lines.append(f"👤 القناة: {safe_title(uploader, 60)}")

        if duration:
            caption_lines.append(f"⏱️ المدة: {format_duration(duration)}")

        caption = "\n".join(caption_lines)

        with open(file_path, "rb") as f:
            if choice == "mp3":
                await query.message.reply_audio(
                    audio=f,
                    title=title,
                    caption=caption,
                )
                inc_stat("total_audio", 1)
                update_user_stat(user_id, "audio", 1)

            elif choice == "mp4":
                await query.message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                )
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
        "استخدم /help لعرض الأوامر."
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
