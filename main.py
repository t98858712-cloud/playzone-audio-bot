import os
import sys
import time
import html
import uuid
import asyncio
import shutil
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
import aiosqlite
import google.generativeai as genai
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================================
# 1. إعدادات بيئة الإنتاج والملفات (Production Environment)
# ==========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL") 
PROXY_URL = os.getenv("PROXY_URL") 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "bot_database.db"
COOKIES_FILE = Path("cookies.txt")
LOG_FILE = DATA_DIR / "bot.log"

# إعداد نظام التسجيل (Logging)
file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger("PlayZone_Enterprise")
logging.getLogger("httpx").setLevel(logging.WARNING)

# تهيئة Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    ai_model = None

# الذاكرة المؤقتة للسرعة القصوى
KNOWN_USERS_CACHE = set()
BANNED_USERS_CACHE = set()
DYNAMIC_ADMINS_CACHE = set()
ACTIVE_USERS = set()
BROADCAST_QUEUE = asyncio.Queue()

MAX_TELEGRAM_SIZE = (2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024)
MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(max(4, (os.cpu_count() or 2) * 2))))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_WORKERS)
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS)

BOT_USERNAME = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot")
progress_lock = threading.Lock()

# ==========================================================
# 2. وظائف الحماية والبيانات
# ==========================================================

def is_admin(user_id: int) -> bool:
    env_admins = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    return user_id in env_admins or user_id in DYNAMIC_ADMINS_CACHE

def format_size(size_bytes) -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return "غير معروف"
    if size_bytes <= 0: return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def generate_users_file(rows, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("👥 قائمة مستخدمي البوت:\n" + "="*60 + "\n")
        for r in rows:
            uid, un, fn, ban, adm = r
            status = " [محظور 🚫]" if ban else (" [إدمن 🛡️]" if adm else "")
            un_str = f"@{un}" if un else "بدون يوزر"
            f.write(f"ID: {uid} | الاسم: {fn} | اليوزر: {un_str}{status}\n")

# ==========================================================
# 3. إدارة قاعدة البيانات
# ==========================================================

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen INTEGER
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
        
        try: await db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        except: pass

        for k in ["requests", "success", "failed", "bytes", "broadcasts"]:
            await db.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (k,))
        await db.commit()
        
        async with db.execute("SELECT id, is_banned, is_admin FROM users") as cursor:
            async for row in cursor:
                uid, banned, admin = row[0], row[1], row[2]
                KNOWN_USERS_CACHE.add(uid)
                if banned == 1: BANNED_USERS_CACHE.add(uid)
                if admin == 1: DYNAMIC_ADMINS_CACHE.add(uid)

async def register_user_cached(user):
    if not user or user.id in KNOWN_USERS_CACHE: return
    now = int(time.time())
    KNOWN_USERS_CACHE.add(user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO users (id, username, first_name, first_seen) VALUES (?, ?, ?, ?)", 
                         (user.id, user.username or "", user.first_name or "", now))
        await db.commit()

async def stat_inc(key: str, value: int = 1):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))
        await db.commit()

async def get_all_stats() -> dict:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT key, value FROM stats") as cursor:
            rows = await cursor.fetchall()
            return {k: v for k, v in rows}

async def update_user_status(user_id: int, column: str, value: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, user_id))
        await db.commit()

# ==========================================================
# 4. أوامر الإدارة من خارج الكود
# ==========================================================

async def manage_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = update.message.text.split()
    if len(msg) < 2 or not msg[1].isdigit():
        return await update.message.reply_text("❌ الصيغة الخاطئة. استخدم مثال: /ban 123456789")
    
    target_id = int(msg[1])
    command = msg[0].lower()

    if command == "/ban":
        await update_user_status(target_id, "is_banned", 1)
        BANNED_USERS_CACHE.add(target_id)
        await update.message.reply_text(f"✅ تم حظر المستخدم: <code>{target_id}</code>", parse_mode="HTML")
    elif command == "/unban":
        await update_user_status(target_id, "is_banned", 0)
        BANNED_USERS_CACHE.discard(target_id)
        await update.message.reply_text(f"✅ تم رفع الحظر عن: <code>{target_id}</code>", parse_mode="HTML")
    elif command == "/promote":
        await update_user_status(target_id, "is_admin", 1)
        DYNAMIC_ADMINS_CACHE.add(target_id)
        await update.message.reply_text(f"✅ تمت ترقية المستخدم للإدارة: <code>{target_id}</code>", parse_mode="HTML")
    elif command == "/demote":
        await update_user_status(target_id, "is_admin", 0)
        DYNAMIC_ADMINS_CACHE.discard(target_id)
        await update.message.reply_text(f"✅ تم سحب صلاحيات الإدارة من: <code>{target_id}</code>", parse_mode="HTML")

async def db_restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        return await update.message.reply_text("❌ يجب الرد (Reply) على ملف قاعدة البيانات .db بهذا الأمر.")
    doc = update.message.reply_to_message.document
    if not doc.file_name.endswith(".db"):
        return await update.message.reply_text("❌ الملف غير مدعوم، يجب أن يكون بصيغة .db")
    
    new_file = await context.bot.get_file(doc.file_id)
    await new_file.download_to_drive(DB_FILE)
    KNOWN_USERS_CACHE.clear(); BANNED_USERS_CACHE.clear(); DYNAMIC_ADMINS_CACHE.clear()
    await init_db()
    await update.message.reply_text("✅ تم استعادة قاعدة البيانات وتحديث الذاكرة المؤقتة بنجاح.")

# ==========================================================
# 5. لوحة تحكم الإدمن التفاعلية (Inline Panel)
# ==========================================================

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server")],
        [InlineKeyboardButton("👥 أسماء المستخدمين", callback_data="adm_users_list"), InlineKeyboardButton("🧹 تنظيف المؤقتات", callback_data="adm_clean")],
        [InlineKeyboardButton("📦 نسخة احتياطية", callback_data="adm_backup"), InlineKeyboardButton("📄 سجلات النظام", callback_data="adm_logs")],
        [InlineKeyboardButton("🧠 تحليل الأخطاء (Gemini)", callback_data="adm_ai_debug")],
        [InlineKeyboardButton("🔄 تحديث yt-dlp", callback_data="adm_update"), InlineKeyboardButton("⚠️ إعادة التشغيل", callback_data="adm_reboot")],
        [InlineKeyboardButton("✖️ إغلاق اللوحة", callback_data="adm_close")]
    ])

async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = (
        "🛠 <b>نظام التشغيل المصغر (Enterprise Admin)</b>\n\n"
        "<i>الأوامر السريعة:</i>\n"
        "• <code>/broadcast نص</code> : للإذاعة.\n"
        "• <code>/ban ID</code> | <code>/unban ID</code> : إدارة الحظر.\n"
        "• <code>/promote ID</code> | <code>/demote ID</code> : إدارة المدراء.\n"
        "• <code>/restore</code> : (رد على ملف .db للاستعادة).\n"
        "• <code>/setcookie</code> : (رد على ملف الكوكيز).\n"
    )
    await update.message.reply_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def set_cookie_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    doc = update.message.document or (update.message.reply_to_message and update.message.reply_to_message.document)
    if not doc:
        return await update.message.reply_text("📥 أرسل ملف `cookies.txt` أو قم بالرد عليه بالأمر.")
    new_file = await context.bot.get_file(doc.file_id)
    await new_file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ تم تركيب ملف الكوكيز الجديد بـنجاح.")

# ==========================================================
# 6. محرك التحميل (yt-dlp Engine)
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "retries": 15, "fragment_retries": 15, "socket_timeout": 30, "cachedir": False,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    }
    if PROXY_URL: opts["proxy"] = PROXY_URL
    if mode == "audio":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        opts["format"] = f"bestvideo[ext=mp4][height<=720][filesize<{max_fs}]+bestaudio[ext=m4a]/best"
        opts["merge_output_format"] = "mp4"

    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        opts["cookiefile"] = str(COOKIES_FILE)

    if job_dir: opts["outtmpl"] = str(job_dir / "media.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
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
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    percent = (d.get("downloaded_bytes", 0) / total) * 100
                    progress_data["text"] = f"📥 جاري التحميل: {percent:.1f}%"
            elif d.get("status") == "finished":
                progress_data["text"] = "⚙️ جاري التجهيز للإرسال..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await message.edit_text(text)
                last_text = text
            except Exception: pass
        await asyncio.sleep(4.0)

# ==========================================================
# 7. نظام الإذاعة بالخلفية
# ==========================================================

async def broadcast_worker(app: Application):
    while True:
        text, users_list, status_msg = await BROADCAST_QUEUE.get()
        sent, fail = 0, 0
        for i, user_id in enumerate(users_list):
            try:
                await app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
                sent += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
                    sent += 1
                except Exception: fail += 1
            except Exception: fail += 1
            await asyncio.sleep(0.04) 

            if i % 1000 == 0 and i > 0:
                try: await status_msg.edit_text(f"📢 جاري الإذاعة...\nتم الإرسال: {sent}\nفشل: {fail}")
                except Exception: pass

        await stat_inc("broadcasts")
        try: await status_msg.edit_text(f"✅ انتهت الإذاعة!\nالناجح: {sent}\nالفاشل: {fail}")
        except Exception: pass
        BROADCAST_QUEUE.task_done()

async def trigger_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace("/broadcast", "", 1).strip()
    if not text: return await update.message.reply_text("اكتب الرسالة بعد الأمر:\n/broadcast رسالتك هنا")

    status = await update.message.reply_text("⏳ جاري سحب بيانات المستخدمين...")
    users = list(KNOWN_USERS_CACHE - BANNED_USERS_CACHE) 
    if not users: return await status.edit_text("❌ لا يوجد مستخدمين مسجلين.")

    await BROADCAST_QUEUE.put((text, users, status))
    await status.edit_text(f"📢 تمت الجدولة لـ ({len(users)} مستخدم). ستعمل في الخلفية.")

# ==========================================================
# 8. معالجة الطلبات، الدردشة مع الذكاء الاصطناعي والأزرار
# ==========================================================

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    uid = update.effective_user.id
    if uid in BANNED_USERS_CACHE: return 
    
    await register_user_cached(update.effective_user)
    text = update.message.text.strip()

    if uid in ACTIVE_USERS:
        return await update.message.reply_text("⏳ لديك طلب قيد التنفيذ.")
    
    # الدردشة الذكية إذا لم يكن النص رابطاً
    if not text.startswith(("http://", "https://")):
        if ai_model:
            status = await update.message.reply_text("💭 لحظات...")
            try:
                response = await asyncio.to_thread(ai_model.generate_content, text)
                reply_text = response.text[:4000] # حدود تيليجرام
                try:
                    return await status.edit_text(reply_text, parse_mode="Markdown")
                except Exception:
                    return await status.edit_text(reply_text) # في حال وجود خطأ بتنسيق الماركدوان
            except Exception as e:
                logger.error(f"Gemini Error: {e}")
                return await status.edit_text("❌ عذراً، لا يمكنني الاستجابة الآن بسبب ضغط على خوادم الذكاء الاصطناعي.")
        else:
            return await update.message.reply_text("❌ أرسل رابط صالح يبدأ بـ http:// أو https://")

    # تحميل الميديا إذا كان رابطاً
    status = await update.message.reply_text("🔍 جاري الفحص...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))

        request_id = uuid.uuid4().hex[:8]
        context.user_data[request_id] = {"url": text, "title": info.get("title", "ملف ميديا")}

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{request_id}"),
             InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{request_id}")]
        ])
        
        await status.edit_text(f"🎬 <b>{html.escape(info.get('title', 'ميديا'))}</b>", reply_markup=kb, parse_mode="HTML")
        await stat_inc("requests")
    except Exception as e:
        logger.warning(f"Metadata extraction failed: {e}")
        await status.edit_text("❌ الرابط غير مدعوم، محذوف، أو محمي بقوة من المنصة.")

async def process_download(query, context, request_id, mode):
    uid = query.from_user.id
    if uid in BANNED_USERS_CACHE: return await query.answer("أنت محظور من الاستخدام.", show_alert=True)
    
    req = context.user_data.pop(request_id, None)
    if not req: return await query.answer("انتهت الجلسة، أرسل الرابط مجدداً.", show_alert=True)
    
    ACTIVE_USERS.add(uid)
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{uuid.uuid4().hex[:6]}"
    job_dir.mkdir(exist_ok=True)
    stop_event = asyncio.Event()
    progress_data = {"text": "⏳ يتم الآن حجز مكان لك في الطابور..."}
    
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        async with DOWNLOAD_SEMAPHORE:
            progress_data["text"] = "🚀 بدأ التحميل..."
            loop = asyncio.get_running_loop()
            
            opts = get_ydl_options(job_dir, progress_data, mode)
            await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(req["url"], download=True))
            
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".ytdl"]]
            if not files: raise RuntimeError("لم يتم العثور على الملف النهائي")
            target_file = files[0]

            file_size = target_file.stat().st_size
            if file_size > MAX_TELEGRAM_SIZE:
                stop_event.set()
                return await query.message.edit_text("❌ الملف كبير جداً.")

            stop_event.set()
            await query.message.edit_text("📤 تم التجهيز، جاري الإرسال...")
            caption = f"🎬 {html.escape(req['title'])}\n\n🤖 {BOT_USERNAME}"
            
            with open(target_file, "rb") as f:
                if mode == "audio":
                    await context.bot.send_audio(query.message.chat_id, audio=f, title=req["title"], caption=caption, parse_mode="HTML", read_timeout=120)
                else:
                    await context.bot.send_video(query.message.chat_id, video=f, caption=caption, supports_streaming=True, parse_mode="HTML", read_timeout=120)
            
            await stat_inc("success")
            await stat_inc("bytes", file_size)
            try: await query.message.delete()
            except Exception: pass

    except (TimedOut, NetworkError):
        await stat_inc("failed")
        try: await query.message.edit_text("❌ تأخر السيرفر في رفع الملف.")
        except Exception: pass
    except Exception as e:
        await stat_inc("failed")
        logger.error(f"Download Error: {e}")
        try: await query.message.edit_text("❌ فشل التحميل.")
        except Exception: pass
    finally:
        stop_event.set()
        try: await updater_task
        except Exception: pass
        await asyncio.to_thread(shutil.rmtree, job_dir, ignore_errors=True)
        ACTIVE_USERS.discard(uid)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id
    
    if data.startswith("adm_"):
        if not is_admin(uid): 
            return await query.answer("❌ لا تملك صلاحيات.", show_alert=True)
        
        if data == "adm_close":
            await query.answer("تم الإغلاق.")
            try: await query.message.delete()
            except Exception: pass
        
        elif data == "adm_stats":
            await query.answer()
            stats = await get_all_stats()
            text = (
                "📊 <b>إحصائيات البوت</b>\n\n"
                f"👥 المستخدمين: <code>{len(KNOWN_USERS_CACHE)}</code> | 🚫 المحظورين: <code>{len(BANNED_USERS_CACHE)}</code>\n"
                f"📥 الطلبات: <code>{stats.get('requests', 0)}</code>\n"
                f"✅ ناجح: <code>{stats.get('success', 0)}</code> | ❌ فاشل: <code>{stats.get('failed', 0)}</code>\n"
                f"💾 تبادل البيانات: <code>{format_size(stats.get('bytes', 0))}</code>\n"
                f"📢 الإذاعات: <code>{stats.get('broadcasts', 0)}</code>"
            )
            await query.message.edit_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")
            
        elif data == "adm_users_list":
            await query.answer("جاري استخراج بيانات المستخدمين...")
            try:
                users_file = DATA_DIR / "users_list.txt"
                async with aiosqlite.connect(DB_FILE) as db:
                    async with db.execute("SELECT id, username, first_name, is_banned, is_admin FROM users ORDER BY first_seen DESC") as cursor:
                        rows = await cursor.fetchall()
                
                await asyncio.to_thread(generate_users_file, rows, users_file)
                with open(users_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id=uid, document=f, filename="Users_List.txt", 
                        caption=f"👥 قائمة بجميع المستخدمين المسجلين ({len(rows)} مستخدم)."
                    )
                users_file.unlink(missing_ok=True)
            except Exception as e:
                await query.message.edit_text(f"❌ فشل سحب البيانات: {e}", reply_markup=admin_main_keyboard())

        elif data == "adm_server":
            await query.answer()
            total_size = sum(p.stat().st_size for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
            file_count = sum(1 for p in BASE_DOWNLOAD_DIR.rglob("*") if p.is_file())
            text = (
                "📁 <b>حالة السيرفر</b>\n\n"
                f"📂 ملفات عالقة: <code>{file_count}</code>\n"
                f"💾 مساحة مستهلكة: <code>{format_size(total_size)}</code>\n"
                f"⚡ نشط حالياً: <code>{len(ACTIVE_USERS)} / {MAX_WORKERS}</code>\n"
                f"🛡️ مدراء السيرفر: <code>{len(DYNAMIC_ADMINS_CACHE) + 1}</code>"
            )
            await query.message.edit_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")
            
        elif data == "adm_clean":
            await query.answer("جاري مسح الملفات...")
            def clear_cache():
                c = 0
                for item in BASE_DOWNLOAD_DIR.iterdir():
                    try:
                        shutil.rmtree(item) if item.is_dir() else item.unlink()
                        c += 1
                    except Exception: pass
                return c
            removed = await asyncio.to_thread(clear_cache)
            await query.message.edit_text(f"🧹 تم التنظيف.\nالملفات المحذوفة: {removed}", reply_markup=admin_main_keyboard())

        elif data == "adm_update":
            await query.answer("جاري التحديث...")
            await query.message.edit_text("🔄 جاري تحديث `yt-dlp`...", parse_mode="Markdown")
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "-U", "yt-dlp",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if process.returncode == 0:
                    await query.message.edit_text("✅ تم تحديث المحرك بنجاح!", reply_markup=admin_main_keyboard())
                else:
                    await query.message.edit_text(f"❌ فشل:\n<code>{stderr.decode()}</code>", reply_markup=admin_main_keyboard(), parse_mode="HTML")
            except Exception as e:
                await query.message.edit_text(f"❌ خطأ: {e}", reply_markup=admin_main_keyboard())
        
        elif data == "adm_ai_debug":
            await query.answer("جاري تحليل المشاكل عبر الذكاء الاصطناعي...")
            if not ai_model:
                return await query.message.edit_text("❌ لم تقم بإضافة GEMINI_API_KEY في السيرفر.", reply_markup=admin_main_keyboard())
            
            try:
                if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
                    return await query.message.edit_text("✅ النظام نظيف تماماً، لا توجد سجلات أخطاء لتحليلها.", reply_markup=admin_main_keyboard())
                
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    logs_data = "".join(f.readlines()[-60:]) # سحب آخر 60 سطراً من الأخطاء
                
                prompt = f"أنت خبير في سيرفرات بايثون وبوتات تيليجرام. راجع هذه السجلات الخاصة بسيرفري، هل توجد أخطاء؟ اشرحها باختصار وقدم كود الإصلاح:\n\n{logs_data}"
                response = await asyncio.to_thread(ai_model.generate_content, prompt)
                
                reply = response.text[:4000]
                try:
                    await query.message.edit_text(f"🧠 **تحليل Gemini للسيرفر:**\n\n{reply}", parse_mode="Markdown", reply_markup=admin_main_keyboard())
                except Exception:
                    await query.message.edit_text(f"🧠 تحليل Gemini:\n\n{reply}", reply_markup=admin_main_keyboard())
            except Exception as e:
                await query.message.edit_text(f"❌ تعذر الاتصال بالذكاء الاصطناعي: {e}", reply_markup=admin_main_keyboard())

        elif data == "adm_backup":
            await query.answer("جاري تحضير النسخة...")
            try:
                with open(DB_FILE, "rb") as f:
                    await context.bot.send_document(chat_id=uid, document=f, filename="bot_backup.db", caption="📦 نسخة احتياطية من قاعدة البيانات.")
            except Exception as e:
                await query.message.edit_text(f"❌ فشل: {e}", reply_markup=admin_main_keyboard())
                
        elif data == "adm_logs":
            await query.answer("جاري سحب السجلات...")
            if LOG_FILE.exists():
                try:
                    with open(LOG_FILE, "rb") as f:
                        await context.bot.send_document(chat_id=uid, document=f, filename="activity.log", caption="📄 سجلات النظام بالكامل.")
                except Exception: pass
            else:
                await query.message.edit_text("❌ لا يوجد ملف سجلات حتى الآن.", reply_markup=admin_main_keyboard())

        elif data == "adm_reboot":
            await query.answer("جاري إعادة التشغيل...")
            await query.message.edit_text("⚠️ <b>جاري عمل Reboot للسيرفر...</b>\n\nسيعود البوت للعمل خلال ثوانٍ.", parse_mode="HTML")
            os.execv(sys.executable, ['python'] + sys.argv)
            
        return

    await query.answer() 
    if data.startswith(("aud:", "vid:")):
        mode = "audio" if data.startswith("aud") else "video"
        request_id = data.split(":")[1]
        asyncio.create_task(process_download(query, context, request_id, mode))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in BANNED_USERS_CACHE: return
    await register_user_cached(update.effective_user)
    text = (
        f"أهلاً بك 👋\n\n"
        "🎬 أرسل لي رابط فيديو لأقوم بتحميله.\n"
        "💬 أو تحدث معي وسأجيبك كذكاء اصطناعي!\n\n"
        "دعمك يهمنا، شارك البوت مع أصدقائك 💚"
    )
    await update.message.reply_text(text)

# ==========================================================
# 9. التشغيل
# ==========================================================

async def post_init(app: Application):
    await init_db()
    asyncio.create_task(broadcast_worker(app))
    try: await app.bot.set_my_commands([BotCommand("start", "بدء استخدام البوت")])
    except Exception: pass

def main():
    if not TOKEN: raise RuntimeError("مفقود: المتغير البيئي TELEGRAM_TOKEN")

    builder = Application.builder().token(TOKEN)
    if LOCAL_API_URL: builder.base_url(LOCAL_API_URL)

    app = (
        builder.post_init(post_init)
        .connect_timeout(30).read_timeout(120).write_timeout(120).pool_timeout(30)
        .concurrent_updates(True)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel_cmd))
    app.add_handler(CommandHandler(["ban", "unban", "promote", "demote"], manage_users_cmd))
    app.add_handler(CommandHandler("broadcast", trigger_broadcast))
    app.add_handler(CommandHandler("setcookie", set_cookie_cmd))
    app.add_handler(CommandHandler("restore", db_restore_cmd))
    
    app.add_handler(MessageHandler(filters.Document.ALL, set_cookie_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🚀 تم إطلاق البوت (Zero-Touch Admin Ready + AI Enabled).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
