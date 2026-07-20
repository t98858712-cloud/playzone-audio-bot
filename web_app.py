import os, sys, uuid, time, requests, json, subprocess, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import yt_dlp

# محاولة استيراد libsql مع حماية كاملة
try:
    import libsql_experimental as libsql
    HAS_LIBSQL = True
except ImportError:
    HAS_LIBSQL = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot"

# إعدادات Turso السحابية
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "libsql://musicbot-t98858712-cloud.aws-eu-west-1.turso.io")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODQ1MjQ1MjYsImlkIjoiMDE5ZjdkZjAtZjcwMS03M2YxLTg3OTQtNTU3OTA5OWZmMjQ0Iiwia2lkIjoiOTlnN1pjeElMMUBvUWtBejdETnhCV2RLRXZRN2l1bXVFYXNUYWp1RVBubyIsInJpZCI6IjE5ZTAyMzE3LWZlNDAtNDUwYS05YzZjLWM5Mzg4MmQ1YjA5NiJ9.S-cAb_n7Q8c8pT3CACaehmhjtiQeHGBtZOOphzBTjqjGWvzv3WIUZM1Xhy_p-XmSJ157TGrd1tozzBkRWoXKCA")

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

# 🆕 دالة جلب قاعدة البيانات المحدثة لمنع انهيار السيرفر عند وجود خطأ في التوكين
def get_db():
    if HAS_LIBSQL and TURSO_URL and TURSO_TOKEN:
        try:
            conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            # تجربة استعلام وهمي لاختبار صحة التوكين والاتصال فوراً
            conn.execute("SELECT 1;")
            return conn
        except Exception:
            pass  # عند فشل Turso يتم التحويل تلقائياً وبصمت للـ SQLite المحلي
            
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")  
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            job_id TEXT PRIMARY KEY,
            status TEXT,
            data TEXT,
            timestamp REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            click_id TEXT PRIMARY KEY,
            status TEXT,
            created_at REAL
        )""")
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
    """ تنظيف دوري صامت للملفات القديمة لتوفير المساحة """
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            conn = get_db()
            conn.execute("DELETE FROM progress WHERE ? - timestamp > 86400", (now,))
            conn.execute("DELETE FROM ads WHERE ? - created_at > 3600", (now,))
            conn.commit()
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

class FavRequest(BaseModel):
    file_id: str
    user_id: str = "default_user"

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
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 15,
        "cachedir": False,
        "no_check_certificate": True,
        # 🆕 حل حظر وتقييد السرعة (Throttling Fix):
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"],
                "player_skip": ["web"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
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
        # إعدادات خاصة ومستقلة بالبحث فقط لتجاوز حظر خوادم الاستضافة
        search_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "no_check_certificate": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7"
            }
        }
        
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            raw_results = ydl.extract_info(f"ytsearch20:{req.query}", download=False) or {}
            
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id")
            title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail") or (entry.get("thumbnails")[0].get("url") if entry.get("thumbnails") else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
                valid_videos.append({
                    "id": video_id, 
                    "title": title,
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
    conn = get_db()
    conn.execute("INSERT INTO ads (click_id, status, created_at) VALUES (?, ?, ?)", (click_id, "pending", time.time()))
    conn.commit()
    AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "https://example.com/ad")
    separator = "&" if "?" in AD_LINK else "?"
    return {"click_id": click_id, "ad_link": f"{AD_LINK}{separator}clickid={click_id}"}

@app.get("/api/ad_callback")
def ad_callback(clickid: str):
    conn = get_db()
    res = conn.execute("SELECT * FROM ads WHERE click_id = ?", (clickid,)).fetchone()
    if res:
        conn.execute("UPDATE ads SET status = 'verified' WHERE click_id = ?", (clickid,))
        conn.commit()
        return {"status": "success", "message": "Verified"}
    return {"status": "error", "message": "Invalid token"}

@app.get("/api/check_ad_status/{click_id}")
def check_ad_status(click_id: str):
    conn = get_db()
    res = conn.execute("SELECT status, created_at FROM ads WHERE click_id = ?", (click_id,)).fetchone()
    if not res: return {"status": "not_found"}
    status, created_at = res[0], res[1]
    if status == "verified" or (time.time() - created_at > 10):
        return {"status": "verified"}
    return {"status": status}

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
            conn = get_db()
            conn.execute("UPDATE progress SET status='downloading', data=?, timestamp=? WHERE job_id=?", (json.dumps(payload), time.time(), job_id))
            conn.commit()
        elif d['status'] == 'finished':
            conn = get_db()
            conn.execute("UPDATE progress SET status='converting', timestamp=? WHERE job_id=?", (time.time(), job_id))
            conn.commit()

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
            
            conn = get_db()
            conn.execute("UPDATE progress SET status='completed', data=? WHERE job_id=?", (json.dumps(payload), job_id))
            
            conn.execute("""
                INSERT OR REPLACE INTO user_library (id, user_id, title, url, thumb, uploader, duration, is_audio, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, str(user_id), payload["title"], file_url, payload["thumb"], payload["uploader"], payload["duration"], 1 if payload["is_audio"] else 0, time.time()))
            
            conn.commit()
    except Exception as e:
        payload = {"status": "error", "error": str(e)}
        conn = get_db()
        conn.execute("UPDATE progress SET status='error', data=? WHERE job_id=?", (json.dumps(payload), job_id))
        conn.commit()

@app.post("/api/download")
async def start_download(req: URLRequest):
    conn = get_db()
    res = conn.execute("SELECT status, created_at FROM ads WHERE click_id = ?", (req.click_id,)).fetchone()
    if not res: return {"success": False, "error": "جلسة إعلانية غير صالحة."}
    status, created_at = res[0], res[1]
    if not (status == "verified" or (time.time() - created_at > 10)):
        return {"success": False, "error": "خطأ: لم يتم تأكيد فك قفل التحميل بعد."}
            
    job_id = uuid.uuid4().hex[:8]
    conn.execute("INSERT INTO progress (job_id, status, data, timestamp) VALUES (?, ?, ?, ?)", (job_id, "starting", "{}", time.time()))
    conn.commit()
        
    DOWNLOAD_POOL.submit(bg_download_worker, job_id, req.url, req.mode, req.resolution, req.user_id)
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    conn = get_db()
    res = conn.execute("SELECT status, data FROM progress WHERE job_id = ?", (job_id,)).fetchone()
    if not res: return {"status": "waiting"}
    status, data = res[0], res[1]
    if status in ["starting", "converting"]: return {"status": status}
    return json.loads(data)

@app.get("/api/library")
async def get_user_library(user_id: str = "default_user"):
    conn = get_db()
    rows = conn.execute("SELECT id, title, url, thumb, uploader, duration, is_audio, favorite, timestamp FROM user_library WHERE user_id = ? ORDER BY timestamp DESC", (str(user_id),)).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r[0], "title": r[1], "url": r[2], "thumb": r[3],
            "uploader": r[4], "duration": r[5], "is_audio": bool(r[6]),
            "favorite": bool(r[7]), "timestamp": r[8]
        })
    return {"success": True, "library": items}

@app.post("/api/library/favorite")
async def toggle_favorite_db(req: FavRequest):
    conn = get_db()
    conn.execute("UPDATE user_library SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ? AND user_id = ?", (req.file_id, str(req.user_id)))
    conn.commit()
    return {"success": True}

@app.delete("/api/library/{file_id}")
async def delete_from_library_db(file_id: str, user_id: str = "default_user"):
    conn = get_db()
    conn.execute("DELETE FROM user_library WHERE id = ? AND user_id = ?", (file_id, str(user_id)))
    conn.commit()
    return {"success": True}

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
