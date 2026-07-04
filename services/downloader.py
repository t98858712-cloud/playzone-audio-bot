import uuid
import shutil
import logging
import asyncio
import urllib.request
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from core.config import COOKIES_FILE, LOCAL_API_URL, EXECUTOR
from utils.helpers import cookie_file_is_usable, make_progress_bar, format_size
from locales.language import _t
from core.exceptions import MediaDownloadException, ContentRestrictedException

logger = logging.getLogger("PlayZoneEnterpriseBot")

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Connection": "keep-alive"
        }
    }
    
    if mode == "audio":
        opts["format"] = "bestaudio/best"
    else:
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        if resolution == "best":
            opts["format"] = f"bestvideo[filesize<?{max_fs}]+bestaudio/best[filesize<?{max_fs}]/best"
        else:
            opts["format"] = f"bestvideo[height<={resolution}][filesize<?{max_fs}]+bestaudio/best[height<={resolution}][filesize<?{max_fs}]/best/best"
            
        opts["merge_output_format"] = "mp4"
        if shutil.which("ffmpeg"):
            opts["postprocessors"] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]

    if cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str) -> dict:
    try:
        opts = get_ydl_options(mode="video")
        opts["skip_download"] = True
        opts.pop("format", None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        if "sign in" in str(e).lower() or "confirm your age" in str(e).lower():
            raise ContentRestrictedException("المقطع يتطلب تسجيل دخول أو مقيد بالفئة العمرية.", {"url": url})
        raise MediaDownloadException(f"فشل استخراج بيانات المعاينة للمقطع: {e}", {"url": url})

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict, resolution: str = "720") -> dict:
    try:
        opts = get_ydl_options(job_dir, progress_data, mode, resolution)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Exception as e:
        raise MediaDownloadException(f"فشل تنزيل ملف الميديا من المصدر: {e}", {"url": url, "mode": mode})

def download_hook(progress_data: dict):
    def hook(d):
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            lang = progress_data.get("lang", "ar")
            if total:
                percent = downloaded / total * 100
                progress_data["text"] = _t("msg_dl_progress", lang, bar=make_progress_bar(percent), percent=f"{percent:.1f}", downloaded=format_size(downloaded, lang), total=format_size(total, lang), speed=format_size(speed, lang))
            else:
                progress_data["text"] = _t("msg_dl_progress_no_total", lang, downloaded=format_size(downloaded, lang))
        elif d.get("status") == "finished":
            progress_data["text"] = _t("msg_dl_finished", progress_data.get("lang", "ar"))
    return hook

async def youtube_health_monitor(app: Application):
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            if not cookie_file_is_usable(COOKIES_FILE):
                continue
            opts = {"quiet": True, "extract_flat": True, "cookiefile": str(COOKIES_FILE)}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info("https://www.youtube.com/watch?v=BaW_jenozKc", download=False)
        except Exception:
            pass
