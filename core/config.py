import os
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
import asyncio

TOKEN = os.getenv("TELEGRAM_TOKEN")
LOCAL_API_URL = os.getenv("TELEGRAM_API_URL") 

BASE_DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "bot_database.db"
DB_LOCK = threading.Lock()

DEFAULT_MAX_SIZE = (2000 * 1024 * 1024) if LOCAL_API_URL else (50 * 1024 * 1024)
MAX_TELEGRAM_SIZE = int(os.getenv("MAX_TELEGRAM_SIZE", str(DEFAULT_MAX_SIZE)))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))

PROGRESS_UPDATE_SECONDS = float(os.getenv("PROGRESS_UPDATE_SECONDS", "3.0"))
REQUEST_EXPIRE_SECONDS = int(os.getenv("REQUEST_EXPIRE_SECONDS", str(15 * 60)))
OLD_DOWNLOADS_EXPIRE_SECONDS = int(os.getenv("OLD_DOWNLOADS_EXPIRE_SECONDS", str(60 * 60)))
MAX_THUMBNAIL_BYTES = int(os.getenv("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", str(os.cpu_count() or 2)))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_WORKERS)
EXECUTOR = ThreadPoolExecutor(max_workers=max(4, MAX_WORKERS * 2))

# 🌟 تنظيف وتأمين معرف البوت برمجياً لمنع تكرار الروابط أو تعطل الأزرار
raw_username = os.getenv("BOT_USERNAME", "@P1ay_Z0ne_Bot").strip()
if "t.me/" in raw_username:
    clean_user = raw_username.split("t.me/")[-1].replace("@", "").strip()
else:
    clean_user = raw_username.replace("@", "").strip()

BOT_USERNAME = f"@{clean_user}[span_1](start_span)"[span_1](end_span)
TELEGRAM_BOT_PLAYZONE = f"https://t.me/{clean_user}[span_2](start_span)"[span_2](end_span)
# --------------------------------------------------------------------------

WEBSITE_PLAYZONE = "http://tasmg1.github.io/tasmg/?[span_3](start_span)"[span_3](end_span)
FACEBOOK_PLAYZONE = "https://www.facebook.com/share/18goJYQebr/?mibextid=wwXIfr[span_4](start_span)"[span_4](end_span)
INSTAGRAM_PLAYZONE = "https://www.instagram.com/p1ay.zone?igsh=MW9uYTB1dTZxZnpocQ%3D%3D&utm_source=qr[span_5](start_span)"[span_5](end_span)
THREADS_PLAYZONE = "https://www.threads.com/@p1ay.zone?igshid=NTc4MTIwNjQ2YQ==[span_6](start_span)"[span_6](end_span)

# 🌟 الرابط المباشر الفعلي المعتمد بعد التفعيل من منصة HilltopAds
HILLTOPADS_LINK = "https://bony-teaching.com/TwZD7z[span_7](start_span)"[span_7](end_span)

# 🌟 الرابط المباشر الفعلي المستخرج من منصة Adsterra (تم تحديثه برابطك الفعلي)
ADSTERRA_LINK = "https://www.effectivecpmnetwork.com/jgv39bh2p?key=8ffb7ed8cb605d90c6d07e1f7a698646[span_8](start_span)"[span_8](end_span)
