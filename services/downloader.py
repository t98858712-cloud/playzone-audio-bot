import uuid
import shutil
import logging
import asyncio
import json
import subprocess
import urllib.request
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from telegram.ext import Application
from core.config import COOKIES_FILE, LOCAL_API_URL, PROGRESS_UPDATE_SECONDS, EXECUTOR
from utils.helpers import cookie_file_is_usable, alert_admins_live, make_progress_bar, format_size
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

def get_media_duration(file_path: Path) -> int:
    """استخراج مدة المقطع الفعلية عبر ffprobe لضمان عدم ظهور 00:00"""
    if not file_path.exists():
        return 0
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(file_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        data = json.loads(res.stdout)
        dur = float(data.get("format", {}).get("duration", 0))
        return int(round(dur))
    except Exception:
        return 0

def get_entry_thumbnail(entry: dict) -> str:
    """استخراج رابط صورة المعاينة الحقيقي من بيانات المنصة دون تخمين روابط"""
    if not entry or not isinstance(entry, dict):
        return ""
    
    thumb = entry.get("thumbnail")
    if thumb and isinstance(thumb, str) and thumb.startswith("http"):
        return thumb

    thumbnails = entry.get("thumbnails")
    if thumbnails and isinstance(thumbnails, list):
        for t in reversed(thumbnails):
            if isinstance(t, dict):
                u = t.get("url")
                if u and isinstance(u, str) and u.startswith("http"):
                    return u
    return ""

def sanitize_video_stream(file_path: Path) -> Path:
    """فحص ومعالجة سريعة تمنع تجمد شاشة الفيديو مع عمل الصوت"""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return file_path
    
    if file_path.suffix.lower() not in [".mp4", ".mkv", ".webm", ".mov"]:
        return file_path

    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,pix_fmt",
            "-of", "json",
            str(file_path)
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=8)
        if res.returncode != 0 or not res.stdout.strip():
            return file_path
        
        probe_data = json.loads(res.stdout)
        streams = probe_data.get("streams", [])
        if not streams:
            return file_path
        
        stream = streams[0]
        codec = str(stream.get("codec_name", "")).lower().strip()
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        pix_fmt = str(stream.get("pix_fmt", "")).lower().strip()

        needs_fix = (
            codec not in ["h264", "avc1"] or
            (width > 0 and width % 2 != 0) or
            (height > 0 and height % 2 != 0) or
            pix_fmt != "yuv420p"
        )

        if not needs_fix:
            return file_path

        temp_fixed = file_path.with_name(f"fixed_{uuid.uuid4().hex[:6]}_{file_path.name}")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(file_path),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "22",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(temp_fixed)
        ]
        run_res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=90)
        if run_res.returncode == 0 and temp_fixed.exists() and temp_fixed.stat().st_size > 0:
            file_path.unlink(missing_ok=True)
            temp_fixed.rename(file_path)
        else:
            if temp_fixed.exists():
                temp_fixed.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Sanitizing stream skipped: {e}")
    return file_path

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "extractor_args": {
            "youtube": {
                # تسلسل متوافق مع الكوكيز وتخطي قيود السيرفرات السحابية
                "player_client": ["android", "web", "ios"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
    
    if mode == "audio":
        opts["format"] = "bestaudio/best"
    else:
        from core.config import LOCAL_API_URL
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        
        if resolution == "best":
            opts["format"] = (
                f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/"
                f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio/"
                f"bestvideo[filesize<?{max_fs}]+bestaudio/"
                f"best"
            )
        else:
            opts["format"] = (
                f"bestvideo[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/"
                f"bestvideo[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]+bestaudio/"
                f"bestvideo[height<={resolution}][filesize<?{max_fs}]+bestaudio/"
                f"best"
            )
            
        opts["merge_output_format"] = "mp4"
        opts["postprocessor_args"] = {"ffmpeg": ["-c:a", "aac", "-b:a", "320k"]}

    from core.config import COOKIES_FILE
    from utils.helpers import cookie_file_is_usable
    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")
    opts["skip_download"] = True
    opts["extract_flat"] = False
    opts.pop("format", None) 
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def search_youtube(query: str, limit: int = 30):
    opts = {
        "quiet": True, 
        "extract_flat": True, 
        "skip_download": True,
        "no_warnings": True, 
        "ignoreerrors": True,
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
    combined_entries = []
    seen_ids = set()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = res.get('entries', []) if res else []
        for entry in entries:
            if entry and entry.get('id') and entry['id'] not in seen_ids:
                entry['thumbnail'] = get_entry_thumbnail(entry)
                combined_entries.append(entry)
                seen_ids.add(entry['id'])
    except Exception as e:
        logger.warning(f"Engine ytsearch failed: {e}")
    return {"entries": combined_entries}

def download_hook(progress_data: dict):
    def hook(d):
        lang = progress_data.get("lang", "ar")
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            if total:
                percent = downloaded / total * 100
                progress_data["text"] = _t("msg_dl_progress", lang, bar=make_progress_bar(percent), percent=f"{percent:.1f}", downloaded=format_size(downloaded, lang), total=format_size(total, lang), speed=format_size(speed, lang))
            else:
                progress_data["text"] = _t("msg_dl_progress_no_total", lang, downloaded=format_size(downloaded, lang))
        elif d.get("status") == "finished":
            progress_data["text"] = _t("msg_dl_finished", lang)
    return hook

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    from handlers.admin import edit_message_smart
    last_text = ""
    while not stop_event.is_set():
        text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=None)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict, resolution: str = "720"):
    opts = get_ydl_options(job_dir, progress_data, mode, resolution)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    
    if mode != "audio":
        for f in job_dir.glob("playzone_stream.*"):
            if f.is_file() and not f.name.endswith(".part"):
                sanitize_video_stream(f)
                break

    return info

def download_thumbnail_safely(thumb_url: str, output_path: Path) -> Path | None:
    from utils.helpers import is_public_host
    try:
        if not thumb_url or not is_public_host(urlparse(thumb_url).hostname or ""): return None
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024: return None
        output_path.write_bytes(data)
        return output_path if output_path.exists() else None
    except Exception: return None

async def youtube_health_monitor(app: Application):
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            if not cookie_file_is_usable(COOKIES_FILE):
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nملف `cookies.txt` غير صالح أو انتهت صلاحيته. يرجى تجديده عبر الأمر /setcookie لمنع توقف التحميل.")
                continue
            opts = {
                "quiet": True, 
                "extract_flat": True, 
                "cookiefile": str(COOKIES_FILE),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web", "ios"]
                    }
                }
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info("https://www.youtube.com/watch?v=BaW_jenozKc", download=False)
        except Exception as e:
            if "Sign in" in str(e) or "cookie" in str(e).lower():
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nيوتيوب يطلب تسجيل الدخول. ملف الكوكيز الحالي محظور.")
