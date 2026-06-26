import time
import uuid
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# هنا نستورد دوالك الأصلية من main.py دون تغيير أي حرف فيها
from main import (
    extract_metadata, 
    execute_download, 
    convert_to_mp3_local, 
    download_thumbnail_safely,
    BASE_DOWNLOAD_DIR,
    EXECUTOR
)

app = FastAPI(title="Play Zone API")

# إعداد CORS للسماح لموقع Play Zone على GitHub بالاتصال بهذا السيرفر
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://tasmg1.github.io", 
        "https://tasmg1.github.io",
        "*" # يمكنك تقييد هذا لاحقاً لزيادة الأمان
    ], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DownloadRequest(BaseModel):
    url: str
    mode: str = "video" # يقبل "video" أو "audio"

def cleanup_job_dir(job_dir: Path):
    """دالة لحذف المجلد المؤقت بعد إرسال الملف للمتصفح"""
    try:
        import shutil
        if job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception:
        pass

@app.post("/api/download")
async def download_media(request: DownloadRequest, background_tasks: BackgroundTasks):
    try:
        # 1. جلب المعلومات باستخدام دالتك الأصلية
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(EXECUTOR, lambda: extract_metadata(request.url))
        
        # 2. تجهيز مجلد التحميل بنفس طريقتك
        job_id = f"web_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        job_dir = BASE_DOWNLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        progress_data = {} # قاموس وهمي لتجنب أخطاء دوال التحديث
        thumb_url = info.get("thumbnail") or ""

        # 3. تحميل الملف الأساسي
        await loop.run_in_executor(EXECUTOR, lambda: execute_download(request.url, request.mode, job_dir, progress_data))
        
        files = [p for p in job_dir.iterdir() if p.is_file() and p.suffix not in [".part", ".tmp", ".ytdl"]]
        if not files:
            raise HTTPException(status_code=500, detail="فشل في حفظ الملف.")
            
        raw_downloaded_file = max(files, key=lambda p: p.stat().st_mtime)
        target_file = raw_downloaded_file

        # 4. معالجة الصوت إذا كان الطلب MP3
        if request.mode == "audio":
            local_thumb = await loop.run_in_executor(EXECUTOR, lambda: download_thumbnail_safely(thumb_url, job_dir / "playzone_thumb.jpg"))
            final_mp3_path = job_dir / "playzone_final_audio.mp3"
            success = await loop.run_in_executor(EXECUTOR, lambda: convert_to_mp3_local(raw_downloaded_file, final_mp3_path, local_thumb))
            if success and final_mp3_path.exists():
                target_file = final_mp3_path

        # 5. جدولة حذف الملفات بعد انتهاء التحميل لحماية السيرفر
        background_tasks.add_task(cleanup_job_dir, job_dir)

        # 6. إعادة الملف للمتصفح للتحميل المباشر
        filename = f"PlayZone_{int(time.time())}{target_file.suffix}"
        return FileResponse(path=target_file, filename=filename, media_type='application/octet-stream')

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Play Zone API is running!"}
