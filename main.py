import os, re, time, html, uuid, asyncio, shutil, sqlite3, logging, threading, subprocess, urllib.request, ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# الإعدادات
# ==========================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL")
BASE_DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "bot_database.db"
DB_LOCK = threading.Lock()

DEFAULT_MAX_SIZE = (2000 * 1024**2) if LOCAL_API_URL else (50 * 1024**2)
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", str(DEFAULT_MAX_SIZE)))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))
PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2)))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_WORKERS)
ACTIVE_USERS, progress_lock = set(), threading.Lock()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
LINKS = {
    "web": "http://tasmg1.github.io/tasmg/?",
    "fb": "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr",
    "ig": "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr",
    "th": "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ==",
    "tg": f"https://t.me/{BOT_USERNAME.replace('@', '')}"
}

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneBot")
for log in ["httpx", "httpcore", "telegram"]: logging.getLogger(log).setLevel(logging.WARNING)

# ==========================================================
# قاعدة البيانات (مُحسّنة ومُختصرة)
# ==========================================================
def _db(query: str, params=(), fetchall=False):
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        if fetchall: conn.row_factory = sqlite3.Row
        cur = conn.execute(query, params)
        return [dict(r) for r in cur.fetchall()] if fetchall else cur.fetchone()

def init_db():
    _db("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER)")
    _db("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
    for k in ["requests", "success", "failed", "bytes", "broadcasts"]:
        _db("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (k,))

def register_user_sync(user):
    if not user: return
    now = int(time.time())
    fs = (_db("SELECT first_seen FROM users WHERE id = ?", (user.id,)) or [now])[0]
    _db("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)", (user.id, user.username or "", user.first_name or "", user.last_name or "", fs, now))

def stat_inc_sync(key: str, val: int = 1): _db("UPDATE stats SET value = value + ? WHERE key = ?", (val, key))
def load_stats_sync(): return {k: v for k, v in _db("SELECT key, value FROM stats")} # type: ignore
def all_user_ids(): return [r[0] for r in _db("SELECT id FROM users")] # type: ignore

# ==========================================================
# أدوات الفحص والتنسيق
# ==========================================================
def is_admin(uid: int): return uid in {int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()}
def esc(t): return html.escape(str(t or ""), quote=False)
def clean_title(t, lim=60): return re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]+", "", str(t or "ملف"))).strip()[:lim]

def format_size(s):
    try: s = float(s)
    except: return "غير معروف"
    for u in ["B", "KB", "MB", "GB"]:
        if s < 1024: return f"{int(s)} {u}" if s == int(s) else f"{s:.1f} {u}"
        s /= 1024
    return f"{s:.1f} GB"

def format_duration(s):
    try: s = int(s)
    except: return "00:00"
    return f"{s//3600}:{s%3600//60:02d}:{s%60:02d}" if s >= 3600 else f"{s//60:02d}:{s%60:02d}"

def is_valid_url(url: str):
    try:
        p = urlparse((url or "").strip())
        return p.scheme in ["http", "https"] and p.netloc and not p.username
    except: return False

def get_best_thumb(info: dict):
    thumbs = info.get("thumbnails", [])
    return max(thumbs, key=lambda x: x.get("width", 0) * x.get("height", 0)).get("url") if thumbs else info.get("thumbnail", "")

def get_artist(info: dict): return next((info.get(k) for k in ["artist", "uploader", "channel"] if info.get(k)), "غير معروف")
def get_max_size(info: dict): return max([int(f.get("filesize") or f.get("filesize_approx") or 0) for f in info.get("formats", [])], default=0)

def manage_pending(ctx: ContextTypes.DEFAULT_TYPE):
    p = ctx.user_data.setdefault("pending_requests", {})
    now = time.time()
    for k in list(p):
        if now - p[k].get("created_at", 0) > 900: p.pop(k, None)
    return p

def cookie_valid():
    try:
        return COOKIES_FILE.exists() and "youtube.com" in COOKIES_FILE.read_text(errors="ignore")
    except: return False

def force_cleanup():
    removed = 0
    for i in BASE_DOWNLOAD_DIR.iterdir():
        try: shutil.rmtree(i) if i.is_dir() else i.unlink(); removed += 1
        except: pass
    return removed

# ==========================================================
# الواجهات والرسائل
# ==========================================================
def main_kb(): return ReplyKeyboardMarkup([["📘 دليل الاستخدام"], ["🔗 روابط PlayZone"]], resize_keyboard=True)
def links_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=LINKS["web"])],
        [InlineKeyboardButton("📘 Facebook", url=LINKS["fb"]), InlineKeyboardButton("📸 Instagram", url=LINKS["ig"])],
        [InlineKeyboardButton("🧵 Threads", url=LINKS["th"]), InlineKeyboardButton("🤖 Telegram Bot", url=LINKS["tg"])],
    ])
def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")],
        [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")],
        [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")],
    ])

async def safe_delete(msg):
    try: await msg.delete()
    except: pass

async def safe_edit(msg, txt, kb=None):
    try: await msg.edit_caption(caption=txt, reply_markup=kb, parse_mode="HTML") if msg.photo or msg.video else await msg.edit_text(txt, reply_markup=kb, parse_mode="HTML")
    except BadRequest as e: 
        if "not modified" not in str(e).lower(): raise

# ==========================================================
# Yt-dlp & FFmpeg
# ==========================================================
def get_ydl_opts(job_dir=None, prog_data=None, mode="video"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "no_check_certificate": True,
        "concurrent_fragment_downloads": 10,
        "external_downloader": "aria2c", # تسريع التحميل الخارجي
        "external_downloader_args": {"aria2c": ["-x", "16", "-s", "16", "-k", "1M"]},
        "http_headers": {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"}
    }
    opts["format"] = "bestaudio/best" if mode == "audio" else f"bestvideo[height<=720][filesize<{int(MAX_TELEGRAM_SIZE)}]+bestaudio/best/best"
    if mode != "audio":
        opts["merge_output_format"] = "mp4"
    if cookie_valid(): opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "media.%(ext)s")
    if prog_data:
        def hook(d):
            with progress_lock:
                if d.get("status") == "downloading":
                    dl, tot = d.get("downloaded_bytes", 0), d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                    pct = dl / tot * 100
                    bar = "🟩" * int(pct // 10) + "⬜" * (10 - int(pct // 10))
                    prog_data["text"] = f"📥 <b>جاري التحميل...</b>\n\n{bar}  {pct:.1f}%\n📦 {format_size(dl)} / {format_size(tot)}\n🚀 {format_size(d.get('speed', 0))}/ث"
                elif d.get("status") == "finished":
                    prog_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز..."
        opts["progress_hooks"] = [hook]
    return opts

async def progress_task(msg, p_data, stop_ev):
    last = ""
    while not stop_ev.is_set():
        with progress_lock: txt = p_data.get("text", "")
        if txt and txt != last:
            await safe_edit(msg, txt); last = txt
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def convert_mp3(inp, out, thumb=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(inp)]
    if thumb and thumb.exists(): cmd += ["-i", str(thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3"]
    else: cmd += ["-vn"]
    subprocess.run(cmd + ["-c:a", "libmp3lame", "-b:a", "320k", str(out)], check=True, timeout=180)
    return out.exists()

# ==========================================================
# الأوامر و المعالجات
# ==========================================================
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    register_user_sync(u.effective_user)
    await u.message.reply_text(f"أهلاً {esc(u.effective_user.first_name)}\n\nأرسل رابط فيديو أو صوت للمعاينة والتحميل.", reply_markup=main_kb(), parse_mode="HTML")

async def admin_panel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_admin(u.effective_user.id):
        await u.message.reply_text("🛠 <b>لوحة الإدارة</b>", reply_markup=admin_kb(), parse_mode="HTML")

async def handle_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt, uid = u.message.text.strip(), u.effective_user.id
    register_user_sync(u.effective_user)

    if txt in ["🔗 روابط PlayZone", "/links"]:
        return await u.message.reply_text("💚 دعمك يصنع الفرق\nتابعنا وشارك الروابط:", reply_markup=links_kb())
    if txt == "📘 دليل الاستخدام":
        return await u.message.reply_text("انسخ الرابط > أرسله > اختر الصيغة.")
    
    if c.user_data.get("bc_active") and is_admin(uid):
        c.user_data["bc_active"], sent, users = False, 0, all_user_ids()
        msg = await u.message.reply_text("📢 جاري الإرسال...")
        for u_id in users:
            try: await c.bot.send_message(u_id, txt); sent += 1; await asyncio.sleep(0.05)
            except: pass
        stat_inc_sync("broadcasts")
        return await msg.edit_text(f"✅ تم الإرسال لـ {sent} مستخدم.")

    if not is_valid_url(txt): return await u.message.reply_text("❌ رابط غير صالح.")
    if uid in ACTIVE_USERS: return await u.message.reply_text("⏳ لديك طلب قيد التنفيذ.")

    msg = await u.message.reply_text("🔍 جاري فحص الرابط...")
    try:
        info = await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(get_ydl_opts()).extract_info(txt, download=False))
        rid = uuid.uuid4().hex[:10]
        manage_pending(c)[rid] = {"url": txt, "title": clean_title(info.get("title")), "artist": get_artist(info), "dur": info.get("duration", 0), "thumb": get_best_thumb(info), "created_at": time.time()}
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎵 صوت", callback_data=f"dl:audio:{rid}"), InlineKeyboardButton("🎬 فيديو", callback_data=f"dl:video:{rid}")], [InlineKeyboardButton("❌", callback_data=f"cancel:{rid}")]])
        cap = f"🎬 <b>{esc(manage_pending(c)[rid]['title'])}</b>\n⏱ {format_duration(manage_pending(c)[rid]['dur'])} - 💾 {format_size(get_max_size(info))}"
        
        await safe_delete(msg)
        await u.message.reply_photo(manage_pending(c)[rid]['thumb'], caption=cap, reply_markup=kb, parse_mode="HTML") if manage_pending(c)[rid]['thumb'] else await u.message.reply_text(cap, reply_markup=kb, parse_mode="HTML")
        stat_inc_sync("requests")
    except Exception as e:
        await msg.edit_text("❌ تعذر قراءة الرابط.")

async def handle_cbs(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    d, uid = q.data, q.from_user.id

    if d.startswith("adm_") and is_admin(uid):
        if d == "adm_close": await q.answer(); return await safe_delete(q.message)
        if d == "adm_clean": await q.answer("تنظيف..."); force_cleanup(); return await safe_edit(q.message, "🧹 تم التنظيف.", admin_kb())
        if d == "adm_bc": c.user_data["bc_active"] = True; return await safe_edit(q.message, "📢 أرسل نص الإذاعة الآن:")
        stats = load_stats_sync()
        txt = f"📊 الطلبات: {stats.get('requests', 0)} | الناجح: {stats.get('success', 0)} | المستخدمين: {len(all_user_ids())}" if d == "adm_stats" else "حالة السيرفر مستقرة."
        return await safe_edit(q.message, txt, admin_kb())

    if d.startswith("cancel:"):
        manage_pending(c).pop(d.split(":")[1], None)
        await q.answer("تم الإلغاء"); return await safe_delete(q.message)

    if d.startswith("dl:"):
        _, mode, rid = d.split(":")
        req = manage_pending(c).pop(rid, None)
        if not req: return await q.answer("❌ انتهت الجلسة، أعد إرسال الرابط.", show_alert=True)
        if uid in ACTIVE_USERS: return await q.answer("⏳ لديك تحميل نشط.", show_alert=True)
        asyncio.create_task(process_download(q, c, req, mode, uid))

async def process_download(q, c, req, mode, uid):
    ACTIVE_USERS.add(uid)
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_ev, p_data = asyncio.Event(), {"text": "⏳ يرجى الانتظار..."}
    t_task = asyncio.create_task(progress_task(q.message, p_data, stop_ev))

    try:
        await safe_edit(q.message, "⏳ يرجى الانتظار...", None)
        async with DOWNLOAD_SEMAPHORE:
            def dl_job():
                thumb_path = job_dir / "thumb.jpg"
                if req["thumb"]:
                    try: urllib.request.urlretrieve(req["thumb"], thumb_path)
                    except: pass
                with yt_dlp.YoutubeDL(get_ydl_opts(job_dir, p_data, mode)) as ydl: ydl.extract_info(req["url"], download=True)
                return max([p for p in job_dir.iterdir() if p.suffix not in [".part", ".tmp", ".ytdl", ".jpg"]], key=lambda x: x.stat().st_mtime), thumb_path
            
            target, thumb = await asyncio.to_thread(dl_job)
            
            if mode == "audio":
                with progress_lock: p_data["text"] = "🎵 جاري المعالجة..."
                mp3_file = job_dir / "final.mp3"
                if await asyncio.to_thread(lambda: convert_mp3(target, mp3_file, thumb)): target = mp3_file

            sz = target.stat().st_size
            if sz > MAX_TELEGRAM_SIZE: raise ValueError("حجم الملف يتجاوز حد تيليجرام.")

            stop_ev.set()
            await safe_edit(q.message, "📤 جاري الإرسال...")
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=f"https://t.me/share/url?url={quote(req['url'])}&text={quote('🎬 '+req['title'])}")]])
            with open(target, "rb") as f:
                if mode == "audio":
                    await c.bot.send_audio(q.message.chat_id, f, title=req["title"][:80], performer=req["artist"], duration=req["dur"], caption=f"- {BOT_USERNAME}", reply_markup=kb, thumbnail=open(thumb, "rb") if thumb.exists() else None, read_timeout=120)
                else:
                    await c.bot.send_video(q.message.chat_id, f, caption=f"- {BOT_USERNAME}", supports_streaming=True, duration=req["dur"], reply_markup=kb, read_timeout=120)

            stat_inc_sync("success"); stat_inc_sync("bytes", sz)
            await safe_delete(q.message)
    except Exception as e:
        stat_inc_sync("failed")
        await safe_edit(q.message, f"❌ فشل التحميل: {e}" if "حجم" in str(e) else "❌ فشل التحميل، حاول مجدداً.")
    finally:
        stop_ev.set(); ACTIVE_USERS.discard(uid)
        try: shutil.rmtree(job_dir)
        except: pass

# ==========================================================
# التشغيل
# ==========================================================
async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("links", "روابط PlayZone")])

if __name__ == "__main__":
    force_cleanup(); init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("links", handle_text))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_cbs))
    app.run_polling(drop_pending_updates=True)
