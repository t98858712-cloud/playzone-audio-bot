import os
import sys
import uuid
import time
import json
import sqlite3
import threading
import subprocess
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

try:
    from database.operations import stat_inc_sync
except ImportError:
    def stat_inc_sync(key: str, value: int = 1): pass

# --- التكوين السحابي والإعدادات العامة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MusicPlayZoneBot")
ADSTERRA_LINK = os.getenv(
    "ADSTERRA_LINK",
    "https://www.effectivecpmnetwork.com/jgv39bh2p?key=8ffb7ed8cb605d90c6d07e1f7a698646"
)

BASE_DOWNLOAD_DIR = Path("./downloads")
WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = "playzone_core.db"

app = FastAPI(title="PlayZone Enterprise Backend")

# السماح بالاتصالات من أي مصدر (CORS) لضمان العمل على السيرفرات والتطبيقات
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# تقديم الملفات المحملة إما عبر /files أو Endpoint التنزيل المباشر
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")

# --- إدارة قاعدة البيانات المحلية (SQLite - WAL Mode) ---
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
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
        # 📌 جدول تسجيل زوار الموقع والجلسات الحية محلياً
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_live_sessions (
                session_id TEXT PRIMARY KEY,
                tg_id TEXT,
                device TEXT,
                last_ping REAL
            )""")
        conn.commit()

init_db()

DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=10)

# تنظيف دوري للملفات القديمة وسجلات قاعدة البيانات المهملة
def cleanup_cron():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            with get_db() as conn:
                conn.execute("DELETE FROM progress WHERE ? - timestamp > 86400", (now,))
                conn.execute("DELETE FROM ads WHERE ? - created_at > 3600", (now,))
                conn.execute("DELETE FROM web_live_sessions WHERE ? - last_ping > 86400", (now,))
                conn.commit()
        except Exception:
            pass
        time.sleep(1800)

threading.Thread(target=cleanup_cron, daemon=True).start()

# --- خيارات yt-dlp المحصنة ---
def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "retries": 15,
        "fragment_retries": 15,
        "socket_timeout": 30,
        "cachedir": False,
        "concurrent_fragment_downloads": 5,
        "no_check_certificate": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv"],
                "player_skip": ["web", "mweb"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    cookie_path = Path("cookies.txt")
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        opts["cookiefile"] = str(cookie_path)
    if outtmpl_path:
        opts["outtmpl"] = str(outtmpl_path)
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts

# --- المسارات والـ API Endpoints ---

@app.get("/")
def home():
    index_path = Path("index.html")
    if not index_path.exists():
        return HTMLResponse(content="<h2>PlayZone Backend Active</h2>", status_code=200)
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read().replace("{BOT_USERNAME}", BOT_USERNAME))

@app.get("/styles.css")
def get_css():
    if Path("styles.css").exists():
        return FileResponse("styles.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/app.js")
def get_js():
    if Path("app.js").exists():
        return FileResponse("app.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/api/download_file/{filename}")
def download_file_direct(filename: str):
    file_path = WEB_DIR / filename
    if file_path.exists() and file_path.is_file():
        media_type = "audio/mpeg" if filename.endswith(".mp3") else "video/mp4"
        return FileResponse(file_path, filename=filename, media_type=media_type)
    raise HTTPException(status_code=404, detail="الملف غير موجود")

# 📡 استقبال نبضات حضور زوار الموقع وتصعيد المستخدم للأعلى بدون تكرار
@app.post("/api/ping_session")
async def ping_session(request: Request):
    try:
        data = await request.json()
        tg_id = str(data.get("tg_id", "")).strip() or "زائر مجهول"
        device = str(data.get("device", "غير معروف"))
        client_ip = request.client.host if request.client else "unknown"
        
        clean_ip = client_ip.replace('.', '_').replace(':', '_')
        
        # 🔑 مفتاح فريد لكل مستخدم يمنع التكرار ويضمن تحديث نفس السجل وتصعيده للأعلى
        if tg_id != "زائر مجهول" and tg_id.isdigit():
            doc_id = f"usr_{tg_id}"
        else:
            doc_id = f"anon_{clean_ip}"
            
        now_ts = time.time()

        # 1️⃣ الحفظ وتحديث التوقيت في SQLite
        with get_db() as conn:
            conn.execute("""
                INSERT INTO web_live_sessions (session_id, tg_id, device, last_ping)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    tg_id=excluded.tg_id,
                    device=excluded.device,
                    last_ping=excluded.last_ping
            """, (doc_id, tg_id, device, now_ts))
            conn.commit()

        # 2️⃣ التحديث الفوري المباشر في Firebase Firestore (تحديث السجل نفسه)
        try:
            from database.connection import db
            if db is not None:
                db.collection('web_visitors').document(doc_id).set({
                    'session_id': doc_id,
                    'tg_id': tg_id,
                    'device': device,
                    'ip': client_ip,
                    'last_ping': now_ts
                }, merge=True)
        except Exception:
            pass

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# 📊 دالة توليد تقرير رادار الزوار بدون تكرار والأحدث بالترتيب الأول
def get_web_visitors_report() -> str:
    now = time.time()
    online_threshold = now - 120
    rows = []
    online_count = 0
    seen_users = set()

    try:
        from database.connection import db
        from firebase_admin import firestore
        from utils.helpers import esc
    except ImportError:
        db = None
        firestore = None
        def esc(s): return str(s) if s else ""

    if db is not None and firestore is not None:
        try:
            docs = db.collection('web_visitors').order_by('last_ping', direction=firestore.Query.DESCENDING).limit(30).stream()
            for doc in docs:
                d = doc.to_dict()
                tg_key = str(d.get('tg_id', '')).strip()
                v_key = tg_key if tg_key != "زائر مجهول" else d.get('session_id')
                
                if v_key in seen_users:
                    continue
                seen_users.add(v_key)
                
                rows.append(d)
                if float(d.get('last_ping', 0)) >= online_threshold:
                    online_count += 1
                if len(rows) >= 15:
                    break
        except Exception:
            rows = []

    if not rows:
        with get_db() as conn:
            cursor = conn.execute(
                "SELECT session_id, tg_id, device, last_ping FROM web_live_sessions ORDER BY last_ping DESC LIMIT 30"
            )
            for r in cursor.fetchall():
                tg_key = str(r['tg_id']).strip()
                v_key = tg_key if tg_key != "زائر مجهول" else r['session_id']
                
                if v_key in seen_users:
                    continue
                seen_users.add(v_key)
                
                lp = float(r['last_ping'])
                if lp >= online_threshold:
                    online_count += 1
                    
                rows.append({
                    'tg_id': r['tg_id'],
                    'device': r['device'],
                    'last_ping': lp
                })
                if len(rows) >= 15:
                    break

    if not rows:
        return "🌐 <b>لا يوجد زوار في الموقع حالياً.</b>"

    text = f"🌐 <b>رادار زوار الموقع الإلكتروني المتكامل (Firebase)</b>\n\n"
    text += f"🟢 <b>المتواجدون الآن (أونلاين):</b> {online_count} زائر\n\n"
    text += "📋 <b>أحدث الزوار ومعلوماتهم والتوقيتات:</b>\n\n"

    local_tz = timezone(timedelta(hours=3))

    for r in rows:
        tg_id_str = str(r.get('tg_id', '')).strip()
        last_ping = float(r.get('last_ping', 0))
        is_online = last_ping >= online_threshold
        status_str = "🟢 أونلاين" if is_online else "🔴 غير متواجد"
        
        dt = datetime.fromtimestamp(last_ping, tz=timezone.utc).astimezone(local_tz)
        exact_time = dt.strftime('%Y-%m-%d %I:%M %p')
        device_str = r.get('device') or "متصفح ويب 🌐"
        
        user_header = "👤 <b>زائر مجهول</b>"
        id_line = ""

        if tg_id_str != "زائر مجهول" and tg_id_str.isdigit() and db is not None:
            try:
                u_doc = db.collection('users').document(tg_id_str).get()
                if u_doc.exists:
                    u = u_doc.to_dict()
                    first_name = esc(u.get('first_name', ''))
                    last_name = esc(u.get('last_name', ''))
                    full_name = f"{first_name} {last_name}".strip() or "مستخدم"
                    username = u.get('username')
                    uname_str = f" (@{esc(username)})" if username and username != "لا يوجد" and username != "" else ""
                    
                    # الاسم والـ ID قابلان للنسخ
                    user_header = f"👤 <code>{full_name}</code>{uname_str}"
                    id_line = f"\n  └ 🆔 <code>{tg_id_str}</code>"
                else:
                    user_header = "👤 <b>مستخدم</b>"
                    id_line = f"\n  └ 🆔 <code>{tg_id_str}</code>"
            except Exception:
                user_header = "👤 <b>مستخدم</b>"
                id_line = f"\n  └ 🆔 <code>{tg_id_str}</code>"
        elif tg_id_str != "زائر مجهول" and tg_id_str.isdigit():
            user_header = "👤 <b>مستخدم</b>"
            id_line = f"\n  └ 🆔 <code>{tg_id_str}</code>"

        text += f"• {user_header}{id_line}\n  └ {device_str} | {status_str}\n  └ 🕒 <code>{exact_time}</code>\n\n"

    return text

@app.get("/api/admin/web_visitors")
def api_admin_web_visitors():
    return {"report": get_web_visitors_report()}

@app.post("/api/search")
async def api_search(request: Request):
    stat_inc_sync("web_requests", 1)
    try:
        data = await request.json()
        query = data.get("query", "")
        opts = get_hardened_ydl_options()
        opts['extract_flat'] = True
        if 'playlist_items' in opts: del opts['playlist_items']
        if 'noplaylist' in opts: del opts['noplaylist']
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw_results = ydl.extract_info(f"ytsearch25:{query}", download=False) or {}
            
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id")
            title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail") or (
                    entry.get("thumbnails")[0].get("url") if entry.get("thumbnails")
                    else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                )
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
async def get_preview(request: Request):
    try:
        data = await request.json()
        url = data.get("url", "")
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "success": True,
                "title": info.get("title", "بدون عنوان"),
                "thumb": info.get("thumbnail", ""),
                "uploader": info.get("uploader", "غير معروف"),
                "duration": info.get("duration", 0)
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/generate_ad_session")
def generate_ad_session():
    stat_inc_sync("adsterra_clicks", 1)
    click_id = uuid.uuid4().hex[:12]
    with get_db() as conn:
        conn.execute("INSERT INTO ads (click_id, status, created_at) VALUES (?, ?, ?)", (click_id, "pending", time.time()))
        conn.commit()
    separator = "&" if "?" in ADSTERRA_LINK else "?"
    return {"click_id": click_id, "ad_link": f"{ADSTERRA_LINK}{separator}clickid={click_id}"}

@app.get("/api/ad_callback")
def ad_callback(clickid: str):
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM ads WHERE click_id = ?", (clickid,))
        if cursor.fetchone():
            conn.execute("UPDATE ads SET status = 'verified' WHERE click_id = ?", (clickid,))
            conn.commit()
            return {"status": "success", "message": "Verified"}
    return {"status": "error", "message": "Invalid token"}

@app.get("/api/check_ad_status/{click_id}")
def check_ad_status(click_id: str):
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM ads WHERE click_id = ?", (click_id,))
        row = cursor.fetchone()
        if not row: return {"status": "not_found"}
        if row["status"] == "verified" or (time.time() - row["created_at"] > 10):
            return {"status": "verified"}
        return {"status": row["status"]}

def bg_download_worker(job_id: str, url: str, mode: str, res: str):
    last_update = [0.0]

    def hook(d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update[0] < 0.5:
                return
            last_update[0] = now

            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            payload = {
                "status": "downloading",
                "percent": round((downloaded / total) * 100, 1) if total > 0 else 0,
                "total_mb": f"{total / 1048576:.1f} MB",
                "dl_mb": f"{downloaded / 1048576:.1f} MB",
                "spd_mb": f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s"
            }
            try:
                with get_db() as conn:
                    conn.execute("UPDATE progress SET status='downloading', data=?, timestamp=? WHERE job_id=?", (json.dumps(payload), time.time(), job_id))
                    conn.commit()
            except Exception:
                pass
        elif d['status'] == 'finished':
            try:
                payload = {"status": "converting", "percent": 99.0, "spd_mb": "معالجة..."}
                with get_db() as conn:
                    conn.execute("UPDATE progress SET status='converting', data=?, timestamp=? WHERE job_id=?", (json.dumps(payload), time.time(), job_id))
                    conn.commit()
            except Exception:
                pass

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook)
    max_fs = "49M"
    
    if mode == 'raw_audio':
        opts.update({
            'format': f"bestaudio[acodec=opus][filesize<?{max_fs}]/bestaudio[ext=m4a][filesize<?{max_fs}]/bestaudio"
        })
    elif mode == 'audio':
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'
            }]
        })
    elif mode == 'raw_video':
        opts.update({
            'format': (
                f"bestvideo[vcodec^=av01][filesize<?{max_fs}]+bestaudio[acodec^=opus]/"
                f"bestvideo[vcodec^=vp09][filesize<?{max_fs}]+bestaudio/"
                f"bestvideo[filesize<?{max_fs}]+bestaudio/"
                f"best"
            ),
            'merge_output_format': 'mp4',
            'postprocessor_args': {'ffmpeg': ['-c:a', 'aac', '-b:a', '320k']}
        })
    else:
        target_res = res if res and res != 'best' else '720'
        opts.update({
            'format': f"bestvideo[vcodec^=avc1][height<={target_res}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/bestvideo[height<={target_res}]+bestaudio/best",
            'merge_output_format': 'mp4',
            'postprocessor_args': {'ffmpeg': ['-c:a', 'aac', '-b:a', '192k']}
        })
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = info.get('ext', 'mp3' if 'audio' in mode else 'mp4')
            filename = f"{job_id}.{ext}"
            payload = {
                "status": "completed",
                "url": f"/files/{filename}",
                "title": info.get('title', 'مقطع'),
                "thumb": info.get('thumbnail', ''),
                "uploader": info.get('uploader', 'غير معروف'),
                "duration": info.get('duration', 0),
                "is_audio": mode in ['audio', 'raw_audio']
            }
            with get_db() as conn:
                conn.execute("UPDATE progress SET status='completed', data=? WHERE job_id=?", (json.dumps(payload), job_id))
                conn.commit()
            stat_inc_sync("web_downloads", 1)
    except Exception as e:
        payload = {"status": "error", "error": str(e)}
        with get_db() as conn:
            conn.execute("UPDATE progress SET status='error', data=? WHERE job_id=?", (json.dumps(payload), job_id))
            conn.commit()

@app.post("/api/download")
async def start_download(request: Request):
    data = await request.json()
    url = data.get("url", "")
    mode = str(data.get("mode", "video")).lower().strip()
    resolution = str(data.get("resolution", "720")).lower().strip()
    click_id = data.get("click_id", "")

    if not url:
        return {"success": False, "error": "يرجى إدخال رابط صحيح."}

    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM ads WHERE click_id = ?", (click_id,))
        row = cursor.fetchone()
        if not row: return {"success": False, "error": "جلسة إعلانية غير صالحة."}
        if not (row["status"] == "verified" or (time.time() - row["created_at"] > 10)):
            return {"success": False, "error": "خطأ: لم يتم تأكيد فك قفل التحميل بعد."}

    stat_inc_sync("adsterra_verified", 1)
    job_id = uuid.uuid4().hex[:8]
    with get_db() as conn:
        conn.execute("INSERT INTO progress (job_id, status, data, timestamp) VALUES (?, ?, ?, ?)", (job_id, "starting", "{}", time.time()))
        conn.commit()
        
    DOWNLOAD_POOL.submit(bg_download_worker, job_id, url, mode, resolution)
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
def get_progress(job_id: str):
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM progress WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row: return {"status": "waiting"}
        status = row["status"]
        if status == "starting": return {"status": "starting", "percent": 0}
        if status == "converting": return {"status": "converting", "percent": 99}
        return json.loads(row["data"])

@app.post("/api/send_telegram")
async def send_to_telegram(request: Request):
    temp_file_created = False
    file_path = None
    try:
        content_type = request.headers.get("content-type", "")

        chat_id = ""
        is_audio = True
        title = "مقطع"
        performer = "PlayZone"
        duration = 0
        thumb = ""
        file_url = ""

        if "application/json" in content_type.lower():
            body = await request.json()
            chat_id = str(body.get("chat_id", ""))
            is_audio = bool(body.get("is_audio", True))
            title = str(body.get("title", "مقطع"))
            performer = str(body.get("performer", "PlayZone"))
            duration = int(body.get("duration", 0))
            thumb = str(body.get("thumb", ""))
            file_url = str(body.get("file_url", ""))
        else:
            form = await request.form()
            chat_id = str(form.get("chat_id", ""))
            is_audio_val = form.get("is_audio", "true")
            is_audio = str(is_audio_val).lower() in ["true", "1", "yes"]
            title = str(form.get("title", "مقطع"))
            performer = str(form.get("performer", "PlayZone"))
            try:
                duration = int(form.get("duration", 0))
            except (ValueError, TypeError):
                duration = 0
            thumb = str(form.get("thumb", ""))
            file_url = str(form.get("file_url", ""))
            
            uploaded_file = form.get("file")
            if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
                ext = ".mp3" if is_audio else ".mp4"
                temp_name = f"temp_upload_{uuid.uuid4().hex[:8]}{ext}"
                file_path = WEB_DIR / temp_name
                with open(file_path, "wb") as f_out:
                    f_out.write(await uploaded_file.read())
                temp_file_created = True

        if not file_path and file_url:
            filename = file_url.split("/")[-1]
            file_path = WEB_DIR / filename

        if not file_path or not file_path.exists():
            return {"success": False, "error": "الملف غير موجود على السيرفر."}

        if not TELEGRAM_TOKEN:
            return {"success": False, "error": "توكن البوت غير مفعل بالخلفية."}

        if file_path.stat().st_size / (1024 * 1024) > 49.5:
            if temp_file_created: file_path.unlink(missing_ok=True)
            return {"success": False, "error": "حجم الملف يتجاوز 50 ميجابايت."}

        api_method = "sendAudio" if is_audio else "sendVideo"
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        dur = int(duration) if duration else 0
        
        time_str = f"{dur // 60:02d}:{dur % 60:02d}"
        if dur > 0:
            caption = f'- @P1ay_Z0ne_Bot , <a href="https://t.me/MusicPlayZoneBot">{time_str}</a>'
        else:
            caption = "- @P1ay_Z0ne_Bot"
        
        share_bot_url = "https://t.me/MusicPlayZoneBot"
        share_text = "📥 حمّل أي فيديو أو أغنية MP3 في ثوانٍ!\n⚡ بوت سريع، مجاني وبأعلى جودة.\n👇 جرّبه الآن:"
        full_share_url = f"https://t.me/share/url?url={quote(share_bot_url)}&text={quote(share_text)}"

        reply_markup = {
            "inline_keyboard": [
                [{"text": "🌟 أعجبك البوت؟ شاركه", "url": full_share_url}]
            ]
        }
        
        data_payload = {
            'chat_id': chat_id,
            'caption': caption,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(reply_markup)
        }
        
        if is_audio:
            data_payload.update({'title': title, 'performer': performer, 'duration': dur})
        else:
            data_payload.update({'supports_streaming': True, 'duration': dur})
            try:
                cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(file_path)]
                res = subprocess.run(cmd, capture_output=True, text=True)
                probe_data = json.loads(res.stdout)
                data_payload.update({'width': probe_data['streams'][0]['width'], 'height': probe_data['streams'][0]['height']})
            except Exception:
                pass

        with open(file_path, 'rb') as f_media:
            files_payload = {'audio' if is_audio else 'video': (file_path.name, f_media)}
            
            if thumb and is_audio:
                try:
                    t_res = requests.get(thumb, timeout=4)
                    if t_res.status_code == 200: files_payload['thumb'] = ('thumb.jpg', t_res.content, 'image/jpeg')
                except Exception:
                    pass
                    
            response = requests.post(telegram_url, data=data_payload, files=files_payload, timeout=120)
            res_data = response.json()
        
        if temp_file_created:
            file_path.unlink(missing_ok=True)

        if response.status_code == 200 and res_data.get("ok"): 
            return {"success": True}
        return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة مع البوت أولاً.")}
        
    except Exception as e: 
        if temp_file_created and file_path and file_path.exists():
            file_path.unlink(missing_ok=True)
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    # استقبال البوت المخصص ديناميكياً من Railway لمنع أخطاء التوصيل والشبكة
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("web_app:app", host="0.0.0.0", port=port)
