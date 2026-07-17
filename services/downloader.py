import uuid
import shutil
import logging
import asyncio
import urllib.request
import requests
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from telegram.ext import Application
from core.config import COOKIES_FILE, LOCAL_API_URL, PROGRESS_UPDATE_SECONDS, EXECUTOR, COOKIES_YOUTUBE
from utils.helpers import cookie_file_is_usable, alert_admins_live, make_progress_bar, format_size, get_cookie_file_for_url
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

def fallback_cobalt_download(url: str, mode: str = "video"):
    """الخطة ب (السلاح السري): استخدام Cobalt API للتحميل بدون كوكيز عند فشل المحرك الأساسي"""
    api_url = "https://api.cobalt.tools/api/json"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "PlayZoneBot/1.0"
    }
    payload = {
        "url": url,
        "vQuality": "720",
        "isAudioOnly": True if mode == "audio" else False,
        "isNoTTWatermark": True, 
    }
    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") in ["stream", "redirect"]:
                return data.get("url")
    except Exception as e:
        logger.error(f"Cobalt API Fallback failed: {e}")
    return None

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720", url: str | None = None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 15, "fragment_retries": 15, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "source_address": "0.0.0.0",
        "force_ipv4": True, 
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv", "mweb"],
                "player_skip": ["web"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
    
    if mode == "audio":
        opts["format"] = "bestaudio/best"
    else:
        from core.config import LOCAL_API_URL
        max_fs = "50M" if not LOCAL_API_URL else "2000M"
        
        if resolution == "best":
            opts["format"] = f"bestvideo[vcodec^=avc1][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/bestvideo[filesize<?{max_fs}]+bestaudio/best"
        else:
            opts["format"] = f"bestvideo[vcodec^=avc1][height<={resolution}][filesize<?{max_fs}]+bestaudio[acodec^=mp4a]/bestvideo[height<={resolution}][filesize<?{max_fs}]+bestaudio/best"
            
        opts["merge_output_format"] = "mp4"
        opts["postprocessor_args"] = {"ffmpeg": ["-c:a", "aac", "-b:a", "320k"]}

    cookie_path = get_cookie_file_for_url(url) if url else None
    if cookie_path and cookie_file_is_usable(cookie_path):
        opts["cookiefile"] = str(cookie_path)
    elif cookie_file_is_usable(COOKIES_FILE):
        opts["cookiefile"] = str(COOKIES_FILE)

    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)]
    return opts

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video", url=url)
    opts["skip_download"] = True
    opts["extract_flat"] = False
    opts["format"] = "best"
    opts.pop("merge_output_format", None)
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.warning(f"yt-dlp metadata failed, using fallback for: {url}. Error: {e}")
        return {
            "title": "مقطع وسائط (تم التخطي بالسيرفر البديل)",
            "thumbnail": "https://via.placeholder.com/640x360.png?text=PlayZone+Media",
            "duration": 0,
            "uploader": "PlayZone Network"
        }

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
    
    if cookie_file_is_usable(COOKIES_YOUTUBE):
        opts["cookiefile"] = str(COOKIES_YOUTUBE)
    elif cookie_file_is_usable(COOKIES_FILE):
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
    opts = get_ydl_options(job_dir, progress_data, mode, resolution, url=url)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Exception as e:
        error_str = str(e).lower()
        if "sign in" in error_str or "cookie" in error_str or "bot" in error_str or "unavailable" in error_str:
            lang = progress_data.get("lang", "ar")
            progress_data["text"] = "🔄 <b>فشل السيرفر الأساسي. جاري التحويل للملاذ الآمن والتحميل المباشر...</b>"
            
            direct_url = fallback_cobalt_download(url, mode)
            if direct_url:
                try:
                    filename = f"fallback_dl.mp3" if mode == 'audio' else f"fallback_dl.mp4"
                    file_path = job_dir / filename
                    response = requests.get(direct_url, stream=True)
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return {"title": "تم التحميل بنجاح", "duration": 0, "uploader": "PlayZone Fallback"}
                except Exception:
                    pass
        raise e

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
            cookie_path = get_cookie_file_for_url("https://youtube.com")
            if not cookie_path:
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nملف كوكيز يوتيوب غير متوفر أو منتهي الصلاحية. يرجى إرساله للبوت لمنع توقف التحميل.")
                continue
            opts = {
                "quiet": True, 
                "extract_flat": True, 
                "cookiefile": str(cookie_path),
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
