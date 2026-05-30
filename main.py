import os
import re
import json
import time
import asyncio
import shutil
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
# الأساس من الكود الأول
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_TELEGRAM_SIZE = 50 * 1024 * 1024
COOKIES_FILE = "cookies.txt"

# ==========================================================
# إعدادات الاشتراك
# ==========================================================

FORCE_SUBSCRIPTION = os.getenv("FORCE_SUBSCRIPTION", "true").lower() == "true"

INSTAGRAM_REQUIRED_URL = os.getenv(
    "INSTAGRAM_REQUIRED_URL",
    "https://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg=="
)

TELEGRAM_REQUIRED_BOT_URL = os.getenv(
    "TELEGRAM_REQUIRED_BOT_URL",
    "https://t.me/P1ay_Z0ne_Bot"
)

# اختياري فقط إذا عندك قناة/مجموعة وتريد تحقق حقيقي
# مثال: TELEGRAM_REQUIRED_CHAT=@YourChannel
TELEGRAM_REQUIRED_CHAT = os.getenv("TELEGRAM_REQUIRED_CHAT", "").strip()

# ==========================================================
# إعدادات الأدمن
# ==========================================================

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()

for x in ADMIN_IDS_RAW.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))

# ==========================================================
# ملفات بيانات بسيطة
# ==========================================================

DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

VERIFIED_FILE = DATA_DIR / "verified_users.json"
STATS_FILE = DATA_DIR / "stats.json"

ACTIVE_USERS = set()

JOB_EXPIRE_SECONDS = 10 * 60
OLD_DOWNLOADS_EXPIRE_SECONDS = 60 * 60
PROGRESS_UPDATE_SECONDS = 3

# ==========================================================
# Logging
# ==========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("PlayZoneSimpleBot")


# ==========================================================
# JSON helpers
# ==========================================================

def load_json(path: Path, default):
    try:
        if not path.exists():
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    except Exception as e:
        logger.warning(f"تعذر حفظ {path}: {e}")


def load_verified():
    data = load_json(VERIFIED_FILE, {})
    return data if isinstance(data, dict) else {}


def is_verified(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True

    return str(user_id) in load_verified()


def set_verified(user_id: int):
    data = load_verified()
    data[str(user_id)] = {
        "verified_at": int(time.time()),
    }
    save_json(VERIFIED_FILE, data)


def reset_verified(user_id: int):
    data = load_verified()
    data.pop(str(user_id), None)
    save_json(VERIFIED_FILE, data)


def load_stats():
    default = {
        "requests": 0,
        "success": 0,
        "failed": 0,
        "audio": 0,
        "video": 0,
        "bytes": 0,
        "last_error": "",
    }
    data = load_json(STATS_FILE, default)
    for k, v in default.items():
        data.setdefault(k, v)
    return data


def save_stats(stats):
    save_json(STATS_FILE, stats)


def stat_inc(key: str, value: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + value
    save_stats(stats)


def set_last_error(text: str):
    stats = load_stats()
    stats["last_error"] = safe_text(text, 700)
    save_stats(stats)


# ==========================================================
# أدوات مساعدة
# ==========================================================

def safe_text(text, limit=3500):
    if not text:
        return ""
    text = str(text)
    return text[:limit] + "..." if len(text) > limit else text


def short_error(e: Exception) -> str:
    msg = str(e)
    msg = re.sub(r"\s+", " ", msg).strip()
    return safe_text(msg, 900)


def safe_title(text: str, limit=80) -> str:
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

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower()
        scheme = parsed.scheme.lower()

        if scheme not in ["http", "https"]:
            return False

        allowed = [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
            "www.youtu.be",
        ]

        return host in allowed or host.endswith(".youtube.com")

    except Exception:
        return False


def has_cookies_file() -> bool:
    path = Path(COOKIES_FILE)
    return path.exists() and path.is_file() and path.stat().st_size > 0


def make_job_dir(user_id: int) -> Path:
    job_dir = BASE_DOWNLOAD_DIR / f"{user_id}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def clean_job_dir(job_dir: Path):
    try:
        if job_dir and job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception as e:
        logger.warning(f"خطأ أثناء تنظيف الملفات: {e}")


def cleanup_old_downloads():
    now = time.time()

    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            if not item.exists():
                continue

            age = now - item.stat().st_mtime
            if age < OLD_DOWNLOADS_EXPIRE_SECONDS:
                continue

            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    except Exception as e:
        logger.warning(f"تعذر تنظيف الملفات القديمة: {e}")


def find_downloaded_file(job_dir: Path):
    try:
        files = [p for p in job_dir.iterdir() if p.is_file()]

        valid = [
            p for p in files
            if not p.name.endswith(".part")
            and not p.name.endswith(".tmp")
            and not p.name.endswith(".ytdl")
        ]

        if not valid:
            return None

        return max(valid, key=lambda p: p.stat().st_mtime)

    except Exception:
        return None


async def safe_edit(message, text: str, reply_markup=None):
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
            logger.warning(f"تعذر تعديل الرسالة: {e}")
    except Exception as e:
        logger.warning(f"تعذر تعديل الرسالة: {e}")


# ==========================================================
# أزرار بسيطة وواضحة
# ==========================================================

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 متابعة الإنستغرام", url=INSTAGRAM_REQUIRED_URL)],
        [InlineKeyboardButton("🤖 متابعة بوت تيليجرام", url=TELEGRAM_REQUIRED_BOT_URL)],
        [InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="sub_check")],
    ])


def choose_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 صوت MP3", callback_data="download_mp3"),
            InlineKeyboardButton("🎬 فيديو MP4", callback_data="download_mp4"),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")],
    ])


def after_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 تحميل رابط آخر", callback_data="new")],
    ])


# ==========================================================
# شرط الاشتراك
# ==========================================================

async def check_telegram_chat_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not TELEGRAM_REQUIRED_CHAT:
        return True, ""

    try:
        member = await context.bot.get_chat_member(TELEGRAM_REQUIRED_CHAT, user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True, ""

        return False, "أنت غير مشترك في قناة/مجموعة تيليجرام المطلوبة."

    except Exception as e:
        return False, f"تعذر التحقق من الاشتراك: {short_error(e)}"


async def send_subscription_message(update: Update):
    text = (
        "🔒 لاستخدام البوت يجب الاشتراك أولاً:\n\n"
        "1️⃣ تابع صفحة الإنستغرام.\n"
        "2️⃣ تابع بوت تيليجرام.\n"
        "3️⃣ اضغط زر التحقق.\n\n"
        "بعد التحقق أرسل رابط يوتيوب مباشرة."
    )

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


async def ensure_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not FORCE_SUBSCRIPTION:
        return True

    user = update.effective_user
    if not user:
        return False

    if is_verified(user.id):
        return True

    await send_subscription_message(update)
    return False


# ==========================================================
# yt-dlp
# ==========================================================

def progress_hook(progress_data: dict):
    def hook(d):
        try:
            status = d.get("status")

            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0

                if total:
                    percent = downloaded / total * 100
                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"📊 {percent:.1f}%\n"
                        f"📦 {format_size(downloaded)} / {format_size(total)}\n"
                        f"⚡ {format_size(speed)}/s"
                    )
                else:
                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"📦 تم تحميل: {format_size(downloaded)}"
                    )

            elif status == "finished":
                progress_data["text"] = "⚙️ تم التحميل، جاري تجهيز الملف..."

        except Exception:
            pass

    return hook


async def progress_updater(status_message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""

    while not stop_event.is_set():
        text = progress_data.get("text", "")

        if text and text != last_text:
            await safe_edit(status_message, text)
            last_text = text

        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)


def build_ydl_options(choice: str, job_dir: Path, progress_data: dict):
    out_tmpl = str(job_dir / "%(title).80s.%(ext)s")

    base_opts = {
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
        "progress_hooks": [progress_hook(progress_data)],
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android"],
                "skip": ["webpage"],
            }
        },
    }

    if has_cookies_file():
        base_opts["cookiefile"] = COOKIES_FILE
    else:
        raise FileNotFoundError(
            "لم يتم العثور على cookies.txt بجانب main.py."
        )

    if choice == "mp3":
        return {
            **base_opts,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

    if choice == "mp4":
        return {
            **base_opts,
            "format": (
                "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/"
                "best[ext=mp4][height<=480]/"
                "best[ext=mp4]/best"
            ),
            "merge_output_format": "mp4",
        }

    raise ValueError("اختيار غير معروف.")


def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = build_ydl_options(choice, job_dir, progress_data)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return info


# ==========================================================
# أوامر البوت
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()

    if not await ensure_subscription(update, context):
        return

    await update.message.reply_text(
        "👋 أهلاً بك في بوت التحميل.\n\n"
        "طريقة الاستخدام بسيطة:\n"
        "1️⃣ أرسل رابط يوتيوب.\n"
        "2️⃣ اختر صوت أو فيديو.\n"
        "3️⃣ انتظر إرسال الملف.\n\n"
        "الأوامر:\n"
        "/help - المساعدة\n"
        "/status - حالة البوت\n"
        "/admin - لوحة الأدمن"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    await update.message.reply_text(
        "📘 المساعدة\n\n"
        "أرسل رابط يوتيوب فقط، وستظهر لك أزرار واضحة:\n\n"
        "🎵 صوت MP3\n"
        "🎬 فيديو MP4\n\n"
        f"حد تيليجرام: {format_size(MAX_TELEGRAM_SIZE)}\n"
        "يجب وجود cookies.txt بجانب main.py."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_subscription(update, context):
        return

    await update.message.reply_text(
        "📊 حالة البوت:\n\n"
        f"🍪 cookies.txt: {'موجود ✅' if has_cookies_file() else 'غير موجود ❌'}\n"
        f"📥 التحميل النشط: {len(ACTIVE_USERS)}\n"
        f"📦 حد الملف: {format_size(MAX_TELEGRAM_SIZE)}"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    stats = load_stats()
    text = (
        "🛠 لوحة الأدمن\n\n"
        f"📩 الطلبات: {stats.get('requests', 0)}\n"
        f"✅ الناجحة: {stats.get('success', 0)}\n"
        f"❌ الفاشلة: {stats.get('failed', 0)}\n"
        f"🎵 الصوت: {stats.get('audio', 0)}\n"
        f"🎬 الفيديو: {stats.get('video', 0)}\n"
        f"📦 الحجم المرسل: {format_size(stats.get('bytes', 0))}\n"
        f"🔒 المستخدمون المتحققون: {len(load_verified())}\n\n"
        "أوامر الأدمن:\n"
        "/clean - تنظيف الملفات\n"
        "/resetverify USER_ID - تصفير اشتراك مستخدم"
    )

    await update.message.reply_text(text)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    cleanup_old_downloads()
    await update.message.reply_text("✅ تم تنظيف الملفات القديمة.")


async def resetverify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم هكذا:\n/resetverify 123456789")
        return

    target = int(context.args[0])
    reset_verified(target)
    await update.message.reply_text(f"✅ تم تصفير تحقق المستخدم: {target}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if not await ensure_subscription(update, context):
        return

    user_id = update.effective_user.id
    url = update.message.text.strip()

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ لديك تحميل يعمل حالياً، انتظر حتى ينتهي.")
        return

    if not is_youtube_url(url):
        await update.message.reply_text(
            "❌ أرسل رابط يوتيوب صحيح فقط.\n\n"
            "مثال:\n"
            "https://youtu.be/xxxx"
        )
        return

    context.user_data["current_url"] = url
    context.user_data["created_at"] = time.time()

    stat_inc("requests", 1)

    await update.message.reply_text(
        "✅ تم استلام الرابط.\n\nاختر الصيغة:",
        reply_markup=choose_format_keyboard(),
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data == "sub_check":
        ok, reason = await check_telegram_chat_membership(user_id, context)

        if not ok:
            await query.edit_message_text(
                f"❌ لم يكتمل الاشتراك.\n\n{reason}",
                reply_markup=subscription_keyboard(),
                disable_web_page_preview=True,
            )
            return

        set_verified(user_id)

        await query.edit_message_text(
            "✅ تم تفعيل البوت بنجاح.\n\n"
            "أرسل رابط يوتيوب الآن."
        )
        return

    if data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("✅ تم إلغاء الطلب.")
        return

    if data == "new":
        context.user_data.clear()
        await query.edit_message_text("📩 أرسل رابط يوتيوب جديد.")
        return

    if data not in ["download_mp3", "download_mp4"]:
        await query.edit_message_text("❌ خيار غير معروف.")
        return

    if not await ensure_subscription(update, context):
        return

    url = context.user_data.get("current_url")
    created_at = context.user_data.get("created_at", 0)

    if not url or time.time() - created_at > JOB_EXPIRE_SECONDS:
        await query.edit_message_text("❌ انتهت صلاحية الطلب. أرسل الرابط مرة أخرى.")
        return

    choice = "mp3" if data == "download_mp3" else "mp4"

    await process_download(update, context, url, choice)


async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, choice: str):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id in ACTIVE_USERS:
        await query.edit_message_text("⏳ لديك تحميل يعمل حالياً، انتظر.")
        return

    ACTIVE_USERS.add(user_id)

    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = query.message

    try:
        job_dir = make_job_dir(user_id)

        await safe_edit(
            status_message,
            "⏳ جاري تجهيز التحميل..."
        )

        progress_data = {
            "text": "🔍 جاري فحص الرابط..."
        }

        updater_task = asyncio.create_task(
            progress_updater(status_message, progress_data, stop_event)
        )

        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.TYPING,
        )

        loop = asyncio.get_running_loop()

        info = await loop.run_in_executor(
            None,
            lambda: download_sync(url, choice, job_dir, progress_data)
        )

        title = "ملف"
        duration = None
        uploader = None

        if isinstance(info, dict):
            title = safe_title(info.get("title", "ملف"))
            duration = info.get("duration")
            uploader = info.get("uploader")

        file_path = find_downloaded_file(job_dir)

        if not file_path or not file_path.exists():
            raise RuntimeError("فشل العثور على الملف بعد التحميل.")

        file_size = file_path.stat().st_size

        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit(
                status_message,
                "❌ حجم الملف أكبر من حد تيليجرام.\n\n"
                f"حجم الملف: {format_size(file_size)}\n"
                f"الحد: {format_size(MAX_TELEGRAM_SIZE)}"
            )
            stat_inc("failed", 1)
            return

        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        await safe_edit(status_message, "📤 جاري إرسال الملف...")

        caption = (
            f"✅ تم التحميل\n"
            f"📌 {title}\n"
            f"📦 {format_size(file_size)}"
        )

        if duration:
            caption += f"\n⏱️ {format_duration(duration)}"

        with open(file_path, "rb") as f:
            if choice == "mp3":
                kwargs = {
                    "audio": f,
                    "title": title,
                    "caption": caption,
                }

                if uploader:
                    kwargs["performer"] = safe_title(uploader, 64)

                if duration:
                    try:
                        kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                await query.message.reply_audio(**kwargs)
                stat_inc("audio", 1)

            else:
                kwargs = {
                    "video": f,
                    "caption": caption,
                    "supports_streaming": True,
                }

                if duration:
                    try:
                        kwargs["duration"] = int(duration)
                    except Exception:
                        pass

                await query.message.reply_video(**kwargs)
                stat_inc("video", 1)

        stat_inc("success", 1)
        stat_inc("bytes", file_size)

        await safe_edit(
            status_message,
            "✅ اكتملت العملية.",
            reply_markup=after_done_keyboard(),
        )

    except FileNotFoundError as e:
        stat_inc("failed", 1)
        set_last_error(short_error(e))
        await safe_edit(status_message, f"⚠️ {short_error(e)}")

    except yt_dlp.utils.DownloadError as e:
        stat_inc("failed", 1)
        set_last_error(short_error(e))
        await safe_edit(
            status_message,
            "❌ فشل التحميل.\n\n"
            f"{short_error(e)}\n\n"
            "جرّب تحديث cookies.txt أو استخدم رابطاً آخر."
        )

    except TimedOut:
        stat_inc("failed", 1)
        set_last_error("Telegram timeout")
        await safe_edit(status_message, "❌ انتهت مهلة تيليجرام، جرّب مرة أخرى.")

    except NetworkError:
        stat_inc("failed", 1)
        set_last_error("Telegram network error")
        await safe_edit(status_message, "❌ مشكلة اتصال، جرّب بعد قليل.")

    except Exception as e:
        logger.exception("خطأ غير متوقع")
        stat_inc("failed", 1)
        set_last_error(short_error(e))
        await safe_edit(
            status_message,
            "❌ حدث خطأ أثناء المعالجة:\n"
            f"{short_error(e)}"
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

        ACTIVE_USERS.discard(user_id)
        context.user_data.pop("current_url", None)
        context.user_data.pop("created_at", None)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ أمر غير معروف. استخدم /help")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error:", exc_info=context.error)


# ==========================================================
# تشغيل البوت
# ==========================================================

def main():
    if not TOKEN:
        raise RuntimeError(
            "لم يتم العثور على TELEGRAM_TOKEN في Railway Variables."
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
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("clean", clean_command))
    app.add_handler(CommandHandler("resetverify", resetverify_command))

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
