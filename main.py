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

FAST_LINK_CHECK = os.getenv("FAST_LINK_CHECK", "false").lower() == "true"

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
# إدارة التخزين والكوكيز
# ==========================================================

def has_cookies_file() -> bool:
    """يتحقق من وجود الكوكيز في الملف الرئيسي أو في متغيرات البيئة"""
    env_cookies = os.getenv("COOKIES_CONTENT")
    if env_cookies:
        try:
            with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                f.write(env_cookies.strip())
            return True
        except Exception as e:
            logger.warning(f"تعذر كتابة كوكيز البيئة: {e}")
            
    path = Path(COOKIES_FILE)
    return path.exists() and path.is_file() and path.stat().st_size > 0

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
    default = {"requests": 0, "success": 0, "failed": 0, "audio": 0, "video": 0, "file": 0, "bytes": 0, "last_error": "", "broadcast_sent": 0, "broadcast_failed": 0}
    data = load_json(STATS_FILE, default)
    for k, v in default.items(): data.setdefault(k, v)
    return data

def stat_inc(key: str, value: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + value
    save_stats(stats)

def save_stats(stats): save_json(STATS_FILE, stats)
def set_last_error(text: str):
    stats = load_stats()
    stats["last_error"] = safe_text(text, 700)
    save_stats(stats)

# ==========================================================
# أدوات مساعدة وتجهيز النصوص والصور
# ==========================================================

def is_admin(user_id: int) -> bool: return user_id in ADMIN_IDS
def safe_text(text, limit=3500): text = str(text or ""); return text[:limit] + "..." if len(text) > limit else text
def short_error(e: Exception) -> str: return safe_text(re.sub(r"\s+", " ", str(e)).strip(), 900)
def is_valid_url(text: str) -> bool:
    try: parsed = urlparse(text.strip()); return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception: return False

def is_youtube_url(url: str) -> bool: return "youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url

def safe_title(text: str, limit=90) -> str:
    if not text: return "ملف ميديا"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() if len(text) > limit else (text or "ملف ميديا")

def format_size(size_bytes) -> str:
    try: sb = int(size_bytes)
    except Exception: return "غير معروف"
    if sb <= 0: return "غير معروف"
    for unit in ['Bytes', 'KB', 'MB', 'GB']:
        if sb < 1024.0: return f"{sb:.1f} {unit}"
        sb /= 1024.0
    return f"{sb:.1f} TB"

def format_duration(seconds) -> str:
    try: s = int(seconds)
    except Exception: return "غير معروف"
    if s <= 0: return "غير معروف"
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def platform_name_from_url(url: str) -> str:
    try: host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception: return "رابط خارجي"
    for p in ["youtube", "tiktok", "instagram", "facebook", "soundcloud"]:
        if p in host: return p.capitalize()
    return "X" if "x.com" in host or "twitter" in host else (host or "رابط")

def get_thumbnail(info: dict) -> str:
    if not info: return ""
    thumbs = info.get("thumbnails") or []
    if thumbs:
        try:
            valid_thumbs = [t for t in thumbs if t.get("url")]
            if valid_thumbs:
                best = sorted(valid_thumbs, key=lambda x: int(x.get("width") or 0) * int(x.get("height") or 0), reverse=True)[0]
                return best.get("url") or ""
        except Exception: pass
    return info.get("thumbnail") or ""

def get_media_author(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator", "playlist_uploader"]:
        val = info.get(key)
        if val: return safe_title(val, 60)
    return "غير معروف"

def build_preview_caption(info: dict, url: str) -> str:
    title = safe_title(info.get("title", "ملف ميديا"), 90)
    duration = format_duration(info.get("duration"))
    author = get_media_author(info)
    platform = safe_title(info.get("extractor_key") or platform_name_from_url(url), 40)
    return (
        f"🎬 *{title}*\n\n"
        f"👤 *الناشر:* {author}\n"
        f"🌐 *المنصة:* {platform}\n"
        f"⏱️ *المدة:* {duration}\n\n"
        "📥 *اختر نوع جودة التحميل المطلوبة من الأسفل:*"
    )

def make_job_dir(user_id: int) -> Path:
    jd = BASE_DOWNLOAD_DIR / f"{user_id}_{int(time.time())}"
    jd.mkdir(parents=True, exist_ok=True)
    return jd

def clean_job_dir(job_dir: Path):
    try:
        if job_dir and job_dir.exists(): shutil.rmtree(job_dir)
    except Exception as e: logger.warning(f"خطأ تنظيف: {e}")

def cleanup_old_downloads():
    now = time.time()
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            if item.exists() and (now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS):
                if item.is_dir(): shutil.rmtree(item, ignore_errors=True)
                else: item.unlink(missing_ok=True)
    except Exception as e: logger.warning(f"تنظيف قديم: {e}")

def find_downloaded_file(job_dir: Path):
    try:
        valid = [p for p in job_dir.iterdir() if p.is_file() and not p.name.endswith(('.part', '.tmp', '.ytdl'))]
        return max(valid, key=lambda p: p.stat().st_mtime) if valid else None
    except Exception: return None

async def safe_edit(message, text: str, reply_markup=None):
    try: await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except RetryAfter as e: await asyncio.sleep(int(e.retry_after) + 1)
    except BadRequest as e:
        if "Message is not modified" not in str(e): logger.warning(f"تعديل رسالة: {e}")
    except Exception as e: logger.warning(f"تعديل رسالة خطأ عام: {e}")

async def remember_ui_message(context: ContextTypes.DEFAULT_TYPE, message_id: int): context.user_data["last_ui_message_id"] = message_id
async def delete_previous_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    old_id = context.user_data.get("last_ui_message_id")
    if chat and old_id:
        try: await context.bot.delete_message(chat_id=chat.id, message_id=int(old_id))
        except Exception: pass

async def send_clean_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if not update.message: return None
    await delete_previous_ui(update, context)
    msg = await update.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    await remember_ui_message(context, msg.message_id)
    return msg

async def edit_or_send(query, text: str, reply_markup=None):
    try: await query.edit_message_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception: await query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)

# ==========================================================
# قوائم الأزرار التفاعلية (Keyboards)
# ==========================================================

def links_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🌐 موقع PlayZone"), KeyboardButton("🤖 بوت PlayZone")],
         [KeyboardButton("🎮 PlayZone"), KeyboardButton("👨‍💻 المطور")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل رابط التحميل هنا..."
    )

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 ملف صوتي عالي الجودة", callback_data="download_audio"),
         InlineKeyboardButton("🎙 مقطع صوتي", callback_data="download_voice")],
        [InlineKeyboardButton("🎬 مقطع فيديو مناسب", callback_data="download_video"),
         InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])

def done_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("🔁 أرسل رابط جديد", callback_data="done")]])
def back_home_keyboard(user_id: int) -> InlineKeyboardMarkup: return admin_welcome_keyboard() if is_admin(user_id) else welcome_keyboard()
def welcome_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("📘 طريقة الاستخدام", callback_data="user_help")]])
def admin_welcome_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("📘 طريقة الاستخدام", callback_data="user_help")], [InlineKeyboardButton("🛠 لوحة الأدمن", callback_data="admin_open")]])
def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"), InlineKeyboardButton("👥 المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("📥 النشط", callback_data="admin_active"), InlineKeyboardButton("🍪 الكوكيز", callback_data="admin_cookies")],
        [InlineKeyboardButton("📢 إرسال تنبيه", callback_data="admin_broadcast"), InlineKeyboardButton("🧹 تنظيف", callback_data="admin_clean")]
    ])
def broadcast_confirm_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("✅ إرسال الآن", callback_data="broadcast_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="broadcast_cancel")]])

# ==========================================================
# محرك إعدادات yt-dlp الذكي لحل الحظر
# ==========================================================

def get_combined_ydl_opts(url: str, choice: str = None, job_dir: Path = None, progress_data: dict = None) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "ignoreerrors": False, "retries": 10, "fragment_retries": 10, "continuedl": True,
        "socket_timeout": 30, "cachedir": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        },
    }

    if is_youtube_url(url):
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"], "skip": ["dash", "hls"]}}

    if has_cookies_file():
        opts["cookiefile"] = COOKIES_FILE

    if choice is None:
        opts["skip_download"] = True
        return opts

    if job_dir: opts["outtmpl"] = str(job_dir / "%(title).80s.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [progress_hook(progress_data)]

    if choice == "audio": opts["format"] = "bestaudio[acodec*=opus][abr>=128]/bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "voice": opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif choice == "video": opts["format"] = "best[ext=mp4][height<=480]/best[ext=mp4]/best"
    
    return opts

def extract_info_sync(url: str):
    with yt_dlp.YoutubeDL(get_combined_ydl_opts(url)) as ydl:
        return ydl.extract_info(url, download=False)

def progress_hook(progress_data: dict):
    def hook(d):
        try:
            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                percent = (downloaded / total * 100) if total else 0
                progress_data["text"] = f"📥 جاري التحميل...\n\n{progress_bar(percent)} {percent:.1f}%\n⚡ السرعة: {format_size(speed)}/s"
            elif status == "finished":
                progress_data["text"] = "⚙️ تم التحميل الفعلي، جاري تجهيز ورفع الملف..."
        except Exception: pass
    return hook

def progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, percent)) // 10)
    return "█" * filled + "░" * (10 - filled)

async def progress_updater(status_message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        text = progress_data.get("text", "")
        if text and text != last_text:
            await safe_edit(status_message, text)
            last_text = text
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def download_sync(url: str, choice: str, job_dir: Path, progress_data: dict):
    with yt_dlp.YoutubeDL(get_combined_ydl_opts(url, choice, job_dir, progress_data)) as ydl:
        return ydl.extract_info(url, download=True)

# ==========================================================
# معالجة الرسائل والاتصال التفاعلي
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_downloads(); register_user(update.effective_user)
    await send_clean_message(update, context, "👋 أهلاً بك في بوت التحميل الشامل.\n\nأرسل الرابط مباشرة، وستظهر خيارات ومعاينة التحميل فوراً.", reply_markup=back_home_keyboard(update.effective_user.id))
    await update.message.reply_text("🔗 أزرار التصفح السريع متوفرة بالأسفل:", reply_markup=links_reply_keyboard())

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await send_clean_message(update, context, "🛠 لوحة الإدارة الضرورية:", reply_markup=admin_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cleanup_old_downloads(); register_user(update.effective_user)
    
    user_id = update.effective_user.id
    text = update.message.text.strip()

    links = {
        "🌐 موقع PlayZone": "https://tasmg1.github.io/tasmg/",
        "🤖 بوت PlayZone": "https://t.me/P1ay_Z0ne_Bot",
        "🎮 PlayZone": "https://www.instagram.com/p1ay.zone?igsh=MWpjdGpodGRqeXdwdg==",
        "👨‍💻 المطور": "https://www.instagram.com/ta_smg?igsh=aTB5dTJzdmRtaTA4&utm_source=qr"
    }
    if text in links:
        await update.message.reply_text(f"{text}:\n{links[text]}", disable_web_page_preview=True)
        return

    if is_admin(user_id) and context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False; context.user_data["broadcast_text"] = text
        await update.message.reply_text("📢 هل تود تأكيد بث هذه الرسالة للجميع؟", reply_markup=broadcast_confirm_keyboard())
        return

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ لديك تحميل يعمل حالياً، انتظر من فضلك...")
        return

    if not is_valid_url(text):
        await update.message.reply_text("❌ عذراً، أرسل رابطاً صالحاً يبدأ بـ http أو https.")
        return

    context.user_data["current_url"] = text
    context.user_data["created_at"] = time.time()
    stat_inc("requests", 1)

    status_msg = await update.message.reply_text("🔍 جاري جلب تفاصيل الرابط الفنية والصورة...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_info_sync(text))
        
        context.user_data["preview_title"] = safe_title(info.get("title", "ملف ميديا"))
        context.user_data["preview_duration"] = info.get("duration") or 0
        context.user_data["preview_author"] = get_media_author(info)
        context.user_data["preview_platform"] = safe_title(info.get("extractor_key") or platform_name_from_url(text), 40)
        
        thumb_url = get_thumbnail(info)
        caption = build_preview_caption(info, text)
        
        await delete_previous_ui(update, context)
        await status_msg.delete()

        if thumb_url:
            try:
                msg = await update.message.reply_photo(photo=thumb_url, caption=caption, reply_markup=download_keyboard())
                await remember_ui_message(context, msg.message_id)
                return
            except Exception as e:
                logger.warning(f"فشل إرسال صورة المعاينة: {e}")

        msg = await update.message.reply_text(caption, reply_markup=download_keyboard())
        await remember_ui_message(context, msg.message_id)

    except Exception as e:
        err = short_error(e)
        set_last_error(err)
        await safe_edit(status_msg, f"⚠️ لم نتمكن من جلب صورة المعاينة بسبب قيود خادم الحماية، ولكن يمكنك محاولة البدء بالتنزيل المباشر الآن:", reply_markup=download_keyboard())

# ==========================================================
# معالجة الأزرار التفاعلية وتحميل الملفات ورفعها
# ==========================================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data or ""

    if data == "admin_open":
        await edit_or_send(query, "🛠 لوحة الإدارة الضرورية:", reply_markup=admin_keyboard()); return
    if data == "done":
         await edit_or_send(query, "📩 تفضل بإرسال الرابط الجديد الآن:"); return
    if data == "user_help":
        await edit_or_send(query, "📘 طريقة الاستخدام بسيطة:\nأرسل أي رابط ميديا مباشر، اختر صيغتك، وسيتم التحميل تلقائياً.", reply_markup=back_home_keyboard(query.from_user.id)); return
    if data == "cancel":
        context.user_data.clear(); await edit_or_send(query, "✅ تم إلغاء العملية بنجاح.", reply_markup=back_home_keyboard(query.from_user.id)); return
    if data.startswith("admin_"): await handle_admin_button(update, context); return
    if data in ["broadcast_confirm", "broadcast_cancel"]: await handle_broadcast_button(update, context); return

    choices = {"download_audio": "audio", "download_voice": "voice", "download_video": "video"}
    if data not in choices: return

    url = context.user_data.get("current_url")
    if not url or (time.time() - context.user_data.get("created_at", 0) > REQUEST_EXPIRE_SECONDS):
        await query.message.reply_text("⏱️ انتهت صلاحية الجلسة، الرجاء إعادة إرسال الرابط.")
        return

    await process_download(update, context, url, choices[data])

async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data = query.data; stats = load_stats()
    if data == "admin_stats":
        await query.message.reply_text(f"📊 إحصائيات البوت:\nالطلبات: {stats.get('requests')}\nالناجحة: {stats.get('success')}\nالفاشلة: {stats.get('failed')}\nالحجم المستهلك: {format_size(stats.get('bytes'))}")
    elif data == "admin_users": await query.message.reply_text(f"👥 عدد المستخدمين المسجلين: {len(all_user_ids())}")
    elif data == "admin_active": await query.message.reply_text(f"📥 التحميلات النشطة الآن: {len(ACTIVE_USERS)}")
    elif data == "admin_cookies": await query.message.reply_text(f"🍪 حالة ملف الكوكيز: {'شغال ومفعّل ✅' if has_cookies_file() else 'غير متوفر ⚠️'}")
    elif data == "admin_clean": cleanup_old_downloads(); await query.message.reply_text("🧹 تم مسح الملفات المؤقتة.")
    elif data == "admin_broadcast": context.user_data["awaiting_broadcast"] = True; await query.message.reply_text("📢 أرسل نص الإذاعة الآن:")

async def handle_broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; text = context.user_data.get("broadcast_text")
    if query.data == "broadcast_confirm" and text:
        await query.message.reply_text("📢 جاري الإرسال...")
        s, f = 0, 0
        for uid in all_user_ids():
            try: await context.bot.send_message(chat_id=uid, text=text); s += 1; await asyncio.sleep(0.05)
            except Exception: f += 1
        await query.message.reply_text(f"✅ اكتمل البث.\nنجح: {s}\nفشل: {f}")
    else: await query.message.reply_text("❌ تم إلغاء عملية البث.")
    context.user_data.pop("broadcast_text", None)

async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, choice: str):
    query = update.callback_query; user_id = query.from_user.id
    ACTIVE_USERS.add(user_id)
    
    job_dir = make_job_dir(user_id)
    stop_event = asyncio.Event()
    progress_data = {"text": "⏳ جاري بدء سحب وتنزيل بيانات الملف..."}
    status_msg = await query.message.reply_text("⚡ جاري تهيئة معالجة الرابط البرمجية...")

    updater_task = asyncio.create_task(progress_updater(status_msg, progress_data, stop_event))

    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: download_sync(url, choice, job_dir, progress_data))
        
        title = safe_title(info.get("title") if isinstance(info, dict) else context.user_data.get("preview_title", "ملف ميديا"))
        duration = info.get("duration") if isinstance(info, dict) else context.user_data.get("preview_duration", 0)
        author = get_media_author(info) if isinstance(info, dict) else context.user_data.get("preview_author", "غير معروف")
        
        file_path = find_downloaded_file(job_dir)
        if not file_path or not file_path.exists(): raise RuntimeError("لم يتم العثور على الملف على قرص السيرفر.")

        file_size = file_path.stat().st_size
        if file_size > MAX_TELEGRAM_SIZE:
            await safe_edit(status_msg, f"❌ حجم الملف الناتج ({format_size(file_size)}) يتجاوز الحد المسموح للبوتات العادية.")
            return

        stop_event.set(); await updater_task
        await safe_edit(status_msg, "📤 جاري رفع الملف الآن إلى تيليجرام...")

        caption = f"✅ تم التحميل بنجاح\n📌 {title}\n⏱️ {format_duration(duration)}\n📦 {format_size(file_size)}"
        
        with open(file_path, "rb") as f:
            if choice == "audio":
                await query.message.reply_audio(audio=f, title=title, performer=author, caption=caption, duration=int(duration) if duration else None)
            elif choice == "voice":
                await query.message.reply_voice(voice=f, caption=caption, duration=int(duration) if duration else None)
            elif choice == "video":
                await query.message.reply_video(video=f, caption=caption, supports_streaming=True, duration=int(duration) if duration else None)

        stat_inc("success"); stat_inc("bytes", file_size)
        await safe_edit(status_msg, "✅ تم تسليم الملف وإرساله بنجاح!", reply_markup=done_keyboard())

    except Exception as e:
        stop_event.set()
        err = short_error(e)
        set_last_error(err)
        await safe_edit(status_msg, f"❌ فشل تحميل الرابط المباشر.\n\nالسبب المحتمل: حظر مؤقت من المنصة. الرجاء محاولة تحديث الـ Cookies أو استعمال رابط آخر لاحقاً.", reply_markup=done_keyboard())
    finally:
        stop_event.set()
        clean_job_dir(job_dir)
        ACTIVE_USERS.discard(user_id)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💡 اضغط /start للبدء بالاستخدام المباشر.", reply_markup=welcome_keyboard())

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطأ داخلي بالتطبيق:", exc_info=context.error)

async def setup_bot_commands(app):
    await app.bot.set_my_commands([BotCommand("start", "بدء تشغيل واستخدام البوت")], scope=BotCommandScopeDefault())

def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN غير موجود بالبيئة.")
    cleanup_old_downloads()
    
    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).connect_timeout(30).read_timeout(30).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)

    logger.info("🚀 تم تشغيل البوت وإصلاح كافة مشاكل المعاينة والتحميل بنجاح!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
