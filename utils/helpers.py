import os
import re
import html
import time
import shutil
import logging
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.config import (
    BASE_DOWNLOAD_DIR, COOKIES_FILE, REQUEST_EXPIRE_SECONDS, 
    OLD_DOWNLOADS_EXPIRE_SECONDS, MAX_THUMBNAIL_BYTES
)
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

def parse_admin_ids():
    admin_ids_raw = os.getenv("ADMIN_IDS", "")
    return {int(item.strip()) for item in admin_ids_raw.split(",") if item.strip().isdigit()}

def is_admin(user_id: int) -> bool:
    return user_id in parse_admin_ids()

async def alert_admins_live(bot, text: str):
    for adm in parse_admin_ids():
        try:
            await bot.send_message(chat_id=adm, text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception: pass

def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)

def clean_title(text: str, limit=60, lang: str = "ar") -> str:
    if not text: return _t("txt_media_file", lang)
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "..." if len(text) > limit else text

def format_size(size_bytes, lang: str = "ar") -> str:
    try: size_bytes = float(size_bytes)
    except Exception: return _t("txt_unknown", lang)
    if size_bytes <= 0: return _t("txt_unknown", lang)
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{int(size_bytes)} {unit}" if size_bytes == int(size_bytes) else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def format_duration(seconds, lang: str = "ar") -> str:
    try: seconds = int(seconds)
    except Exception: return _t("txt_unknown", lang)
    if seconds <= 0: return "00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def is_public_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}: return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)
    except ValueError: return True

def is_valid_url(text: str) -> bool:
    try:
        text = (text or "").strip()
        if len(text) > 2000: return False
        parsed = urlparse(text)
        if parsed.scheme not in ["http", "https"] or not parsed.netloc: return False
        if parsed.username or parsed.password: return False
        return is_public_host(parsed.hostname or "")
    except Exception: return False

def get_thumbnail(info: dict) -> str:
    try:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            best = sorted(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)[0]
            return best.get("url") or info.get("thumbnail") or ""
        return info.get("thumbnail") or ""
    except Exception: return ""

def get_artist(info: dict, lang: str = "ar") -> str:
    for key in ["artist", "uploader", "channel", "creator"]:
        val = info.get(key)
        if val: return clean_title(val, 35, lang)
    return _t("txt_unknown", lang)

def make_progress_bar(percent: float) -> str:
    filled = int(max(0, min(100, float(percent))) // 10)
    return "🟩" * filled + "⬜" * (10 - filled)

def get_largest_estimated_size(info: dict) -> int:
    sizes = []
    for f in info.get("formats", []) or []:
        try: sizes.append(int(f.get("filesize") or f.get("filesize_approx") or 0))
        except Exception: pass
    return max(sizes) if sizes else 0

def ensure_pending_requests(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("pending_requests", {})

def trim_old_pending_requests(context: ContextTypes.DEFAULT_TYPE, max_items: int = 8):
    pending = ensure_pending_requests(context)
    now = int(time.time())
    for rid, item in list(pending.items()):
        if now - int(item.get("created_at", 0)) > REQUEST_EXPIRE_SECONDS:
            pending.pop(rid, None)
    if len(pending) > max_items:
        items = sorted(pending.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)
        context.user_data["pending_requests"] = dict(items[:max_items])

def cookie_file_is_usable(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0: return False
        now = int(time.time())
        has_valid_cookie = False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split("\t")
                if len(parts) < 7: continue
                domain, _, _, _, expires, name, value = parts[:7]
                try: exp = int(expires)
                except Exception: exp = 0
                if value.strip() and (exp == 0 or exp > now):
                    has_valid_cookie = True
                    break
        return has_valid_cookie
    except Exception: return False

def _cleanup_old_downloads_sync():
    now = time.time()
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            try:
                if now - item.stat().st_mtime > OLD_DOWNLOADS_EXPIRE_SECONDS:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            except Exception: pass
    except Exception: pass

def _force_cleanup_all_sync() -> int:
    removed = 0
    try:
        for item in BASE_DOWNLOAD_DIR.iterdir():
            try:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
                removed += 1
            except Exception: pass
    except Exception: pass
    return removed

async def send_preview(update: Update, thumb: str, caption: str, keyboard: InlineKeyboardMarkup):
    if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
        try:
            return await update.message.reply_photo(
                photo=thumb,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception: pass
        
    return await update.message.reply_text(
        text=caption,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

def get_cookie_file_for_url(url: str) -> Path | None:
    """بحث ذكي وآلي: يقرأ الرابط ويبحث في مجلد السيرفر عن أي ملف نصي يحتوي على اسم المنصة"""
    if not url:
        return None
    
    url_lower = url.lower()
    from core.config import COOKIES_DIR, COOKIES_FILE
    
    # 1. تحديد الكلمة المفتاحية (اسم المنصة) بناءً على الرابط
    target = None
    if "youtube.com" in url_lower or "youtu.be" in url_lower: target = "youtube"
    elif "tiktok.com" in url_lower: target = "tiktok"
    elif "instagram.com" in url_lower: target = "instagram"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower or "fb.com" in url_lower: target = "facebook"
    elif "x.com" in url_lower or "twitter.com" in url_lower: target = "twitter"
    elif "spotify.com" in url_lower: target = "spotify"

    # 2. فحص مجلد الكوكيز آلياً والبحث عن أي ملف يحمل هذه الكلمة
    if target and COOKIES_DIR.exists():
        for file_path in COOKIES_DIR.glob("*.txt"):
            file_name = file_path.name.lower()
            if target in file_name or (target == "facebook" and "fb" in file_name) or (target == "twitter" and "x.com" in file_name):
                if cookie_file_is_usable(file_path):
                    return file_path

    # 3. خطة بديلة (Fallback): استخدم ملف الكوكيز العام إذا وُجد
    if cookie_file_is_usable(COOKIES_FILE):
        return COOKIES_FILE
        
    return None
