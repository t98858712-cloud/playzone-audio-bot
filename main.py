import os, re, time, html, uuid, asyncio, shutil, sqlite3, logging, threading, subprocess, urllib.request, ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# إعدادات المتغيرات والثوابت
# ==========================================================
TOKEN, LOCAL_API_URL = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_API_URL")
BASE_DOWNLOAD_DIR, DATA_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")), Path(os.getenv("DATA_DIR", "./data"))
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True); DATA_DIR.mkdir(exist_ok=True)

DB_FILE, DB_LOCK = DATA_DIR / "bot_database.db", threading.Lock()
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", str((2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024))))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))

PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))
REQUEST_EXPIRE_SECONDS, OLD_DOWNLOADS_EXPIRE_SECONDS = int(os.getenv("REQUEST_EXPIRE_SECONDS", "900")), int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", "3600"))
MAX_THUMBNAIL_BYTES = int(os.getenv("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2)))
DOWNLOAD_SEMAPHORE, EXECUTOR = asyncio.Semaphore(MAX_WORKERS), ThreadPoolExecutor(max_workers=max(2, MAX_WORKERS))
ACTIVE_USERS, progress_lock = set(), threading.Lock()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
WEBSITE_PLAYZONE, TELEGRAM_BOT_PLAYZONE = "http://tasmg1.github.io/tasmg/?", f"https://t.me/{BOT_USERNAME.replace('@', '')}"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")
for noisy in ["httpx", "httpcore", "telegram", "telegram.ext"]: logging.getLogger(noisy).setLevel(logging.WARNING)

# ==========================================================
# إدارة قاعدة البيانات (SQLite3 WAL Mode) المدمجة
# ==========================================================
def db_execute(query: str, params: tuple = (), fetch: str = None):
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row if fetch == "all_dict" else None
        cursor = conn.execute(query, params)
        if fetch == "one": return cursor.fetchone()
        if fetch == "all": return cursor.fetchall()
        if fetch == "all_dict": return [dict(r) for r in cursor.fetchall()]
        conn.commit()

def init_db():
    db_execute("PRAGMA journal_mode=WAL;")
    db_execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER)")
    db_execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.executemany("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", [(k,) for k in ["requests", "success", "failed", "bytes", "broadcasts"]])

def register_user_sync(u):
    if not u: return
    now = int(time.time())
    first_seen = (db_execute("SELECT first_seen FROM users WHERE id = ?", (u.id,), "one") or [now])[0]
    db_execute("INSERT OR REPLACE INTO users (id, username, first_name, last_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)", (u.id, u.username or "", u.first_name or "", u.last_name or "", first_seen, now))

def stat_inc_sync(key: str, value: int = 1): db_execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))
def load_stats_sync() -> dict: return {k: v for k, v in (db_execute("SELECT key, value FROM stats", fetch="all") or [])}
def all_user_ids() -> list: return [r[0] for r in (db_execute("SELECT id FROM users", fetch="all") or [])]
def get_latest_users(limit: int = 10) -> list: return db_execute(f"SELECT * FROM users ORDER BY last_seen DESC LIMIT {limit}", fetch="all_dict")

# ==========================================================
# أدوات الفحص والتنسيق
# ==========================================================
def is_admin(user_id: int) -> bool: return user_id in {int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()}
def esc(text) -> str: return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]+", "", str(text or "ملف ميديا"))).strip()
    return t[:limit] + "..." if len(t) > limit else t

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
        h, m = divmod(int(seconds), 3600); m, s = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    except Exception: return "غير معروف"

def is_public_host(host: str) -> bool:
    if not (host := (host or "").strip().lower()) or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}: return False
    try: return not (ip := ipaddress.ip_address(host)).is_private and not ip.is_loopback and not ip.is_link_local and not ip.is_multicast and not ip.is_reserved
    except ValueError: return True

def is_valid_url(text: str) -> bool:
    try:
        if len(text := (text or "").strip()) > 2000: return False
        p = urlparse(text)
        return p.scheme in ["http", "https"] and bool(p.netloc) and not (p.username or p.password) and is_public_host(p.hostname or "")
    except Exception: return False

def get_thumbnail(info: dict) -> str:
    try: return max(info.get("thumbnails") or [], key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), default={}).get("url") or info.get("thumbnail") or ""
    except Exception: return ""

def get_artist(info: dict) -> str: return next((clean_title(info[k], 35) for k in ["artist", "uploader", "channel", "creator"] if info.get(k)), "غير معروف")
def make_progress_bar(percent: float) -> str: return "🟩" * (f := int(max(0, min(100, float(percent))) // 10)) + "⬜" * (10 - f)
def get_largest_estimated_size(info: dict) -> int: return max([int(f.get("filesize") or f.get("filesize_approx") or 0) for f in info.get("formats", [])], default=0)

def ensure_pending_requests(context: ContextTypes.DEFAULT_TYPE) -> dict: return context.user_data.setdefault("pending_requests", {})
def trim_old_pending_requests(context: ContextTypes.DEFAULT_TYPE, max_items=8):
    p, now = ensure_pending_requests(context), int(time.time())
    for rid in list(p):
        if now - int(p[rid].get("created_at", 0)) > REQUEST_EXPIRE_SECONDS: p.pop(rid, None)
    if len(p) > max_items: context.user_data["pending_requests"] = dict(sorted(p.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:max_items])

def cookie_file_is_usable(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0: return False
        now, has_yt, has_valid = int(time.time()), False, False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not (line := line.strip()) or line.startswith("#"): continue
                if len(parts := line.split("\t")) < 7: continue
                has_yt = has_yt or "youtube.com" in parts[0]
                exp = int(parts[4]) if parts[4].isdigit() else 0
                if parts[6].strip() and (exp == 0 or exp > now): has_valid = True
        return has_yt and has_valid
    except Exception: return False

def _cleanup_old_downloads_sync():
    now = time.time()
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            if now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS: shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
    except Exception: pass

def _force_cleanup_all_sync() -> int:
    return sum(1 for item in BASE_DOWNLOAD_DIR.iterdir() if not (shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)))

# ==========================================================
# الواجهات والأزرار
# ==========================================================
def user_main_keyboard() -> ReplyKeyboardMarkup: return ReplyKeyboardMarkup([[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]], resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط هنا...")
def build_preview_keyboard(rid: str) -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{rid}"), InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{rid}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{rid}")]])
def build_playzone_links_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)], [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)], [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)]])
def build_playzone_links_text() -> str: return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."
def admin_main_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")], [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")], [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]])
def admin_broadcast_keyboard() -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="adm_cancel_bc")]])

def build_start_text(first_name: str) -> str: return f"أهلاً {esc(first_name)} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.\n\nابدأ بإرسال الرابط مباشرة."
def build_guide_text() -> str: return "📘 طريقة الاستخدام\n\n1) انسخ رابط المقطع.\n2) أرسله هنا في البوت.\n3) انتظر ظهور المعاينة.\n4) اختر التحميل صوت أو فيديو."
def build_preview_caption(title: str, artist: str, duration: str, est_size: str) -> str: return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"
def build_admin_stats_text() -> str:
    s = load_stats_sync()
    return f"📊 <b>إحصائيات البوت</b>\n\n• الطلبات الكلية: {s.get('requests', 0)}\n• التحميلات الناجحة: {s.get('success', 0)}\n• العمليات الفاشلة: {s.get('failed', 0)}\n• عدد المستخدمين: {len(all_user_ids())}\n• حجم الملفات المرسلة: {format_size(s.get('bytes', 0))}\n• عدد الإذاعات: {s.get('broadcasts', 0)}"

def build_admin_users_text(limit=10) -> str:
    return "\n".join(["👥 <b>آخر المستخدمين النشطين:</b>"] + [f"• {esc(u.get('first_name') or 'بدون اسم')} — {esc('@'+u.get('username') if u.get('username') else 'لا يوجد')} — ID: <code>{u.get('id')}</code>" for u in get_latest_users(limit)])

def build_server_status_text() -> str:
    files = list(BASE_DOWNLOAD_DIR.rglob("*"))
    return f"📁 <b>حالة السيرفر</b>\n\n• مجلد التحميل: <code>{BASE_DOWNLOAD_DIR}</code>\n• الملفات المؤقتة: {sum(1 for p in files if p.is_file())}\n• حجم الملفات المؤقتة: {format_size(sum(p.stat().st_size for p in files if p.is_file()))}\n• العمليات النشطة: {len(ACTIVE_USERS)}\n• الحد الأقصى المتزامن: {MAX_WORKERS}"

# ==========================================================
# الرسائل الآمنة
# ==========================================================
async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def edit_message_smart(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try: await (message.edit_caption if any(getattr(message, k, None) for k in ["photo", "video", "document"]) else message.edit_text)(caption=text, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise
    except Exception as e: logger.debug(f"تخطي تحديث الرسالة: {e}")

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    if thumb and thumb.startswith(("http://", "https://")):
        try: return await update.message.reply_photo(photo=thumb, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        except Exception: pass
    return await update.message.reply_text(text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# yt-dlp و FFmpeg
# ==========================================================
def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1", "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False, "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Connection": "keep-alive"},
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "webpage_safari"], "skip": ["webpage"]}}
    }
    if mode == "audio": opts["format"] = "bestaudio/best"
    else:
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        opts.update({"format": f"bestvideo[height<=720][filesize<{max_fs}]+bestaudio/best[height<=720][filesize<{max_fs}]/best", "merge_output_format": "mp4", "postprocessors": [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]})
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
                down, tot, spd = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0, d.get("speed") or 0
                progress_data["text"] = f"📥 <b>جاري تحميل الملف...</b>\n\n{make_progress_bar(down/tot*100)}  {down/tot*100:.1f}%\n📦 الحجم: {format_size(down)} / {format_size(tot)}\n🚀 السرعة: {format_size(spd)}/ث" if tot else f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(down)}"
            elif d.get("status") == "finished": progress_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            try: await edit_message_smart(message, text, reply_markup=None); last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict):
    with yt_dlp.YoutubeDL(get_ydl_options(job_dir, progress_data, mode)) as ydl: return ydl.extract_info(url, download=True)

def download_thumbnail_safely(thumb_url: str, output_path: Path) -> Path | None:
    try:
        if not thumb_url or not is_public_host(urlparse(thumb_url).hostname or ""): return None
        with urllib.request.urlopen(urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=6) as response:
            if len(data := response.read(MAX_THUMBNAIL_BYTES + 1)) <= MAX_THUMBNAIL_BYTES: output_path.write_bytes(data)
        return output_path if output_path.exists() else None
    except Exception: return None

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)]
        if local_thumb and local_thumb.exists(): cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else: cmd.extend(["-vn"])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")
        return False

# ==========================================================
# أوامر الإدارة الديناميكية
# ==========================================================
async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("🔄 جاري تحديث محرك التحميل...")
    try:
        subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
        await msg.edit_text("✅ تم تحديث محرك `yt-dlp` بنجاح إلى أحدث إصدار.")
    except Exception as e: await msg.edit_text(f"❌ فشل التحديث: {e}")

async def set_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.document: return await update.message.reply_text("📥 أرسل ملف `cookies.txt` كـ Document مع هذا الأمر لتخطي قيود يوتيوب.")
    await (await context.bot.get_file(update.message.document.file_id)).download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم استلام وتركيب ملف الكوكيز بنجاح!")

async def backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        with open(DB_FILE, "rb") as f: await update.message.reply_document(document=f, filename="bot_database.db", caption="📦 نسخة احتياطية من قاعدة البيانات.")
    except Exception as e: await update.message.reply_text(f"❌ تعذر سحب النسخة: {e}")

# ==========================================================
# أحداث المستخدم والروابط الموحدة
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(build_start_text(update.effective_user.first_name or ""), reply_markup=user_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop("bc_active", None)
    await update.message.reply_text("🛠 <b>لوحة الإدارة المتقدمة</b>\n\nأوامر إضافية للمدير:\n/update_dlp - لتحديث محرك التحميل\n/setcookie - لتجديد ملف الكوكيز\n/backup - لسحب قاعدة البيانات وحمايتها من الضياع", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    context.user_data["bc_active"] = False
    if not (users := all_user_ids()): return await update.message.reply_text("لا يوجد مستخدمون مسجلون.")
    status, sent, fail = await update.message.reply_text("📢 جاري إرسال الرسالة للمستخدمين..."), 0, 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True); sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
            try: await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True); sent += 1
            except Exception: fail += 1
        except Exception: fail += 1
    stat_inc_sync("broadcasts")
    await status.edit_text(f"✅ تم إرسال الإذاعة.\n\n• تم الإرسال: {sent}\n• فشل الإرسال: {fail}")

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    register_user_sync(update.effective_user); uid, text = update.effective_user.id, update.message.text.strip()

    if text in ["🔗 روابط PlayZone", "/links", "\\links"]: return await show_playzone_links(update, context)
    if text == "📘 دليل الاستخدام": return await update.message.reply_text(build_guide_text(), disable_web_page_preview=True)
    if is_admin(uid) and context.user_data.get("bc_active"): return await handle_broadcast_text(update, context, text)
    if uid in ACTIVE_USERS: return await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ.\n\nانتظر حتى يكتمل، ثم أرسل رابطاً جديداً.")
    if not is_valid_url(text): return await update.message.reply_text("❌ الرابط غير صحيح.\n\nأرسل رابط يبدأ بـ:\nhttp:// أو https://")

    status = await update.message.reply_text("🔍 جاري فحص الرابط وتجهيز المعاينة...")
    try:
        info = await asyncio.get_running_loop().run_in_executor(EXECUTOR, lambda: extract_metadata(text))
        title, artist, duration_raw = clean_title(info.get("title")), get_artist(info), info.get("duration") or 0
        est_size, thumb, request_id = format_size(get_largest_estimated_size(info)), get_thumbnail(info), uuid.uuid4().hex[:10]

        ensure_pending_requests(context)[request_id] = {"url": text, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb, "created_at": int(time.time())}
        trim_old_pending_requests(context)

        await safe_delete(status)
        await send_preview(update, thumb, build_preview_caption(title, artist, format_duration(duration_raw), est_size), build_preview_keyboard(request_id))
        stat_inc_sync("requests")
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة: {e}")
        await status.edit_text("❌ تعذر قراءة الرابط.\n\nتأكد أن المقطع متاح للعامة وغير محذوف، ثم حاول مرة أخرى.")

# ==========================================================
# الأزرار ونظام الطابور الذكي (Semaphore Queue)
# ==========================================================
async def handle_admin_callbacks(query, context: ContextTypes.DEFAULT_TYPE):
    data = query.data
    if data == "adm_close": await query.answer("تم الإغلاق"); return await safe_delete(query.message)
    elif data == "adm_stats": await query.answer(); return await query.message.edit_text(build_admin_stats_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_users": await query.answer(); return await query.message.edit_text(build_admin_users_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_server": await query.answer(); return await query.message.edit_text(build_server_status_text(), reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_clean":
        await query.answer("جاري تنظيف الملفات المؤقتة...")
        return await query.message.edit_text(f"🧹 تم تنظيف الملفات المؤقتة.\n\nالعناصر المحذوفة: {await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)}", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    elif data == "adm_bc":
        context.user_data["bc_active"] = True; await query.answer()
        return await query.message.edit_text("📢 أرسل نص الرسالة التي تريد إرسالها لجميع المستخدمين:", reply_markup=admin_broadcast_keyboard(), parse_mode="HTML")
    elif data == "adm_cancel_bc":
        context.user_data["bc_active"] = False; await query.answer("تم إلغاء الإذاعة")
        return await query.message.edit_text("تم إلغاء العملية.", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (query := update.callback_query): return
    uid, data = query.from_user.id, query.data or ""

    if data.startswith("adm_"): return await handle_admin_callbacks(query, context) if is_admin(uid) else await query.answer("صلاحية إدارة فقط.", show_alert=True)
    if data.startswith("cancel:"): ensure_pending_requests(context).pop(data.split(":")[1], None); await query.answer("تم إلغاء طلب التحميل"); return await safe_delete(query.message)
    if data.startswith(("aud:", "vid:")):
        request = ensure_pending_requests(context).pop(data.split(":")[1], None); trim_old_pending_requests(context)
        if not request: return await query.answer("انتهت جلسة هذا الطلب، يرجى إعادة إرسال الرابط.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ حالياً.", show_alert=True)
        await start_download_from_callback(query, context, request, "audio" if data.startswith("aud:") else "video")

async def start_download_from_callback(query, context: ContextTypes.DEFAULT_TYPE, request: dict, mode: str):
    uid, url = query.from_user.id, request.get("url"); ACTIVE_USERS.add(uid)
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
            if not (files := [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]): raise RuntimeError("محرك الميديا فشل في حفظ الملف النهائي على القرص")
            raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with progress_lock: progress_data["text"] = "🎵 جاري تحويل الصوت ودمج الغلاف الخارجي..."
                final_mp3_path = job_dir / "playzone_final_audio.mp3"
                target_file = final_mp3_path if await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb)) else raw_downloaded_file
            else: target_file = raw_downloaded_file

            if (file_size := target_file.stat().st_size) > MAX_TELEGRAM_SIZE:
                stop_event.set(); return await edit_message_smart(query.message, f"❌ حجم الملف يتجاوز الحد المسموح.\n\nالحجم: {format_size(file_size)}\nالحد: {format_size(MAX_TELEGRAM_SIZE)}", reply_markup=None)

            stop_event.set(); await edit_message_smart(query.message, "📤 تم تجهيز الملف، جاري الإرسال...", reply_markup=None)

            title, duration = clean_title(request.get("title", "ملف ميديا"), 80), int(request.get("duration") or 0)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration))}"
            share_link = f"https://t.me/share/url?url={quote(url)}&text={quote('🎬 ' + title)}"
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=share_link)]])
            kw = {"chat_id": query.message.chat_id, "caption": caption, "duration": duration, "reply_markup": media_keyboard, "parse_mode": "HTML", "read_timeout": 120, "write_timeout": 120}

            with open(target_file, "rb") as f:
                if mode == "audio":
                    t_file = open(local_thumb, "rb") if local_thumb and local_thumb.exists() else None
                    try: await context.bot.send_audio(audio=f, title=title, performer=request.get("artist", "غير معروف"), thumbnail=t_file, **kw)
                    finally:
                        if t_file: t_file.close()
                else: await context.bot.send_video(video=f, supports_streaming=True, **kw)

            stat_inc_sync("success"); stat_inc_sync("bytes", file_size); await safe_delete(query.message)

    except (TimedOut, NetworkError) as e:
        stat_inc_sync("failed"); logger.error(f"فشل اتصال تيليجرام: {e}")
        try: await edit_message_smart(query.message, "❌ تعذر إرسال الملف بسبب ضعف الاتصال أو ضغط مؤقت.\n\nحاول مرة أخرى بعد قليل.")
        except Exception: pass
    except Exception as e:
        stat_inc_sync("failed"); logger.error(f"فشل المعالجة: {e}")
        try: await edit_message_smart(query.message, "❌ فشل تحميل المقطع.\n\nقد يكون الرابط غير متاح أو يتجاوز الحد المسموح به.")
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        try: shutil.rmtree(job_dir)
        except Exception: pass
        ACTIVE_USERS.discard(uid)

# ==========================================================
# التشغيل
# ==========================================================
async def post_init(app: Application):
    try: await app.bot.set_my_commands([BotCommand("start", "بدء استخدام البوت"), BotCommand("links", "دعم روابط PlayZone")]); await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e: logger.warning(f"فشل تهيئة الأوامر: {e}")

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")
    init_db(); _cleanup_old_downloads_sync()

    app = (Application.builder().token(TOKEN).base_url(LOCAL_API_URL) if LOCAL_API_URL else Application.builder().token(TOKEN)).post_init(post_init).connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30).concurrent_updates(True).build()
    
    for cmd, fn in [("start", start), ("links", show_playzone_links), ("admin", admin_panel), ("update_dlp", update_ytdlp_command), ("setcookie", set_cookie_command), ("backup", backup_db_command)]: app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم تشغيل البوت بالنسخة النهائية (Smart Queue & Database Protection).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
