import os
import re
import sys
import time
import html
import uuid
import asyncio
import shutil
import logging
import threading
import urllib.request
import ipaddress
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
import aiosqlite

# استخدام المكتبة الجديدة والحديثة من جوجل للذكاء الاصطناعي
from google import genai
from google.genai import types

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError, Conflict, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================================
# 1. إعدادات PlayZone والبيئة الإنتاجية
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

MAX_THUMBNAIL_BYTES = int(os.getenv("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))
PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))

file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger("PlayZone_Enterprise")
logging.getLogger("httpx").setLevel(logging.WARNING)

if not shutil.which("ffmpeg"):
    logger.error("⚠️ تحذير: برنامج FFmpeg غير مثبت في السيرفر! دمج الفيديو والصوت قد يفشل.")

# ==========================================================
# 🤖 إعداد الذكاء الاصطناعي (أحدث إصدار من Google GenAI)
# ==========================================================
USER_CHATS = {} 
USER_CHAT_COUNT = {}

if GEMINI_API_KEY:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    genai_aclient = genai_client.aio 
    
    system_persona = (
        "أنت المساعد الذكي والودود لبوت PlayZone على تيليجرام. "
        "مهمتك هي مساعدة المستخدمين، الإجابة على أسئلتهم بأسلوب احترافي، وتصحيح نصوصهم إذا طلبوا ذلك. "
        "تتحدث العربية بطلاقة وبأسلوب محبب. "
        "البوت يستطيع تحميل أي فيديو أو صوت من الإنترنت. إذا طلب المستخدم تحميل شيء، "
        "أخبره ببساطة أن يرسل الرابط مباشرة في المحادثة لتقوم بتحميله فوراً."
    )
    
    safety_settings = [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
    ]
    
    ai_config = types.GenerateContentConfig(
        system_instruction=system_persona,
        safety_settings=safety_settings
    )
else:
    genai_aclient = None
    ai_config = None

# ==========================================================
# 2. الذاكرة المؤقتة والتحكم
# ==========================================================
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
WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?"
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr"
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr"
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ=="
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{BOT_USERNAME.replace('@', '')}"

progress_lock = threading.Lock()

# ==========================================================
# 3. أدوات الفحص وقواعد البيانات
# ==========================================================

def is_admin(user_id: int) -> bool:
    env_admins = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    return user_id in env_admins or user_id in DYNAMIC_ADMINS_CACHE

def esc(text) -> str: return html.escape(str(text or ""), quote=False)

# دالة ذكية لتنظيف نصوص الذكاء الاصطناعي لمنع تعطل تيليجرام
def clean_markdown_for_telegram(text: str) -> str:
    # نقوم بإرسال النص كما هو بدون تنسيق Markdown كحل نهائي ومستقر
    return text.replace("*", "").replace("_", "").replace("`", "")

def clean_title(text: str, limit=60) -> str:
    if not text: return "ملف ميديا"
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes) -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return "غير معروف"
    if size_bytes <= 0: return "غير معروف"
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if size_bytes < 1024.0: return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds) -> str:
    try: seconds = int(seconds)
    except Exception: return "00:00"
    if seconds <= 0: return "00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def is_valid_url(text: str) -> bool:
    try:
        if len(text) > 2000: return False
        parsed = urlparse(text)
        if parsed.scheme not in ["http", "https"] or not parsed.netloc: return False
        return True
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

def get_largest_estimated_size(info: dict) -> int:
    sizes = []
    for f in info.get("formats", []) or []:
        try: sizes.append(int(f.get("filesize") or f.get("filesize_approx") or 0))
        except Exception: pass
    return max(sizes) if sizes else 0

def generate_users_file(rows, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("👥 قائمة مستخدمي البوت:\n" + "="*60 + "\n")
        for r in rows:
            uid, un, fn, ln, ban, adm = r
            status = " [محظور 🚫]" if ban else (" [إدمن 🛡️]" if adm else "")
            un_str = f"@{un}" if un else "بدون يوزر"
            full = f"{fn or ''} {ln or ''}".strip()
            f.write(f"ID: {uid} | الاسم: {full} | اليوزر: {un_str}{status}\n")

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, first_seen INTEGER, last_seen INTEGER, is_banned INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
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
    if not user: return
    now = int(time.time())
    is_new = user.id not in KNOWN_USERS_CACHE
    KNOWN_USERS_CACHE.add(user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        if is_new:
            await db.execute("INSERT OR IGNORE INTO users (id, username, first_name, last_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)", 
                             (user.id, user.username or "", user.first_name or "", user.last_name or "", now, now))
        else:
            await db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, user.id))
        await db.commit()

async def stat_inc(key: str, value: int = 1):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE stats SET value = value + ? WHERE key = ?", (value, key))
        await db.commit()

async def get_all_stats() -> dict:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT key, value FROM stats") as cursor:
            return {k: v for k, v in await cursor.fetchall()}

async def update_user_status(user_id: int, column: str, value: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, user_id))
        await db.commit()

# ==========================================================
# 4. واجهات ونصوص PlayZone الأصلية
# ==========================================================

def user_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📘 دليل الاستخدام")], [KeyboardButton("🔗 روابط PlayZone")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="أرسل الرابط هنا..."
    )

def build_preview_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"aud:{request_id}"),
         InlineKeyboardButton("🎬 تحميل فيديو", callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{request_id}")],
    ])

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
    ])

def build_playzone_links_text() -> str:
    return "💚 دعمك يصنع الفرق\n\nتابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\nكل متابعة تساعدنا نكبر ونقدّم تجربة أفضل."

def build_start_text(first_name: str) -> str:
    return (
        f"أهلاً {esc(first_name)} 👋\n\n"
        "أرسل رابط فيديو أو صوت، وسأعرض لك معاينة قبل التحميل.\n\n"
        "💚 دعمك يصنع الفرق\n\n"
        "تابع روابط PlayZone الرسمية وشاركها مع أصدقائك،\n"
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

async def safe_delete(message):
    try: await message.delete()
    except Exception: pass

async def edit_message_smart(message, text: str, reply_markup=None):
    try: await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): pass
    except Exception: pass

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    # إرسال المعاينة كنص فقط لتجنب مشاكل WebP الخاصة بيوتيوب والتي ترفضها خوادم تيليجرام
    return await update.message.reply_text(text=caption, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# 5. لوحة الإدارة الذكية التفاعلية
# ==========================================================

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server")],
        [InlineKeyboardButton("👥 أسماء المستخدمين", callback_data="adm_users_list"), InlineKeyboardButton("👤 إدارة مستخدم", callback_data="adm_manage_user")],
        [InlineKeyboardButton("📢 إذاعة للجميع", callback_data="adm_start_bc"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")],
        [InlineKeyboardButton("📦 نسخة احتياطية", callback_data="adm_backup"), InlineKeyboardButton("🧠 تحليل السيرفر", callback_data="adm_ai_debug")],
        [InlineKeyboardButton("🔄 تحديث yt-dlp", callback_data="adm_update"), InlineKeyboardButton("⚠️ إعادة التشغيل", callback_data="adm_reboot")],
        [InlineKeyboardButton("✖️ إغلاق اللوحة", callback_data="adm_close")]
    ])

def user_manage_keyboard(target_id: int) -> InlineKeyboardMarkup:
    is_ban = target_id in BANNED_USERS_CACHE
    is_adm = target_id in DYNAMIC_ADMINS_CACHE
    ban_btn = InlineKeyboardButton("✅ فك الحظر", callback_data=f"unban:{target_id}") if is_ban else InlineKeyboardButton("🚫 حظر", callback_data=f"ban:{target_id}")
    adm_btn = InlineKeyboardButton("🔻 سحب الإدارة", callback_data=f"demote:{target_id}") if is_adm else InlineKeyboardButton("🛡️ ترقية لإدمن", callback_data=f"promote:{target_id}")
    return InlineKeyboardMarkup([[ban_btn, adm_btn], [InlineKeyboardButton("🔙 عودة للوحة", callback_data="adm_back")]])

async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.pop('admin_state', None)
    text = (
        "🛠 <b>نظام الإدارة الذكي (PlayZone Enterprise)</b>\n\n"
        "<i>تلميحات ذكية:</i>\n"
        "• لتحديث الكوكيز: فقط أرسل ملف <code>cookies.txt</code> للمحادثة.\n"
        "• لاستعادة البيانات: فقط أرسل ملف <code>.db</code> وسيتعرف عليه البوت.\n"
    )
    await update.message.reply_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")

async def handle_admin_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    doc = update.message.document
    if not doc: return

    file_name = doc.file_name.lower()
    if file_name == "cookies.txt":
        status = await update.message.reply_text("⏳ جاري تركيب الكوكيز الجديد...")
        new_file = await context.bot.get_file(doc.file_id)
        await new_file.download_to_drive(COOKIES_FILE)
        await status.edit_text("✅ <b>تم تركيب ملف الكوكيز الجديد بنجاح!</b>\nسيتخطى البوت الآن حظر يوتيوب.", parse_mode="HTML")
    elif file_name.endswith(".db"):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("استعادة هذه النسخة ⚠️", callback_data=f"restore_{doc.file_id}")]])
        await update.message.reply_text("📦 <b>تم اكتشاف ملف قاعدة بيانات.</b>\nهل أنت متأكد من رغبتك في استعادة هذه النسخة؟ (سيتم مسح البيانات الحالية)", reply_markup=kb, parse_mode="HTML")

# ==========================================================
# 6. محرك التحميل yt-dlp 
# ==========================================================

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video"):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 15, "socket_timeout": 30, "cachedir": False, "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}}
    if PROXY_URL: opts["proxy"] = PROXY_URL
    
    # تحسين صيغ الـ Audio و Video لتجنب صيغ Webm المزعجة لتيليجرام
    if mode == "audio": 
        opts["format"] = "m4a/bestaudio/best"
    else: 
        opts["format"] = f"bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/mp4/best"
        
    opts["merge_output_format"] = "mp4"
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0: opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)

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
                    progress_data["text"] = f"📥 جاري التحميل...\n📦 تم تحميل: {format_size(downloaded)}"
            elif d.get("status") == "finished":
                progress_data["text"] = "⚙️ اكتمل التحميل، جاري التجهيز والضغط الاحترافي..."
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    last_text = ""
    while not stop_event.is_set():
        with progress_lock: text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=None)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

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
            except Forbidden:
                fail += 1
                await update_user_status(user_id, "is_banned", 1)
                BANNED_USERS_CACHE.add(user_id)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await app.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
                    sent += 1
                except Exception: fail += 1
            except Exception: fail += 1
            await asyncio.sleep(0.04) 

            if i % 1000 == 0 and i > 0:
                try: await status_msg.edit_text(f"📢 جاري الإذاعة...\nتم الإرسال: {sent}\nفشل/حظر: {fail}")
                except Exception: pass

        await stat_inc("broadcasts")
        try: await status_msg.edit_text(f"✅ انتهت الإذاعة!\nالناجح: {sent}\nالفاشل: {fail}")
        except Exception: pass
        BROADCAST_QUEUE.task_done()

# ==========================================================
# 8. التوجيه الذكي للرسائل، الأزرار والذكاء الاصطناعي
# ==========================================================

async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    uid = update.effective_user.id
    if uid in BANNED_USERS_CACHE: return 
    
    await register_user_cached(update.effective_user)
    text = update.message.text.strip()

    # 1. التحقق من حالات الإدارة الذكية
    admin_state = context.user_data.get('admin_state')
    if is_admin(uid):
        if admin_state == 'awaiting_broadcast':
            context.user_data.pop('admin_state', None)
            users = list(KNOWN_USERS_CACHE - BANNED_USERS_CACHE)
            if not users: return await update.message.reply_text("❌ لا يوجد مستخدمين مسجلين.")
            status = await update.message.reply_text("⏳ تم استلام رسالتك، جاري الإذاعة في الخلفية...")
            await BROADCAST_QUEUE.put((text, users, status))
            return
            
        elif admin_state == 'awaiting_user_id':
            context.user_data.pop('admin_state', None)
            if not text.isdigit(): return await update.message.reply_text("❌ الـ ID يجب أن يكون أرقاماً فقط. تم الإلغاء.")
            target_id = int(text)
            return await update.message.reply_text(f"👤 لوحة تحكم المستخدم: <code>{target_id}</code>", reply_markup=user_manage_keyboard(target_id), parse_mode="HTML")

    # 2. أزرار ونصوص PlayZone الأصلية
    if text in ["🔗 روابط PlayZone", "/links", "\\links"]:
        return await update.message.reply_text(build_playzone_links_text(), reply_markup=build_playzone_links_keyboard(), disable_web_page_preview=True)
    if text == "📘 دليل الاستخدام":
        return await update.message.reply_text(build_guide_text(), disable_web_page_preview=True)

    if uid in ACTIVE_USERS:
        return await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ.\n\nانتظر حتى يكتمل، ثم أرسل رابطاً جديداً.")
    
    # 3. الدردشة مع الذكاء الاصطناعي (مع حماية النصوص)
    if not is_valid_url(text):
        if genai_aclient:
            await context.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
            try:
                if uid not in USER_CHATS or USER_CHAT_COUNT.get(uid, 0) > 20:
                    USER_CHATS[uid] = genai_aclient.chats.create(model='gemini-2.5-flash', config=ai_config)
                    USER_CHAT_COUNT[uid] = 0
                    
                response = await USER_CHATS[uid].send_message(text)
                USER_CHAT_COUNT[uid] += 1
                
                # استخدام المنظف للنص كخيار آمن 100% لتجنب تعطل رسائل تيليجرام
                safe_reply = clean_markdown_for_telegram(response.text[:4000])
                return await update.message.reply_text(safe_reply)
                
            except Exception as e:
                logger.error(f"Gemini Chat Error: {e}")
                USER_CHATS.pop(uid, None) 
                return await update.message.reply_text("❌ عذراً، لا يمكنني الاستجابة الآن بسبب ضغط على الخوادم.")
        else:
            return await update.message.reply_text("❌ الرابط غير صحيح.\n\nأرسل رابط يبدأ بـ:\nhttp:// أو https://")

    # 4. تحميل الميديا وإظهار المعاينة
    status = await update.message.reply_text("🔍 جاري فحص الرابط وتجهيز المعاينة...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(text))

        title = clean_title(info.get("title"))
        artist = get_artist(info)
        duration_raw = info.get("duration") or 0
        est_size = format_size(get_largest_estimated_size(info))
        thumb = get_thumbnail(info)
        request_id = uuid.uuid4().hex[:10]

        context.user_data[request_id] = {"url": text, "title": title, "artist": artist, "duration": duration_raw, "thumb_url": thumb}

        caption = build_preview_caption(title, artist, format_duration(duration_raw), est_size)
        await safe_delete(status)
        await send_preview(update, thumb, caption, build_preview_keyboard(request_id))
        await stat_inc("requests")
    except Exception as e:
        logger.warning(f"فشل جلب المعاينة: {e}")
        await status.edit_text("❌ تعذر قراءة الرابط.\n\nتأكد أن المقطع متاح للعامة وغير محذوف، ثم حاول مرة أخرى.")

async def start_download(query, context, request: dict, mode: str):
    uid = query.from_user.id
    ACTIVE_USERS.add(uid)
    job_dir = BASE_DOWNLOAD_DIR / f"{uid}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    progress_data = {"text": "⏳ يرجى الانتظار..."}
    updater_task = asyncio.create_task(run_progress_updates(query.message, progress_data, stop_event))

    try:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

        async with DOWNLOAD_SEMAPHORE:
            progress_data["text"] = "🚀 بدأ التحميل... يرجى الانتظار ⏬"
            loop = asyncio.get_running_loop()
            
            opts = get_ydl_options(job_dir, progress_data, mode)
            await loop.run_in_executor(EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(request["url"], download=True))
            
            # الفلترة الذكية للملفات: أخذ ملفات الفيديو والصوت الواضحة فقط
            valid_extensions = ['.mp4', '.m4a', '.mp3', '.mkv', '.webm']
            files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_extensions]
            
            if not files: raise RuntimeError("لم يتم العثور على الملف النهائي")
            target_file = files[0]

            file_size = target_file.stat().st_size
            if file_size > MAX_TELEGRAM_SIZE:
                stop_event.set()
                return await edit_message_smart(query.message, f"❌ حجم الملف يتجاوز الحد المسموح.\n\nالحجم: {format_size(file_size)}\nالحد: {format_size(MAX_TELEGRAM_SIZE)}")

            stop_event.set()
            await edit_message_smart(query.message, "📤 تم تجهيز الملف، جاري الإرسال...", reply_markup=None)

            title = clean_title(request.get("title", "ملف ميديا"), 80)
            duration = int(request.get("duration") or 0)
            caption = f"- {esc(BOT_USERNAME)}، {esc(format_duration(duration))}"
            share_link = f"https://t.me/share/url?url={quote(request['url'])}&text={quote('🎬 ' + title)}"
            media_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📤 مشاركة", url=share_link)]])

            # إرسال المقطع مع تجاوز مشكلة الصور التالفة بوضعها كخيار تلقائي لتيليجرام
            with open(target_file, "rb") as f:
                try:
                    if mode == "audio":
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id, audio=f, title=title,
                            performer=request.get("artist", "غير معروف"), duration=duration,
                            caption=caption, reply_markup=media_keyboard, parse_mode="HTML", read_timeout=120
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id, video=f, caption=caption, supports_streaming=True,  
                            duration=duration, reply_markup=media_keyboard, parse_mode="HTML", read_timeout=120
                        )
                except Exception as inner_e:
                    logger.error(f"Telegram Upload Error: {inner_e}")
                    raise inner_e
            
            await stat_inc("success")
            await stat_inc("bytes", file_size)
            await safe_delete(query.message)
    except Exception as e:
        await stat_inc("failed")
        try: await edit_message_smart(query.message, "❌ فشل تحميل المقطع.\n\nقد يكون الرابط غير متاح أو يتجاوز الحد المسموح به.")
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
    
    if data.startswith("restore_"):
        if not is_admin(uid): return await query.answer("❌ لا تملك صلاحيات.", show_alert=True)
        file_id = data.split("_")[1]
        await query.answer("جاري الاستعادة...")
        try:
            new_file = await context.bot.get_file(file_id)
            await new_file.download_to_drive(DB_FILE)
            Path(str(DB_FILE) + "-wal").unlink(missing_ok=True)
            Path(str(DB_FILE) + "-shm").unlink(missing_ok=True)
            KNOWN_USERS_CACHE.clear(); BANNED_USERS_CACHE.clear(); DYNAMIC_ADMINS_CACHE.clear()
            await init_db()
            await query.message.edit_text("✅ <b>تم استعادة قاعدة البيانات وتحديث النظام بنجاح!</b>", parse_mode="HTML")
        except Exception as e: await query.message.edit_text(f"❌ حدث خطأ: {e}")
        return

    if data.startswith(("ban:", "unban:", "promote:", "demote:")):
        if not is_admin(uid): return await query.answer("❌ لا تملك صلاحيات.", show_alert=True)
        action, target_str = data.split(":")
        target_id = int(target_str)
        
        if action == "ban":
            await update_user_status(target_id, "is_banned", 1)
            BANNED_USERS_CACHE.add(target_id)
            await query.answer("تم الحظر ✅")
        elif action == "unban":
            await update_user_status(target_id, "is_banned", 0)
            BANNED_USERS_CACHE.discard(target_id)
            await query.answer("تم فك الحظر ✅")
        elif action == "promote":
            await update_user_status(target_id, "is_admin", 1)
            DYNAMIC_ADMINS_CACHE.add(target_id)
            await query.answer("أصبح إدمن ✅")
        elif action == "demote":
            await update_user_status(target_id, "is_admin", 0)
            DYNAMIC_ADMINS_CACHE.discard(target_id)
            await query.answer("تم سحب الإدارة ✅")
        
        return await query.message.edit_reply_markup(reply_markup=user_manage_keyboard(target_id))

    if data.startswith("adm_"):
        if not is_admin(uid): return await query.answer("❌ لا تملك صلاحيات.", show_alert=True)
        
        if data == "adm_close":
            context.user_data.pop('admin_state', None)
            await query.answer("تم الإغلاق.")
            return await safe_delete(query.message)
            
        elif data == "adm_back":
            context.user_data.pop('admin_state', None)
            await query.message.edit_text("🛠 <b>نظام الإدارة الذكي (PlayZone Enterprise)</b>", reply_markup=admin_main_keyboard(), parse_mode="HTML")

        elif data == "adm_start_bc":
            context.user_data['admin_state'] = 'awaiting_broadcast'
            await query.answer()
            await query.message.edit_text("📢 <b>وضع الإذاعة نشط:</b>\n\nقم بكتابة الرسالة التي تريد إرسالها للجميع وأرسلها الآن. (لإلغاء العملية اضغط إغلاق اللوحة أو تراجع)", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ تراجع", callback_data="adm_back")]]))
            
        elif data == "adm_manage_user":
            context.user_data['admin_state'] = 'awaiting_user_id'
            await query.answer()
            await query.message.edit_text("👤 <b>إدارة المستخدمين:</b>\n\nأرسل لي رقم الـ ID الخاص بالمستخدم الذي تريد إدارته الآن.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ تراجع", callback_data="adm_back")]]))

        elif data == "adm_stats":
            await query.answer()
            stats = await get_all_stats()
            text = f"📊 <b>الإحصائيات</b>\n\n👥 مستخدمين: {len(KNOWN_USERS_CACHE)}\n🚫 محظورين: {len(BANNED_USERS_CACHE)}\n📥 طلبات: {stats.get('requests', 0)}\n✅ ناجح: {stats.get('success', 0)}\n💾 تبادل: {format_size(stats.get('bytes', 0))}"
            await query.message.edit_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")
            
        elif data == "adm_users_list":
            await query.answer("استخراج البيانات...")
            users_file = DATA_DIR / "users_list.txt"
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute("SELECT id, username, first_name, last_name, is_banned, is_admin FROM users") as c: rows = await c.fetchall()
            await asyncio.to_thread(generate_users_file, rows, users_file)
            with open(users_file, "rb") as f: await context.bot.send_document(chat_id=uid, document=f, filename="Users.txt")
            users_file.unlink(missing_ok=True)

        elif data == "adm_server":
            await query.answer()
            await query.message.edit_text(f"📁 <b>حالة السيرفر</b>\n\n⚡ نشط الآن: {len(ACTIVE_USERS)} / {MAX_WORKERS} عمليات متزامنة", reply_markup=admin_main_keyboard(), parse_mode="HTML")
            
        elif data == "adm_clean":
            await query.answer("جاري المسح...")
            r = await asyncio.to_thread(lambda: sum(1 for i in BASE_DOWNLOAD_DIR.iterdir() if not i.is_dir() and not i.unlink()))
            await query.message.edit_text(f"🧹 تم تنظيف الملفات المؤقتة بنجاح.", reply_markup=admin_main_keyboard())

        elif data == "adm_update":
            await query.answer("تحديث...")
            await query.message.edit_text("🔄 جاري تحديث المحرك...")
            await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "-U", "yt-dlp")
            await query.message.edit_text("✅ تم تحديث `yt-dlp` بنجاح!", reply_markup=admin_main_keyboard(), parse_mode="HTML")
        
        elif data == "adm_ai_debug":
            await query.answer("تحليل ذكي...")
            if not genai_aclient or not LOG_FILE.exists(): return await query.message.edit_text("❌ لا يوجد أخطاء مسجلة أو مفتاح الذكاء الاصطناعي مفقود.", reply_markup=admin_main_keyboard())
            with open(LOG_FILE, "r", encoding="utf-8") as f: logs = "".join(f.readlines()[-40:])
            
            try:
                resp = await genai_aclient.models.generate_content(model='gemini-2.5-flash', contents=f"اشرح هذه الأخطاء إن وجدت باختصار:\n{logs}")
                reply_content = resp.text[:3000]
                await query.message.edit_text(f"🧠 {clean_markdown_for_telegram(reply_content)}", reply_markup=admin_main_keyboard())
            except Exception as e:
                await query.message.edit_text(f"❌ فشل الاتصال بالذكاء الاصطناعي: {e}", reply_markup=admin_main_keyboard())

        elif data == "adm_backup":
            await query.answer()
            with open(DB_FILE, "rb") as f: await context.bot.send_document(chat_id=uid, document=f, filename="bot_backup.db")
                
        elif data == "adm_reboot":
            await query.answer()
            await query.message.edit_text("⚠️ <b>Rebooting...</b>", parse_mode="HTML")
            os.execv(sys.executable, ['python'] + sys.argv)
        return

    if data.startswith("cancel:"):
        context.user_data.pop(data.split(":")[1], None)
        await query.answer("تم الإلغاء")
        return await safe_delete(query.message)

    await query.answer() 
    if data.startswith(("aud:", "vid:")):
        mode = "audio" if data.startswith("aud") else "video"
        request_id = data.split(":")[1]
        req = context.user_data.pop(request_id, None)
        if not req: return await query.answer("انتهت الجلسة، يرجى إعادة إرسال الرابط.", show_alert=True)
        if uid in ACTIVE_USERS: return await query.answer("لديك تحميل قيد التنفيذ حالياً.", show_alert=True)
        asyncio.create_task(start_download(query, context, req, mode))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in BANNED_USERS_CACHE: return
    await register_user_cached(update.effective_user)
    await update.message.reply_text(build_start_text(update.effective_user.first_name or ""), reply_markup=user_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
# 9. التشغيل
# ==========================================================

async def post_init(app: Application):
    await init_db()
    asyncio.create_task(broadcast_worker(app))
    try: await app.bot.set_my_commands([BotCommand("start", "بدء استخدام البوت"), BotCommand("links", "دعم روابط PlayZone")])
    except Exception: pass

def main():
    if not TOKEN: raise RuntimeError("المتغير البيئي TELEGRAM_TOKEN غير متوفر بالسيرفر!")
    
    try:
        builder = Application.builder().token(TOKEN)
        if LOCAL_API_URL: builder.base_url(LOCAL_API_URL)
        app = builder.post_init(post_init).concurrent_updates(True).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("links", handle_incoming_text))
        app.add_handler(CommandHandler("admin", admin_panel_cmd))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_admin_files))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
        app.add_handler(CallbackQueryHandler(handle_callbacks))

        logger.info("🚀 إقلاع (PlayZone Origin + Smart Interactive Admin + Gemini AI)")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Conflict:
        logger.error("❌ يوجد نسخة أخرى من البوت تعمل حالياً. سيتم الإغلاق لتجنب التعارض.")
        sys.exit(1)

if __name__ == "__main__":
    main()
