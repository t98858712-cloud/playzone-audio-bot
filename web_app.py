import os, sys, uuid, time, requests, json, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import yt_dlp
import libsql_experimental as libsql

# -------------------------------------------------------------
# 1. إعدادات البوت والاتصال السحابي
# -------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot"

# ضع هنا ID القناة الخاصة التي تم إنشاؤها لتخزين الملفات (مثال: -1001234567890)
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID", "-100XXXXXXXXXX") 

# بيانات قاعدة بيانات Turso السحابية المباشرة
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "libsql://musicbot-t98858712-cloud.aws-eu-west-1.turso.io")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODQ1MjQ1MjYsImlkIjoiMDE5ZjdkZjAtZjcwMS03M2YxLTg3OTQtNTU3OTA5OWZmMjQ0Iiwia2lkIjoiOTlnN1pjeElMMUJvUWtBejdETnhCV2RLRXZRN2l1bXVFYXNUYWp1RVBubyIsInJpZCI6IjE5ZTAyMzE3LWZlNDAtNDUwYS05YzZjLWM5Mzg4MmQ1YjA5NiJ9.S-cAb_n7Q8c8pT3CACaehmhjtiQeHGBtZOOphzBTjqjGWvzv3WIUZM1Xhy_p-XmSJ157TGrd1tozzBkRWoXKCA")

# مجلد التنزيل المؤقت (سيتم مسح الملفات منه فور رفعها لسحابة تيليجرام)
TEMP_DIR = Path("./temp_downloads")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PlayZone Cloud Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# 2. إدارة قاعدة البيانات السحابية (Turso)
# -------------------------------------------------------------
def get_db():
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)

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
            telegram_file_id TEXT,
            file_url TEXT,
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

# -------------------------------------------------------------
# 3. النماذج والدوال المساعدة
# -------------------------------------------------------------
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
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 30, "cachedir": False,
        "concurrent_fragment_downloads": 5, "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

# رفع الملف إلى قناة التخزين السحابية واستخراج رابط مباشر يدوم للأبد
def upload_to_telegram_cloud(file_path: Path, is_audio: bool, title: str, duration: int):
    if not TELEGRAM_TOKEN or STORAGE_CHANNEL_ID == "-100XXXXXXXXXX":
        return None, None
        
    api_method = "sendAudio" if is_audio else "sendVideo"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
    
    data = {'chat_id': STORAGE_CHANNEL_ID, 'caption': f"📁 {title}\n🤖 @{BOT_USERNAME}", 'duration': duration}
    
    try:
        with open(file_path, 'rb') as f:
            files = {'audio' if is_audio else 'video': f}
            res = requests.post(url, data=data, files=files, timeout=120)
            res_json = res.json()
            
            if res_json.get("ok"):
                msg = res_json["result"]
                file_obj = msg.get("audio") or msg.get("video")
                file_id = file_obj.get("file_id")
                
                file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
                if file_info.get("ok"):
                    file_path_tg = file_info["result"]["file_path"]
                    direct_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path_tg}"
                    return file_id, direct_url
    except Exception:
        pass
    return None, None

# -------------------------------------------------------------
# 4. معالجة التحميل بالخلفية والرفع السحابي
# -------------------------------------------------------------
def bg_download_worker(job_id: str, url: str, mode: str, res: str, user_id: str):
    temp_file = TEMP_DIR / f"{job_id}.{'mp3' if mode == 'audio' else 'mp4'}"
    
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

    opts = get_hardened_ydl_options(outtmpl_path=temp_file, progress_hook=hook)
    if mode == 'audio':
        opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    else:
        opts.update({'format': f"bestvideo[height<={res}]+bestaudio/best", 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'مقطع')
            duration = info.get('duration', 0)
            thumb = info.get('thumbnail', '')
            uploader = info.get('uploader', 'غير معروف')

            # الرفع فوراً إلى السحابة
            file_id, cloud_url = upload_to_telegram_cloud(temp_file, mode == 'audio', title, duration)
            
            # مسح الملف المؤقت من الاستضافة لتوفير الذاكرة
            if temp_file.exists():
                temp_file.unlink()

            final_url = cloud_url or f"/files/{temp_file.name}"
            payload = {
                "status": "completed", "url": final_url, "file_id": file_id or "",
                "title": title, "thumb": thumb, "uploader": uploader,
                "duration": duration, "is_audio": mode == 'audio'
            }

            # حفظ البيانات في Turso
            conn = get_db()
            conn.execute("UPDATE progress SET status='completed', data=? WHERE job_id=?", (json.dumps(payload), job_id))
            conn.execute("""
                INSERT OR REPLACE INTO user_library (id, user_id, title, telegram_file_id, file_url, thumb, uploader, duration, is_audio, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, str(user_id), title, file_id or "", final_url, thumb, uploader, duration, 1 if mode == 'audio' else 0, time.time()))
            conn.commit()

    except Exception as e:
        if temp_file.exists(): temp_file.unlink()
        payload = {"status": "error", "error": str(e)}
        conn = get_db()
        conn.execute("UPDATE progress SET status='error', data=? WHERE job_id=?", (json.dumps(payload), job_id))
        conn.commit()

# -------------------------------------------------------------
# 5. مسارات الـ API للواجهة الأمامية
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read().replace("{BOT_USERNAME}", BOT_USERNAME))

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        opts = get_hardened_ydl_options()
        opts['extract_flat'] = True
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
    conn = get_db()
    conn.execute("INSERT INTO ads (click_id, status, created_at) VALUES (?, ?, ?)", (click_id, "pending", time.time()))
    conn.commit()
    AD_LINK = "https://bony-teaching.com/TwZD7z"
    separator = "&" if "?" in AD_LINK else "?"
    return {"click_id": click_id, "ad_link": f"{AD_LINK}{separator}clickid={click_id}"}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    conn = get_db()
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
    rows = conn.execute("SELECT id, title, file_url, thumb, uploader, duration, is_audio, favorite, timestamp FROM user_library WHERE user_id = ? ORDER BY timestamp DESC", (str(user_id),)).fetchall()
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
