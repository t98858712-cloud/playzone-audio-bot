import os
import re
import json
import time
import html
import uuid
import asyncio
import shutil
import logging
import threading
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, quote

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    MenuButtonCommands,
)
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
# الإعدادات الأساسية الفائقة والمصادر
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

PROGRESS_UPDATE_SECONDS = 2.0  
ACTIVE_USERS = set()

BOT_USERNAME = "@P1ay_Z0ne_Bot"

WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{BOT_USERNAME.replace('@', '')}"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PlayZoneEnterpriseBot")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

progress_lock = threading.Lock()

# ==========================================================
# إدارة قاعدة البيانات والبيانات الإحصائية
# ==========================================================

def load_json(path: Path, default):
    try:
        if not path.exists(): return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or default
    except Exception as e:
        logger.warning(f"فشل قراءة {path}: {e}")
        return default

def save_json(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"فشل حفظ البيانات: {e}")

def register_user(user):
    if not user: return
    data = load_json(USERS_FILE, {})
    old = data.get(str(user.id), {})
    data[str(user.id)] = {
        "id": user.id,
        "username": user.username or old.get("username", ""),
        "first_name": user.first_name or old.get("first_name", ""),
        "last_name": user.last_name or old.get("last_name", ""),
        "first_seen": old.get("first_seen", int(time.time())),
        "last_seen": int(time.time()),
    }
    save_json(USERS_FILE, data)

def all_user_ids():
    data = load_json(USERS_FILE, {})
    return [int(k) for k in data.keys() if str(k).isdigit()]

def load_stats():
    default = {"requests": 0, "success": 0, "failed": 0, "bytes": 0, "broadcasts": 0}
    data = load_json(STATS_FILE, default)
    for k, v in default.items(): data.setdefault(k, v)
    return data

def stat_inc(key: str, value: int = 1):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + value
    save_json(STATS_FILE, stats)

# ==========================================================
# أدوات الفحص والتنسيق الذكية
# ==========================================================

def is_admin(user_id: int) -> bool:
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return user_id in [int(i.strip()) for i in admin_ids_raw.split(",") if i.strip().isdigit()]

def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60) -> str:
    if not text: return "ملف ميديا"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except Exception: return "غير معروف"
    if seconds <= 0: return "00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text.strip())
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception: return False

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception: return ""

def get_artist(info: dict) -> str:
    for key in ["artist", "uploader", "channel", "creator"]:
        val = info.get(key)
        if val: return clean_title(val, 35)
    return "غير معروف"

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def get_largest_estimated_size(info: dict) -> int:
    sizes = []
    for f in info.get("formats", []) or []:
        try: sizes.append(int(f.get("filesize") or f.get("filesize_approx") or 0))
        except Exception: pass
    return max(sizes) if sizes else 0

def ensure_pending_requests(context: ContextTypes.DEFAULT_TYPE) -> dict:
    pending = context.user_data.setdefault("pending_requests", {})
    if not isinstance(pending, dict):
        pending = {}
        context.user_data["pending_requests"] = pending
    return pending

def make_request_id() -> str:
    return uuid.uuid4().hex[:10]

def trim_old_pending_requests(context: ContextTypes.DEFAULT_TYPE, max_items: int = 8):
    pending = ensure_pending_requests(context)
    if len(pending) <= max_items: return
    items = sorted(pending.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)
    context.user_data["pending_requests"] = dict(items[:max_items])

# ==========================================================
# صناعة الأزرار والواجهات الاحترافية
# ==========================================================

def user_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل الرابط هنا...",
    )

def build_preview_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{request_id}"),
                InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{request_id}"),
            ],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{request_id}")],
        ]
    )

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
            [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
            [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
        ]
    )

def build_playzone_links_text() -> str:
    return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")],
            [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")],
            [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")],
        ]
    )

def admin_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="adm_cancel_bc")]])

def build_start_text(first_name: str) -> str:
    return (
        f"أهلاً {esc(first_name)} 👋\n\n"
        "أرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n"
        "💚 دعمك يصنع الفرق\n\n"
        f"تابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\n"
        "كل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.\n\n"
        "ابدأ بإرسال الرابط مباشرة."
    )

def build_guide_text() -> str:
    return (
        "📘 طريقة الاستخدام\n\n"
        "1) انسخ رابط المقطع.\n"
        "2) أرسله هنا في البوت.\n"
        "3) انتظر ظهور المعاينة.\n"
        "4) اختر التحميل صوت أو فيديو."
    )

def build_preview_caption(title: str, artist: str, duration: str, est_size: str) -> str:
    return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"

def build_admin_stats_text() -> str:
    stats = load_stats()
    users_count = len(all_user_ids())
    return (
        "📊 إحصائيات البوت\n\n"
        f"• الطلبات الكلية: {stats.get('requests', 0)}\n"
        f"• التحميلات الناجحة: {stats.get('success', 0)}\n"
        f"• العمليات الفاشلة: {stats.get('failed', 0)}\n"
        f"• عدد المستخدمين: {users_count}\n"
        f"• حجم الملفات المرسلة: {format_size(stats.get('bytes', 0))}\n"
        f"• عدد الإذاعات: {stats.get('broadcasts', 0)}"
    )

def build_admin_users_text(limit: int = 10) -> str:
    data = load_json(USERS_FILE, {})
    users = list(data.values())
    users.sort(key=lambda u: u.get("last_seen", 0), reverse=True)
    lines = [f"👥 عدد المستخدمين: {len(users)}"]
    if users:
        lines.append("\nآخر المستخدمين:")
        for u in users[:limit]:
            name = u.get("first_name") or "بدون اسم"
            username = f"@{u.get('username')}" if u.get("username") else "لا يوجد"
            lines.append(f"• {esc(name)} — {esc(username)} — ID: {u.get('id')}")
    return "\n".join(lines)

def build_server_status_text() -> str:
    total_size = 0
    file_count = 0
    try:
        for p in BASE_DOWNLOAD_DIR.rglob("*"):
            if p.is_file():
                file_count += 1
                total_size += p.stat().st_size
    except Exception: pass
    return (
        "📁 حالة السيرفر\n\n"
        f"• مجلد التحميل: {BASE_DOWNLOAD_DIR}\n"
        f"• الملفات المؤقتة: {file_count}\n"
        f"• حجم الملفات المؤقتة: {format_size(total_size)}\n"
        f"• العمليات النشطة: {len(ACTIVE_USERS)}"
    )

# ==========================================================
# أدوات الرسائل الذكية الآمنة
# ==========================================================

async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def edit_message_smart(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try:
        if getattr(message, "photo", None) or getattr(message, "video", None) or getattr(message, "document", None):
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.debug(f"تخطي تحديث الرسالة: {e}")

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
        try:
            return await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"تم تخطي غلاف المعاينة، الإرسال نصياً: {e}")
    return await update.message.reply_text(text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# خيارات المحرك وفصل معالجة الصوت جذرياً
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video"):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "cachedir": False,
        "concurrent_fragment_downloads": 5, 
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
            "Connection": "keep-alive",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios"],
                "skip": ["webpage"],
            }
        },
    }
    
    if mode == "audio":
        # 🎯 [الحل الصارم]: نطلب الدفق الصوتي الأصلي الخام المتاح دون شروط دمج تمنع التحميل
        opts["format"] = "bestaudio/best"
    else:
        # نظام الهبوط التدريجي الديناميكي المتكامل للفيديو
        opts["format"] = (
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/"
            "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]/"
            "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4][height<=360]/"
            "bestvideo+bestaudio/best"
        )
        opts["merge_output_format"] = "mp4"

    cookies_path = Path(COOKIES_FILE)
    if cookies_path.exists() and cookies_path.stat().st_size > 0:
        try:
            with open(cookies_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "Netscape" in content or ".youtube.com" in content:
                opts["cookiefile"] = COOKIES_FILE
        except Exception: pass

    if job_dir:
        opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None:
        opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_hook(progress_data: dict):
    def hook(d):
        with progress_lock:
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                if total:
                    percent = downloaded / total * 100
                    progress_data["text"] = (
                        "📥 <b>جاري تحميل الملف...</b>\n\n"
                        f"{make_progress_bar(percent)}  {percent:.1f}%\n"
                        f"📦 الحجم: {format_size(downloaded)} / {format_size(total)}\n"
                        f"🚀 السرعة: {format_size(speed)}/ث"
                    )
                else:
                    progress_data["text"] = (
                        f"📥 جاري تحميل البيانات...\n\n"
                        f"📦 تم تحميل: {format_size(downloaded)}"
                    )
            elif d.get("status") == "finished":
                progress_data["text"] = "⚙️ اكتمل التحميل، جاري تجهيز الملف..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock:
            text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=None)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict):
    opts = get_ydl_options(job_dir, progress_data, mode)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# 🛠️ دالة التحويل المحلي الصارم باستخدام FFmpeg المستقل لمنع أخطاء السيرفرات
def convert_to_mp3_local(input_file: Path, output_file: Path) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_file),
            "-vn", "-ar", "44100", "-ac", "2", "-b:a", "320k",
            str(output_file)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return output_file.exists()
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")
        return False

# ==========================================================
# الأحداث وتفاعل المستخدم
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.message.reply_text(
        build_start_text(update.effective_user.first_name or ""),
        reply_markup=user_main_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    await update.message.reply_text("🛠 لوحة الإدارة", reply_markup=admin_main_keyboard())

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    context.user_data["bc_active"] = False
    users = all_user_ids()
    if not users:
        await update.message.reply_text("لا يوجد مستخدمون مسجلون.")
        return
    status = await update.message.reply_text("📢 جاري إرسال الرسالة للمستخدمين...")
    sent, fail = 0, 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
        except Exception: fail += 1
    stat_inc("broadcasts")
    await status.edit_text(f"✅ تم إرسال الإذاعة.\n\n• تم الإرسال: {sent}\n• فشل الإرسال: {fail}")

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    register_user(update.effective_user)
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text == "📘 دليل الاستخدام":
        await update.message.reply_text(build_guide_text(), disable_web_page_preview=True)
        return
    if text == "🔗 روابط PlayZone":
        await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)
        return
    if is_admin(uid) and context.user_data.get("bc_active"):
        await handle_broadcast_text(update, context, text)
        return
    if uid in ACTIVE_USERS:
        await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ.\n\nانتظر حتى يكتمل، ثم أرسل رابطاً جديداً.")
        return
    if not is_valid_url(text):
        await update.message.reply_text("❌ الرابط غير صحيح.\n\nأرسل رابط يبدأ بـ:\nhttp:// أو https://")
        return

    status = await update.message.reply_text("🔍 جاري فحص الرابط وتجهيز المعاينة...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: extract_metadata(text))

        title = clean_title(info.get("title"))
        artist = get_artist(info)
        duration_raw = info.get("duration") or 0
        duration = format_duration(duration_raw)
        est_size_raw = get_largest_estimated_size(info)
        est_size = format_size(est_size_raw)
        thumb = get_thumbnail(info)

        request_id = make_request_id()
        pending = ensure_pending_requests(context)
        pending[request_id] = {
            "url": text,
            "title": title,
            "artist": artist,
            "duration": duration_raw,
            "thumb_url": thumb,
            "created_at": int(time.time()),
        }
        trim_old_pending_requests(context)

        caption = build_preview_caption(title, artist, duration, est_size)
        keyboard = build_preview_keyboard(request_id)

        await safe_delete(status)
        await send_preview(update, thumb, caption, keyboard)
        stat_inc("requests")
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة السحابية: {e}")
        await status.edit_text("❌ تعذر قراءة الرابط.\n\nتأكد أن المقطع متاح للعامة وغير محذوف، ثم حاول مرة أخرى.")

# ==========================================================
# معالجة الأزرار التفاعلية وعمليات الإرسال المستقرة
# ==========================================================

async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    if data == "adm_close":
        await query.answer("تم الإغلاق")
        await safe_delete(query.message)
        return
    if data == "adm_stats":
        await query.answer()
        await query.message.edit_text(build_admin_stats_text(), reply_markup=admin_main_keyboard())
        return
    if data == "adm_users":
        await query.answer()
        await query.message.edit_text(build_admin_users_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
        return
    if data == "adm_server":
        await query.answer()
        await query.message.edit_text(build_server_status_text(), reply_markup=admin_main_keyboard())
        return
    if data == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة...")
        removed = 0
        try:
            for item in BASE_DOWNLOAD_DIR.iterdir():
                try:
                    if item.is_dir(): shutil.rmtree(item)
                    else: item.unlink()
                    removed += 1
                except Exception: pass
            await query.message.edit_text(f"🧹 تم تنظيف الملفات المؤقتة.\n\nالعناصر المحذوفة: {removed}", reply_markup=admin_main_keyboard())
        except Exception:
            await query.message.edit_text("⚠️ تعذر حذف بعض الملفات لأنها قيد الاستخدام حالياً.", reply_markup=admin_main_keyboard())
        return
    if data == "adm_bc":
        context.user_data["bc_active"] = True
        await query.answer()
        await query.message.edit_text("📢 أرسل نص الرسالة التي تريد إرسالها لجميع المستخدمين:", reply_markup=admin_broadcast_keyboard())
        return
    if data == "adm_cancel_bc":
        context.user_data["bc_active"] = False
        await query.answer("تم إلغاء الإذاعة")
        await query.message.edit_text("تم إلغاء العملية.", reply_markup=admin_main_keyboard())
        return

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    data = query.data or ""
    uid = query.from_user.id

    if data.startswith("adm_"):
        if not is_admin(uid):
            await query.answer("صلاحية إدارة فقط.", show_alert=True)
            return
        await handle_admin_callbacks(query, context)
        return
    if data.startswith("cancel:"):
        request_id = data.split(":", 1)[1]
        pending = ensure_pending_requests(context)
        pending.pop(request_id, None)
        await query.answer("تم إلغاء طلب التحميل")
        await safe_delete(query.message)
        return
    if data.startswith("aud:") or data.startswith("vid:"):
        mode = "audio" if data.startswith("aud:") else "video"
        request_id = data.split(":", 1)[1]
        pending = ensure_pending_requests(context)
        request = pending.get(request_id)
        if not request:
            await query.answer("انتهت جلسة هذا الطلب، يرجى إعادة إرسال الرابط.", show_alert=True)
            return
        if uid in ACTIVE_USERS:
            await query.answer("لديك تحميل قيد التنفيذ حالياً.", show_alert=True)
            return
        await start_download_from_callback(query, context, request, mode)
        return

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str):
    uid = query.from_user.id
    url = request.get("url")
    if not url:
        await query.answer("حدث خطأ في الطلب.", show_alert=True)
        return

    await query.answer("بدأ التحميل...")
    ACTIVE_USERS.add(uid)

    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)

    stop_event = asyncio.Event()
    progress_data = {"text": "⏳ جاري تجهيز التحميل..."}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: execute_download(url, mode, job_dir, progress_data))

        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        if not files: raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")

        raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)

        # ⚡ [المرحلة الثانية والحل الجذري للصوت]: إذا كان الطلب صوتياً، نقوم بالهندسة والتحويل المحلي فوراً
        if mode == "audio":
            with progress_lock:
                progress_data["text"] = "🎵 جاري تحويل الملف إلى MP3..."
            
            final_mp3_path = job_dir / "playzone_final_audio.mp3"
            success = await loop.run_in_executor(None, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path))
            
            if success and final_mp3_path.exists():
                target_file = final_mp3_path
            else:
                # خطة الطوارئ البديلة: إذا فشل التشفير لسبب ما، نرسل الملف الأصلي لكي لا ينقطع التحميل عن المستخدم
                target_file = raw_downloaded_file
        else:
            target_file = raw_downloaded_file

        file_size = target_file.stat().st_size

        if file_size > MAX_TELEGRAM_SIZE:
            stop_event.set()
            try: await updater_task
            except Exception: pass
            await edit_message_smart(
                query.message,
                f"❌ حجم الملف كبير جداً.\n\n"
                f"الحجم: {format_size(file_size)}\n"
                f"الحد المسموح: 50MB",
                reply_markup=None
            )
            return

        thumb_url = request.get("thumb_url")
        local_thumb = None
        if thumb_url and (thumb_url.startswith("http://") or thumb_url.startswith("https://")):
            try:
                local_thumb = job_dir / "playzone_secure_thumb.jpg"
                req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as response, open(local_thumb, "wb") as out_file:
                    out_file.write(response.read())
            except Exception: local_thumb = None

        stop_event.set()
        try: await updater_task
        except Exception: pass

        await edit_message_smart(query.message, "📤 تم تجهيز الملف، جاري الإرسال...", reply_markup=None)

        title = request.get("title", "ملف ميديا")
        duration = int(request.get("duration") or 0)
        caption = f"- @MusicPlayZoneBot، {esc(format_duration(duration))}"

        share_text = f"🎬 {title}"
        share_link = f"https://t.me/share/url?url={quote(url)}&text={quote(share_text)}"
        media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=share_link)]])

        try:
            action = ChatAction.UPLOAD_AUDIO if mode == "audio" else ChatAction.UPLOAD_VIDEO
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action=action)
        except Exception: pass

        with open(target_file, "rb") as f:
            if mode == "audio":
                t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                try:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        title=title,
                        performer=request.get("artist", "غير معروف"),
                        duration=duration,
                        caption=caption,
                        thumbnail=t_file,       
                        reply_markup=media_keyboard, 
                        parse_mode="HTML",
                    )
                finally:
                    if t_file: t_file.close()
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    duration=duration,
                    reply_markup=media_keyboard, 
                    parse_mode="HTML",
                )

        stat_inc("success")
        stat_inc("bytes", file_size)
        await safe_delete(query.message)

    except Exception as e:
        stat_inc("failed")
        logger.error(f"فشل نظام المعالجة أو الإرسال: {e}")
        try:
            await edit_message_smart(
                query.message,
                "❌ فشل تحميل المقطع.\n\n"
                "قد يكون الرابط غير متاح، أو توجد مشكلة مؤقتة في المنصة.\n"
                "حاول لاحقاً أو أرسل رابطاً آخر.",
                reply_markup=None
            )
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        try: shutil.rmtree(job_dir)
        except Exception: pass
        ACTIVE_USERS.discard(uid)

# ==========================================================
# طبقة التشغيل وهيكلة الأوامر لبوت PlayZone
# ==========================================================

async def set_bot_commands(app: Application):
    commands = [BotCommand("start", "بدء استخدام البوت"), BotCommand("links", "دعم روابط PlayZone")]
    try:
        await app.bot.set_my_commands(commands)
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e: logger.warning(f"فشل تهيئة أوامر قائمة تليجرام: {e}")

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")
    app = Application.builder().token(TOKEN).post_init(set_bot_commands).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("links", show_playzone_links))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم إطلاق نظام الفصل المعزول والتحويل المحلي الحاسم للصوت بنجاح!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
