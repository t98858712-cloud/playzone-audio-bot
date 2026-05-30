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
# إعدادات أساسية
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
STATS_FILE = DATA_DIR / "stats.json"

MAX_TELEGRAM_SIZE = 50 * 1024 * 1024
COOKIES_FILE = "cookies.txt"

REQUEST_EXPIRE_SECONDS = 15 * 60
OLD_DOWNLOADS_EXPIRE_SECONDS = 60 * 60
PROGRESS_UPDATE_SECONDS = 3

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()
for item in ADMIN_IDS_RAW.split(","):
    item = item.strip()
    if item.isdigit():
        ADMIN_IDS.add(int(item))

ACTIVE_USERS = set()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PlayZoneBot")


# ==========================================================
# حفظ بسيط للبيانات
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
        logger.warning(f"تعذر حفظ البيانات: {e}")


def register_user(user):
    if not user:
        return

    data = load_json(USERS_FILE, {})
    data[str(user.id)] = {
        "id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
        "last_seen": int(time.time()),
    }
    save_json(USERS_FILE, data)


def all_user_ids():
    data = load_json(USERS_FILE, {})
    result = []
    for key in data.keys():
        try:
            result.append(int(key))
        except Exception:
            pass
    return result


def load_stats():
    default = {
        "requests": 0,
        "success": 0,
        "failed": 0,
        "audio": 0,
        "video": 0,
        "file": 0,
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

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def safe_text(text, limit=3500):
    if not text:
        return ""
    text = str(text)
    return text[:limit] + "..." if len(text) > limit else text


def short_error(e: Exception) -> str:
    msg = str(e)
    msg = re.sub(r"\s+", " ", msg).strip()
    return safe_text(msg, 900)


def safe_title(text: str, limit=90) -> str:
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
        return f"{gb:.1f} GB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{kb:.1f} KB"


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
    return f"{m:02d}:{s:02d}"


def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception:
        return False


def is_youtube_url(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url


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
        print(f"خطأ أثناء تنظيف الملفات: {e}")


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


def estimate_size(info: dict) -> str:
    try:
        sizes = []
        for f in info.get("formats") or []:
            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                sizes.append(size)

        if sizes:
            return format_size(max(sizes))

        return "غير معروف"
    except Exception:
        return "غير معروف"


def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(
                thumbs,
                key=lambda x: (x.get("width") or 0) * (x.get("height") or 0),
                reverse=True,
            )[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception:
        return ""


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
# الأزرار
# ==========================================================

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 صوت", callback_data="download_audio"),
            InlineKeyboardButton("🎬 فيديو", callback_data="download_video"),
        ],
        [
            InlineKeyboardButton("📁 ملف", callback_data="download_file"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ],
    ])


def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الإرسال", callback_data="done")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
            InlineKeyboardButton("👥 المستخدمين", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("📥 النشط", callback_data="admin_active"),
            InlineKeyboardButton("🍪 الكوكيز", callback_data="admin_cookies"),
        ],
        [
            InlineKeyboardButton("🧹 تنظيف", callback_data="admin_clean"),
        ],
    ])


# ==========================================================
# yt-dlp
# ==========================================================

def base_ydl_opts(job_dir: Path | None = None, progress_data: dict | None = None):
    opts = {
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
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }

    if job_dir:
        opts["outtmpl"] = str(job_dir / "%(title).80s.%(ext)s")

    if progress_data is not None:
        opts["progress_hooks"] = [progress_hook(progress_data)]

    # cookies اختياري لكل المنصات، ومفيد جداً مع يوتيوب/إنستغرام/تيك توك أحياناً
    if has_cookies_file():
        opts["cookiefile"] = COOKIES_FILE

    return opts


def apply_platform_tweaks(opts: dict, url: str):
    if is_youtube_url(url):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "android"],
                "skip": ["webpage"],
            }
        }
    return opts


def extract_info_sync(url: str):
    opts = base_ydl_opts()
    opts["skip_download"] = True
    opts = apply_platform_tweaks(opts, url)

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


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


def build_download_options(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = base_ydl_opts(job_dir, progress_data)

    if choice == "audio":
        # بدون تحويل MP3 حتى لا يحتاج ffmpeg
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"

    elif choice == "video":
        # صيغة جاهزة قدر الإمكان لتجنب دمج يحتاج ffmpeg
        opts["format"] = (
            "best[ext=mp4][height<=720]/"
            "best[ext=mp4]/"
            "best[height<=720]/"
            "best"
        )

    elif choice == "file":
        opts["format"] = "best"

    else:
        raise ValueError("اختيار غير معروف.")

    return apply_platform_tweaks(opts, url)


def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = build_download_options(url, choice, job_dir, progress_data)

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


# ==========================================================
# أوامر المستخدم
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()
    register_user(update.effective_user)

    await update.message.reply_text(
        "👋 أهلاً بك في بوت التحميل الشامل.\n\n"
        "أرسل رابط فيديو أو صوت من أي منصة مدعومة.\n\n"
        "يدعم غالباً:\n"
        "YouTube • TikTok • Instagram • Facebook • X • SoundCloud وغيرها.\n\n"
        "بعد إرسال الرابط سأعرض لك معلوماته وخيارات التحميل."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "📘 طريقة الاستخدام:\n\n"
        "1️⃣ أرسل الرابط.\n"
        "2️⃣ اختر: صوت أو فيديو أو ملف.\n"
        "3️⃣ انتظر حتى يصلك الملف.\n\n"
        "ملاحظات:\n"
        f"• حد الملف: {format_size(MAX_TELEGRAM_SIZE)}\n"
        "• بعض المنصات قد تحتاج cookies.txt.\n"
        "• لا يوجد اشتراك إجباري في هذه النسخة."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "📊 حالة البوت:\n\n"
        f"🍪 cookies.txt: {'موجود ✅' if has_cookies_file() else 'غير موجود / اختياري ⚠️'}\n"
        f"📥 التحميل النشط: {len(ACTIVE_USERS)}\n"
        f"📦 حد الملف: {format_size(MAX_TELEGRAM_SIZE)}"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    await update.message.reply_text(
        "🛠 لوحة الإدارة الضرورية",
        reply_markup=admin_keyboard(),
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("استخدم الأمر هكذا:\n/broadcast نص الرسالة")
        return

    sent = 0
    failed = 0

    for uid in all_user_ids():
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await update.message.reply_text(f"✅ تم الإرسال: {sent}\n❌ فشل: {failed}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    cleanup_old_downloads()
    register_user(update.effective_user)

    user_id = update.effective_user.id
    url = update.message.text.strip()

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ لديك تحميل يعمل الآن، انتظر حتى ينتهي.")
        return

    if not is_valid_url(url):
        await update.message.reply_text(
            "❌ أرسل رابطاً صحيحاً يبدأ بـ http أو https.\n\n"
            "مثال:\n"
            "https://www.youtube.com/watch?v=...\n"
            "https://www.tiktok.com/...\n"
            "https://www.instagram.com/..."
        )
        return

    status = await update.message.reply_text("🔍 جاري فحص الرابط...")

    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_info_sync(url))

        title = safe_title(info.get("title", "ملف ميديا"))
        duration = info.get("duration")
        duration_text = format_duration(duration) if duration else "غير معروف"
        size = estimate_size(info)
        extractor = safe_title(info.get("extractor_key", "منصة"), 40)
        thumb = get_thumbnail(info)

        context.user_data["current_url"] = url
        context.user_data["created_at"] = time.time()

        stat_inc("requests", 1)

        caption = (
            f"✅ تم جلب الرابط\n\n"
            f"📌 {title}\n"
            f"🌐 المنصة: {extractor}\n"
            f"⏱️ المدة: {duration_text}\n"
            f"📦 الحجم التقريبي: {size}\n\n"
            "اختر نوع التحميل:"
        )

        try:
            await status.delete()
        except Exception:
            pass

        if thumb:
            try:
                await update.message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    reply_markup=download_keyboard(),
                )
                return
            except Exception:
                pass

        await update.message.reply_text(
            caption,
            reply_markup=download_keyboard(),
            disable_web_page_preview=True,
        )

    except Exception as e:
        await safe_edit(
            status,
            "❌ تعذر فحص الرابط.\n\n"
            "قد يكون الرابط غير مدعوم، أو يحتاج cookies.txt، أو أن المنصة تمنع التحميل حالياً.\n\n"
            f"التفاصيل:\n{short_error(e)}"
        )


# ==========================================================
# الأزرار
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    register_user(query.from_user)

    data = query.data or ""

    if data == "done":
        await query.answer("تم")
        return

    if data == "cancel":
        context.user_data.clear()
        await query.message.reply_text("✅ تم إلغاء الطلب. أرسل رابطاً جديداً متى أردت.")
        return

    if data.startswith("admin_"):
        await handle_admin_button(update, context)
        return

    choices = {
        "download_audio": "audio",
        "download_video": "video",
        "download_file": "file",
    }

    if data not in choices:
        await query.message.reply_text("❌ خيار غير معروف.")
        return

    url = context.user_data.get("current_url")
    created_at = context.user_data.get("created_at", 0)

    if not url or time.time() - created_at > REQUEST_EXPIRE_SECONDS:
        await query.message.reply_text("⏱️ انتهت صلاحية الطلب. أرسل الرابط مرة أخرى.")
        return

    await process_download(update, context, url, choices[data])


async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not is_admin(query.from_user.id):
        await query.message.reply_text("❌ هذا الزر للأدمن فقط.")
        return

    data = query.data
    stats = load_stats()

    if data == "admin_stats":
        await query.message.reply_text(
            "📊 الإحصائيات:\n\n"
            f"📩 الطلبات: {stats.get('requests', 0)}\n"
            f"✅ الناجحة: {stats.get('success', 0)}\n"
            f"❌ الفاشلة: {stats.get('failed', 0)}\n"
            f"🎵 الصوت: {stats.get('audio', 0)}\n"
            f"🎬 الفيديو: {stats.get('video', 0)}\n"
            f"📁 الملفات: {stats.get('file', 0)}\n"
            f"📦 الحجم المرسل: {format_size(stats.get('bytes', 0))}"
        )
        return

    if data == "admin_users":
        await query.message.reply_text(f"👥 عدد المستخدمين: {len(all_user_ids())}")
        return

    if data == "admin_active":
        await query.message.reply_text(f"📥 التحميل النشط: {len(ACTIVE_USERS)}")
        return

    if data == "admin_cookies":
        await query.message.reply_text(
            f"🍪 cookies.txt: {'موجود ✅' if has_cookies_file() else 'غير موجود ⚠️'}\n\n"
            "وجوده اختياري، لكنه يساعد مع بعض المنصات."
        )
        return

    if data == "admin_clean":
        cleanup_old_downloads()
        await query.message.reply_text("✅ تم تنظيف الملفات القديمة.")
        return


async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, choice: str):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id in ACTIVE_USERS:
        await query.message.reply_text("⏳ لديك تحميل يعمل الآن، انتظر.")
        return

    ACTIVE_USERS.add(user_id)

    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = None

    try:
        job_dir = make_job_dir(user_id)
        status_message = await query.message.reply_text("⏳ جاري التحميل...")

        progress_data = {"text": "⏳ جاري التحميل..."}

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

        title = "ملف ميديا"
        duration = None
        extractor = ""

        if isinstance(info, dict):
            title = safe_title(info.get("title", "ملف ميديا"))
            duration = info.get("duration")
            extractor = safe_title(info.get("extractor_key", ""), 40)

        file_path = find_downloaded_file(job_dir)

        if not file_path or not file_path.exists():
            raise RuntimeError("لم يتم العثور على الملف بعد تحميله.")

        file_size = file_path.stat().st_size

        if file_size <= 0:
            raise RuntimeError("الملف الناتج فارغ أو غير صالح.")

        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit(
                status_message,
                "❌ حجم الملف أكبر من حد تيليجرام للبوتات العادية.\n\n"
                f"📦 الحجم: {format_size(file_size)}\n"
                f"📌 الحد: {format_size(MAX_TELEGRAM_SIZE)}\n\n"
                "جرّب اختيار الصوت أو رابط أقصر."
            )
            stat_inc("failed", 1)
            return

        stop_event.set()

        if updater_task:
            try:
                await updater_task
            except Exception:
                pass

        await safe_edit(status_message, "📤 جاري الرفع إلى تيليجرام...")

        caption = (
            f"✅ تم التحميل بنجاح\n"
            f"📌 {title}\n"
            f"🌐 {extractor}\n"
            f"⏱️ {format_duration(duration) if duration else 'غير معروف'}\n"
            f"📦 {format_size(file_size)}"
        )

        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )

        with open(file_path, "rb") as f:
            if choice == "audio":
                await query.message.reply_audio(
                    audio=f,
                    title=title,
                    caption=caption,
                    duration=int(duration) if duration else None,
                )
                stat_inc("audio", 1)

            elif choice == "video":
                await query.message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    duration=int(duration) if duration else None,
                )
                stat_inc("video", 1)

            else:
                await query.message.reply_document(
                    document=f,
                    caption=caption,
                )
                stat_inc("file", 1)

        stat_inc("success", 1)
        stat_inc("bytes", file_size)

        await safe_edit(status_message, "✅ تم إرسال الملف.", reply_markup=done_keyboard())

    except yt_dlp.utils.DownloadError as e:
        stat_inc("failed", 1)
        set_last_error(short_error(e))
        await safe_edit(
            status_message,
            "❌ فشل التحميل.\n\n"
            "الرابط قد يكون غير مدعوم، أو يحتاج cookies.txt، أو المنصة تمنع التحميل حالياً.\n\n"
            f"التفاصيل:\n{short_error(e)}"
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
        await safe_edit(status_message, f"❌ حدث خطأ:\n{short_error(e)}")

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
    await update.message.reply_text("❌ أمر غير معروف. استخدم /start")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error:", exc_info=context.error)


# ==========================================================
# تشغيل
# ==========================================================

def main():
    if not TOKEN:
        raise RuntimeError("لم يتم العثور على TELEGRAM_TOKEN في Railway Variables.")

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
    app.add_handler(CommandHandler("broadcast", broadcast_command))

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
