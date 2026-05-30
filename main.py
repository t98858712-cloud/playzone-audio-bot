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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat, ReplyKeyboardMarkup, KeyboardButton
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

FAST_LINK_CHECK = os.getenv("FAST_LINK_CHECK", "true").lower() == "true"

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
# تخزين بسيط
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
    ids = []
    for key in data.keys():
        try:
            ids.append(int(key))
        except Exception:
            pass
    return ids

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
        "broadcast_sent": 0,
        "broadcast_failed": 0,
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
# أدوات مساعدة مطورة
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
    if gb >= 1: return f"{gb:.1f} GB"
    if mb >= 1: return f"{mb:.1f} MB"
    return f"{kb:.1f} KB"

def format_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except Exception:
        return "غير معروف"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h: return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception:
        return False

def is_youtube_url(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url

def platform_name_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return "رابط"
    if "youtube" in host or "youtu.be" in host: return "YouTube"
    if "tiktok" in host: return "TikTok"
    if "instagram" in host: return "Instagram"
    if "facebook" in host or "fb.watch" in host: return "Facebook"
    if "x.com" in host or "twitter" in host: return "X"
    if "soundcloud" in host: return "SoundCloud"
    return host or "رابط"

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
            if not item.exists(): continue
            age = now - item.stat().st_mtime
            if age < OLD_DOWNLOADS_EXPIRE_SECONDS: continue
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
        if not valid: return None
        return max(valid, key=lambda p: p.stat().st_mtime)
    except Exception:
        return None

def get_media_author(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator", "playlist_uploader"]:
        value = info.get(key)
        if value: return safe_title(value, 60)
    return "غير معروف"

def progress_bar(percent: float) -> str:
    try:
        percent = max(0, min(100, float(percent)))
        filled = int(percent // 10)
        empty = 10 - filled
        return "█" * filled + "░" * empty
    except Exception:
        return "░" * 10

async def safe_edit(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except RetryAfter as e:
        await asyncio.sleep(int(e.retry_after) + 1)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"تعذر تعديل الرسالة: {e}")
    except Exception as e:
        logger.warning(f"تعذر تعديل الرسالة: {e}")

async def remember_ui_message(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    context.user_data["last_ui_message_id"] = message_id

async def delete_previous_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat: return
    old_id = context.user_data.get("last_ui_message_id")
    if not old_id: return
    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=int(old_id))
    except Exception:
        pass

async def send_clean_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if not update.message: return None
    await delete_previous_ui(update, context)
    msg = await update.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    await remember_ui_message(context, msg.message_id)
    return msg

async def edit_or_send(query, text: str, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" in str(e): return
        await query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)

# ==========================================================
# قوائم الأزرار (Keyboards)
# ==========================================================

def links_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🌐 موقع PlayZone"), KeyboardButton("🤖 بوت PlayZone")],
            [KeyboardButton("🎮 PlayZone"), KeyboardButton("👨‍💻 المطور")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="أرسل رابط التحميل هنا..."
    )

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 ملف صوتي عالي الجودة", callback_data="download_audio"),
            InlineKeyboardButton("🎙 مقطع صوتي", callback_data="download_voice"),
        ],
        [
            InlineKeyboardButton("🎬 مقطع فيديو", callback_data="download_video"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ],
    ])

def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔁 أرسل رابط جديد", callback_data="done")]])

def back_home_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return admin_welcome_keyboard() if is_admin(user_id) else welcome_keyboard()

def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📘 طريقة الاستخدام", callback_data="user_help")]])

def admin_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 طريقة الاستخدام", callback_data="user_help")],
        [InlineKeyboardButton("🛠 لوحة الأدمن", callback_data="admin_open")],
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
            InlineKeyboardButton("📢 إرسال تنبيه", callback_data="admin_broadcast"),
            InlineKeyboardButton("🧹 تنظيف", callback_data="admin_clean"),
        ],
    ])

def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ إرسال الآن", callback_data="broadcast_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="broadcast_cancel"),
        ],
    ])

# ==========================================================
# محرك بناء الإعدادات الموحد (تم إصلاح تشتت الخيارات والحظر)
# ==========================================================

def get_combined_ydl_opts(url: str, choice: str = None, job_dir: Path = None, progress_data: dict = None) -> dict:
    """
    يقوم ببناء خيارات الهندسة البرمجية لـ yt-dlp كاملة بدون تضارب أو تجزئة لتجنب حظر يوتيوب والمنصات.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "socket_timeout": 30,
        "cachedir": False,
        "max_filesize": MAX_TELEGRAM_SIZE,
        "windowsfilenames": True,
        "restrictfilenames": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        },
    }

    # التعديل الرئيسي: الاندرويد أولاً لتجاوز فحص الروبوت الافتراضي للبوتات
    if is_youtube_url(url):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["webpage", "hls"],
            }
        }

    if has_cookies_file():
        opts["cookiefile"] = COOKIES_FILE

    # إذا كنا نقوم بعملية فحص مسبق فقط
    if choice is None:
        opts["skip_download"] = True
        return opts

    # إعدادات الحفظ على القرص والامتدادات
    if job_dir:
        opts["outtmpl"] = str(job_dir / "%(title).80s.%(ext)s")

    if progress_data is not None:
        opts["progress_hooks"] = [progress_hook(progress_data)]

    # تنسيق الجودة المطلوبة بدقة هندسية تناسب تليجرام
    if choice == "audio":
        opts["format"] = "bestaudio[acodec*=opus][abr>=128]/bestaudio[acodec*=opus]/bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "voice":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "video":
        opts["format"] = "best[ext=mp4][height<=480]/best[ext=mp4]/best"
    elif choice == "file":
        opts["format"] = "best"

    return opts

def extract_info_sync(url: str):
    opts = get_combined_ydl_opts(url=url)
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
                eta = d.get("eta") or 0

                if total:
                    percent = downloaded / total * 100
                    bar = progress_bar(percent)
                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"{bar} {percent:.1f}%\n\n"
                        f"📦 {format_size(downloaded)} / {format_size(total)}\n"
                        f"⚡ {format_size(speed)}/s\n"
                        f"⏱️ المتبقي: {eta} ثانية"
                    )
                else:
                    progress_data["text"] = (
                        "📥 جاري التحميل...\n\n"
                        f"📦 تم تحميل: {format_size(downloaded)}\n"
                        f"⚡ {format_size(speed)}/s"
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

def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = get_combined_ydl_opts(url=url, choice=choice, job_dir=job_dir, progress_data=progress_data)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# ==========================================================
# معالجة أوامر المستخدم والمحادثة
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()
    register_user(update.effective_user)

    await send_clean_message(
        update, context,
        "👋 أهلاً بك في بوت التحميل الشامل.\n\n"
        "أرسل الرابط مباشرة، وستظهر خيارات التحميل فوراً.\n\n"
        "يدعم غالباً:\n"
        "YouTube • TikTok • Instagram • Facebook • X • SoundCloud وغيرها.\n\n"
        "✅ أرسل الرابط الآن للبدء.",
        reply_markup=admin_welcome_keyboard() if is_admin(update.effective_user.id) else welcome_keyboard(),
    )
    await update.message.reply_text(
        "🔗 روابط PlayZone الرسمية متاحة من الأزرار بالأسفل.",
        reply_markup=links_reply_keyboard(),
        disable_web_page_preview=True
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(f"❌ هذا الأمر للأدمن فقط.\nID حسابك هو: {update.effective_user.id}\nضعه في خيارات البيئة لـ Railway.")
        return

    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("broadcast_text", None)
    await send_clean_message(update, context, "🛠 لوحة الإدارة الضرورية\n\nاختر ما تحتاجه فقط:", reply_markup=admin_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cleanup_old_downloads()
    register_user(update.effective_user)

    user_id = update.effective_user.id
    text = update.message.text.strip()

    # شاشة الردود النصية الثابتة للأزرار السفلية
    if text == "🌐 موقع PlayZone":
        await update.message.reply_text("🌐 موقع PlayZone:\nhttps://tasmg1.github.io/tasmg/", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "🤖 بوت PlayZone":
        await update.message.reply_text("🤖 بوت PlayZone:\nhttps://t.me/P1ay_Z0ne_Bot", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "🎮 PlayZone":
        await update.message.reply_text("🎮 PlayZone:\nhttps://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg==", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "👨‍💻 المطور":
        await update.message.reply_text("👨‍💻 المطور:\nhttps://www.instagram.com/ta_smg?igsh=aTB5dTJzdmRtaTA4&utm_source=qr", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return

    if is_admin(user_id) and context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        context.user_data["broadcast_text"] = text
        await update.message.reply_text(f"📢 معاينة التنبيه:\n\n{text}\n\nسيتم إرساله إلى {len(all_user_ids())} مستخدم. هل تريد الإرسال؟", reply_markup=broadcast_confirm_keyboard())
        return

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ لديك تحميل يعمل الآن، انتظر حتى ينتهي.")
        return

    if not is_valid_url(text):
        await send_clean_message(update, context, "❌ لم أتعرف على هذا كرابط.\nأرسل رابطاً يبدأ بـ http أو https.", reply_markup=back_home_keyboard(user_id))
        return

    context.user_data["current_url"] = text
    context.user_data["created_at"] = time.time()
    stat_inc("requests", 1)

    if FAST_LINK_CHECK:
        await send_clean_message(
            update, context,
            f"✅ تم استلام الرابط.\n🌐 المنصة: {platform_name_from_url(text)}\n\nاختر نوع التحميل المطلوب:",
            reply_markup=download_keyboard()
        )
    else:
        status = await update.message.reply_text("🔍 جاري جلب تفاصيل الرابط الفنية الفورية...")
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, lambda: extract_info_sync(text))
            context.user_data["preview_title"] = safe_title(info.get("title", "ملف ميديا"))
            context.user_data["preview_duration"] = info.get("duration") or 0
            context.user_data["preview_author"] = get_media_author(info)
            context.user_data["preview_platform"] = safe_title(info.get("extractor_key") or platform_name_from_url(text), 40)
            
            await status.delete()
            caption = f"🎬 {context.user_data['preview_title']}\n🌐 {context.user_data['preview_platform']}\n⏱️ {format_duration(context.user_data['preview_duration'])}\n\nاختر صيغة التحميل:"
            msg = await update.message.reply_text(caption, reply_markup=download_keyboard(), disable_web_page_preview=True)
            await remember_ui_message(context, msg.message_id)
        except Exception as e:
            err = short_error(e)
            set_last_error(err)
            await safe_edit(status, f"✅ تم استقبال الرابط.\n📌 تعذر فحص المعاينة الفنية المسبقة، يمكنك محاولة الضغط المباشر على خيارات التحميل الأساسية للبدء:", reply_markup=download_keyboard())

# ==========================================================
# معالجة ضغطات الأزرار التفاعلية
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    register_user(query.from_user)
    data = query.data or ""

    if data == "admin_open":
        if not is_admin(query.from_user.id): return
        await edit_or_send(query, "🛠 لوحة الإدارة الضرورية\n\nاختر ما تحتاجه فقط:", reply_markup=admin_keyboard())
        return
    if data == "done":
        await edit_or_send(query, "📩 أرسل الرابط الجديد الآن مباشرة في المحادثة.", reply_markup=back_home_keyboard(query.from_user.id))
        return
    if data == "user_help":
        await edit_or_send(query, "📘 طريقة الاستخدام:\n\n1️⃣ أرسل الرابط فقط.\n2️⃣ اختر نوع التحميل.\n3️⃣ انتظر إرسال الملف مباشرة بدون أوامر معقدة.", reply_markup=back_home_keyboard(query.from_user.id))
        return
    if data == "cancel":
        context.user_data.clear()
        await edit_or_send(query, "✅ تم إلغاء الطلب بنجاح. أرسل رابطاً جديداً متى أردت.", reply_markup=back_home_keyboard(query.from_user.id))
        return
    if data.startswith("admin_"):
        await handle_admin_button(update, context)
        return
    if data in ["broadcast_confirm", "broadcast_cancel"]:
        await handle_broadcast_button(update, context)
        return

    choices = {"download_audio": "audio", "download_voice": "voice", "download_video": "video"}
    if data not in choices: return

    url = context.user_data.get("current_url")
    created_at = context.user_data.get("created_at", 0)

    if not url or time.time() - created_at > REQUEST_EXPIRE_SECONDS:
        await query.message.reply_text("⏱️ انتهت صلاحية الطلب. أرسل الرابط مرة أخرى.")
        return

    await process_download(update, context, url, choices[data])

async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return
    data = query.data
    stats = load_stats()

    if data == "admin_stats":
        await query.message.reply_text(
            "📊 الإحصائيات الشاملة:\n\n"
            f"📩 الطلبات: {stats.get('requests', 0)}\n"
            f"✅ الناجحة: {stats.get('success', 0)}\n"
            f"❌ الفاشلة: {stats.get('failed', 0)}\n"
            f"🎵 الصوت: {stats.get('audio', 0)}\n"
            f"🎬 الفيديو: {stats.get('video', 0)}\n"
            f"📦 الحجم المرسل: {format_size(stats.get('bytes', 0))}\n"
            f"🧾 آخر خطأ: {safe_text(stats.get('last_error', 'لا يوجد'), 250)}"
        )
    elif data == "admin_users":
        await query.message.reply_text(f"👥 عدد المستخدمين: {len(all_user_ids())}")
    elif data == "admin_active":
        await query.message.reply_text(f"📥 التحميل النشط حالياً: {len(ACTIVE_USERS)}")
    elif data == "admin_cookies":
        await query.message.reply_text(f"🍪 ملف الكوكيز (cookies.txt): {'موجود وشغال ✅' if has_cookies_file() else 'غير موجود ⚠️'}")
    elif data == "admin_clean":
        cleanup_old_downloads()
        await query.message.reply_text("✅ تم تنظيف السيرفر وإفراغ الملفات المؤقتة بنجاح.")
    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.message.reply_text("📢 اكتب الآن نص التنبيه الذي تريد إذاعته لجميع المستخدمين:")

async def handle_broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return

    if query.data == "broadcast_cancel":
        context.user_data.pop("broadcast_text", None)
        await query.message.reply_text("✅ تم إلغاء البث بنجاح.")
        return

    text = context.user_data.get("broadcast_text")
    if not text: return

    await query.message.reply_text("📢 جاري إرسال البث لجميع المشتركين...")
    sent, failed = 0, 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    stat_inc("broadcast_sent", sent)
    stat_inc("broadcast_failed", failed)
    context.user_data.pop("broadcast_text", None)
    await query.message.reply_text(f"✅ انتهى البث الإذاعي.\n\nتم الإرسال: {sent}\nفشل: {failed}")

# ==========================================================
# دالة معالجة التنزيل والرفع الفعلي (محمية ومؤمنة بالكامل)
# ==========================================================

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
    status_message = await query.message.reply_text("⚡ جاري الاتصال بخوادم المصدر والتحميل...")

    try:
        job_dir = make_job_dir(user_id)
        progress_data = {"text": "⏳ جاري بدء سحب البيانات البرمجية..."}
        
        updater_task = asyncio.create_task(progress_updater(status_message, progress_data, stop_event))
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: download_sync(url, choice, job_dir, progress_data))

        title = safe_title(info.get("title") if isinstance(info, dict) else context.user_data.get("preview_title", "ملف ميديا"))
        duration = info.get("duration") if isinstance(info, dict) else context.user_data.get("preview_duration", 0)
        extractor = safe_title(info.get("extractor_key") if isinstance(info, dict) else context.user_data.get("preview_platform", "رابط"), 40)
        author = get_media_author(info) if isinstance(info, dict) else context.user_data.get("preview_author", "")

        file_path = find_downloaded_file(job_dir)
        if not file_path or not file_path.exists():
            raise RuntimeError("الملف الميديا المحمل غير موجود أو تالف.")

        file_size = file_path.stat().st_size
        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit(status_message, f"❌ حجم الملف الناتج ({format_size(file_size)}) أكبر من قيود تيليجرام للبوتات العادية (50MB).")
            stat_inc("failed", 1)
            return

        stop_event.set()
        if updater_task: await updater_task

        await safe_edit(status_message, "📤 جاري الرفع الآن إلى خوادم تيليجرام...")
        
        upload_action = ChatAction.UPLOAD_VOICE if choice in ["audio", "voice"] else ChatAction.UPLOAD_VIDEO
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=upload_action)

        caption = f"✅ تم التحميل بنجاح\n📌 {title}\n🌐 {extractor}\n⏱️ {format_duration(duration)}\n📦 {format_size(file_size)}"

        with open(file_path, "rb") as f:
            if choice == "audio":
                await query.message.reply_audio(audio=f, title=title, performer=author, caption=caption, duration=int(duration) if duration else None)
                stat_inc("audio", 1)
            elif choice == "voice":
                await query.message.reply_voice(voice=f, caption=caption, duration=int(duration) if duration else None)
                stat_inc("audio", 1)
            elif choice == "video":
                await query.message.reply_video(video=f, caption=caption, supports_streaming=True, duration=int(duration) if duration else None)
                stat_inc("video", 1)

        stat_inc("success", 1)
        stat_inc("bytes", file_size)
        await safe_edit(status_message, "✅ تم إرسال الملف بنجاح للتطبيق!", reply_markup=done_keyboard())

    except Exception as e:
        stat_inc("failed", 1)
        err = short_error(e)
        set_last_error(err)
        logger.warning(f"فشل التحميل الكلي للمستخدم {user_id}: {err}")
        
        if "Sign in to confirm" in err or "cookies" in err:
            msg_error = "❌ قام الموقع بحجب الطلب الفوري؛ يرجى رفع ملف cookies.txt جديد من لوحة التحكم للأدمن."
        elif "max-filesize" in err:
            msg_error = "❌ حجم الملف كبير للغاية ويتجاوز 50 ميجابايت."
        else:
            msg_error = "❌ حدث خطأ أثناء معالجة الرابط، يرجى المحاولة لاحقاً مع رابط آخر."
            
        await safe_edit(status_message, msg_error, reply_markup=done_keyboard())

    finally:
        stop_event.set()
        if updater_task:
            try: await updater_task
            except Exception: pass
        if job_dir: clean_job_dir(job_dir)
        ACTIVE_USERS.discard(user_id)
        context.user_data.clear()

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💡 اضغط /start ثم أرسل رابط التحميل مباشرة بدون أوامر مسبقة.", reply_markup=welcome_keyboard())

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطأ داخلي عام في التطبيق:", exc_info=context.error)

# ==========================================================
# تخصيص قائمة الأوامر المنسدلة
# ==========================================================

async def setup_bot_commands(app):
    await app.bot.set_my_commands([BotCommand("start", "بدء تشغيل واستخدام البوت")], scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands([BotCommand("start", "بدء تشغيل البوت"), BotCommand("admin", "لوحة التحكم للإدارة")], scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass

# ==========================================================
# بدء التشغيل الرئيسي (Main Entry Point)
# ==========================================================

def main():
    if not TOKEN:
        raise RuntimeError("خطأ فادح: لم يتم العثور على المتغير البرمجي TELEGRAM_TOKEN")
    cleanup_old_downloads()

    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).connect_timeout(30).read_timeout(30).write_timeout(30).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)

    logger.info("🚀 تم تحديث وإصلاح كافة المشاكل، البوت قيد العمل الفوري الآن...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
