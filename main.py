import os, re, time, html, uuid, asyncio, shutil, sqlite3, logging, threading, subprocess, urllib.request, ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.error import BadRequest, TimedOut, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# إعدادات المتغيرات والثوابت
# ==========================================================
TOKEN, LOCAL_API_URL = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_API_URL")
BASE_DOWNLOAD_DIR, DATA_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")), Path(os.getenv("DATA_DIR", "./data"))
for d in (BASE_DOWNLOAD_DIR, DATA_DIR): d.mkdir(exist_ok=True, parents=True)

DB_FILE, DB_LOCK, COOKIES_FILE = DATA_DIR / "bot_database.db", threading.Lock(), Path(os.getenv("COOKIES_FILE", "cookies.txt"))
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", (2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024)))
PROGRESS_UPDATE_SECONDS, REQUEST_EXPIRE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0")), int(os.getenv("REQUEST_EXPIRE_SECONDS", "900"))
OLD_DOWNLOADS_EXPIRE_SECONDS, MAX_THUMBNAIL_BYTES = int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", "3600")), int(os.getenv("MAX_THUMBNAIL_BYTES", "2097152"))
MAX_WORKERS = max(2, int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2))))

DOWNLOAD_SEMAPHORE, EXECUTOR = asyncio.Semaphore(MAX_WORKERS), ThreadPoolExecutor(max_workers=MAX_WORKERS * 2)
ACTIVE_USERS, PROGRESS_LOCK = set(), threading.Lock()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
WEBSITE_PLAYZONE, TELEGRAM_BOT_PLAYZONE = "http://tasmg1.github.io/tasmg/?", f"https://t.me/{BOT_USERNAME.replace('@', '')}"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("PlayZoneEnterpriseBot")
for noisy in ["httpx", "httpcore", "telegram", "telegram.ext"]: logging.getLogger(noisy).setLevel(logging.WARNING)

# ==========================================================
# إدارة قاعدة البيانات (SQLite3 WAL)
# ==========================================================
def db_execute(query: str, params: tuple = (), fetch: str = None):
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row if fetch == "all_dict" else None
        cursor = conn.execute(query, params)
        if fetch == "one": return cursor.fetchone()
        elif fetch == "all": return cursor.fetchall()
        elif fetch == "all_dict": return [dict(r) for r in cursor.fetchall()]
        conn.commit()

def init_db():
    db_execute("PRAGMA journal_mode=WAL;")
    db_execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER)")
    db_execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: conn.executemany("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", [(k,) for k in ["requests", "success", "failed", "bytes", "broadcasts"]])

def register_user_sync(u):
    if not u: return
    now = int(time.time())
    first_seen = (db_execute("SELECT first_seen FROM users WHERE id = ?", (u.id,), "one") or [now])[0]
    db_execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)", (u.id, u.username or "", u.first_name or "", u.last_name or "", first_seen, now))

def stat_inc_sync(key: str, val: int = 1): db_execute("UPDATE stats SET value = value + ? WHERE key = ?", (val, key))
def load_stats_sync() -> dict: return dict(db_execute("SELECT key, value FROM stats", fetch="all") or [])
def all_user_ids() -> list: return [r[0] for r in db_execute("SELECT id FROM users", fetch="all")]
def get_latest_users(limit=10) -> list: return db_execute(f"SELECT * FROM users ORDER BY last_seen DESC LIMIT {limit}", fetch="all_dict")

# ==========================================================
# أدوات الفحص والتنسيق
# ==========================================================
def is_admin(user_id: int) -> bool: return user_id in {int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()}
def esc(text) -> str: return html.escape(str(text or ""), quote=False)
def clean_title(text: str, limit=60) -> str: return (t := re.sub(r"[\\/:*?\"<>|]+", "", re.sub(r"\s+", " ", str(text or "ملف ميديا"))).strip())[:limit] + "..." if len(t) > limit else t

def format_size(size_bytes) -> str:
    try:
        s = float(size_bytes)
        for unit in ["Bytes", "KB", "MB", "GB"]:
            if s < 1024.0: return f"{int(s)} {unit}" if s.is_integer() else f"{s:.1f} {unit}"
            s /= 1024.0
        return f"{s:.1f} GB"
    except Exception: return "غير معروف"

def format_duration(sec) -> str:
    try:
        h, m = divmod(int(sec), 3600); m, s = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    except Exception: return "00:00"

def is_valid_url(text: str) -> bool:
    try:
        if len(text := str(text or "").strip()) > 2000: return False
        p = urlparse(text)
        return p.scheme in ["http", "https"] and bool(p.netloc) and not p.username and not (ipaddress.ip_address(p.hostname).is_private if p.hostname.replace('.', '').isdigit() else False)
    except Exception: return text.startswith(("http://", "https://"))

def get_metadata(info: dict) -> tuple:
    thumb = info.get("thumbnail", "")
    if t := info.get("thumbnails"): thumb = max(t, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)).get("url", thumb)
    artist = next((clean_title(info.get(k), 35) for k in ["artist", "uploader", "channel", "creator"] if info.get(k)), "غير معروف")
    est_size = max([int(f.get("filesize") or f.get("filesize_approx") or 0) for f in info.get("formats", [])], default=0)
    return thumb, artist, est_size

def ensure_pending(ctx: ContextTypes.DEFAULT_TYPE) -> dict: return ctx.user_data.setdefault("pending_requests", {})
def trim_pending(ctx: ContextTypes.DEFAULT_TYPE, max_items=8):
    p, now = ensure_pending(ctx), time.time()
    for rid in list(p):
        if now - p[rid].get("created_at", 0) > REQUEST_EXPIRE_SECONDS: p.pop(rid, None)
    if len(p) > max_items: ctx.user_data["pending_requests"] = dict(sorted(p.items(), key=lambda kv: kv[1].get("created_at", 0))[-max_items:])

def _force_cleanup_all_sync() -> int:
    return sum(1 for item in BASE_DOWNLOAD_DIR.iterdir() if not (shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)))

# ==========================================================
# الواجهات والنصوص
# ==========================================================
def user_kbd(): return ReplyKeyboardMarkup([["📘 دليل الاستخدام"], ["🔗 روابط PlayZone"]], resize_keyboard=True, input_field_placeholder="أرسل الرابط هنا...")
def preview_kbd(rid): return InlineKeyboardMarkup([[InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{rid}"), InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{rid}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{rid}")]])
def links_kbd(): return InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)], [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)], [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)]])
def admin_kbd(): return InlineKeyboardMarkup([[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")], [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")], [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]])

# ==========================================================
# الرسائل الآمنة
# ==========================================================
async def edit_msg(msg, text: str, markup=None):
    try: await (msg.edit_caption if msg.photo or msg.video or msg.document else msg.edit_text)(caption=text, text=text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception: pass

# ==========================================================
# محرك yt-dlp و FFmpeg
# ==========================================================
def get_ydl_opts(job_dir=None, p_data=None, mode="video"):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 15, "concurrent_fragment_downloads": 10, "http_headers": {"User-Agent": "Mozilla/5.0"}, "extractor_args": {"youtube": {"player_client": ["ios", "android"]}}}
    opts["format"] = "bestaudio/best" if mode == "audio" else f"bestvideo[height<=720][filesize<{'2000M' if LOCAL_API_URL else '50M'}]+bestaudio/best[height<=720]/best"
    if mode != "audio": opts.update({"merge_output_format": "mp4", "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]})
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0: opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if p_data is not None:
        def hook(d):
            with PROGRESS_LOCK:
                if d.get("status") == "downloading":
                    tot, down, spd = d.get("total_bytes") or d.get("total_bytes_estimate") or 0, d.get("downloaded_bytes") or 0, d.get("speed") or 0
                    fill = int(max(0, min(100, down / tot * 100 if tot else 0)) // 10)
                    p_data["text"] = f"📥 <b>جاري تحميل الملف...</b>\n\n{'🟩'*fill + '⬜'*(10-fill)} {down/tot*100:.1f}%\n📦 الحجم: {format_size(down)} / {format_size(tot)}\n🚀 السرعة: {format_size(spd)}/ث" if tot else f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(down)}"
                elif d.get("status") == "finished": p_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
        opts["progress_hooks"] = [hook]
    return opts

def extract_meta(url: str):
    with yt_dlp.YoutubeDL(dict(get_ydl_opts(mode="video"), skip_download=True)) as ydl: return ydl.extract_info(url, download=False)

def dl_thumb(url: str, path: Path):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=5) as r:
            if len(data := r.read(MAX_THUMBNAIL_BYTES + 1)) <= MAX_THUMBNAIL_BYTES: path.write_bytes(data)
        return path if path.exists() else None
    except Exception: return None

def convert_mp3(inp: Path, out: Path, thumb: Path = None) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(inp)]
    if thumb and thumb.exists(): cmd.extend(["-i", str(thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
    else: cmd.append("-vn")
    cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(out)])
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return out.exists()

# ==========================================================
# الأحداث والتنزيل
# ==========================================================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user)
    await update.message.reply_text(f"أهلاً {esc(update.effective_user.first_name)} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n💚 دعمك يصنع الفرق\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك.\n\nابدأ بإرسال الرابط مباشرة.", reply_markup=user_kbd(), parse_mode="HTML")

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not (text := update.message.text.strip()): return
    uid = update.effective_user.id; register_user_sync(update.effective_user)
    
    if text in ["🔗 روابط PlayZone", "/links"]: return await update.message.reply_text("💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك.", reply_markup=links_kbd())
    if text == "📘 دليل الاستخدام": return await update.message.reply_text("📘 طريقة الاستخدام\n\n1) انسخ رابط المقطع.\n2) أرسله هنا.\n3) انتظر المعاينة.\n4) اختر صوت أو فيديو.")
    if is_admin(uid) and ctx.user_data.get("bc_active"):
        ctx.user_data["bc_active"] = False; sent = fail = 0; status = await update.message.reply_text("📢 جاري الإرسال...")
        for u in all_user_ids():
            try: await ctx.bot.send_message(u, text); sent += 1; await asyncio.sleep(0.05)
            except Exception: fail += 1
        stat_inc_sync("broadcasts"); return await status.edit_text(f"✅ تم الإرسال.\nنجاح: {sent} | فشل: {fail}")
    if uid in ACTIVE_USERS: return await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ.")
    if not is_valid_url(text): return await update.message.reply_text("❌ الرابط غير صحيح. أرسل رابط يبدأ بـ http:// أو https://")

    msg = await update.message.reply_text("🔍 جاري فحص الرابط وتجهيز المعاينة...")
    try:
        info = await asyncio.get_running_loop().run_in_executor(EXECUTOR, extract_meta, text)
        rid = uuid.uuid4().hex[:10]; thumb, artist, est_size = get_metadata(info)
        ensure_pending(ctx)[rid] = {"url": text, "title": clean_title(info.get("title")), "artist": artist, "duration": info.get("duration") or 0, "thumb": thumb, "created_at": time.time()}
        trim_pending(ctx)
        cap = f"🎬 <b>{esc(clean_title(info.get('title')))}</b>\n<b>{esc(artist)}</b>\n⏱ {format_duration(info.get('duration') or 0)} - 💾 {format_size(est_size)}"
        await msg.delete()
        if thumb:
            try: await update.message.reply_photo(thumb, caption=cap, reply_markup=preview_kbd(rid), parse_mode="HTML"); stat_inc_sync("requests"); return
            except Exception: pass
        await update.message.reply_text(cap, reply_markup=preview_kbd(rid), parse_mode="HTML")
        stat_inc_sync("requests")
    except Exception: await msg.edit_text("❌ تعذر قراءة الرابط. تأكد أن المقطع متاح للعامة.")

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; uid, data = query.from_user.id, query.data
    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("صلاحية إدارة فقط.", show_alert=True)
        if data == "adm_close": await query.message.delete(); return await query.answer()
        elif data == "adm_stats": s = load_stats_sync(); return await edit_msg(query.message, f"📊 <b>الإحصائيات</b>\nطلبات: {s.get('requests',0)}\nنجاح: {s.get('success',0)}\nفشل: {s.get('failed',0)}\nمستخدمين: {len(all_user_ids())}\nحجم: {format_size(s.get('bytes',0))}", admin_kbd())
        elif data == "adm_clean": await query.answer("جاري التنظيف..."); return await edit_msg(query.message, f"🧹 تم التنظيف. محذوفات: {await asyncio.get_running_loop().run_in_executor(EXECUTOR, _force_cleanup_all_sync)}", admin_kbd())
        elif data == "adm_bc": ctx.user_data["bc_active"] = True; return await edit_msg(query.message, "📢 أرسل نص الرسالة:", InlineKeyboardMarkup([[(InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_bc"))]]))
        elif data == "adm_cancel_bc": ctx.user_data["bc_active"] = False; return await edit_msg(query.message, "تم الإلغاء.", admin_kbd())
        
    if data.startswith("cancel:"): ensure_pending(ctx).pop(data.split(":")[1], None); await query.answer("تم الإلغاء"); return await query.message.delete()
    if data.startswith(("aud:", "vid:")):
        req = ensure_pending(ctx).pop(data.split(":")[1], None); trim_pending(ctx)
        if not req: return await query.answer("انتهت الجلسة، أعد إرسال الرابط.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ.", show_alert=True)
        asyncio.create_task(process_dl(query, ctx, req, "audio" if data.startswith("aud:") else "video"))

async def update_prog(msg, p_data, stop):
    last = ""
    while not stop.is_set():
        with PROGRESS_LOCK: text = p_data.get("text", "")
        if text and text != last: await edit_msg(msg, text); last = text
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

async def process_dl(query, ctx: ContextTypes.DEFAULT_TYPE, req: dict, mode: str):
    uid, loop, job_dir = query.from_user.id, asyncio.get_running_loop(), BASE_DOWNLOAD_DIR / f"{uid}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(parents=True, exist_ok=True); ACTIVE_USERS.add(uid)
    stop, p_data = asyncio.Event(), {"text": "⏳ يرجى الانتظار..."}
    upd_task = asyncio.create_task(update_prog(query.message, p_data, stop))

    try:
        await edit_msg(query.message, "🚀 بدأ التحميل... يرجى الانتظار ⏬")
        async with DOWNLOAD_SEMAPHORE:
            t_path = await loop.run_in_executor(EXECUTOR, dl_thumb, req["thumb"], job_dir / "thumb.jpg")
            await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(get_ydl_opts(job_dir, p_data, mode)).extract_info(req["url"], download=True))
            
            if not (files := [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".ytdl"]]): raise Exception("فشل الحفظ")
            target = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with PROGRESS_LOCK: p_data["text"] = "🎵 جاري تحويل الصوت..."
                if await loop.run_in_executor(EXECUTOR, convert_mp3, target, job_dir / "final.mp3", t_path): target = job_dir / "final.mp3"

            if (f_size := target.stat().st_size) > MAX_TELEGRAM_SIZE: stop.set(); return await edit_msg(query.message, f"❌ حجم الملف يتجاوز الحد المسموح: {format_size(f_size)}")

            stop.set(); await edit_msg(query.message, "📤 تم التجهيز، جاري الإرسال...")
            
            share_link = f"https://t.me/share/url?text={quote('📥 حمّل أي فيديو أو أغنية MP3 في ثوانٍ!\\n\\n⚡ بوت سريع، مجاني وبأعلى جودة.\\n👇 جرّبه الآن:\\nhttps://t.me/MusicPlayZoneBot')}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌟 أعجبك البوت؟ شاركه", url=share_link)]])
            kw = {"chat_id": query.message.chat_id, "caption": f"- {esc(BOT_USERNAME)}، {format_duration(req['duration'])}", "reply_markup": kb, "parse_mode": "HTML", "read_timeout": 120, "write_timeout": 120}
            
            with open(target, "rb") as f:
                if mode == "audio":
                    t_file = open(t_path, "rb") if t_path and t_path.exists() else None
                    try: await ctx.bot.send_audio(audio=f, title=clean_title(req["title"], 80), performer=req["artist"], thumbnail=t_file, duration=int(req["duration"]), **kw)
                    finally:
                        if t_file: t_file.close()
                else: await ctx.bot.send_video(video=f, supports_streaming=True, duration=int(req["duration"]), **kw)

            stat_inc_sync("success"); stat_inc_sync("bytes", f_size); await query.message.delete()

    except Exception: stat_inc_sync("failed"); await edit_msg(query.message, "❌ فشل تحميل المقطع أو إرساله.")
    finally:
        stop.set(); await asyncio.gather(upd_task, return_exceptions=True)
        shutil.rmtree(job_dir, ignore_errors=True); ACTIVE_USERS.discard(uid)

# ==========================================================
# أوامر الإدارة الأساسية والتشغيل
# ==========================================================
async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id): ctx.user_data.pop("bc_active", None); await update.message.reply_text("🛠 <b>لوحة الإدارة المتقدمة</b>", reply_markup=admin_kbd(), parse_mode="HTML")

async def post_init(app: Application):
    try: await app.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("links", "روابط PlayZone")]); await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception: pass

def main():
    init_db()
    app = (Application.builder().token(TOKEN).base_url(LOCAL_API_URL) if LOCAL_API_URL else Application.builder().token(TOKEN)).post_init(post_init).concurrent_updates(True).build()
    for cmd, fn in [("start", start_cmd), ("links", lambda u, c: u.message.reply_text("💚 دعمك يصنع الفرق\nتابع الروابط:", reply_markup=links_kbd())), ("admin", admin_cmd)]: app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)); app.add_handler(CallbackQueryHandler(cb_handler))
    logger.info("🚀 تشغيل النسخة المدمجة والسريعة."); app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
