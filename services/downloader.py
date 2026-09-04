import uuid
import shutil
import logging
import asyncio
import urllib.request
import subprocess
import json
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from telegram.ext import Application
from core.config import COOKIES_FILE, LOCAL_API_URL, PROGRESS_UPDATE_SECONDS, EXECUTOR
from utils.helpers import cookie_file_is_usable, alert_admins_live, make_progress_bar, format_size
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

def ensure_compatible_video(file_path: Path) -> Path:
    if not file_path.exists() or file_path.stat().st_size == 0:
        return file_path
        
    target_path = file_path.with_suffix(".mp4")
    temp_out = file_path.with_name(f"temp_fix_{uuid.uuid4().hex[:6]}.mp4")
    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt,width,height",
            "-of", "json", str(file_path)
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        data = json.loads(res.stdout) if res.stdout else {}
        v_streams = data.get("streams", [])
        
        v_codec = ""
        v_pix = ""
        w = 0
        h = 0
        if v_streams:
            v_codec = v_streams[0].get("codec_name", "").lower()
            v_pix = v_streams[0].get("pix_fmt", "").lower()
            w = int(v_streams[0].get("width") or 0)
            h = int(v_streams[0].get("height") or 0)

        probe_a = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "json", str(file_path)
        ]
        res_a = subprocess.run(probe_a, capture_output=True, text=True)
        data_a = json.loads(res_a.stdout) if res_a.stdout else {}
        a_streams = data_a.get("streams", [])
        a_codec = a_streams[0].get("codec_name", "").lower() if a_streams else ""

        # الشروط القياسية لتشغيل الفيديو بدون تجمد: H.264 + yuv420p + أبعاد زوجية + صوت aac
        need_v = (v_codec != "h264") or (v_pix != "yuv420p") or (w % 2 != 0) or (h % 2 != 0)
        need_a = (a_codec != "aac") and (a_codec != "")
        is_mp4 = (file_path.suffix.lower() == ".mp4")

        if is_mp4 and not need_v and not need_a:
            return file_path

        cmd = ["ffmpeg", "-y", "-i", str(file_path)]

        if need_v:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-pix_fmt", "yuv420p"
            ])
        else:
            cmd.extend(["-c:v", "copy"])

        if need_a:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        elif a_codec:
            cmd.extend(["-c:a", "copy"])

        cmd.extend(["-movflags", "+faststart", str(temp_out)])

        sub_res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sub_res.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 1000:
            if file_path.exists() and file_path != target_path:
                file_path.unlink(missing_ok=True)
            temp_out.replace(target_path)
            return target_path
        else:
            if temp_out.exists():
                temp_out.unlink(missing_ok=True)
    except Exception:
        if temp_out.exists():
            temp_out.unlink(missing_ok=True)
    return file_path

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv"],
                "player_skip": ["web", "mweb"]
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
                f"best[vcodec^=avc1][filesize<?{max_fs}]/"
                f"best"
            )
        else:
            opts["format"] = (
                f"bestvideo[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/"
                f"bestvideo[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]+bestaudio/"
                f"bestvideo[height<={resolution}][filesize<?{max_fs}]+bestaudio/"
                f"best[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]/"
                f"best"
            )
            
        opts["merge_output_format"] = "mp4"

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
        info = ydl.extract_info(url, download=True)
    
    if mode != "audio":
        for video_file in list(job_dir.glob("*")):
            if video_file.is_file() and video_file.suffix.lower() in [".mp4", ".mkv", ".webm", ".mov"] and not video_file.name.startswith("temp_fix_"):
                ensure_compatible_video(video_file)
                
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
                        "player_client": ["android", "ios", "tv"],
                        "player_skip": ["web", "mweb"]
                    }
                }
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info("https://www.youtube.com/watch?v=BaW_jenozKc", download=False)
        except Exception as e:
            if "Sign in" in str(e) or "cookie" in str(e).lower():
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nيوتيوب يطلب تسجيل الدخول. ملف الكوكيز الحالي محظور.")
