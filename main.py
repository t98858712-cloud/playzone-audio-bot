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
# إعدادات أساسية ومسارات النظام
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

REQUEST_EXPIRE_SECONDS = 20 * 60
OLD_DOWNLOADS_EXPIRE_SECONDS = 45 * 60
PROGRESS_UPDATE_SECONDS = 0.8  # تحديث فائق السرعة لمنح تأثير حركي للمؤشر

ACTIVE_USERS = set()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PlayZoneBot")

# ==========================================================
# إدارة البيانات وقاعدة البيانات المصغرة
# ==========================================================

def load_json(path: Path, default):
    try:
        if not path.exists(): return default
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e: logger.warning(f"تعذر حفظ البيانات: {e}")

def register_user(user):
    if not user: return
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
    return [int(k) for k in data.keys() if k.isdigit()]

def load_stats():
    default = {
        "requests": 0, "success": 0, "failed": 0,
        "audio": 0, "video": 0, "file": 0, "bytes": 0,
        "last_error": "", "broadcast_sent": 0, "broadcast_failed": 0,
    }
    data = load_json(STATS_FILE, default)
    for k, v in default.items(): data.setdefault(k, v)
    return data

def stat_inc(key: str, value: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + value
    save_json(STATS_FILE, stats)

def set_last_error(text: str):
    stats = load_stats()
    stats["last_error"] = text[:700]
    save_json(STATS_FILE, stats)

# ==========================================================
# الأدوات والمؤثرات البصرية وتنسيق النصوص
# ==========================================================

def is_admin(user_id: int) -> bool:
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return user_id in [int(i.strip()) for i in admin_ids_raw.split(",") if i.strip().isdigit()]

def safe_title(text: str, limit=75) -> str:
    if not text: return "ملف ميديا مجهز"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else (text or "ملف ميديا مجهز")

def format_size(size_bytes) -> str:
    try: size_bytes = int(size_bytes)
    except: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    for unit in ['Bytes', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0: return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_number(num) -> str:
    try:
        num = int(num)
        if num >= 1_000_000: return f"{num/1_000_000:.1f}M"
        if num >= 1_000: return f"{num/1_000:.1f}K"
        return str(num)
    except: return "⚡ متفاعل"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except: return "⏳ مباشر/غير معروف"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"⏱ {h}:{m:02d}:{s:02d}" if h else f"⏱ {m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except: return False

def platform_name_from_url(url: str) -> str:
    try: host = urlparse(url).netloc.lower().replace("www.", "")
    except: return "رابط ميديا 🌐"
    if "youtube" in host or "youtu.be" in host: return "YouTube 🟥"
    if "tiktok" in host: return "TikTok ⬛"
    if "instagram" in host: return "Instagram 🟪"
    if "facebook" in host or "fb.watch" in host: return "Facebook 🟦"
    if "x.com" in host or "twitter" in host: return "Twitter / X 🐦"
    if "soundcloud" in host: return "SoundCloud 🟧"
    return host.capitalize() + " 🌐"

def has_cookies_file() -> bool:
    return Path(COOKIES_FILE).exists() and Path(COOKIES_FILE).stat().st_size > 0

def make_job_dir(user_id: int) -> Path:
    d = BASE_DOWNLOAD_DIR / f"{user_id}_{int(time.time())}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def clean_job_dir(job_dir: Path):
    try:
        if job_dir and job_dir.exists(): shutil.rmtree(job_dir)
    except: pass

def cleanup_old_downloads():
    try:
        now = time.time()
        for item in BASE_DOWNLOAD_DIR.iterdir():
            if item.is_dir() and (now - item.stat().st_mtime) > OLD_DOWNLOADS_EXPIRE_SECONDS:
                shutil.rmtree(item, ignore_errors=True)
    except: pass

def find_downloaded_file(job_dir: Path):
    try:
        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
    except: return None

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except: return ""

def get_media_author(info: dict) -> str:
    for key in ["uploader", "channel", "artist", "creator"]:
        val = info.get(key)
        if val: return safe_title(val, 40)
    return "غير معروف"

def progress_bar(percent: float) -> str:
    percent = max(0, min(100, float(percent)))
    filled = int(percent // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

# ==========================================================
# لوحات التحكم والأزرار التفاعلية
# ==========================================================

def links_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🎮 PlayZone"), KeyboardButton("🌍 موقع PlayZone")],
         [KeyboardButton("🤖 بوت PlayZone"), KeyboardButton("👨‍💻 المطور")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل رابط المقطع هنا مباشرة..."
    )

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 ملف صوتي MP3 🔊", callback_data="download_audio"),
         InlineKeyboardButton("🎙 ريكورد صوتي 💬", callback_data="download_voice")],
        [InlineKeyboardButton("🎬 مقطع فيديو MP4 📺", callback_data="download_video")],
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel")]
    ])

def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحميل رابط آخر", callback_data="done")]])

def welcome_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("📘 دليل الاستخدام السريع", callback_data="user_help")]]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("🛠 لوحة تحكم الإدارة", callback_data="admin_open")])
    return InlineKeyboardMarkup(buttons)

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"), InlineKeyboardButton("👥 الأعضاء", callback_data="admin_users")],
        [InlineKeyboardButton("📢 بث تنبيه جماعي", callback_data="admin_broadcast"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="admin_clean")]
    ])

# ==========================================================
# المحرك الداخلي وإعدادات YT-DLP فائقة السرعة
# ==========================================================

def base_ydl_opts(job_dir: Path | None = None, progress_data: dict | None = None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 5, "fragment_retries": 5, "continuedl": True, "socket_timeout": 10,
        "cachedir": False, "windowsfilenames": True,
        "extractor_args": {"youtube": {"player_client": ["ios", "android"], "skip": ["webpage"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        }
    }
    if job_dir: opts["outtmpl"] = str(job_dir / "%(title).60s.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [progress_hook(progress_data)]
    if has_cookies_file(): opts["cookiefile"] = COOKIES_FILE
    return opts

def extract_info_fast(url: str):
    opts = base_ydl_opts()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def progress_hook(progress_data: dict):
    def hook(d):
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0
            if total:
                percent = downloaded / total * 100
                progress_data["text"] = (
                    f"📥 **جاري سحب ومعالجة حزم الميديا...**\n\n"
                    f"{progress_bar(percent)}  **{percent:.1f}%**\n\n"
                    f"📦 **الحجم:** {format_size(downloaded)} / {format_size(total)}\n"
                    f"⚡ **السرعة الحالية:** {format_size(speed)}/ثانية\n"
                    f"⏳ **الوقت المتبقي:** {eta} ثانية"
                )
            else:
                progress_data["text"] = f"📥 **جاري دمج البث الحي المستمر...**\n📦 **المستلم:** {format_size(downloaded)} | ⚡ {format_size(speed)}/ث"
        elif d.get("status") == "finished":
            progress_data["text"] = "⚙️ **اكتمل المعالجة الأساسية! جاري صقل وتجهيز الملف للرفع الفوري...**"
    return hook

async def progress_updater(status_message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await status_message.edit_text(text, parse_mode="Markdown")
                last_text = text
            except: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    opts = base_ydl_opts(job_dir, progress_data)
    if choice == "audio": opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "voice": opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "video": opts["format"] = "best[ext=mp4][height<=720][filesize<48M]/best[ext=mp4][height<=480]/best"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# ==========================================================
# الأحداث الأساسية ومعالجة الرسائل والروابط
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads()
    register_user(update.effective_user)
    uid = update.effective_user.id

    await update.message.reply_text(
        f"👋 **أهلاً بك يا {update.effective_user.first_name} في محرك التحميل الذكي الحركي!**\n\n"
        f"🚀 أرسل لي أي رابط فيديو أو صوت من أي منصة (يوتيوب، تيك توك، إنستغرام، وغيرها) وسأعرض لك معلوماته فوراً مع خيارات تحميل فائقة السرعة.",
        reply_markup=welcome_keyboard(uid), parse_mode="Markdown"
    )
    await update.message.reply_text("✨ تصفح روابط المنصة الرسمية من خلال الأزرار أدناه للوصول السريع والآمن:", reply_markup=links_reply_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cleanup_old_downloads()
    register_user(update.effective_user)

    uid = update.effective_user.id
    text = update.message.text.strip()

    # الردود التفاعلية السريعة للأزرار الثابتة
    replies = {
        "🎮 PlayZone": "🎮 **حساب PlayZone الرسمي على إنستغرام:**\nhttps://www.instagram.com/p1ay.zone",
        "🌍 موقع PlayZone": "🌍 **موقع الويب الرسمي لمنصتنا:**\nhttps://tasmg1.github.io/tasmg/",
        "🤖 بوت PlayZone": "🤖 **معرف البوت الرسمي الخاص بنا:**\n@P1ay_Z0ne_Bot",
        "👨‍💻 المطور": "👨‍💻 **حساب المطور الرسمي مباشرة:**\nhttps://www.instagram.com/ta_smg"
    }
    if text in replies:
        await update.message.reply_text(replies[text], disable_web_page_preview=True)
        return

    if uid in ACTIVE_USERS:
        await update.message.reply_text("⏳ **يرجى التريث! لديك عملية تحميل نشطة حالياً.** انتظر ثوانٍ حتى تنتهي لتحميل مقطع آخر.")
        return

    if not is_valid_url(text):
        await update.message.reply_text("❌ **عذراً، الرابط المرسل غير صالح أو غير مدعوم!** تأكد من نسخ الرابط بشكل كامل ثم أعد إرساله مرة أخرى.")
        return

    # بداية تأثيرات الفحص السريع والمعاينة
    status = await update.message.reply_text("🔍 **جاري فحص الرابط وقراءة البيانات سحابياً بلمح البصر...** ✨")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_info_fast(text))

        # حفظ الجلسة المؤقتة
        context.user_data["current_url"] = text
        context.user_data["created_at"] = time.time()
        context.user_data["p_title"] = info.get("title", "ملف ميديا مجهز")
        context.user_data["p_duration"] = info.get("duration") or 0
        context.user_data["p_author"] = get_media_author(info)
        context.user_data["p_platform"] = platform_name_from_url(text)

        stat_inc("requests", 1)
        thumb = get_thumbnail(info)

        caption = (
            f"🎬 **معلومات المقطع الجاهز للتحميل:**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **العنوان:** {safe_title(info.get('title'))}\n"
            f"👤 **الناشر:** {get_media_author(info)}\n"
            f"🌐 **المنصة:** {platform_name_from_url(text)}\n"
            f"📊 **المدة:** {format_duration(info.get('duration'))}\n"
            f"👁 **المشاهدات:** {format_number(info.get('view_count'))} | ❤️ **الإعجابات:** {format_number(info.get('like_count'))}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 اختر صيغة التحميل المفضلة لبدء المعالجة والرفع فوراً:"
        )

        try: await status.delete()
        except: pass

        if thumb:
            await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=download_keyboard(), parse_mode="Markdown")
        else:
            await update.message.reply_text(caption, reply_markup=download_keyboard(), parse_mode="Markdown")

    except Exception as e:
        stat_inc("requests", 1)
        logger.warning(f"خطأ استخراج سريع: {e}")
        # خيار السقوط الآمن السريع في حال تعذر جلب الصورة لضمان التحميل الفوري
        context.user_data["current_url"] = text
        context.user_data["created_at"] = time.time()
        context.user_data["p_title"] = "ملف ميديا"
        context.user_data["p_duration"] = 0
        context.user_data["p_author"] = "غير معروف"
        context.user_data["p_platform"] = platform_name_from_url(text)

        await status.edit_text(
            f"✅ **تم الاتصال بالرابط بنجاح!**\n\n🌐 **المصدر:** {platform_name_from_url(text)}\n\nاضغط على الصيغة المطلوبة في الأسفل لبدء التجهيز الفوري وتخطي قيود الفحص البصري:",
            reply_markup=download_keyboard(), parse_mode="Markdown"
        )

# ==========================================================
# معالجة تفاعلات الأزرار والرفع الفوري
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    register_user(query.from_user)
    data = query.data

    if data == "done":
        await query.answer("📥 أرسل طلبك الجديد الآن")
        await query.message.reply_text("📩 **أرسل رابط المقطع الجديد مباشرة وسأتولى الباقي:**")
        return
    if data == "user_help":
        await query.answer("📘 دليل الاستخدام")
        await query.message.reply_text("📘 **خطوات التحميل البسيطة:**\n\n1️⃣ انسخ رابط الفيديو من أي تطبيق.\n2️⃣ أرسل الرابط هنا مباشرة.\n3️⃣ اختر الصيغة وسيقوم البوت بإرسال الملف لك مجهز بالكامل وصالح للتشغيل المستمر.")
        return
    if data == "cancel":
        context.user_data.pop("current_url", None)
        await query.answer("❌ تم إلغاء العملية")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ **تم إلغاء الطلب بنجاح.** أنا مستعد لاستقبال أي روابط أخرى وقتما تشاء!")
        return

    choices = {"download_audio": "audio", "download_voice": "voice", "download_video": "video"}
    if data not in choices: return

    url = context.user_data.get("current_url")
    created_at = context.user_data.get("created_at", 0)

    if not url or (time.time() - created_at) > REQUEST_EXPIRE_SECONDS:
        await query.answer("⚠️ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط", show_alert=True)
        return

    await query.answer("🚀 انطلقت عملية المعالجة الفائقة...")
    await process_download(update, context, url, choices[data])

async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, choice: str):
    query = update.callback_query
    uid = query.from_user.id

    if uid in ACTIVE_USERS: return
    ACTIVE_USERS.add(uid)

    job_dir = None
    stop_event = asyncio.Event()
    updater_task = None
    status_message = None

    try:
        job_dir = make_job_dir(uid)
        status_message = await query.message.reply_text("⚡ **جاري تأسيس نفق سريع وربط السيرفر بالمنصة...**")
        progress_data = {"text": "⏳ **بدء تدفيق الحزم الرقمية الأولى للمقطع...**"}

        updater_task = asyncio.create_task(progress_updater(status_message, progress_data, stop_event))
        
        # تفعيل المؤشر الحركي للرفع أو الكتابة حسب الاختيار لزيادة التفاعل البصري
        action = ChatAction.UPLOAD_VIDEO if choice == "video" else ChatAction.UPLOAD_VOICE
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=action)

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: download_sync(url, choice, job_dir, progress_data))

        # جمع بيانات العرض النهائية
        title = safe_title(info.get("title") or context.user_data.get("p_title"))
        duration = info.get("duration") or context.user_data.get("p_duration")
        platform = context.user_data.get("p_platform") or platform_name_from_url(url)
        author = get_media_author(info) if isinstance(info, dict) else context.user_data.get("p_author")

        file_path = find_downloaded_file(job_dir)
        if not file_path or not file_path.exists(): raise RuntimeError("مجلد الحفظ فارغ")

        file_size = file_path.stat().st_size
        if file_size > MAX_TELEGRAM_SIZE:
            stop_event.set()
            if updater_task: await updater_task
            await status_message.edit_text(f"❌ **حجم الملف ({format_size(file_size)}) تجاوز الحد المسموح للبوتات المجانية (50MB).**\n\nيرجى محاولة اختيار صيغ صوتية أو مقاطع ذات مدة زمنية أقصر.")
            stat_inc("failed", 1)
            return

        stop_event.set()
        if updater_task: await updater_task

        await status_message.edit_text("📤 **اكتملت المعالجة الصافية! جاري دفع الملف إلى حسابك الآن...** ✨")
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=action)

        caption = (
            f"✅ **تم تجهيز وتحميل ملف الميديا الخاص بك بنجاح!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **الاسم:** {title}\n"
            f"🌐 **المصدر:** {platform}\n"
            f"⏱ **المدة:** {format_duration(duration)}\n"
            f"📦 **الحجم الصافي:** {format_size(file_size)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **بواسطة البوت الرسمي:** @P1ay_Z0ne_Bot"
        )

        with open(file_path, "rb") as f:
            if choice == "audio":
                await query.message.reply_audio(audio=f, title=title, performer=author, caption=caption, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("audio", 1)
            elif choice == "voice":
                await query.message.reply_voice(voice=f, caption=caption, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("audio", 1)
            elif choice == "video":
                await query.message.reply_video(video=f, caption=caption, supports_streaming=True, duration=int(duration) if duration else None, parse_mode="Markdown")
                stat_inc("video", 1)

        stat_inc("success", 1)
        stat_inc("bytes", file_size)
        await status_message.edit_text("🎉 **تم تسليم الملف بالكامل وصالح للتشغيل المباشر دائمًا!** ✨", reply_markup=done_keyboard())

    except Exception as e:
        stat_inc("failed", 1)
        err_msg = str(e)[:200]
        set_last_error(err_msg)
        logger.error(f"خطأ تحميل للمستخدم {uid}: {e}")
        try:
            await status_message.edit_text("❌ **حدث خطأ مفاجئ أثناء محاولة تحميل الميديا وتدقيقها.**\nتأكد من أن المقطع ليس خاصاً أو محمي بقيود العمر، ثم أعد المحاولة لاحقاً.")
        except: pass
    finally:
        stop_event.set()
        if updater_task:
            try: await updater_task
            except: pass
        if job_dir: clean_job_dir(job_dir)
        ACTIVE_USERS.discard(uid)

# ==========================================================
# دالة تشغيل وإقلاع النظام الشامل
# ==========================================================

def main():
    if not TOKEN: raise RuntimeError("متغير البيئة TELEGRAM_TOKEN مفقود بالكامل!")
    cleanup_old_downloads()

    app = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))

    logger.info("🚀 محرك البوت التفاعلي السريع انطلق ويعمل بكفاءة مطلقة الآن...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
