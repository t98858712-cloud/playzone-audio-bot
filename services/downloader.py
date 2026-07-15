import uuid
import shutil
import logging
import asyncio
import urllib.request
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse
from telegram.ext import Application
from core.config import COOKIES_FILE, LOCAL_API_URL, PROGRESS_UPDATE_SECONDS, EXECUTOR
from utils.helpers import cookie_file_is_usable, alert_admins_live, make_progress_bar, format_size
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")[span_115](start_span)[span_115](end_span)

def get_ydl_options(job_dir: Path | None = None, progress_data: dict | None = None, mode: str = "video", resolution: str = "720"):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",[span_116](start_span)[span_116](end_span)
        "retries": 15, "fragment_retries": 15, "socket_timeout": 45, "cachedir": False,[span_117](start_span)[span_117](end_span)
        "concurrent_fragment_downloads": 10, "no_check_certificate": True,[span_118](start_span)[span_118](end_span)
        "extractor_args": {[span_119](start_span)[span_119](end_span)
            "youtube": {[span_120](start_span)[span_120](end_span)
                "player_client": ["android", "ios", "tv"],[span_121](start_span)[span_121](end_span)
                "player_skip": ["web", "mweb"][span_122](start_span)[span_122](end_span)
            }[span_123](start_span)[span_123](end_span)
        },[span_124](start_span)[span_124](end_span)
        "http_headers": {[span_125](start_span)[span_125](end_span)
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",[span_126](start_span)[span_126](end_span)
            "Accept-Language": "en-US,en;q=0.9",[span_127](start_span)[span_127](end_span)
        }[span_128](start_span)[span_128](end_span)
    }
    
    if mode == "audio":[span_129](start_span)[span_129](end_span)
        opts["format"] = "bestaudio/best[span_130](start_span)"[span_130](end_span)
    else:
        from core.config import LOCAL_API_URL[span_131](start_span)[span_131](end_span)
        max_fs = "50M" if not LOCAL_API_URL else "2000M[span_132](start_span)"[span_132](end_span)
        
        if resolution == "best":[span_133](start_span)[span_133](end_span)
            opts["format"] = f"bestvideo[ext=mp4][filesize<?{max_fs}]+bestaudio/bestvideo+bestaudio/best[span_134](start_span)"[span_134](end_span)
        else:
            opts["format"] = f"bestvideo[ext=mp4][height<={resolution}][filesize<?{max_fs}]+bestaudio/bestvideo[height<={resolution}]+bestaudio/best[span_135](start_span)"[span_135](end_span)
            
        opts["merge_output_format"] = "mp4[span_136](start_span)"[span_136](end_span)
        opts["postprocessor_args"] = {"ffmpeg": ["-c:a", "aac", "-b:a", "320k"]}[span_137](start_span)[span_137](end_span)

    from core.config import COOKIES_FILE[span_138](start_span)[span_138](end_span)
    from utils.helpers import cookie_file_is_usable[span_139](start_span)[span_139](end_span)
    if cookie_file_is_usable(COOKIES_FILE):[span_140](start_span)[span_140](end_span)
        opts["cookiefile"] = str(COOKIES_FILE)[span_141](start_span)[span_141](end_span)
    if job_dir: opts["outtmpl"] = str(job_dir / "playzone_stream.%(ext)s")[span_142](start_span)[span_142](end_span)
    if progress_data is not None: opts["progress_hooks"] = [download_hook(progress_data)][span_143](start_span)[span_143](end_span)
    return opts[span_144](start_span)[span_144](end_span)

def extract_metadata(url: str):
    opts = get_ydl_options(mode="video")[span_145](start_span)[span_145](end_span)
    opts["skip_download"] = True[span_146](start_span)[span_146](end_span)
    opts["extract_flat"] = False[span_147](start_span)[span_147](end_span)
    opts.pop("format", None)[span_148](start_span)[span_148](end_span)
    
    with yt_dlp.YoutubeDL(opts) as ydl:[span_149](start_span)[span_149](end_span)
        return ydl.extract_info(url, download=False)[span_150](start_span)[span_150](end_span)

def search_youtube(query: str, limit: int = 30):
    opts = {
        "quiet": True,[span_151](start_span)[span_151](end_span)
        "extract_flat": True,[span_152](start_span)[span_152](end_span)
        "no_warnings": True,[span_153](start_span)[span_153](end_span)
        "ignoreerrors": True,[span_154](start_span)[span_154](end_span)
        "extractor_args": {[span_155](start_span)[span_155](end_span)
            "youtube": {[span_156](start_span)[span_156](end_span)
                "player_client": ["android", "ios", "tv"],[span_157](start_span)[span_157](end_span)
                "player_skip": ["web", "mweb"][span_158](start_span)[span_158](end_span)
            }[span_159](start_span)[span_159](end_span)
        }
    }
    if cookie_file_is_usable(COOKIES_FILE):[span_160](start_span)[span_160](end_span)
        opts["cookiefile"] = str(COOKIES_FILE)[span_161](start_span)[span_161](end_span)
    combined_entries = [][span_162](start_span)[span_162](end_span)
    seen_ids = set()[span_163](start_span)[span_163](end_span)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:[span_164](start_span)[span_164](end_span)
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)[span_165](start_span)[span_165](end_span)
            entries = res.get('entries', []) if res else [][span_166](start_span)[span_166](end_span)
        for entry in entries:[span_167](start_span)[span_167](end_span)
            if entry and entry.get('id') and entry['id'] not in seen_ids:[span_168](start_span)[span_168](end_span)
                combined_entries.append(entry)[span_169](start_span)[span_169](end_span)
                seen_ids.add(entry['id'])[span_170](start_span)[span_170](end_span)
    except Exception as e:
        logger.warning(f"Engine ytsearch failed: {e}")[span_171](start_span)[span_171](end_span)
    return {"entries": combined_entries}[span_172](start_span)[span_172](end_span)

def download_hook(progress_data: dict):
    def hook(d):
        lang = progress_data.get("lang", "ar")[span_173](start_span)[span_173](end_span)
        if d.get("status") == "downloading":[span_174](start_span)[span_174](end_span)
            downloaded = d.get("downloaded_bytes") or 0[span_175](start_span)[span_175](end_span)
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0[span_176](start_span)[span_176](end_span)
            speed = d.get("speed") or 0[span_177](start_span)[span_177](end_span)
            if total:[span_178](start_span)[span_178](end_span)
                percent = downloaded / total * 100[span_179](start_span)[span_179](end_span)
                progress_data["text"] = _t("msg_dl_progress", lang, bar=make_progress_bar(percent), percent=f"{percent:.1f}", downloaded=format_size(downloaded, lang), total=format_size(total, lang), speed=format_size(speed, lang))[span_180](start_span)[span_180](end_span)
            else:
                progress_data["text"] = _t("msg_dl_progress_no_total", lang, downloaded=format_size(downloaded, lang))[span_181](start_span)[span_181](end_span)
        elif d.get("status") == "finished":[span_182](start_span)[span_182](end_span)
            progress_data["text"] = _t("msg_dl_finished", lang)[span_183](start_span)[span_183](end_span)
    return hook[span_184](start_span)[span_184](end_span)

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    from handlers.admin import edit_message_smart[span_185](start_span)[span_185](end_span)
    last_text = "[span_186](start_span)"[span_186](end_span)
    while not stop_event.is_set():[span_187](start_span)[span_187](end_span)
        text = progress_data.get("text", "")[span_188](start_span)[span_188](end_span)
        if text and text != last_text:[span_189](start_span)[span_189](end_span)
            try:
                await edit_message_smart(message, text, reply_markup=None)[span_190](start_span)[span_190](end_span)
                last_text = text[span_191](start_span)[span_191](end_span)
            except Exception: pass[span_192](start_span)[span_192](end_span)
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)[span_193](start_span)[span_193](end_span)

def execute_download(url: str, mode: str, job_dir: Path, progress_data: dict, resolution: str = "720"):
    opts = get_ydl_options(job_dir, progress_data, mode, resolution)[span_194](start_span)[span_194](end_span)
    with yt_dlp.YoutubeDL(opts) as ydl:[span_195](start_span)[span_195](end_span)
        return ydl.extract_info(url, download=True)[span_196](start_span)[span_196](end_span)

def download_thumbnail_safely(thumb_url: str, output_path: Path) -> Path | None:
    from utils.helpers import is_public_host[span_197](start_span)[span_197](end_span)
    try:
        if not thumb_url or not is_public_host(urlparse(thumb_url).hostname or ""): return None[span_198](start_span)[span_198](end_span)
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})[span_199](start_span)[span_199](end_span)
        with urllib.request.urlopen(req, timeout=6) as response:[span_200](start_span)[span_200](end_span)
            data = response.read(2 * 1024 * 1024 + 1)[span_201](start_span)[span_201](end_span)
        if len(data) > 2 * 1024 * 1024: return None[span_202](start_span)[span_202](end_span)
        output_path.write_bytes(data)[span_203](start_span)[span_203](end_span)
        return output_path if output_path.exists() else None[span_204](start_span)[span_204](end_span)
    except Exception: return None[span_205](start_span)[span_205](end_span)

async def youtube_health_monitor(app: Application):
    while True:
        await asyncio.sleep(6 * 3600)[span_206](start_span)[span_206](end_span)
        try:
            if not cookie_file_is_usable(COOKIES_FILE):[span_207](start_span)[span_207](end_span)
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nملف `cookies.txt` غير صالح أو انتهت صلاحيته. يرجى تجديده عبر الأمر /setcookie لمنع توقف التحميل.")[span_208](start_span)[span_208](end_span)
                continue[span_209](start_span)[span_209](end_span)
            opts = {[span_210](start_span)[span_210](end_span)
                "quiet": True,[span_211](start_span)[span_211](end_span)
                "extract_flat": True,[span_212](start_span)[span_212](end_span)
                "cookiefile": str(COOKIES_FILE),[span_213](start_span)[span_213](end_span)
                "extractor_args": {[span_214](start_span)[span_214](end_span)
                    "youtube": {[span_215](start_span)[span_215](end_span)
                        "player_client": ["android", "ios", "tv"],[span_216](start_span)[span_216](end_span)
                        "player_skip": ["web", "mweb"][span_217](start_span)[span_217](end_span)
                    }[span_218](start_span)[span_218](end_span)
                }[span_219](start_span)[span_219](end_span)
            }
            with yt_dlp.YoutubeDL(opts) as ydl:[span_220](start_span)[span_220](end_span)
                ydl.extract_info("https://www.youtube.com/watch?v=BaW_jenozKc", download=False)[span_221](start_span)[span_221](end_span)
        except Exception as e:
            if "Sign in" in str(e) or "cookie" in str(e).lower():[span_222](start_span)[span_222](end_span)
                await alert_admins_live(app.bot, "⚠️ <b>تنبيه من السيرفر:</b>\nيوتيوب يطلب تسجيل الدخول. ملف الكوكيز الحالي محظور.")[span_223](start_span)[span_223](end_span)
