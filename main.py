import os, re, time, html, uuid, asyncio, shutil, sqlite3, logging, threading, subprocess, urllib.request, ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# إعدادات PlayZone / Railway
# ==========================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL")
BASE_DOWNLOAD_DIR, DATA_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")), Path(os.getenv("DATA_DIR", "./data"))
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True); DATA_DIR.mkdir(exist_ok=True)

DB_FILE, DB_LOCK = DATA_DIR / "bot_database.db", threading.Lock()
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", (2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024)))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))

PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))
REQUEST_EXPIRE_SECONDS = int(os.getenv("REQUEST_EXPIRE_SECONDS", "900"))
OLD_DOWNLOADS_EXPIRE_SECONDS = int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", "3600"))
MAX_THUMBNAIL_BYTES = int(os.getenv("MAX_THUMBNAIL_BYTES", "2097152"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2)))

DOWNLOAD_SEMAPHORE, EXECUTOR = asyncio.Semaphore(MAX_WORKERS), ThreadPoolExecutor(max_workers=max(2, MAX_WORKERS))
ACTIVE_USERS, progress_lock = set(), threading.Lock()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{BOT_USERNAME.replace('@', '')}"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")
for noisy_logger in ["httpx", "httpcore", "telegram", "telegram.ext"]: logging.getLogger(noisy_logger).setLevel(logging.WARNING)

# ==========================================================
# إدارة قاعدة البيانات (SQLite3 WAL Mode)
# ==========================================================
def init_db():
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER);
            CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER);
        """)
        conn.executemany("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", [(k,) for k in ["requests", "success", "failed", "bytes", "broadcasts"]])

def register_user_sync(user):
    if not user: return
    now = int(time.time())
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        first_seen = (conn.execute("SELECT first_seen FROM users WHERE id = ?", (user.id,)).fetchone() or [now])[0]
        conn.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)", (user.id, user.username or "", user.first_name or "", user.last_name or "", first_seen, now))

def stat_inc_sync(key: str, value: int = 1):
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: conn.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))

def load_stats_sync() -> dict:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: return dict(conn.execute("SELECT key, value FROM stats").fetchall())

def all_user_ids() -> list:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: return [r[0] for r in conn.execute("SELECT id FROM users").fetchall()]

def get_latest_users(limit: int = 10) -> list:
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()]

# ==========================================================
# أدوات الفحص والتنسيق
# ==========================================================
def parse_admin_ids(): return {int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()}
def is_admin(user_id: int) -> bool: return user_id in parse_admin_ids()
def esc(text) -> str: return html.escape(str(text or ""), quote=False)
def clean_title(text: str, limit=60) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]+", "", str(text or "ملف ميديا"))).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    try:
        s = float(size_bytes)
        if s <= 0: return "غير معروف"
        for unit in ["Bytes", "KB", "MB", "GB"]:
            if s < 1024.0: return f"{int(s)} {unit}" if s.is_integer() else f"{s:.1f} {unit}"
            s /= 1024.0
        return f"{s:.1f} GB"
    except Exception: return "غير معروف"

def format_duration(seconds) -> str:
    try:
        s = int(seconds)
        if s <= 0: return "00:00"
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
    except Exception: return "غير معروف"

def is_public_host(host: str) -> bool:
    if (host := (host or "").strip().lower()) in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}: return False
    try: return not (ip := ipaddress.ip_address(host)).is_private and not ip.is_loopback and not ip.is_link_local
    except ValueError: return True

def is_valid_url(text: str) -> bool:
    try:
        if len(text := (text or "").strip()) > 2000: return False
        parsed = urlparse(text)
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc) and not parsed.username and is_public_host(parsed.hostname)
    except Exception: return False

def get_thumbnail(info: dict) -> str:
    try:
        if thumbs := info.get("thumbnails"): return max(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)).get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception: return ""

def get_artist(info: dict) -> str:
    return next((clean_title(info[k], 35) for k in ["artist", "uploader", "channel", "creator"] if info.get(k)), "غير معروف")

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def get_largest_estimated_size(info: dict) -> int:
    return max([int(f.get("filesize") or f.get("filesize_approx") or 0) for f in info.get("formats", [])], default=0)

def ensure_pending_requests(context: ContextTypes.DEFAULT_TYPE) -> dict: return context.user_data.setdefault("pending_requests", {})

def trim_old_pending_requests(context: ContextTypes.DEFAULT_TYPE, max_items: int = 8):
    pending, now = ensure_pending_requests(context), int(time.time())
    for rid, item in list(pending.items()):
        if now - int(item.get("created_at", 0)) > REQUEST_EXPIRE_SECONDS: pending.pop(rid, None)
    if len(pending) > max_items: context.user_data["pending_requests"] = dict(sorted(pending.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:max_items])

def cookie_file_is_usable(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0: return False
        now, has_yt, has_valid = int(time.time()), False, False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip() or line.startswith("#"): continue
                parts = line.strip().split("\t")
                if len(parts) < 7: continue
                has_yt = has_yt or "youtube.com" in parts[0]
                exp = int(parts[4]) if parts[4].isdigit() else 0
                if parts[6].strip() and (exp == 0 or exp > now): has_valid = True
        return has_yt and has_valid
    except Exception: return False

def _cleanup_old_downloads_sync():
    now = time.time()
    for item in BASE_DOWNLOAD_DIR.iterdir():
        if now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS: shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)

def _force_cleanup_all_sync() -> int:
    removed = 0
    for item in BASE_DOWNLOAD_DIR.iterdir():
        shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
        removed += 1
    return removed

# ==========================================================
# الواجهات والأزرار
# ==========================================================
def user_main_keyboard(): return ReplyKeyboardMarkup([["📘 دليل الاستخدام"], ["🔗 روابط PlayZone"]], resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط هنا...")
def build_preview_keyboard(rid: str): return InlineKeyboardMarkup([[InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{rid}"), InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{rid}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{rid}")]])
def build_playzone_links_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)], [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)], [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)]])
def build_playzone_links_text(): return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."
def admin_main_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")], [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")], [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]])
def admin_broadcast_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="adm_cancel_bc")]])
def build_start_text(first_name: str): return f"أهلاً {esc(first_name)} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n{build_playzone_links_text()}\n\nابدأ بإرسال الرابط مباشرة."
def build_guide_text(): return "📘 طريقة الاستخدام\n\n1) انسخ رابط المقطع.\n2) أرسله هنا في البوت.\n3) انتظر ظهور المعاينة.\n4) اختر التحميل صوت أو فيديو."
def build_preview_caption(title: str, artist: str, duration: str, est_size: str): return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"

def build_admin_stats_text():
    s = load_stats_sync()
    return f"📊 <b>إحصائيات البوت</b>\n\n• الطلبات الكلية: {s.get('requests',0)}\n• التحميلات الناجحة: {s.get('success',0)}\n• العمليات الفاشلة: {s.get('failed',0)}\n• عدد المستخدمين: {len(all_user_ids())}\n• حجم الملفات المرسلة: {format_size(s.get('bytes',0))}\n• عدد الإذاعات: {s.get('broadcasts',0)}"

def build_admin_users_text(limit=10):
    return "\n".join(["👥 <b>آخر المستخدمين النشطين:</b>"] + [f"• {esc(u.get('first_name') or 'بدون اسم')} — @{esc(u.get('username') or 'لا يوجد')} — ID: <code>{u.get('id')}</code>" for u in get_latest_users(limit)])

def build_server_status_text():
    files = list(BASE_DOWNLOAD_DIR.rglob("*"))
    return f"📁 <b>حالة السيرفر</b>\n\n• مجلد التحميل: <code>{BASE_DOWNLOAD_DIR}</code>\n• الملفات المؤقتة: {sum(1 for p in files if p.is_file())}\n• الحجم المؤقت: {format_size(sum(p.stat().st_size for p in files if p.is_file()))}\n• العمليات النشطة: {len(ACTIVE_USERS)}\n• الحد الأقصى المتزامن: {MAX_WORKERS}"

# ==========================================================
# الرسائل الآمنة
# ==========================================================
async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def edit_message_smart(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try:
        await (message.edit_caption if any(getattr(message, k, None) for k in ["photo", "video", "document"]) else message.edit_text)(caption=text, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise
    except Exception: pass

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    if thumb and thumb.startswith("http"):
        try: return await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        except Exception: pass
    return await update.message.reply_text(text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# yt-dlp و FFmpeg
# ==========================================================
def get_ydl_options(job_dir: Path = None, progress_data: dict = None, mode: str = "video"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15", "Accept": "*/*", "Connection": "keep-alive"},
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "webpage_safari"], "skip": ["webpage"]}}
    }
    if mode == "audio": opts["format"] = "bestaudio/best"
    else:
        opts["format"] = f"bestvideo[height<=720][filesize<{'2000M' if LOCAL_API_URL else '50M'}]+bestaudio/best[height<=720]/best"
        opts.update({"merge_output_format": "mp4", "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]})
    if cookie_file_is_usable(COOKIES_FILE): opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    with yt_dlp.YoutubeDL(dict(get_ydl_options(mode="video"), skip_download=True)) as ydl: return ydl.extract_info(url, download=False)

def download_hook(progress_data: dict):
    def hook(d):
        with progress_lock:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                down, speed = d.get("downloaded_bytes") or 0, d.get("speed") or 0
                progress_data["text"] = f"📥 <b>جاري تحميل الملف...</b>\n\n{make_progress_bar(down/total*100 if total else 0)}  {down/total*100:.1f}%\n📦 الحجم: {format_size(down)} / {format_size(total)}\n🚀 السرعة: {format_size(speed)}/ث" if total else f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(down)}"
            elif d.get("status") == "finished": progress_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            await edit_message_smart(message, text, reply_markup=None)
            last_text = text
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url, mode, job_dir, progress_data):
    with yt_dlp.YoutubeDL(get_ydl_options(job_dir, progress_data, mode)) as ydl: return ydl.extract_info(url, download=True)

def download_thumbnail_safely(thumb_url: str, output_path: Path):
    try:
        if not thumb_url or not is_public_host(urlparse(thumb_url).hostname or ""): return None
        with urllib.request.urlopen(urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=6) as r:
            if len(data := r.read(MAX_THUMBNAIL_BYTES + 1)) <= MAX_THUMBNAIL_BYTES: output_path.write_bytes(data)
        return output_path if output_path.exists() else None
    except Exception: return None

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)]
        if local_thumb and local_thumb.exists(): cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else: cmd.append("-vn")
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل التحويل: {e}")
        return False

# ==========================================================
# أوامر الإدارة الديناميكية
# ==========================================================
async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("🔄 جاري تحديث محرك التحميل...")
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text("✅ تم تحديث `yt-dlp` بنجاح.")
    except Exception as e: await msg.edit_text(f"❌ فشل التحديث: {e}")

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.document: return await update.message.reply_text("📥 أرسل ملف `cookies.txt` كـ Document.")
    await (await context.bot.get_file(update.message.document.file_id)).download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم استلام وتركيب ملف الكوكيز بنجاح!")

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        with open(DB_FILE, "rb") as f: await update.message.reply_document(document=f, filename="bot_database.db", caption="📦 نسخة احتياطية.")
    except Exception as e: await update.message.reply_text(f"❌ تعذر سحب النسخة: {e}")

# ==========================================================
# أحداث المستخدم والروابط الموحدة
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(build_start_text(update.effective_user.first_name or ""), reply_markup=user_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        context.user_data.pop("bc_active", None)
        await update.message.reply_text("🛠 <b>لوحة الإدارة المتقدمة</b>\n\nأوامر إضافية:\n/update_dlp - تحديث\n/setcookie - كوكيز\n/backup - نسخة احتياطية", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    context.user_data["bc_active"] = False
    if not (users := all_user_ids()): return await update.message.reply_text("لا يوجد مستخدمون.")
    status, sent, fail = await update.message.reply_text("📢 جاري الإرسال..."), 0, 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u, text=text, disable_web_page_preview=True); sent += 1
            await asyncio.sleep(0.05)
        except Exception: fail += 1
    stat_inc_sync("broadcasts")
    await status.edit_text(f"✅ تم الإرسال.\n\n• نجاح: {sent}\n• فشل: {fail}")

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not (text := update.message.text.strip()): return
    register_user_sync(update.effective_user); uid = update.effective_user.id
    if text in ["🔗 روابط PlayZone", "/links", "\\links"]: return await show_playzone_links(update, context)
    if text == "📘 دليل الاستخدام": return await update.message.reply_text(build_guide_text(), disable_web_page_preview=True)
    if is_admin(uid) and context.user_data.get("bc_active"): return await handle_broadcast_text(update, context, text)
    if uid in ACTIVE_USERS: return await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ. انتظر حتى يكتمل.")
    if not is_valid_url(text): return await update.message.reply_text("❌ الرابط غير صحيح. أرسل رابط يبدأ بـ http:// أو https://")

    status = await update.message.reply_text("🔍 جاري فحص الرابط وتجهيز المعاينة...")
    try:
        info = await asyncio.get_running_loop().run_in_executor(EXECUTOR, lambda: extract_metadata(text))
        req_id = uuid.uuid4().hex[:10]
        ensure_pending_requests(context)[req_id] = {"url": text, "title": clean_title(info.get("title")), "artist": get_artist(info), "duration": info.get("duration") or 0, "thumb_url": get_thumbnail(info), "created_at": int(time.time())}
        trim_old_pending_requests(context)
        await safe_delete(status)
        await send_preview(update, get_thumbnail(info), build_preview_caption(clean_title(info.get("title")), get_artist(info), format_duration(info.get("duration") or 0), format_size(get_largest_estimated_size(info))), build_preview_keyboard(req_id))
        stat_inc_sync("requests")
    except Exception as e:
        logger.warning(f"فشل المعاينة: {e}")
        await status.edit_text("❌ تعذر قراءة الرابط. تأكد أن المقطع متاح للعامة.")

# ==========================================================
# الأزرار ونظام الطابور الذكي
# ==========================================================
async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    d = query.data
    if d == "adm_close": await query.answer("تم الإغلاق"); return await safe_delete(query.message)
    elif d == "adm_stats": await query.answer(); return await query.message.edit_text(build_admin_stats_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif d == "adm_users": await query.answer(); return await query.message.edit_text(build_admin_users_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif d == "adm_server": await query.answer(); return await query.message.edit_text(build_server_status_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif d == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة...")
        return await query.message.edit_text(f"🧹 تم التنظيف. العناصر المحذوفة: {await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)}", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif d == "adm_bc":
        context.user_data["bc_active"] = True; await query.answer()
        return await query.message.edit_text("📢 أرسل نص الرسالة:", reply_markup=admin_broadcast_keyboard(), parse_mode="HTML")
    elif d == "adm_cancel_bc":
        context.user_data["bc_active"] = False; await query.answer("تم الإلغاء")
        return await query.message.edit_text("تم إلغاء العملية.", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (query := update.callback_query): return
    uid, data = query.from_user.id, query.data or ""
    if data.startswith("adm_"): return await handle_admin_callbacks(query, context) if is_admin(uid) else await query.answer("صلاحية إدارة فقط.", show_alert=True)
    if data.startswith("cancel:"):
        ensure_pending_requests(context).pop(data.split(":")[1], None); await query.answer("تم الإلغاء")
        return await safe_delete(query.message)
    if data.startswith(("aud:", "vid:")):
        request = ensure_pending_requests(context).pop(data.split(":")[1], None); trim_old_pending_requests(context)
        if not request: return await query.answer("انتهت الجلسة، أعد إرسال الرابط.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ.", show_alert=True)
        await start_download_from_callback(query, context, request, "audio" if data.startswith("aud:") else "video")

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str):
    uid, url = query.from_user.id, request.get("url")
    ACTIVE_USERS.add(uid)
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"; job_dir.mkdir(parents=True, exist_ok=True)
    stop_event, progress_data = asyncio.Event(), {"text": "⏳ يرجى الانتظار..."}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

        async with DOWNLOAD_SEMAPHORE:
            with progress_lock: progress_data["text"] = "🚀 بدأ التحميل... يرجى الانتظار ⏬"
            loop = asyncio.get_running_loop()
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(request.get("thumb_url"), job_dir / "playzone_thumb.jpg"))
            await loop.run_in_executor(EXECUTOR, lambda: execute_download(url, mode, job_dir, progress_data))
            
            if not (files := [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]): raise RuntimeError("فشل حفظ الملف")
            target_file = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with progress_lock: progress_data["text"] = "🎵 جاري تحويل الصوت..."
                final_mp3 = job_dir / "playzone_final_audio.mp3"
                if await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(target_file, final_mp3, local_thumb)): target_file = final_mp3

            if (file_size := target_file.stat().st_size) > MAX_TELEGRAM_SIZE:
                stop_event.set(); return await edit_message_smart(query.message, f"❌ حجم الملف يتجاوز الحد.\n\nالحجم: {format_size(file_size)}\nالحد: {format_size(MAX_TELEGRAM_SIZE)}")

            stop_event.set()
            await edit_message_smart(query.message, "📤 تم التجهيز، جاري الإرسال...", reply_markup=None)

            # --- التعديل المطلوب هنا على زر المشاركة ---
            share_text = "📥 حمّل أي فيديو أو أغنية MP3 في ثوانٍ!\n\n⚡ بوت سريع، مجاني وبأعلى جودة.\n👇 جرّبه الآن:\nhttps://t.me/MusicPlayZoneBot"
            share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(share_text)}"
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🌟 أعجبك البوت؟ شاركه", url=share_link)]])

            kwargs = {
                "chat_id": query.message.chat_id, "caption": f"- {esc(BOT_USERNAME)}، {esc(format_duration(request.get('duration') or 0))}",
                "duration": int(request.get("duration") or 0), "reply_markup": media_keyboard, "parse_mode": "HTML", "read_timeout": 120, "write_timeout": 120
            }
            with open(target_file, "rb") as f:
                if mode == "audio":
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                    try: await context.bot.send_audio(audio=f, title=clean_title(request.get("title", "ملف"), 80), performer=request.get("artist", "غير معروف"), thumbnail=t_file, **kwargs)
                    finally:
                        if t_file: t_file.close()
                else: await context.bot.send_video(video=f, supports_streaming=True, **kwargs)

            stat_inc_sync("success"); stat_inc_sync("bytes", file_size)
            await safe_delete(query.message)

    except (TimedOut, NetworkError): stat_inc_sync("failed"); await edit_message_smart(query.message, "❌ تعذر إرسال الملف بسبب ضعف الاتصال.")
    except Exception: stat_inc_sync("failed"); await edit_message_smart(query.message, "❌ فشل تحميل المقطع.")
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        shutil.rmtree(job_dir, ignore_errors=True)
        ACTIVE_USERS.discard(uid)

# ==========================================================
# التشغيل
# ==========================================================
async def post_init(app: Application):
    try: await app.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("links", "روابط PlayZone")]); await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception: pass

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر!")
    init_db(); _cleanup_old_downloads_sync()
    app = Application.builder().token(TOKEN).base_url(LOCAL_API_URL) if LOCAL_API_URL else Application.builder().token(TOKEN)
    app = app.post_init(post_init).connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30).concurrent_updates(True).build()
    
    for cmd, handler in [("start", start), ("links", show_playzone_links), ("admin", admin_panel), ("update_dlp", update_ytdlp_command), ("setcookie", set_cookie_command), ("backup", backup_db_command)]: app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم التشغيل بالنسخة السريعة المحسنة.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
