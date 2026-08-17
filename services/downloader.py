import uuid
import shutil
import logging
import asyncio
import subprocess
import json
import urllib.request
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from telegram.ext import Application
from core.config import COOKIES_FILE, LOCAL_API_URL, PROGRESS_UPDATE_SECONDS, EXECUTOR
from utils.helpers import cookie_file_is_usable, alert_admins_live, make_progress_bar, format_size
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

def ensure_compatible_video(input_file: Path) -> Path:
    """
    فحص ترميز الفيديو والصوت وإصلاحهما تلقائياً لمنع تجمد الصورة أو انقطاع الصوت
    """
    try:
        if not input_file.exists() or input_file.suffix.lower() not in [".mp4", ".mkv", ".webm", ".mov"]:
            return input_file

        cmd_probe = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=codec_type,codec_name',
            '-of', 'json', str(input_file)
        ]
        res = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)
        data = json.loads(res.stdout)
        streams = data.get('streams', [])
        
        v_codec = ""
        a_codec = ""
        for s in streams:
            if s.get('codec_type') == 'video' and not v_codec:
                v_codec = s.get('codec_name', '').lower()
            elif s.get('codec_type') == 'audio' and not a_codec:
                a_codec = s.get('codec_name', '').lower()
        
        # إذا كان الفيديو H.264 والصوت AAC فهو متوافق 100% دون الحاجة لمعالجة
        if v_codec in ['h264', 'avc', 'avc1'] and a_codec in ['aac', 'mp4a']:
            return input_file
        
        # إصلاح الفيديو السريع إذا كان الترميز VP9/AV1 أو الصوت Opus
        output_file = input_file.parent / f"fixed_{uuid.uuid4().hex[:6]}.mp4"
        v_args = ['-c:v', 'copy'] if v_codec in ['h264', 'avc', 'avc1'] else ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-pix_fmt', 'yuv420p']
        a_args = ['-c:a', 'copy'] if a_codec in ['aac', 'mp4a'] else ['-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-ar', '44100']
        
        cmd_convert = [
            'ffmpeg', '-y',
            '-i', str(input_file),
            *v_args,
            *a_args,
            '-movflags', '+faststart',
            str(output_file)
        ]
        
        conv_res = subprocess.run(cmd_convert, capture_output=True, timeout=90)
        if conv_res.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
            input_file.unlink(missing_ok=True)
            output_file.rename(input_file.with_suffix('.mp4'))
            return input_file.with_suffix('.mp4')
    except Exception as e:
        logger.error(f"Error in ensure_compatible_video: {e}")
    return input_file

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
    opts = {
        "quiet": True, 
        "no_warnings": True, 
        "noplaylist": True, 
        "playlist_items": "1",
        "retries": 15, 
        "fragment_retries": 15, 
        "socket_timeout": 45, 
        "cachedir": False,
        "concurrent_fragment_downloads": 10, 
        "no_check_certificate": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv"],
                "player_skip": ["web", "mweb"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
    
    from core.config import LOCAL_API_URL
    max_fs = "50M" if not LOCAL_API_URL else "2000M"

    # 1️⃣ صوت أصلي
    if mode == "raw_audio":
        opts["format"] = f"bestaudio[filesize<?{max_fs}]/bestaudio/best"

    # 2️⃣ صوت مفلتر (MP3)
    elif mode == "audio":
        opts["format"] = f"bestaudio[filesize<?{max_fs}]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]

    # 3️⃣ فيديو أصلي
    elif mode == "raw_video":
        opts["format"] = (
            f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio[acodec^=mp4a][filesize<?{max_fs}]/"
            f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"bestvideo[filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"best[vcodec!=none][acodec!=none][filesize<?{max_fs}]/"
            f"bestvideo+bestaudio/best"
        )
        opts["merge_output_format"] = "mp4"

    # 4️⃣ فيديو مفلتر
    else:
        target_res = resolution if resolution and resolution != "best" else "720"
        opts["format"] = (
            f"bestvideo[vcodec^=avc1][height<={target_res}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a][filesize<?{max_fs}]/"
            f"bestvideo[vcodec^=avc1][width<={target_res}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a][filesize<?{max_fs}]/"
            f"bestvideo[vcodec^=avc1][height<={target_res}][filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"bestvideo[vcodec^=avc1][width<={target_res}][filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"bestvideo[height<={target_res}][filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"bestvideo[width<={target_res}][filesize<?{max_fs}]+bestaudio[filesize<?{max_fs}]/"
            f"best[vcodec!=none][acodec!=none][height<={target_res}][filesize<?{max_fs}]/"
            f"best[vcodec!=none][acodec!=none][width<={target_res}][filesize<?{max_fs}]/"
            f"best[vcodec!=none][acodec!=none][filesize<?{max_fs}]/"
            f"bestvideo+bestaudio/best"
        )
        opts["merge_output_format"] = "mp4"

    from core.config import COOKIES_FILE
    from utils.helpers import cookie_file_is_usable
    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: 
        opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: 
        opts["progress_hooks"] = [download_hook(progress_data)]
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
        "no_warnings": True, 
        "ignoreerrors": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv"],
                "player_skip": ["web", "mweb"]
            }
        }
    }
    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)
    combined_entries = []
    seen_ids = set()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = res.get('entries', []) if res else []
        for entry in entries:
            if entry and entry.get('id') and entry['id'] not in seen_ids:
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
        return ydl.extract_info(url, download=True)

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
                        "player_client": ["android", "ios", "tv"],
                        "player_skip": ["web", "mweb"]
                    }
                }
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
        except Exception as e:
            if "Sign in" in str(e) or "cookie" in str(e).lower():
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nيوتيوب يطلب تسجيل الدخول. ملف الكوكيز الحالي محظور.")
