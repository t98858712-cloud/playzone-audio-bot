import threading
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import time
import uuid
from pathlib import Path

# استيراد دوالك الأصلية من ملف main.py دون تعديلها
from main import (
    extract_metadata, 
    execute_download, 
    convert_to_mp3_local, 
    download_thumbnail_safely,
    BASE_DOWNLOAD_DIR,
    EXECUTOR,
    main as start_telegram_bot # استيراد دالة تشغيل البوت
)

app = FastAPI(title="Play Zone API")

# إعداد CORS للموقع
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DownloadRequest(BaseModel):
    url: str
    mode: str = "video"

def cleanup_job_dir(job_dir: Path):
    try:
        import shutil
        if job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception:
        pass

# تشغيل بوت تيليجرام في خلفية السيرفر (Thread منفصل) ليعملا معاً مجاناً
def run_bot_in_background():
    print("🚀 جاري إطلاق بوت تيليجرام في الخلفية...")
    start_telegram_bot()

bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
bot_thread.start()

@app.post("/api/download")
async def download_media(request: DownloadRequest, background_tasks: BackgroundTasks):
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(request.url))
        
        job_id = f"web_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        job_dir = BASE_DOWNLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        progress_data = {}
        thumb_url = info.get("thumbnail") or ""

        await loop.run_in_executor(EXECUTOR, lambda: execute_download(request.url, request.mode, job_dir, progress_data))
        
        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        if not files:
            raise HTTPException(status_code=500, detail="فشل التحميل.")
            
        raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)
        target_file = raw_downloaded_file

        if request.mode == "audio":
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(thumb_url, job_dir / "playzone_thumb.jpg"))
            final_mp3_path = job_dir / "playzone_final_audio.mp3"
            success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
            if success and final_mp3_path.exists():
                target_file = final_mp3_path

        background_tasks.add_task(cleanup_job_dir, job_dir)

        filename = f"PlayZone_{int(time.time())}{target_file.suffix}"
        return FileResponse(path=target_file, filename=filename, media_type='application/octet-stream')

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Play Zone API & Bot are running perfectly on Hugging Face 16GB RAM!"}
