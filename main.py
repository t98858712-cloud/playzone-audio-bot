import os
import re
import json
import time
import asyncio
import shutil
import logging
from pathlib import Path
from urllib.parse import urlparse, quote

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================================
# الإعدادات الأساسية والمصادر
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

PROGRESS_UPDATE_SECONDS = 1.5

ACTIVE_USERS = set()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("CleanDownloadBot")

# ==========================================================
# إدارة قاعدة البيانات والبيانات الإحصائية
# ==========================================================

def load_json(path: Path, default):
    try:
        if not path.exists(): return default
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e: logger.warning(f"فشل حفظ البيانات: {e}")

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
    default = {"requests": 0, "success": 0, "failed": 0, "bytes": 0}
    data = load_json(STATS_FILE, default)
    for k, v in default.items(): data.setdefault(k, v)
    return data

def stat_inc(key: str, value: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + value
    save_json(STATS_FILE, stats)

# ==========================================================
# أدوات الفحص والتنسيق المقتضب
# ==========================================================

def is_admin(user_id: int) -> bool:
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return user_id in [int(i.strip()) for i in admin_ids_raw.split(",") if i.strip().isdigit()]

def clean_title(text: str, limit=60) -> str:
    if not text: return "ملف ميديا"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    try: size_bytes = int(size_bytes)
    except: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    for unit in ['Bytes', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0: return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except: return "غير معروف"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except: return False

def get_platform(url: str) -> str:
    try: host = urlparse(url).netloc.lower().replace("www.", "")
    except: return "رابط مباشر"
    if "youtube" in host or "youtu.be" in host: return "YouTube"
    if "tiktok" in host: return "TikTok"
    if "instagram" in host: return "Instagram"
    if "facebook" in host or "fb.watch" in host: return "Facebook"
    if "soundcloud" in host: return "SoundCloud"
    return host.split('.')[0].capitalize()

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except: return ""

def get_artist(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator"]:
        val = info.get(key)
        if val: return clean_title(val, 35)
    return "غير معروف"

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

# ==========================================================
# صناعة الأزرار والواجهات
# ==========================================================

def user_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📘 دليل الاستخدام")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط مباشرة هنا..."
    )

def build_preview_keyboard(url: str, title: str) -> InlineKeyboardMarkup:
    # صناعة رابط مشاركة تيليجرام مباشر ونظيف دون تكديس
    share_text = f"🎙 استمع وشاهد: {title}"
    share_url = f"https://t.me/share/url?url={quote(url)}&text={quote(share_text)}"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 تحميل بصيغة MP3", callback_data="down_audio"),
         InlineKeyboardButton("🎬 تحميل بصيغة MP4", callback_data="down_video")],
        [InlineKeyboardButton("🔗 مشاركة المقطع مع صديق", url=share_url)],
        [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_request")]
    ])

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات العامة", callback_data="adm_stats"),
         InlineKeyboardButton("👥 عدد المشتركين", callback_data="adm_users")],
        [InlineKeyboardButton("📢 إذاعة تنبيه جماعي", callback_data="adm_bc"),
         InlineKeyboardButton("🧹 تفريغ ملفات الكاش", callback_data="adm_clean")]
    ])

# ==========================================================
# محرك التحميل السحابي (Fast Backend)
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 4, "fragment_retries": 4, "socket_timeout": 10, "cachedir": False,
        "extractor_args": {"youtube": {"player_client": ["ios", "android"], "skip": ["webpage"]}},
    }
    if job_dir: opts["outtmpl"] = str(job_dir / "%(title).50s.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    if Path(COOKIES_FILE).exists() and Path(COOKIES_FILE).stat().st_size > 0: opts["cookiefile"] = COOKIES_FILE
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_hook(progress_data: dict):
    def hook(d):
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            if total:
                percent = downloaded / total * 100
                progress_data["text"] = (
                    f"📥 **جاري جلب الملف حالياً...**\n\n"
                    f"{make_progress_bar(percent)}  {percent:.1f}%\n"
                    f"📦 الحجم: {format_size(downloaded)} / {format_size(total)}\n"
                    f"⚡ السرعة: {format_size(speed)}/ث"
                )
            else:
                progress_data["text"] = f"📥 جاري استقبال البيانات المستمرة: {format_size(downloaded)}"
        elif d.get("status") == "finished":
            progress_data["text"] = "⚙️ اكتمل التحميل الفعلي، جاري معالجة ورفع الملف..."
    return hook

async def run_progress_updates(query, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await query.message.edit_caption(caption=text, parse_mode="Markdown")
                last_text = text
            except: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict):
    opts = get_ydl_options(job_dir, progress_data)
    if mode == "audio": opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else: opts["format"] = "best[ext=mp4][height<=720][filesize<48M]/best"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# ==========================================================
# الأحداث وتفاعل المستخدم
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.message.reply_text(
        f"👋 **أهلاً بك يا {update.effective_user.first_name}**\n\n"
        f"💡 **طريقة الاستخدام:**\n"
        f"قم بنسخ رابط الفيديو أو الأغنية من أي منصة وأرسله هنا مباشرة، وسيتكفل البوت بالباقي.",
        reply_markup=user_main_keyboard(), parse_mode="Markdown"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    await update.message.reply_text("🛠 **لوحة تحكم الإدارة المغلقة:**", reply_markup=admin_main_keyboard())

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    register_user(update.effective_user)
    
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text == "📘 دليل الاستخدام":
        await update.message.reply_text("📖 فقط أرسل رابط المقطع (يوتيوب، إنستغرام، تيك توك) وسيقوم البوت بالتحليل وعرض خيارات التحميل الفورية.")
        return

    if is_admin(uid) and context.user_data.get("bc_active"):
        context.user_data["bc_active"] = False
        await update.message.reply_text("📢 جاري بدء البث الجماعي لكافة الأعضاء...")
        sent, fail = 0, 0
        for user_id in all_user_ids():
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
                sent += 1
                await asyncio.sleep(0.05)
            except: fail += 1
        await update.message.reply_text(f"✅ تم انتهاء البث.\n👍 ناجح: {sent}\n👎 فشل: {fail}")
        return

    if uid in ACTIVE_USERS:
        await update.message.reply_text("⏳ يرجى الانتظار، هناك عملية تحميل جارية لحسابك حالياً.")
        return

    if not is_valid_url(text):
        await update.message.reply_text("❌ الرابط المرسل غير صحيح، يرجى إرسال رابط مباشر وصالح.")
        return

    # فحص الرسالة لمنع التكديس والتنظيف التلقائي
    status = await update.message.reply_text("🔍 جاري قراءة بيانات الرابط...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_metadata(text))

        # استخراج البيانات المتناسقة دون مبالغة لفظية
        title = clean_title(info.get("title"))
        artist = get_artist(info)
        duration = format_duration(info.get("duration"))
        platform = get_platform(text)
        
        sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in info.get("formats", [])]
        est_size = format_size(max(sizes)) if sizes else "غير معروف"

        # حفظ الجلسة للخطوة القادمة
        context.user_data["current_url"] = text
        context.user_data["meta_title"] = title
        context.user_data["meta_artist"] = artist
        context.user_data["meta_duration"] = info.get("duration") or 0
        context.user_data["meta_platform"] = platform

        caption = (
            f"🎵 **اسم المقطع:** {title}\n"
            f"👤 **المغني/الناشر:** {artist}\n"
            f"⏱ **الوقت:** {duration}\n"
            f"📦 **الحجم المقدر:** {est_size}\n"
            f"🌐 **المنصة:** {platform}"
        )

        thumb = get_thumbnail(info)
        await status.delete() # حذف رسالة الفحص لمنع تراكم الرسائل

        if thumb:
            await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=build_preview_keyboard(text, title), parse_mode="Markdown")
        else:
            await update.message.reply_text(text=caption, reply_markup=build_preview_keyboard(text, title), parse_mode="Markdown")
        
        stat_inc("requests")
    except Exception as e:
        logger.warning(f"فشل التحليل: {e}")
        await status.edit_text("❌ تعذر جلب معلومات هذا الرابط تلقائياً، تأكد من أن المقطع عام وليس خاصاً.")

# ==========================================================
# التفاعل مع الأزرار والتحميل الذكي المباشر
# ==========================================================

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    # معالجة أزرار الآدمن أولاً لضمان الخصوصية والأمان
    if data.startswith("adm_") and is_admin(uid):
        await query.answer()
        stats = load_stats()
        if data == "adm_stats":
            await query.message.reply_text(f"📊 **إحصائيات النظام:**\n\n• الطلبات: {stats['requests']}\n• الناجحة: {stats['success']}\n• الفاشلة: {stats['failed']}\n• الباندويث: {format_size(stats['bytes'])}")
        elif data == "adm_users":
            await query.message.reply_text(f"👥 إجمالي المستخدمين في النظام: {len(all_user_ids())}")
        elif data == "adm_clean":
            try:
                for item in BASE_DOWNLOAD_DIR.iterdir(): shutil.rmtree(item) if item.is_dir() else item.unlink()
                await query.message.reply_text("🧹 تم مسح ملفات الكاش المؤقتة من السيرفر بالكامل.")
            except: await query.message.reply_text("⚠️ فشل تنظيف بعض الملفات النشطة.")
        elif data == "adm_bc":
            context.user_data["bc_active"] = True
            await query.message.reply_text("📢 أرسل نص الرسالة التي تريد بثها لجميع الأعضاء الآن:")
        return

    if data == "cancel_request":
        await query.answer("تم إلغاء العملية")
        try: await query.message.delete()
        except: pass
        return

    modes = {"down_audio": "audio", "down_video": "video"}
    if data not in modes: return

    url = context.user_data.get("current_url")
    if not url:
        await query.answer("⚠️ انتهت صلاحية الطلب، أرسل الرابط مجدداً.", show_alert=True)
        return

    if uid in ACTIVE_USERS:
        await query.answer("⏳ لديك عملية جارية بالفعل.", show_alert=True)
        return

    await query.answer("🚀 بدأت المعالجة الفورية...")
    ACTIVE_USERS.add(uid)

    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    
    stop_event = asyncio.Event()
    progress_data = {"text": "⏳ جاري الاتصال بالسيرفر وتجهيز الحزم..."}
    updater_task = asyncio.create_task(run_progress_updates(query, progress_data, stop_event))

    try:
        # كتم كرت الأزرار أثناء التحميل لمنع التلاعب المزدوج
        await query.message.edit_reply_markup(reply_markup=None)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: execute_download(url, modes[data], job_dir, progress_data))
        
        # البحث عن الملف الناتج
        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        if not files: raise RuntimeError()
        target_file = max(files, key=lambda p: p.stat().st_mtime)
        
        f_size = target_file.stat().st_size
        if f_size > MAX_TELEGRAM_SIZE:
            await query.message.edit_caption(caption="❌ تخطى حجم الملف 50 ميغابايت، وهو الحد الأقصى المسموح به للبوتات.")
            return

        stop_event.set()
        await updater_task
        
        await query.message.edit_caption(caption="📤 جاري النقل النهائي للملف إلى حسابك...")
        
        title = context.user_data.get("meta_title", "ملف ميديا")
        artist = context.user_data.get("meta_artist", "غير معروف")
        duration = context.user_data.get("meta_duration", 0)
        platform = context.user_data.get("meta_platform", "منصة خارجية")

        caption = f"✅ **تم التحميل بنجاح**\n• {title}\n• المصدر: {platform}"

        with open(target_file, "rb") as f:
            if modes[data] == "audio":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, title=title, performer=artist, duration=int(duration), caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=caption, supports_streaming=True, duration=int(duration), parse_mode="Markdown")

        stat_inc("success")
        stat_inc("bytes", f_size)
        try: await query.message.delete() # مسح كرت المعاينة والصورة بعد الإرسال لصفر تكديس في الشات
        except: pass

    except Exception as e:
        stat_inc("failed")
        logger.error(f"خطأ تحميل: {e}")
        try: await query.message.edit_caption(caption="❌ فشل تحميل المقطع، قد يكون محمي أو غير متاح في بلد السيرفر حالياً.")
        except: pass
    finally:
        stop_event.set()
        try: await updater_task
        except: pass
        try: shutil.rmtree(job_dir)
        except: pass
        ACTIVE_USERS.discard(uid)

# ==========================================================
# إقلاع النظام وتشغيل البوت
# ==========================================================

def main():
    if not TOKEN: raise RuntimeError("توكن البوت TELEGRAM_TOKEN مفقود!")
    
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🤖 انطلق البوت بنظام الواجهات النظيفة والسرعة العالية...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
