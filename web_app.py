# main.py
import os, threading, uuid, time, requests, json, subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot"

try:
    from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK
except ImportError:
    BASE_DOWNLOAD_DIR = Path("./downloads")
    HILLTOPADS_LINK = "https://bony-teaching.com/TwZD7z"
    ADSTERRA_LINK = "https://www.effectivecpmnetwork.com/jgv39bh2p?key=8ffb7ed8cb605d90c6d07e1f7a698646"

app = FastAPI(title="PlayZone Dashboard")

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

PROGRESS_CACHE = {}
AD_VERIFICATIONS = {}

AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "https://example.com/ad")

def cleanup_daemon():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            expired_jobs = [jid for jid, data in list(PROGRESS_CACHE.items()) if now - data.get("timestamp", now) > 86400]
            for jid in expired_jobs:
                PROGRESS_CACHE.pop(jid, None)
                
            expired_ads = [cid for cid, data in list(AD_VERIFICATIONS.items()) if now - data.get("created_at", now) > 3600]
            for cid in expired_ads:
                AD_VERIFICATIONS.pop(cid, None)
        except Exception:
            pass
        time.sleep(3600)

threading.Thread(target=cleanup_daemon, daemon=True).start()

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"
    click_id: str = ""

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

def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    
    # --- قراءة الكوكيز بشكل إجباري ومباشر ---
    try:
        cookie_path = Path("cookies.txt")
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            opts["cookiefile"] = str(cookie_path)
    except Exception:
        pass
    # ----------------------------------------
    
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 25):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    if 'playlist_items' in opts: del opts['playlist_items']
    if 'noplaylist' in opts: del opts['noplaylist']
    with yt_dlp.YoutubeDL(opts) as ydl: 
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

def formatTime(secs):
    if not secs: return "0:00"
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}:{s:02d}"

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{BOT_USERNAME}", BOT_USERNAME)
    return HTMLResponse(content=html)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        raw_results = search_youtube(req.query, limit=25) or {}
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id")
            title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail")
                if not thumb_url and entry.get("thumbnails"):
                    thumb_url = entry.get("thumbnails")[0].get("url")
                if not thumb_url:
                    thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                valid_videos.append({
                    "id": video_id, "title": title,
                    "duration": entry.get("duration") or 0,
                    "uploader": entry.get("uploader") or entry.get("channel") or "غير معروف",
                    "thumbnail": thumb_url
                })
            if len(valid_videos) == 5: break
        return {"success": True, "entries": valid_videos}
    except Exception as e: return {"success": False, "error": str(e)}

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
    AD_VERIFICATIONS[click_id] = {"status": "pending", "created_at": time.time()}
    
    separator = "&" if "?" in AD_LINK else "?"
    tracked_link = f"{AD_LINK}{separator}clickid={click_id}"
    
    return {"click_id": click_id, "ad_link": tracked_link}

@app.get("/api/ad_callback")
def ad_callback(clickid: str):
    if clickid in AD_VERIFICATIONS:
        AD_VERIFICATIONS[clickid]["status"] = "verified"
        return {"status": "success", "message": "Ad verified successfully"}
    return {"status": "error", "message": "Invalid token"}

@app.get("/api/check_ad_status/{click_id}")
def check_ad_status(click_id: str):
    session = AD_VERIFICATIONS.get(click_id)
    if not session:
        return {"status": "not_found"}
        
    if session["status"] == "verified" or (time.time() - session["created_at"] > 10):
        return {"status": "verified"}
        
    return {"status": session["status"]}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            PROGRESS_CACHE[job_id] = {
                "status": "downloading", "percent": round((downloaded / total) * 100, 1),
                "total_mb": f"{total / 1048576:.1f} MB", "dl_mb": f"{downloaded / 1048576:.1f} MB",
                "spd_mb": f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s", "timestamp": time.time()
            }
        elif d['status'] == 'finished': 
            PROGRESS_CACHE[job_id] = {"status": "converting", "timestamp": time.time()}

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook)
    
    if mode == 'audio': 
        opts.update({
            'format': 'bestaudio/best', 
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        })
    else: 
        max_fs = "49M" # حد أمان لتليجرام
        if res == "best":
            opts.update({
                'format': (
                    f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/"
                    f"bestvideo[filesize<?{max_fs}]+bestaudio/"
                    f"best"
                )
            })
        else:
            opts.update({
                'format': (
                    f"bestvideo[vcodec^=avc1][height<={res}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/"
                    f"bestvideo[height<={res}][filesize<?{max_fs}]+bestaudio/"
                    f"best"
                )
            })
            
        opts.update({
            'merge_output_format': 'mp4',
            'postprocessor_args': {'ffmpeg': ['-c:a', 'aac', '-b:a', '320k']}
        })
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url, download=True)
            filename = f"{job_id}.mp3" if mode == 'audio' else f"{job_id}.mp4"
            PROGRESS_CACHE[job_id] = {
                "status": "completed", "url": f"/files/{filename}", "title": info.get('title', 'مقطع'), 
                "thumb": info.get('thumbnail', ''), "uploader": info.get('uploader', 'غير معروف'),
                "duration": info.get('duration', 0), "is_audio": mode == 'audio', "timestamp": time.time()
            }
    except Exception as e: PROGRESS_CACHE[job_id] = {"status": "error", "error": str(e), "timestamp": time.time()}

@app.post("/api/download")
async def start_download(req: URLRequest):
    session = AD_VERIFICATIONS.get(req.click_id)
    if not session:
        return {"success": False, "error": "جلسة إعلانية غير صالحة."}
        
    is_verified = session["status"] == "verified"
    is_expired_safe = (time.time() - session["created_at"] > 10)
    
    if not (is_verified or is_expired_safe):
        return {"success": False, "error": "خطأ: لم يتم تأكيد فك قفل التحميل بعد."}
        
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting", "timestamp": time.time()}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.post("/api/send_telegram")
def send_to_telegram(req: TelegramRequest):
    try:
        filename = req.file_url.split("/")[-1]
        file_path = WEB_DIR / filename
        if not file_path.exists(): return {"success": False, "error": "الملف غير موجود."}
        if not TELEGRAM_TOKEN: return {"success": False, "error": "البوت غير مفعل."}
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
            # استخراج القياسات الأصلية للفيديو وإجبار تيليجرام عليها
            try:
                cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(file_path)]
                res = subprocess.run(cmd, capture_output=True, text=True)
                probe_data = json.loads(res.stdout)
                w = probe_data['streams'][0]['width']
                h = probe_data['streams'][0]['height']
                data.update({'width': w, 'height': h})
            except Exception:
                pass

        with open(file_path, 'rb') as f:
            file_data = f.read()
            
        files = {'audio' if req.is_audio else 'video': (filename, file_data)}
        
        # إرسال الصورة المصغرة للصوتيات فقط
        if req.thumb and req.is_audio:
            try:
                t_res = requests.get(req.thumb, timeout=4)
                if t_res.status_code == 200: files['thumb'] = ('thumb.jpg', t_res.content, 'image/jpeg')
            except: pass
                
        response = requests.post(url, data=data, files=files, timeout=60)
        res_data = response.json()
        if response.status_code == 200 and res_data.get("ok"): return {"success": True}
        return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة أولاً مع البوت.")}
    except Exception as e: return {"success": False, "error": str(e)}
