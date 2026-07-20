import os, sys, uuid, time, requests, json, subprocess, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import yt_dlp

# محاولة استيراد مكتبة Supabase بشكل آمن
try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot"

# 🔑 بيانات Supabase الخاصة بك
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qnuklkpcyvwaefxclfff.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_L7HdgLQJ1Z5e3fjKNo7x1g_gSKJSb-x")

supabase: Client = None
if HAS_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

try:
    from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK
except ImportError:
    BASE_DOWNLOAD_DIR = Path("./downloads")
    HILLTOPADS_LINK = "https://bony-teaching.com/TwZD7z"
    ADSTERRA_LINK = "https://www.effectivecpmnetwork.com/jgv39bh2p?key=8ffb7ed8cb605d90c6d07e1f7a698646"

app = FastAPI(title="PlayZone Enterprise Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")

DB_PATH = "playzone_core.db"

# ⚡ ذاكرة التتبع السريعة لعدم إبطاء التحميل (In-Memory RAM)
PROGRESS_STORE = {}
ADS_STORE = {}

def get_sqlite():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")  
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_sqlite() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_library (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                url TEXT,
                thumb TEXT,
                uploader TEXT,
                duration INTEGER,
                is_audio INTEGER,
                favorite INTEGER DEFAULT 0,
                timestamp REAL
            )""")
        conn.commit()

init_db()

DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=10) 

def cleanup_cron():
    """ تنظيف دوري صامت للملفات والذاكرة المؤقتة """
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            expired_progress = [k for k, v in PROGRESS_STORE.items() if now - v.get("timestamp", 0) > 86400]
            for k in expired_progress:
                PROGRESS_STORE.pop(k, None)

            expired_ads = [k for k, v in ADS_STORE.items() if now - v.get("created_at", 0) > 3600]
            for k in expired_ads:
                ADS_STORE.pop(k, None)
                
        except Exception:
            pass
        time.sleep(1800)

import threading
threading.Thread(target=cleanup_cron, daemon=True).start()

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"
    click_id: str = ""
    user_id: str = "default_user"

class SearchRequest(BaseModel):
    query: str

class TelegramRequest(BaseModel):
    file_url: str
    chat_id: str  
    is_audio: bool
    title: str = "مقطع"
    performer: str = "PlayZone"
    duration: int = 0
    thumb: str = ""

    @field_validator('chat_id', mode='before')
    @classmethod
    def coerce_str(cls, v):
        return str(v)

def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 30, "cachedir": False,
        "concurrent_fragment_downloads": 5, "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    try:
        cookie_path = Path("cookies.txt")
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            opts["cookiefile"] = str(cookie_path)
    except Exception:
        pass
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read().replace("{BOT_USERNAME}", BOT_USERNAME))

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        opts = get_hardened_ydl_options()
        opts['extract_flat'] = True
        if 'playlist_items' in opts: del opts['playlist_items']
        if 'noplaylist' in opts: del opts['noplaylist']
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw_results = ydl.extract_info(f"ytsearch25:{req.query}", download=False) or {}
            
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id")
            title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail") or (entry.get("thumbnails")[0].get("url") if entry.get("thumbnails") else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
                valid_videos.append({
                    "id": video_id, "title": title,
                    "duration": entry.get("duration") or 0,
                    "uploader": entry.get("uploader") or entry.get("channel") or "غير معروف",
                    "thumbnail": thumb_url
                })
            if len(valid_videos) == 15: break 
        return {"success": True, "entries": valid_videos}
    except Exception as e: 
        return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"success": True, "title": info.get("title", "بدون عنوان"), "thumb": info.get("thumbnail", "")}
    except Exception as e: return {"success": False, "error": str(e)}

@app.get("/api/generate_ad_session")
def generate_ad_session():
    click_id = uuid.uuid4().hex[:12]
    ADS_STORE[click_id] = {"status": "pending", "created_at": time.time()}
    AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "https://example.com/ad")
    separator = "&" if "?" in AD_LINK else "?"
    return {"click_id": click_id, "ad_link": f"{AD_LINK}{separator}clickid={click_id}"}

@app.get("/api/ad_callback")
def ad_callback(clickid: str):
    if clickid in ADS_STORE:
        ADS_STORE[clickid]["status"] = "verified"
        return {"status": "success", "message": "Verified"}
    return {"status": "error", "message": "Invalid token"}

@app.get("/api/check_ad_status/{click_id}")
def check_ad_status(click_id: str):
    row = ADS_STORE.get(click_id)
    if not row: return {"status": "not_found"}
    if row["status"] == "verified" or (time.time() - row["created_at"] > 10):
        return {"status": "verified"}
    return {"status": row["status"]}

def save_item_to_library(item_data: dict):
    """ حفظ العناصر المكتملة في Supabase مع محرك احتياطي لـ SQLite """
    if supabase:
        try:
            supabase.table("user_library").upsert({
                "id": item_data["id"],
                "user_id": str(item_data["user_id"]),
                "title": item_data["title"],
                "url": item_data["url"],
                "thumb": item_data["thumb"],
                "uploader": item_data["uploader"],
                "duration": item_data["duration"],
                "is_audio": 1 if item_data["is_audio"] else 0,
                "timestamp": item_data["timestamp"]
            }).execute()
            return
        except Exception:
            pass

    with get_sqlite() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO user_library (id, user_id, title, url, thumb, uploader, duration, is_audio, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item_data["id"], str(item_data["user_id"]), item_data["title"], item_data["url"],
            item_data["thumb"], item_data["uploader"], item_data["duration"],
            1 if item_data["is_audio"] else 0, item_data["timestamp"]
        ))
        conn.commit()

def bg_download_worker(job_id: str, url: str, mode: str, res: str, user_id: str = "default_user"):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            payload = {
                "status": "downloading", "percent": round((downloaded / total) * 100, 1),
                "total_mb": f"{total / 1048576:.1f} MB", "dl_mb": f"{downloaded / 1048576:.1f} MB",
                "spd_mb": f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s"
            }
            # التحديث في الذاكرة يحدث بسرعة 0ms وبدون إبطاء التنزيل
            PROGRESS_STORE[job_id] = {"status": "downloading", "data": payload, "timestamp": time.time()}
        elif d['status'] == 'finished':
            PROGRESS_STORE[job_id] = {"status": "converting", "timestamp": time.time()}

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook)
    if mode == 'audio':
        opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    else:
        max_fs = "49M"
        opts.update({
            'format': f"bestvideo[vcodec^=avc1][height<={res if res!='best' else '1080'}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/best",
            'merge_output_format': 'mp4', 'postprocessor_args': {'ffmpeg': ['-c:a', 'aac', '-b:a', '192k']}
        })
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = f"{job_id}.mp3" if mode == 'audio' else f"{job_id}.mp4"
            file_url = f"/files/{filename}"
            payload = {
                "status": "completed", "url": file_url, "title": info.get('title', 'مقطع'),
                "thumb": info.get('thumbnail', ''), "uploader": info.get('uploader', 'غير معروف'),
                "duration": info.get('duration', 0), "is_audio": mode == 'audio'
            }
            PROGRESS_STORE[job_id] = {"status": "completed", "data": payload, "timestamp": time.time()}

            # حفظ النتيجة النهائية فقط في Supabase
            save_item_to_library({
                "id": job_id, "user_id": user_id, "title": payload["title"],
                "url": file_url, "thumb": payload["thumb"], "uploader": payload["uploader"],
                "duration": payload["duration"], "is_audio": payload["is_audio"],
                "timestamp": time.time()
            })
    except Exception as e:
        payload = {"status": "error", "error": str(e)}
        PROGRESS_STORE[job_id] = {"status": "error", "data": payload, "timestamp": time.time()}

@app.post("/api/download")
async def start_download(req: URLRequest):
    row = ADS_STORE.get(req.click_id)
    if not row: return {"success": False, "error": "جلسة إعلانية غير صالحة."}
    if not (row["status"] == "verified" or (time.time() - row["created_at"] > 10)):
        return {"success": False, "error": "خطأ: لم يتم تأكيد فك قفل التحميل بعد."}
            
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_STORE[job_id] = {"status": "starting", "data": {}, "timestamp": time.time()}
        
    DOWNLOAD_POOL.submit(bg_download_worker, job_id, req.url, req.mode, req.resolution, req.user_id)
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    row = PROGRESS_STORE.get(job_id)
    if not row: return {"status": "waiting"}
    status = row["status"]
    if status in ["starting", "converting"]: return {"status": status}
    return row.get("data", {})

@app.get("/api/library")
async def get_user_library(user_id: str = "default_user"):
    if supabase:
        try:
            res = supabase.table("user_library").select("*").eq("user_id", str(user_id)).order("timestamp", desc=True).execute()
            items = []
            for r in res.data:
                items.append({
                    "id": r["id"], "title": r["title"], "url": r["url"], "thumb": r["thumb"],
                    "uploader": r["uploader"], "duration": r["duration"], "is_audio": bool(r["is_audio"]),
                    "favorite": bool(r.get("favorite", 0)), "timestamp": r["timestamp"]
                })
            return {"success": True, "library": items}
        except Exception:
            pass

    with get_sqlite() as conn:
        rows = conn.execute("SELECT id, title, url, thumb, uploader, duration, is_audio, favorite, timestamp FROM user_library WHERE user_id = ? ORDER BY timestamp DESC", (str(user_id),)).fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r[0], "title": r[1], "url": r[2], "thumb": r[3],
                "uploader": r[4], "duration": r[5], "is_audio": bool(r[6]),
                "favorite": bool(r[7]), "timestamp": r[8]
            })
        return {"success": True, "library": items}

@app.post("/api/send_telegram")
def send_to_telegram(req: TelegramRequest):
    try:
        filename = req.file_url.split("/")[-1]
        file_path = WEB_DIR / filename
        
        if not file_path.exists(): return {"success": False, "error": "الملف غير موجود."}
        if not TELEGRAM_TOKEN: return {"success": False, "error": "البوت غير مفعل بالخلفية."}
        if file_path.stat().st_size / (1024 * 1024) > 49.5: return {"success": False, "error": "حجم الملف يتجاوز 50 ميجابايت."}

        api_method = "sendAudio" if req.is_audio else "sendVideo"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        dur = int(req.duration) if req.duration else 0
        
        caption = f"- @P1ay_Z0ne_Bot , {dur//60}:{dur%60:02d}" if dur > 0 else f"- @P1ay_Z0ne_Bot"
        reply_markup = {"inline_keyboard": [[{"text": "🌟 أعجبك البوت؟ شاركه", "url": "https://t.me/share/url?url=https://t.me/P1ay_Z0ne_Bot"}]]}
        
        data = {'chat_id': req.chat_id, 'caption': caption, 'reply_markup': json.dumps(reply_markup)}
        
        if req.is_audio:
            data.update({'title': req.title, 'performer': req.performer, 'duration': req.duration})
        else:
            data.update({'supports_streaming': True, 'duration': req.duration})
            try:
                cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(file_path)]
                res = subprocess.run(cmd, capture_output=True, text=True)
                probe_data = json.loads(res.stdout)
                data.update({'width': probe_data['streams'][0]['width'], 'height': probe_data['streams'][0]['height']})
            except Exception: pass

        with open(file_path, 'rb') as f: file_data = f.read()
        files = {'audio' if req.is_audio else 'video': (filename, file_data)}
        
        if req.thumb and req.is_audio:
            try:
                t_res = requests.get(req.thumb, timeout=4)
                if t_res.status_code == 200: files['thumb'] = ('thumb.jpg', t_res.content, 'image/jpeg')
            except: pass
                
        response = requests.post(url, data=data, files=files, timeout=60)
        res_data = response.json()
        
        if response.status_code == 200 and res_data.get("ok"): return {"success": True}
        return {"success": False, "error": res_data.get("description", "تأكد من بدء البوت أولاً.")}
        
    except Exception as e: return {"success": False, "error": str(e)}
