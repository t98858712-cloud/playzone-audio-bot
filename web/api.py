import uuid
import time
import threading
import requests
import json
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from web.design import INDEX_HTML
from video.downloader import search_youtube, get_ydl_options, execute_download
from core.config import BASE_DOWNLOAD_DIR
from links.urls import HILLTOPADS_LINK, ADSTERRA_LINK, TELEGRAM_TOKEN

WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
PROGRESS_CACHE = {}
AD_VERIFICATIONS = {}
AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "https://example.com/ad")

class SearchRequest(BaseModel): query: str
class URLRequest(BaseModel): url: str; mode: str = "video"; resolution: str = "720"; click_id: str = ""
class TelegramRequest(BaseModel): file_url: str; chat_id: str; is_audio: bool; title: str = "مقطع"; performer: str = "PlayZone"; duration: int = 0; thumb: str = ""

def init_web_routes(app: FastAPI):
    @app.get("/", response_class=HTMLResponse)
    async def home(): return HTMLResponse(content=INDEX_HTML)

    @app.post("/api/search")
    async def api_search(req: SearchRequest):
        try:
            raw_results = search_youtube(req.query, limit=25) or {}; entries = raw_results.get("entries") or []; valid_videos = []
            for entry in entries:
                if not entry: continue
                video_id = entry.get("id"); title = entry.get("title")
                if video_id and title:
                    thumb_url = entry.get("thumbnail")
                    if not thumb_url and entry.get("thumbnails"): thumb_url = entry.get("thumbnails")[0].get("url")
                    if not thumb_url: thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    valid_videos.append({ "id": video_id, "title": title, "duration": entry.get("duration") or 0, "uploader": entry.get("uploader") or entry.get("channel") or "غير معروف", "thumbnail": thumb_url })
                if len(valid_videos) == 5: break
            return {"success": True, "entries": valid_videos}
        except Exception as e: return {"success": False, "error": str(e)}

    @app.get("/api/generate_ad_session")
    def generate_ad_session():
        click_id = uuid.uuid4().hex[:12]; AD_VERIFICATIONS[click_id] = {"status": "pending", "created_at": time.time()}; separator = "&" if "?" in AD_LINK else "?"
        return {"click_id": click_id, "ad_link": f"{AD_LINK}{separator}clickid={click_id}"}

    @app.get("/api/ad_callback")
    def ad_callback(clickid: str):
        if clickid in AD_VERIFICATIONS: AD_VERIFICATIONS[clickid]["status"] = "verified"; return {"status": "success", "message": "Ad verified successfully"}
        return {"status": "error", "message": "Invalid token"}

    @app.get("/api/check_ad_status/{click_id}")
    def check_ad_status(click_id: str):
        session = AD_VERIFICATIONS.get(click_id); if not session: return {"status": "not_found"}
        if session["status"] == "verified" or (time.time() - session["created_at"] > 10): return {"status": "verified"}
        return {"status": session["status"]}
