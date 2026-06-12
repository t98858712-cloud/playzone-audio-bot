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
for d in (BASE_DOWNLOAD_DIR, DATA_DIR): d.mkdir(exist_ok=True, parents=True)

DB_FILE, DB_LOCK, COOKIES_FILE = DATA_DIR / "bot_database.db", threading.Lock(), Path(os.getenv("COOKIES_FILE", "cookies.txt"))
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", str((2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024))))
PROGRESS_UPDATE_SECONDS, REQUEST_EXPIRE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0")), int(os.getenv("REQUEST_EXPIRE_SECONDS", "900"))
OLD_DOWNLOADS_EXPIRE_SECONDS, MAX_THUMBNAIL_BYTES = int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", "3600")), int(os.getenv("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))

MAX_WORKERS = max(2, int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2))))
DOWNLOAD_SEMAPHORE, EXECUTOR, ACTIVE_USERS, progress_lock = asyncio.Semaphore(MAX_WORKERS), ThreadPoolExecutor(max_workers=MAX_WORKERS), set(), threading.Lock()

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
WEBSITE_PLAYZONE, TELEGRAM_BOT_PLAYZONE = "http://tasmg1.github.io/tasmg/?", f"https://t.me/{BOT_USERNAME.replace('@', '')}"
FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr", "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO); logger = logging.getLogger("PlayZoneBot")
for noisy in ["httpx", "httpcore", "telegram", "telegram.ext"]: logging.getLogger(noisy).setLevel(logging.WARNING)

# ==========================================================
# إدارة قاعدة البيانات (SQLite3 WAL) المدمجة
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
    db_execute("PRAGMA journal_mode=WAL;"); db_execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER)"); db_execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
    with DB_LOCK, sqlite3.connect(DB_FILE) as conn: conn.executemany("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", [(k,) for k in ["requests", "success", "failed", "bytes", "broadcasts"]])

def register_user_sync(u):
    if not u: return
    now = int(time.time()); first_seen = (db_execute("SELECT first_seen FROM users WHERE id = ?", (u.id,), "one") or [now])[0]
    db_execute("INSERT OR REPLACE INTO users (id, username, first_name, last_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)", (u.id, u.username or "", u.first_name or "", u.last_name or "", first_seen, now))

def stat_inc_sync(key: str, value: int = 1): db_execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))
def load_stats_sync() -> dict: return {k: v for k, v in (db_execute("SELECT key, value FROM stats", fetch="all") or [])}
def all_user_ids() -> list: return [r[0] for r in (db_execute("SELECT id FROM users", fetch="all") or [])]
def get_latest_users(limit: int = 10) -> list: return db_execute(f"SELECT * FROM users ORDER BY last_seen DESC LIMIT {limit}", fetch="all_dict")

# ==========================================================
# أدوات الفحص والتنسيق
# ==========================================================
def is_admin(uid: int) -> bool: return uid in {int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()}
def esc(text) -> str: return html.escape(str(text or ""), quote=False)
def clean_title(text: str, limit=60) -> str: return (t := re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]+", "", str(text or "ملف ميديا"))).strip())[:limit] + "..." if len(t) > limit else t
def format_size(sz) -> str: return next((f"{float(sz)/1024**i:.1f} {u}" if float(sz)/1024**i < 1024 else "" for i, u in enumerate(["Bytes", "KB", "MB", "GB"])), f"{float(sz)/1024**3:.1f} GB") if float(sz) > 0 else "غير معروف"
def format_duration(sec) -> str: return f"{int(sec)//3600}:{(int(sec)%3600)//60:02d}:{int(sec)%60:02d}" if int(sec) >= 3600 else f"{int(sec)//60:02d}:{int(sec)%60:02d}"
def is_valid_url(t: str) -> bool: return len(t.strip()) <= 2000 and (p := urlparse(t.strip())).scheme in ["http", "https"] and bool(p.netloc) and not p.username
def get_thumbnail(info: dict) -> str: return max(info.get("thumbnails") or [], key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), default={}).get("url") or info.get("thumbnail") or ""
def get_artist(info: dict) -> str: return next((clean_title(info[k], 35) for k in ["artist", "uploader", "channel", "creator"] if info.get(k)), "غير معروف")
def make_progress_bar(pct: float) -> str: return "🟩" * (f := int(max(0, min(100, float(pct))) // 10)) + "⬜" * (10 - f)
def get_largest_estimated_size(info: dict) -> int: return max([int(f.get("filesize") or f.get("filesize_approx") or 0) for f in info.get("formats", [])], default=0)

def ensure_pending_requests(ctx: ContextTypes.DEFAULT_TYPE) -> dict: return ctx.user_data.setdefault("pending_requests", {})
def trim_old_pending_requests(ctx: ContextTypes.DEFAULT_TYPE, max_items=8):
    p, now = ensure_pending_requests(ctx), time.time()
    for rid in list(p):
        if now - p[rid].get("created_at", 0) > REQUEST_EXPIRE_SECONDS: p.pop(rid, None)
    if len(p) > max_items: ctx.user_data["pending_requests"] = dict(sorted(p.items(), key=lambda kv: kv[1].get("created_at", 0))[-max_items:])

def cookie_file_is_usable(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0: return False
        return any("youtube.com" in line.split("\t")[0] and (int(line.split("\t")[4]) > time.time() or int(line.split("\t")[4]) == 0) for line in open(path, "r", encoding="utf-8", errors="ignore") if line.strip() and not line.startswith("#") and len(line.split("\t")) >= 7)
    except Exception: return False

def _cleanup_old_downloads_sync():
    now = time.time()
    for item in BASE_DOWNLOAD_DIR.iterdir():
        if now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS: shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
def _force_cleanup_all_sync() -> int: return sum(1 for item in BASE_DOWNLOAD_DIR.iterdir() if not (shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)))

# ==========================================================
# الواجهات والأزرار والرسائل الآمنة
# ==========================================================
def user_main_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]], resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط هنا...")
def build_preview_keyboard(rid: str): return InlineKeyboardMarkup([[InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{rid}"), InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{rid}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{rid}")]])
def build_playzone_links_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)], [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)], [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)]])
def admin_main_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("👥 المستخدمون", callback_data="adm_users")], [InlineKeyboardButton("📢 إذاعة", callback_data="adm_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")], [InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server"), InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]])
def build_playzone_links_text(): return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."
def build_start_text(first_name: str): return f"أهلاً {esc(first_name)} 👋\n\nأرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل.\n\nابدأ بإرسال الرابط مباشرة."
def build_guide_text(): return "📘 طريقة الاستخدام\n\n1) انسخ رابط المقطع.\n2) أرسله هنا في البوت.\n3) انتظر ظهور المعاينة.\n4) اختر التحميل صوت أو فيديو."
def build_preview_caption(title, artist, duration, est_size): return f"🎬 <b>{esc(title)}</b>\n<b>{esc(artist)}</b>\n⏱ {esc(duration)} - 💾 {esc(est_size)}"

async def safe_delete(msg):
    try: await msg.delete()
    except Exception: pass

async def edit_message_smart(msg, text: str, reply_markup=None, parse_mode: str = "HTML"):
    try: await (msg.edit_caption if any(getattr(msg, k, None) for k in ["photo", "video", "document"]) else msg.edit_text)(caption=text, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise
    except Exception: pass

# ==========================================================
# محرك yt-dlp و FFmpeg ومهام التحميل
# ==========================================================
def get_ydl_options(job_dir: Path = None, p_data: dict = None, mode: str = "video"):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1", "retries": 15, "fragment_retries": 15, "concurrent_fragment_downloads": 10, "no_check_certificate": True, "http_headers": {"User-Agent": "Mozilla/5.0"}, "extractor_args": {"youtube": {"player_client": ["ios", "android", "webpage_safari"], "skip": ["webpage"]}}}
    if mode == "audio": opts["format"] = "bestaudio/best"
    else: opts.update({"format": f"bestvideo[height<=720][filesize<{'2000M' if LOCAL_API_URL else '50M'}]+bestaudio/best[height<=720]/best", "merge_output_format": "mp4", "postprocessors": [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]})
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0: opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if p_data:
        def hook(d):
            with progress_lock:
                if d.get("status") == "downloading":
                    down, tot, spd = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0, d.get("speed") or 0
                    p_data["text"] = f"📥 <b>جاري تحميل الملف...</b>\n\n{make_progress_bar(down/tot*100)}  {down/tot*100:.1f}%\n📦 الحجم: {format_size(down)} / {format_size(tot)}\n🚀 السرعة: {format_size(spd)}/ث" if tot else f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(down)}"
                elif d.get("status") == "finished": p_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
        opts["progress_hooks"] = [hook]
    return opts

def extract_metadata(url: str):
    with yt_dlp.YoutubeDL(dict(get_ydl_options(mode="video"), skip_download=True)) as ydl: return ydl.extract_info(url, download=False)

def download_thumbnail_safely(url: str, path: Path) -> Path | None:
    try:
        if not url: return None
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=6) as r:
            if len(data := r.read(MAX_THUMBNAIL_BYTES + 1)) <= MAX_THUMBNAIL_BYTES: path.write_bytes(data)
        return path if path.exists() else None
    except Exception: return None

def convert_to_mp3_local(inp: Path, out: Path, thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(inp)]
        if thumb and thumb.exists(): cmd.extend(["-i", str(thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else: cmd.extend(["-vn"])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(out)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return out.exists()
    except Exception: return False

async def run_progress_updates(msg, p_data: dict, stop: asyncio.Event):
    last = ""
    while not stop.is_set():
        with progress_lock: text = p_data.get("text", "")
        if text and text != last: await edit_message_smart(msg, text); last = text
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

# ==========================================================
# الأحداث (Handlers) والأوامر (Commands)
# ==========================================================
async def update_ytdlp_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd.effective_user.id): return
    msg = await upd.message.reply_text("🔄 جاري التحديث...")
    try: subprocess.check_call([os.sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]); await msg.edit_text("✅ تم تحديث `yt-dlp`.")
    except Exception as e: await msg.edit_text(f"❌ فشل: {e}")

async def set_cookie_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd.effective_user.id): return
    if not upd.message.document: return await upd.message.reply_text("📥 أرسل ملف `cookies.txt` كـ Document.")
    await (await ctx.bot.get_file(upd.message.document.file_id)).download_to_drive(COOKIES_FILE); await upd.message.reply_text("✅ تم استلام وتركيب ملف الكوكيز بنجاح!")

async def backup_db_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(upd.effective_user.id):
        try: await upd.message.reply_document(document=open(DB_FILE, "rb"), filename="bot_database.db", caption="📦 نسخة احتياطية.")
        except Exception as e: await upd.message.reply_text(f"❌ تعذر السحب: {e}")

async def start_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    register_user_sync(upd.effective_user)
    await upd.message.reply_text(build_start_text(upd.effective_user.first_name or ""), reply_markup=user_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

async def admin_panel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(upd.effective_user.id): ctx.user_data.pop("bc_active", None); await upd.message.reply_text("🛠 <b>لوحة الإدارة المتقدمة</b>\n\nأوامر:\n/update_dlp - تحديث المحرك\n/setcookie - تجديد الكوكيز\n/backup - نسخة قاعدة البيانات", reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def text_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not upd.message or not upd.message.text: return
    uid, text = upd.effective_user.id, upd.message.text.strip(); register_user_sync(upd.effective_user)

    if text in ["🔗 روابط PlayZone", "/links"]: return await upd.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)
    if text == "📘 دليل الاستخدام": return await upd.message.reply_text(build_guide_text())
    if is_admin(uid) and ctx.user_data.get("bc_active"):
        ctx.user_data["bc_active"] = False; s = await upd.message.reply_text("📢 جاري الإرسال..."); sent = fail = 0
        for u in all_user_ids():
            try: await ctx.bot.send_message(u, text, disable_web_page_preview=True); sent += 1; await asyncio.sleep(0.05)
            except Exception: fail += 1
        stat_inc_sync("broadcasts"); return await s.edit_text(f"✅ تم الإرسال.\nنجاح: {sent} | فشل: {fail}")
    if uid in ACTIVE_USERS: return await upd.message.reply_text("⏳ لديك تحميل قيد التنفيذ حالياً.")
    if not is_valid_url(text): return await upd.message.reply_text("❌ الرابط غير صحيح. يبدأ بـ http:// أو https://")

    status = await upd.message.reply_text("🔍 جاري تجهيز المعاينة...")
    try:
        info = await asyncio.get_running_loop().run_in_executor(EXECUTOR, lambda: extract_metadata(text))
        rid, title, artist, dur, thumb = uuid.uuid4().hex[:10], clean_title(info.get("title")), get_artist(info), info.get("duration") or 0, get_thumbnail(info)
        ensure_pending_requests(ctx)[rid] = {"url": text, "title": title, "artist": artist, "duration": dur, "thumb_url": thumb, "created_at": time.time()}; trim_old_pending_requests(ctx)
        await safe_delete(status)
        cap = build_preview_caption(title, artist, format_duration(dur), format_size(get_largest_estimated_size(info)))
        if thumb and thumb.startswith("http"):
            try: return await upd.message.reply_photo(thumb, caption=cap, reply_markup=build_preview_keyboard(rid), parse_mode="HTML")
            except Exception: pass
        await upd.message.reply_text(cap, reply_markup=build_preview_keyboard(rid), parse_mode="HTML", disable_web_page_preview=True); stat_inc_sync("requests")
    except Exception: await status.edit_text("❌ تعذر قراءة الرابط. تأكد أنه متاح للعامة وغير محذوف.")

async def cb_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = upd.callback_query; uid, data = query.from_user.id, query.data or ""
    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("للإدارة فقط", show_alert=True)
        if data == "adm_close": await query.answer("تم الإغلاق"); return await safe_delete(query.message)
        elif data == "adm_stats": s = load_stats_sync(); return await edit_message_smart(query.message, f"📊 <b>الإحصائيات</b>\nطلبات: {s.get('requests',0)}\nنجاح: {s.get('success',0)}\nفشل: {s.get('failed',0)}\nمستخدمين: {len(all_user_ids())}\nمستندات: {format_size(s.get('bytes',0))}", admin_main_keyboard())
        elif data == "adm_users": return await edit_message_smart(query.message, "\n".join(["👥 <b>آخر النشطين:</b>"] + [f"• {esc(u.get('first_name'))} — @{esc(u.get('username') or '-')} — <code>{u.get('id')}</code>" for u in get_latest_users()]), admin_main_keyboard())
        elif data == "adm_server": f = list(BASE_DOWNLOAD_DIR.rglob("*")); return await edit_message_smart(query.message, f"📁 <b>السيرفر</b>\nمؤقت: {sum(1 for p in f if p.is_file())} ({format_size(sum(p.stat().st_size for p in f if p.is_file()))})\nعمليات: {len(ACTIVE_USERS)} / {MAX_WORKERS}", admin_main_keyboard())
        elif data == "adm_clean": await query.answer("جاري التنظيف..."); return await edit_message_smart(query.message, f"🧹 تم. محذوفات: {await asyncio.get_running_loop().run_in_executor(None, _force_cleanup_all_sync)}", admin_main_keyboard())
        elif data == "adm_bc": ctx.user_data["bc_active"] = True; return await edit_message_smart(query.message, "📢 أرسل نص الرسالة:", InlineKeyboardMarkup([[(InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_bc"))]]))
        elif data == "adm_cancel_bc": ctx.user_data["bc_active"] = False; return await edit_message_smart(query.message, "تم الإلغاء.", admin_main_keyboard())

    if data.startswith("cancel:"): ensure_pending_requests(ctx).pop(data.split(":")[1], None); await query.answer("تم الإلغاء"); return await safe_delete(query.message)
    if data.startswith(("aud:", "vid:")):
        req = ensure_pending_requests(ctx).pop(data.split(":")[1], None); trim_old_pending_requests(ctx)
        if not req: return await query.answer("انتهت الجلسة، أعد الإرسال.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ.", show_alert=True)
        asyncio.create_task(process_download(query, ctx, req, "audio" if data.startswith("aud:") else "video"))

async def process_download(q, ctx, req, mode):
    uid, loop, job_dir = q.from_user.id, asyncio.get_running_loop(), BASE_DOWNLOAD_DIR / f"{uid}_{uuid.uuid4().hex[:6]}"; job_dir.mkdir(parents=True, exist_ok=True); ACTIVE_USERS.add(uid)
    stop, p_data = asyncio.Event(), {"text": "⏳ يرجى الانتظار..."}; upd_task = asyncio.create_task(run_progress_updates(q.message, p_data, stop))

    try:
        try: await q.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        async with DOWNLOAD_SEMAPHORE:
            with progress_lock: p_data["text"] = "🚀 بدأ التحميل..."
            t_path = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(req["thumb_url"], job_dir / "thumb.jpg"))
            await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(get_ydl_options(job_dir, p_data, mode)).extract_info(req["url"], download=True))
            if not (files := [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".ytdl"]]): raise Exception("فشل الحفظ")
            target = max(files, key=lambda p: p.stat().st_mtime)

            if mode == "audio":
                with progress_lock: p_data["text"] = "🎵 جاري تحويل الصوت ودمج الغلاف الخارجي..."
                mp3 = job_dir / "final.mp3"; target = mp3 if await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(target, mp3, t_path)) else target

            if (f_size := target.stat().st_size) > MAX_TELEGRAM_SIZE: stop.set(); return await edit_message_smart(q.message, f"❌ حجم الملف يتجاوز الحد.\n\nالحجم: {format_size(f_size)}")
            stop.set(); await edit_message_smart(q.message, "📤 تم التجهيز، جاري الإرسال...", reply_markup=None)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=f"https://t.me/share/url?url={quote(req['url'])}&text={quote('🎬 '+clean_title(req['title']))}")]])
            kw = {"chat_id": q.message.chat_id, "caption": f"- {esc(BOT_USERNAME)}، {esc(format_duration(req['duration']))}", "duration": int(req["duration"]), "reply_markup": kb, "parse_mode": "HTML", "read_timeout": 120, "write_timeout": 120}

            with open(target, "rb") as f:
                if mode == "audio":
                    t_f = open(t_path, "rb") if t_path and t_path.exists() else None
                    try: await ctx.bot.send_audio(audio=f, title=clean_title(req["title"], 80), performer=req["artist"], thumbnail=t_f, **kw)
                    finally:
                        if t_f: t_f.close()
                else: await ctx.bot.send_video(video=f, supports_streaming=True, **kw)
            stat_inc_sync("success"); stat_inc_sync("bytes", f_size); await safe_delete(q.message)
    except Exception as e: stat_inc_sync("failed"); await edit_message_smart(q.message, "❌ فشل تحميل المقطع أو تجاوز الحد.")
    finally: stop.set(); await asyncio.gather(upd_task, return_exceptions=True); shutil.rmtree(job_dir, ignore_errors=True); ACTIVE_USERS.discard(uid)

# ==========================================================
# التشغيل الرئيسي
# ==========================================================
async def post_init(app: Application):
    try: await app.bot.set_my_commands([BotCommand("start", "بدء"), BotCommand("links", "دعم روابط PlayZone")]); await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception: pass

def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN غير متوفر!")
    init_db(); _cleanup_old_downloads_sync()
    app = (Application.builder().token(TOKEN).base_url(LOCAL_API_URL) if LOCAL_API_URL else Application.builder().token(TOKEN)).post_init(post_init).connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30).concurrent_updates(True).build()
    
    for c, f in [("start", start), ("links", show_playzone_links), ("admin", admin_panel), ("update_dlp", update_ytdlp_cmd), ("setcookie", set_cookie_cmd), ("backup", backup_db_cmd)]: app.add_handler(CommandHandler(c, f))
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_cmd)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)); app.add_handler(CallbackQueryHandler(cb_handler))
    
    logger.info("🚀 تشغيل النسخة المسرعة والنهائية."); app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__": main()
