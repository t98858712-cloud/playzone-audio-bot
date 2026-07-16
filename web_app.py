import os, threading, uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Template
import yt_dlp

# استيراد إعدادات البوت وقاعدة البيانات الخاصة بك
from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK
from database.connection import init_db
from database.operations import load_stats_sync, all_user_ids, get_active_users_48h, get_latest_users
from services.downloader import extract_metadata, search_youtube

app = FastAPI()
init_db()

# تجهيز مجلد لحفظ ملفات الموقع بعيداً عن حذف البوت التلقائي
WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")

PROGRESS_CACHE = {}

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"

class SearchRequest(BaseModel):
    query: str

# ==========================================
# 1. واجهة الموقع الرئيسية (HTML + JS)
# ==========================================
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>PlayZone | تحميل ومكتبة</title>
    <script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script>tailwind.config={darkMode:'class',theme:{extend:{colors:{primary:'#00e676',cardbg:'#1e293b'}}}}</script>
</head>
<body class="bg-[#0f172a] text-white font-sans p-4">
    <div class="max-w-4xl mx-auto space-y-6">
        <div class="bg-cardbg p-6 rounded-2xl shadow-lg border border-gray-700 text-center">
            <h1 class="text-3xl font-bold text-primary mb-2">📥 PlayZone Web</h1>
            <p class="text-gray-400 mb-6">أرسل رابط فيديو أو اكتب اسم الأغنية للبحث عنها 🔎</p>
            <input type="text" id="url" placeholder="الرابط أو كلمة البحث..." class="w-full bg-gray-800 text-white p-4 rounded-lg border border-gray-600 mb-4 focus:outline-none focus:border-primary">
            <button onclick="processInput()" id="mainBtn" class="bg-blue-600 px-6 py-3 rounded-lg font-bold w-full md:w-auto"><i class="fas fa-search"></i> فحص / بحث</button>
            <div id="searchResults" class="hidden mt-6 text-right space-y-2"></div>
            <div id="previewBox" class="hidden mt-6 p-4 bg-gray-800 rounded-lg text-right flex flex-col md:flex-row gap-4 items-center border border-gray-600">
                <img id="thumb" class="w-full md:w-32 rounded-lg shadow-md">
                <div class="flex-1 w-full">
                    <h3 id="title" class="font-bold text-lg text-primary truncate"></h3>
                    <p id="duration" class="text-sm text-gray-400 mb-4"></p>
                    <div id="adGate" class="bg-green-900/30 border border-primary p-3 rounded-lg text-center mb-4">
                        <p class="text-sm mb-2">لفتح قفل التحميل، اضغط على الإعلان وعد بعد 12 ثانية ❤️</p>
                        <a href="{{ ad_link }}" target="_blank" onclick="startAdTimer()" class="bg-primary text-gray-900 px-4 py-1 rounded font-bold text-sm inline-block"><i class="fas fa-eye"></i> مشاهدة الإعلان</a>
                        <button id="verifyBtn" onclick="unlockDownload()" disabled class="bg-gray-600 text-gray-400 px-4 py-1 rounded font-bold text-sm inline-block ml-2"><i class="fas fa-lock"></i> تحقق</button>
                    </div>
                    <div id="dlOptions" class="hidden space-y-2">
                        <div class="grid grid-cols-2 gap-2">
                            <select id="mode" onchange="toggleRes()" class="bg-gray-700 p-2 rounded-lg text-sm w-full"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                            <select id="resolution" class="bg-gray-700 p-2 rounded-lg text-sm w-full"><option value="360">360p</option><option value="480">480p</option><option value="720" selected>720p</option><option value="1080">1080p</option></select>
                        </div>
                        <button onclick="downloadMedia()" class="w-full bg-primary text-gray-900 font-bold py-2 rounded-lg"><i class="fas fa-download"></i> بدء التحميل</button>
                    </div>
                </div>
            </div>
            <div id="progressBox" class="hidden mt-6 text-right">
                <div class="flex justify-between text-sm mb-1 text-gray-300"><span id="progStatus">جاري التحميل...</span><span id="progPercent">0%</span></div>
                <div class="w-full bg-gray-700 rounded-full h-3 mb-2"><div id="progBar" class="bg-primary h-3 rounded-full transition-all duration-500" style="width: 0%"></div></div>
                <div class="flex justify-between text-xs text-gray-400"><span id="progSize">0 MB / 0 MB</span><span id="progSpeed">0 MB/s</span></div>
            </div>
        </div>
        <div class="bg-cardbg p-6 rounded-2xl shadow-lg border border-gray-700">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-blue-400"><i class="fas fa-play-circle"></i> مكتبتي السحابية</h2>
                <div><a href="/admin" class="text-sm bg-blue-600 px-3 py-1 rounded mr-2">لوحة التحكم</a><button onclick="location.reload()" class="text-sm bg-gray-700 px-3 py-1 rounded"><i class="fas fa-sync"></i></button></div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                {% for file in files %}
                <div class="bg-gray-800 p-4 rounded-xl border border-gray-600">
                    <p class="text-sm font-bold truncate mb-3" dir="ltr">{{ file.name }}</p>
                    {% if file.is_audio %}<audio controls class="w-full h-10"><source src="{{ file.url }}" type="audio/mpeg"></audio>
                    {% else %}<video controls class="w-full h-32 bg-black rounded-lg"><source src="{{ file.url }}" type="video/mp4"></video>{% endif %}
                    <a href="{{ file.url }}" download class="mt-2 block text-center text-xs bg-gray-700 hover:bg-gray-600 py-2 rounded text-white">📥 تحميل للجهاز</a>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
    <script>
        let currentUrl = "", adWatched = false;
        async function processInput() {
            const input = document.getElementById('url').value; if(!input) return;
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            if (input.startsWith('http')) await fetchPreview(input);
            else {
                const res = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                const data = await res.json();
                if(data.success) {
                    const box = document.getElementById('searchResults'); box.innerHTML = '<h4 class="text-primary font-bold">🔎 نتائج البحث:</h4>';
                    data.entries.forEach(v => box.innerHTML += `<div onclick="fetchPreview('https://youtube.com/watch?v=${v.id}')" class="bg-gray-800 p-3 rounded-lg border border-gray-600 cursor-pointer mt-2"><p class="font-bold text-sm truncate">${v.title}</p></div>`);
                    box.classList.remove('hidden');
                }
            }
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-search"></i> فحص / بحث';
        }
        async function fetchPreview(url) {
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden');
            const res = await fetch('/api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
            const data = await res.json();
            if(data.success) {
                document.getElementById('previewBox').classList.remove('hidden');
                document.getElementById('thumb').src = data.thumb; document.getElementById('title').innerText = data.title;
                document.getElementById('adGate').classList.remove('hidden'); document.getElementById('dlOptions').classList.add('hidden');
                document.getElementById('verifyBtn').disabled = true; document.getElementById('verifyBtn').className = "bg-gray-600 text-gray-400 px-4 py-1 rounded font-bold text-sm inline-block ml-2"; adWatched = false;
            }
        }
        function startAdTimer() { setTimeout(()=>{adWatched=true;}, 12000); const vBtn = document.getElementById('verifyBtn'); vBtn.disabled=false; vBtn.className="bg-blue-600 text-white px-4 py-1 rounded font-bold text-sm inline-block ml-2"; }
        function unlockDownload() { if(!adWatched) return alert('❌ انتظر 12 ثانية!'); document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden'); }
        function toggleRes() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }
        async function downloadMedia() {
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            const res = await fetch('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:currentUrl, mode:document.getElementById('mode').value, resolution:document.getElementById('resolution').value})});
            const data = await res.json();
            if(data.success) {
                const interval = setInterval(async ()=>{
                    const progRes = await fetch(`/api/progress/${data.job_id}`); const prog = await progRes.json();
                    if(prog.status === 'downloading') { document.getElementById('progPercent').innerText=prog.percent+'%'; document.getElementById('progBar').style.width=prog.percent+'%'; document.getElementById('progSize').innerText=prog.downloaded+' / '+prog.total; document.getElementById('progSpeed').innerText=prog.speed; }
                    else if(prog.status === 'converting') document.getElementById('progStatus').innerHTML='⚙️ جاري الدمج والضغط...';
                    else if(prog.status === 'completed') { clearInterval(interval); document.getElementById('progStatus').innerHTML='✅ اكتمل التحميل!'; setTimeout(()=>location.reload(), 1500); }
                }, 1500);
            }
        }
    </script>
</body></html>
"""

# ==========================================
# 2. واجهة لوحة التحكم الإدارية
# ==========================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>لوحة التحكم</title>
    <script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script>tailwind.config={darkMode:'class',theme:{extend:{colors:{primary:'#00e676',cardbg:'#1e293b'}}}}</script>
</head>
<body class="bg-[#0f172a] text-white font-sans p-6">
    <div class="max-w-4xl mx-auto space-y-6">
        <div class="flex justify-between items-center"><h1 class="text-3xl font-bold text-primary"><i class="fas fa-server"></i> الإدارة</h1><a href="/" class="bg-gray-700 px-4 py-2 rounded text-sm">رجوع</a></div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-blue-500 text-center"><p class="text-gray-400">مستخدمين</p><h2 class="text-3xl font-bold">{{ total_users }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-green-500 text-center"><p class="text-gray-400">نجاح</p><h2 class="text-3xl font-bold">{{ stats.success }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-red-500 text-center"><p class="text-gray-400">فشل</p><h2 class="text-3xl font-bold">{{ stats.failed }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-purple-500 text-center"><p class="text-gray-400">نشط (48h)</p><h2 class="text-3xl font-bold">{{ active }}</h2></div>
        </div>
    </div>
</body></html>
"""

# ==========================================
# 3. مسارات النظام (API Endpoints)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def home():
    files = [{"name": i.name, "url": f"/files/{i.name}", "is_audio": i.suffix=='.mp3'} for i in WEB_DIR.glob("*") if i.is_file() and i.suffix in ['.mp4','.mp3']]
    ad = HILLTOPADS_LINK if HILLTOPADS_LINK else ADSTERRA_LINK
    return Template(INDEX_HTML).render(files=files, ad_link=ad)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try: return {"success": True, "entries": search_youtube(req.query, limit=5).get("entries", [])}
    except Exception as e: return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        info = extract_metadata(req.url)
        return {"success": True, "title": info.get("title"), "thumb": info.get("thumbnail")}
    except Exception as e: return {"success": False, "error": str(e)}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            PROGRESS_CACHE[job_id] = {
                "status": "downloading", "percent": round((d.get('downloaded_bytes',0)/total)*100, 1),
                "downloaded": f"{round(d.get('downloaded_bytes',0)/1048576, 1)} MB", "total": f"{round(total/1048576, 1)} MB",
                "speed": f"{round(d.get('speed',0)/1048576, 1)} MB/s" if d.get('speed') else "0 MB/s"
            }
        elif d['status'] == 'finished': PROGRESS_CACHE[job_id] = {"status": "converting"}

    opts = {'outtmpl': str(WEB_DIR / f'{job_id}_%(title)s.%(ext)s'), 'quiet': True, 'progress_hooks': [hook]}
    if mode == 'audio': opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]})
    else: opts.update({'format': f'bestvideo[height<={res}]+bestaudio/best', 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        PROGRESS_CACHE[job_id] = {"status": "completed"}
    except Exception: PROGRESS_CACHE[job_id] = {"status": "error"}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting"}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    return Template(ADMIN_HTML).render(stats=load_stats_sync(), total_users=len(all_user_ids()), active=len(get_active_users_48h()))
