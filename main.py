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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
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
PROGRESS_UPDATE_SECONDS = 2  # تسريع تحديث شريط التحميل لزيادة التفاعل

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
# أدوات مساعدة وتأثيرات بصرية
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
        return "ملف ميديا"
    text = str(text)
    text = re.sub(r"[\\/:*?\"<>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() if len(text) > limit else (text or "ملف ميديا")

def format_size(size_bytes) -> str:
    try: size_bytes = int(size_bytes)
    except Exception: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    kb = size_bytes / 1024
    mb = kb / 1024
    gb = mb / 1024
    if gb >= 1: return f" {gb:.1f} GB"
    if mb >= 1: return f" {mb:.1f} MB"
    return f" {kb:.1f} KB"

def format_number(num) -> str:
    try:
        num = int(num)
        if num >= 1_000_000: return f"{num/1_000_000:.1f}M"
        if num >= 1_000: return f"{num/1_000:.1f}K"
        return str(num)
    except: return "غير متاح"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except Exception: return "غير معروف"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"⏱ {h}:{m:02d}:{s:02d}" if h else f"⏱ {m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception:
        return False

def platform_name_from_url(url: str) -> str:
    try: host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception: return "رابط خارجي"
    if "youtube" in host or "youtu.be" in host: return "YouTube 🟥"
    if "tiktok" in host: return "TikTok ⬛"
    if "instagram" in host: return "Instagram 🟪"
    if "facebook" in host or "fb.watch" in host: return "Facebook 🟦"
    if "x.com" in host or "twitter" in host: return "X (Twitter) 🐦"
    if "soundcloud" in host: return "SoundCloud 🟧"
    return host.capitalize()

def has_cookies_file() -> bool:
    path = Path(COOKIES_FILE)
    return path.exists() and path.is_file() and path.stat().st_size > 0

def make_job_dir(user_id: int) -> Path:
    job_dir = BASE_DOWNLOAD_DIR / f"{user_id}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir

def clean_job_dir(job_dir: Path):
    try:
        if job_dir and job_dir.exists(): shutil.rmtree(job_dir)
    except Exception as e: logger.warning(f"خطأ تنظيف: {e}")

def cleanup_old_downloads():
    now = time.time()
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            if not item.exists(): continue
            if (now - item.stat().st_mtime) < OLD_DOWNLOADS_EXPIRE_SECONDS: continue
            if item.is_dir(): shutil.rmtree(item, ignore_errors=True)
            else: item.unlink(missing_ok=True)
    except Exception as e: logger.warning(f"تنظيف قديم: {e}")

def find_downloaded_file(job_dir: Path):
    try:
        files = [p for p in job_dir.iterdir() if p.is_file()]
        valid = [p for p in files if not p.suffix in [".part", ".tmp", ".ytdl"]]
        if not valid: return None
        return max(valid, key=lambda p: p.stat().st_mtime)
    except Exception: return None

def estimate_size(info: dict) -> str:
    try:
        sizes = []
        for f in info.get("formats") or []:
            size = f.get("filesize") or f.get("filesize_approx")
            if size: sizes.append(size)
        return format_size(max(sizes)) if sizes else "غير معروف"
    except Exception: return "غير معروف"

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception: return ""

def get_media_author(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator", "playlist_uploader"]:
        value = info.get(key)
        if value: return safe_title(value, 60)
    return "غير معروف"

def build_preview_caption(info: dict, url: str) -> str:
    title = safe_title(info.get("title", "ملف ميديا مجهول"), 80)
    duration = format_duration(info.get("duration")) if info.get("duration") else "⏱ غير معروف"
    size = estimate_size(info)
    platform = platform_name_from_url(url)
    author = get_media_author(info)
    views = format_number(info.get("view_count"))
    likes = format_number(info.get("like_count"))
    
    return (
        f"✨ **تم جلب معلومات الرابط بنجاح!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **العنوان:** {title}\n"
        f"👤 **الناشر:** {author}\n"
        f"🌐 **المنصة:** {platform}\n"
        f"📊 **المدة:** {duration} | 📦 **الحجم المتوقع:** {size}\n"
        f"👁 **المشاهدات:** {views} | ❤️ **الإعجابات:** {likes}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👇 يرجى اختيار صيغة ونوع التحميل المفضلة لديك:"
    )

async def send_preview_card(update: Update, context: ContextTypes.DEFAULT_TYPE, info: dict, url: str):
    thumb = get_thumbnail(info)
    caption = build_preview_caption(info, url)
    await delete_previous_ui(update, context)

    if thumb:
        try:
            msg = await update.message.reply_photo(
                photo=thumb,
                caption=caption,
                reply_markup=download_keyboard(),
                parse_mode="Markdown"
            )
            await remember_ui_message(context, msg.message_id)
            return msg
        except Exception: pass

    msg = await update.message.reply_text(
        caption,
        reply_markup=download_keyboard(),
        disable_web_page_preview=False,
        parse_mode="Markdown"
    )
    await remember_ui_message(context, msg.message_id)
    return msg

def progress_bar(percent: float) -> str:
    try:
        percent = max(0, min(100, float(percent)))
        filled = int(percent // 10)
        # استخدام شريط تحميل ذو مظهر جذاب تفاعلي
        return "🟢" * filled + "⚪" * (10 - filled)
    except Exception:
        return "⚪" * 10

async def safe_edit(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode="Markdown")
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
    try: await context.bot.delete_message(chat_id=chat.id, message_id=int(old_id))
    except Exception: pass

async def send_clean_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if not update.message: return None
    await delete_previous_ui(update, context)
    msg = await update.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode="Markdown")
    await remember_ui_message(context, msg.message_id)
    return msg

async def edit_or_send(query, text: str, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode="Markdown")
    except BadRequest as e:
        if "Message is not modified" in str(e): return
        await query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode="Markdown")

# ==========================================================
# الأزرار والواجهات التفاعلية
# ==========================================================

def links_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🎮 PlayZone"), KeyboardButton("🌍 موقع PlayZone")],
            [KeyboardButton("🤖 بوت PlayZone"), KeyboardButton("👨‍💻 المطور")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل رابط المقطع هنا..."
    )

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 ملف صوتي 🔊", callback_data="download_audio"),
            InlineKeyboardButton("🎙 مقطع صوتي 💬", callback_data="download_voice"),
        ],
        [
            InlineKeyboardButton("🎬 مقطع فيديو 📺", callback_data="download_video"),
            InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel"),
        ],
    ])

def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحميل رابط آخر", callback_data="done")]])

def back_home_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return admin_welcome_keyboard() if is_admin(user_id) else welcome_keyboard()

def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📘 دليل الاستخدام السريع", callback_data="user_help")]])

def admin_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 دليل الاستخدام", callback_data="user_help")],
        [InlineKeyboardButton("🛠 لوحة تحكم الإدارة", callback_data="admin_open")],
    ])

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"), InlineKeyboardButton("👥 الأعضاء", callback_data="admin_users")],
        [InlineKeyboardButton("📥 العمليات", callback_data="admin_active"), InlineKeyboardButton("🍪 ملفات الارتباط", callback_data="admin_cookies")],
        [InlineKeyboardButton("📢 إذاعة تنبيه", callback_data="admin_broadcast"), InlineKeyboardButton("🧹 تنظيف الذاكرة", callback_data="admin_clean")],
    ])

def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد وبث", callback_data="broadcast_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="broadcast_cancel")],
    ])

# ==========================================================
# إعدادات الميديا والتحميل الذكي
# ==========================================================

def base_ydl_opts(job_dir: Path | None = None, progress_data: dict | None = None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "ignoreerrors": False,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "socket_timeout": 15,
        "cachedir": False,
        "windowsfilenames": True,
        "restrictfilenames": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web_safari"],
                "skip": ["webpage"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    }

    if job_dir: opts["outtmpl"] = str(job_dir / "%(title).80s.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [progress_hook(progress_data)]
    if has_cookies_file(): opts["cookiefile"] = COOKIES_FILE

    return opts

def extract_info_sync(url: str):
    opts = base_ydl_opts()
    opts["skip_download"] = True
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
                eta = d.get("eta")

                if total:
                    percent = downloaded / total * 100
                    bar = progress_bar(percent)
                    progress_data["text"] = (
                        f"📥 **جاري تحميل الملف حالياً...**\n\n"
                        f"{bar} **{percent:.1f}%**\n\n"
                        f"📦 **الحجم:** {format_size(downloaded)} من {format_size(total)}\n"
                        f"⚡ **السرعة الحالية:** {format_size(speed)}/ثانية\n"
                        f"⏳ **الوقت المتبقي المقدر:** {eta if eta else '---'} ثانية"
                    )
                else:
                    progress_data["text"] = (
                        f"📥 **جاري تحميل دفق البيانات المستمر...**\n\n"
                        f"📦 **تم استقبال:** {format_size(downloaded)}\n"
                        f"⚡ **السرعة المتاحة:** {format_size(speed)}/ثانية"
                    )
            elif status == "finished":
                progress_data["text"] = "⚙️ **اكتمل التحميل المحتوي! جاري معالجة وتجهيز الملف وتدفيقه الآن...**"
        except Exception: pass
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
        opts["format"] = "bestaudio[ext=m4a]/bestaudio[filesize<49M]/bestaudio/best"
    elif choice == "voice":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "video":
        opts["format"] = "best[ext=mp4][height<=720][filesize<49M]/best[ext=mp4][height<=480]/best[filesize<49M]/best"
    elif choice == "file":
        opts["format"] = "best[filesize<49M]/best"
    else:
        raise ValueError("صيغة غير مدعومة.")
    return opts

def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = build_download_options(url, choice, job_dir, progress_data)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# ==========================================================
# معالجة طلبات المستخدم والتفاعل التلقائي
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()
    register_user(update.effective_user)

    await send_clean_message(
        update, context,
        f"👋 **أهلاً بك يا {update.effective_user.first_name} في بوت التحميل الذكي المطور!**\n\n"
        f"🚀 يمكنك إرسال أي رابط ميديا مباشرة من منصات التواصل الاجتماعي المفضلة لديك وسنقوم بمعالجتها بلمح البصر.\n\n"
        f"✅ **أرسل الرابط الآن للبدء فورا:**",
        reply_markup=admin_welcome_keyboard() if is_admin(update.effective_user.id) else welcome_keyboard(),
    )
    await update.message.reply_text(
        "✨ تصفح روابطنا الرسمية عبر الأزرار أدناه للوصول السريع:",
        reply_markup=links_reply_keyboard(),
        disable_web_page_preview=True
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(f"❌ عذراً، هذا الإجراء مخصص لطاقم الإدارة.\n\nمعرف حسابك: `{update.effective_user.id}`", parse_mode="Markdown")
        return

    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("broadcast_text", None)
    await send_clean_message(update, context, "🛠 **لوحة تحكم الإدارة الشاملة:**\n\nاختر التبويب أو الإجراء الذي ترغب في مراجعته:", reply_markup=admin_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cleanup_old_downloads()
    register_user(update.effective_user)

    user_id = update.effective_user.id
    text = update.message.text.strip()

    # الأزرار التفاعلية الثابتة
    if text == "🎮 PlayZone":
        await update.message.reply_text("🎮 **حساب PlayZone الرسمي على إنستغرام:**\nhttps://www.instagram.com/p1ay.zone", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "🌍 موقع PlayZone":
        await update.message.reply_text("🌍 **موقع الويب الرسمي لمنصتنا:**\nhttps://tasmg1.github.io/tasmg/", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "🤖 بوت PlayZone":
        await update.message.reply_text("🤖 **معرف البوت الرسمي الخاص بنا:**\n@P1ay_Z0ne_Bot", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return
    if text == "👨‍💻 المطور":
        await update.message.reply_text("👨‍💻 **حساب المطور الرسمي مباشرة:**\nhttps://www.instagram.com/ta_smg", reply_markup=links_reply_keyboard(), disable_web_page_preview=True)
        return

    if is_admin(user_id) and context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        context.user_data["broadcast_text"] = text
        await update.message.reply_text(f"📢 **معاينة رسالة الإذاعة قبل البث:**\n\n{text}\n\n⚠️ سيتم الإرسال لـ `{len(all_user_ids())}` مشترك.\nهل تؤكد البث الآن؟", reply_markup=broadcast_confirm_keyboard(), parse_mode="Markdown")
        return

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ **عذراً! لديك عملية تحميل جارية بالفعل.** يرجى الانتظار حتى تكتمل وتصلك رسالتها للبدء من جديد.")
        return

    if not is_valid_url(text):
        await send_clean_message(update, context, "❌ **الرابط المرسل غير مدعوم أو غير صحيح!**\nيرجى التأكد من أن الرابط يبدأ بـ `http://` أو `https://` ثم أعد المحاولة.", reply_markup=back_home_keyboard(user_id))
        return

    # تأثيرات حركية أثناء قراءة الرابط
    status = await update.message.reply_text("🔍 **جاري فحص وقراءة الرابط المرفق، يرجى الانتظار ثوانٍ معدودة...** ⚡")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_info_sync(text))

        context.user_data["current_url"] = text
        context.user_data["created_at"] = time.time()
        context.user_data["preview_title"] = safe_title(info.get("title", "ملف ميديا"))
        context.user_data["preview_author"] = get_media_author(info)
        context.user_data["preview_platform"] = platform_name_from_url(text)
        context.user_data["preview_duration"] = info.get("duration") or 0

        stat_inc("requests", 1)
        try: await status.delete()
        except Exception: pass

        await send_preview_card(update, context, info, text)
    except Exception as e:
        err = short_error(e)
        set_last_error(err)
        logger.warning(f"فشل أولي {user_id}: {err}")

        context.user_data["current_url"] = text
        context.user_data["created_at"] = time.time()
        context.user_data["preview_title"] = "ملف ميديا عام"
        context.user_data["preview_author"] = "غير معروف"
        context.user_data["preview_platform"] = platform_name_from_url(text)
        context.user_data["preview_duration"] = 0
        stat_inc("requests", 1)

        await safe_edit(
            status,
            f"✅ **تم استلام وتجهيز الرابط بنجاح!**\n\n🌐 **المنصة:** {platform_name_from_url(text)}\n\n💡 سنقوم بمعالجة وتحميل الملف مباشرة فور اختيار الصيغة المطلوبة بالأسفل:",
            reply_markup=download_keyboard()
        )

# ==========================================================
# معالجة الضغط على الأزرار والإشعارات التفاعلية (Flash Messages)
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    register_user(query.from_user)

    data = query.data or ""
    
    # رسائل فلاشية تفاعلية منبثقة عند الضغط على الأزرار لراحة واستمتاع المستخدم
    if data == "admin_open":
        if not is_admin(query.from_user.id): return
        await query.answer("📂 تم فتح لوحة الإدارة")
        await edit_or_send(query, "🛠 **لوحة تحكم الإدارة الشاملة:**\n\nاختر التبويب أو الإجراء الذي ترغب في مراجعته:", reply_markup=admin_keyboard())
        return
    if data == "done":
        await query.answer("📥 جاهز لاستقبال طلبك القادم")
        await edit_or_send(query, "📩 **أرسل رابط المقطع الجديد الآن مباشرة في المحادثة:**", reply_markup=back_home_keyboard(query.from_user.id))
        return
    if data == "user_help":
        await query.answer("📘 تم فتح دليل الاستخدام")
        await edit_or_send(query, "📘 **طريقة استخدام البوت السهلة:**\n\n1️⃣ انسخ رابط المقطع من أي تطبيق (يوتيوب، إنستغرام، تيك توك، الخ).\n2️⃣ ألصق الرابط وأرسله هنا للمحادثة مباشرة.\n3️⃣ ستظهر لك بطاقة المعلومات، اختر نوع الصيغة المناسبة لك وانتظر إرسال ملفك المجهز بالكامل! ✨", reply_markup=back_home_keyboard(query.from_user.id))
        return
    if data == "cancel":
        context.user_data.pop("current_url", None)
        await query.answer("❌ تم إلغاء طلبك")
        await edit_or_send(query, "✅ **تم إلغاء العملية السابقة بنجاح.**\nيمكنك إرسال أي رابط آخر في أي وقت تريده وسأكون بالخدمة!", reply_markup=back_home_keyboard(query.from_user.id))
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
        await query.answer("⚠️ انتهت الصلاحية", show_alert=True)
        await query.message.reply_text("⏱ **عذراً، انتهت صلاحية الجلسة للطلب القديم.** يرجى إعادة إرسال الرابط مجدداً لتحديث البيانات.")
        return

    await query.answer("🚀 بدأت المعالجة والتحميل الآن!")
    await process_download(update, context, url, choices[data])

async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return
    data = query.data
    stats = load_stats()
    await query.answer("📊 تم تحديث البيانات")

    if data == "admin_stats":
        await query.message.reply_text(
            f"📊 **إحصائيات المنصة الحالية:**\n\n"
            f"📩 **إجمالي الطلبات المستلمة:** `{stats.get('requests', 0)}`\n"
            f"✅ **عمليات ناجحة مسجلة:** `{stats.get('success', 0)}`\n"
            f"❌ **عمليات متعثرة:** `{stats.get('failed', 0)}`\n"
            f"🎵 **ملفات صوتية مُرسلة:** `{stats.get('audio', 0)}`\n"
            f"🎬 **مقاطع مرئية (فيديو):** `{stats.get('video', 0)}`\n"
            f"📦 **الحجم المستهلك الإجمالي:** `{format_size(stats.get('bytes', 0))}`\n\n"
            f"🧾 **سجل لآخر تعارض للعمليات:**\n`{safe_text(stats.get('last_error', 'لا يوجد سجل أخطاء حالي'))}`",
            parse_mode="Markdown"
        )
    elif data == "admin_users":
        await query.message.reply_text(f"👥 **إجمالي المشتركين المسجلين في قاعدة البيانات:** `{len(all_user_ids())}` مستخدم.", parse_mode="Markdown")
    elif data == "admin_active":
        await query.message.reply_text(f"📥 **عدد طلبات التحميل النشطة التي يتم معالجتها حالياً:** `{len(ACTIVE_USERS)}` عملية.", parse_mode="Markdown")
    elif data == "admin_cookies":
        await query.message.reply_text(f"🍪 **حالة اتصال ملف الارتباط الاحتياطي:** {'متصل ومفعّل بالكامل ✅' if has_cookies_file() else 'غير مرفوع أو مفقود حالياً ⚠️'}")
    elif data == "admin_clean":
        cleanup_old_downloads()
        await query.message.reply_text("🧹 **تم إفراغ الذاكرة المؤقتة وتنظيف الكاش بنجاح تـام!**")
    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.message.reply_text("📢 **الوضع الجاهز للإذاعة العامة:**\nيرجى كتابة وإرسال نص الرسالة التنبيهية الآن لتصميمها وبثها...")

async def handle_broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id): return

    if query.data == "broadcast_cancel":
        context.user_data.pop("broadcast_text", None)
        await query.answer("❌ تم الإلغاء")
        await query.message.reply_text("❌ **تم إلغاء عملية البث الجماعي وسحب المسودة.**")
        return

    text = context.user_data.get("broadcast_text")
    if not text: return

    await query.answer("📢 بدأ البث الجماعي")
    await query.message.reply_text("📢 **جاري إرسال وبث الرسالة الجماعية لكل المشتركين، يرجى عدم تكرار الإجراء...**")
    sent, failed = 0, 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception: failed += 1

    stat_inc("broadcast_sent", sent)
    stat_inc("broadcast_failed", failed)
    context.user_data.pop("broadcast_text", None)
    await query.message.reply_text(f"✅ **اكتملت عملية الإذاعة الجماعية بنجاح!**\n\n👍 **تم تسليمها بنجاح لـ:** `{sent}` مشترك\n👎 **تعذر تسليمها لـ:** `{failed}` مشترك", parse_mode="Markdown")

async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, choice: str):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id in ACTIVE_USERS:
        await query.message.reply_text("⏳ **عذراً، عملياتك السابقة لا تزال قيد التنفيذ، يرجى الصبر دقيقة.**")
        return

    ACTIVE_USERS.add(user_id)
    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = None

    try:
        job_dir = make_job_dir(user_id)
        status_message = await query.message.reply_text("⚡ **جاري بدء الاتصال السريع بالمنصة وتهيئة طلبك...**")
        progress_data = {"text": "⏳ **جاري تأسيس الاتصال وتدفيق الحزم للمقطع...**"}

        updater_task = asyncio.create_task(progress_updater(status_message, progress_data, stop_event))
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: download_sync(url, choice, job_dir, progress_data))

        title = context.user_data.get("preview_title") or "ملف ميديا مجهز"
        duration = context.user_data.get("preview_duration") or None
        extractor = context.user_data.get("preview_platform") or ""
        author = context.user_data.get("preview_author") or "غير معروف"

        if isinstance(info, dict):
            title = safe_title(info.get("title", title))
            duration = info.get("duration") or duration
            extractor = platform_name_from_url(url)
            author = get_media_author(info)

        file_path = find_downloaded_file(job_dir)
        if not file_path or not file_path.exists():
            raise RuntimeError("الملف المذكور غير متوفر في المسار المحدد.")

        file_size = file_path.stat().st_size
        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit(status_message, f"❌ **حجم الملف النهائي ({format_size(file_size)}) تخطى الحد الأقصى المسموح به للبوتات (50MB).**\n\n💡 يرجى اختيار جودة ميديا أقل أو إرسال مقطع آخر بمدة أقصر للتمكن من تحميله بنجاح.")
            stat_inc("failed", 1)
            return

        stop_event.set()
        if updater_task: await updater_task

        await safe_edit(status_message, "📤 **اكتمل التحميل في سيرفراتنا! جاري نقل ورفع الملف إلى تيليجرام الآن...** ✨")

        caption = (
            f"✅ **تم تحميل ملفك وجاهز للمشاهدة الآن!**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **الاسم:** {title}\n"
            f"🌐 **المصدر:** {extractor}\n"
            f"⏱ **المدة الزمنية:** {format_duration(duration) if duration else 'غير معروف'}\n"
            f"📦 **حجم الملف الصافي:** {format_size(file_size)}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👑 **بواسطة:** @P1ay_Z0ne_Bot"
        )
        
        upload_action = ChatAction.UPLOAD_VIDEO if choice == "video" else ChatAction.UPLOAD_VOICE
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=upload_action)

        with open(file_path, "rb") as f:
            if choice == "audio":
                await query.message.reply_audio(audio=f, title=title, performer=author, caption=caption, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("audio", 1)
            elif choice == "voice":
                try:
                    await query.message.reply_voice(voice=f, caption=caption, duration=int(duration) if duration else None, parse_mode="Markdown")
                except Exception:
                    f.seek(0)
                    await query.message.reply_audio(audio=f, title=title, caption=caption, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("audio", 1)
            elif choice == "video":
                await query.message.reply_video(video=f, caption=caption, supports_streaming=True, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("video", 1)

        stat_inc("success", 1)
        stat_inc("bytes", file_size)
        await safe_edit(status_message, "🎉 **تهانينا! تم إرسال وتسليم الملف الخاص بك بنجاح مطلق ودون أي قيود.** ✨", reply_markup=done_keyboard())

    except Exception as e:
        stat_inc("failed", 1)
        err = short_error(e)
        set_last_error(err)
        await safe_edit(status_message, f"❌ **عذراً، حدث خطأ غير متوقع أثناء معالجة رابط هذه المنصة.**\nيرجى المحاولة مرة أخرى أو التأكد من صلاحية المقطع وظهوره للعامة.")
    finally:
        stop_event.set()
        if updater_task:
            try: await updater_task
            except Exception: pass
        if job_dir: clean_job_dir(job_dir)
        ACTIVE_USERS.discard(user_id)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💡 **ليست هناك حاجة لأوامر معقدة!** فقط اضغط على زر البدء /start ثم أرسل الرابط مباشرة للحصول على ملفك المجهز.", reply_markup=welcome_keyboard())

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram Exception Handler captured:", exc_info=context.error)

async def setup_bot_commands(app):
    await app.bot.set_my_commands([BotCommand("start", "تشغيل وتحديث واجهة البوت")], scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands([BotCommand("start", "تشغيل البوت"), BotCommand("admin", "لوحة الإدارة الرئيسية")], scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception: pass

# ==========================================================
# دالة التشغيل الرئيسية لـ البوت المطور
# ==========================================================

def main():
    if not TOKEN: raise RuntimeError("لم يتم العثور على متغير البيئة TELEGRAM_TOKEN")
    cleanup_old_downloads()

    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).connect_timeout(40).read_timeout(40).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)

    logger.info("🚀 البوت الذكي والمطور جاهز ومستقر للعمل بأعلى سرعة وتفاعلية...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
