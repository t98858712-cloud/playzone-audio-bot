import os, threading, uuid, secrets
from pathlib import Path
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from jinja2 import Template
import yt_dlp

# استيراد إعدادات البوت وقاعدة البيانات والكوكيز
from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
from database.connection import init_db
from database.operations import load_stats_sync, all_user_ids, get_active_users_48h, get_latest_users
from services.downloader import extract_metadata, search_youtube
from utils.helpers import cookie_file_is_usable

app = FastAPI()
init_db()

# إعدادات الحماية للوحة التحكم
security = HTTPBasic()

def check_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, "Tareqkash")
    correct_pass = secrets.compare_digest(credentials.password, "playzone2026")
    
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="غير مصرح لك بالدخول",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

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

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>PlayZone | تحميل ومكتبة</title>
    <script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script>tailwind.config={darkMode:'class',theme:{extend:{colors:{primary:'#00e676',cardbg:'var(--card-bg)',pagebg:'var(--page-bg)',textm:'var(--text-main)',texts:'var(--text-sec)'}}}}</script>
    <style>
        :root { --page-bg: #f8fafc; --card-bg: #ffffff; --text-main: #0f172a; --text-sec: #64748b; border-color: #e2e8f0; }
        html.dark { --page-bg: #0f172a; --card-bg: #1e293b; --text-main: #f8fafc; --text-sec: #94a3b8; border-color: #334155; }
        body { background-color: var(--page-bg); color: var(--text-main); transition: background-color 0.3s, color 0.3s; }
        .theme-card { background-color: var(--card-bg); border: 1px solid var(--border-color); }
        .theme-input { background-color: var(--page-bg); color: var(--text-main); border: 1px solid var(--border-color); }
        #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 8px; padding: 16px; position: fixed; z-index: 50; left: 50%; bottom: 30px; font-size: 15px; transform: translateX(-50%); transition: visibility 0.4s, opacity 0.4s linear; opacity: 0; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        #toast.show { visibility: visible; opacity: 1; }
        html.dark #toast { background-color: #00e676; color: #000; font-weight: bold;}
    </style>
</head>
<body class="font-sans p-4">
    <div id="toast">تم الإجراء بنجاح!</div>
    <div class="max-w-4xl mx-auto space-y-6">
        <div class="flex justify-between items-center theme-card p-4 rounded-2xl shadow-sm">
            <h1 class="text-2xl font-bold text-primary">📥 PlayZone Web</h1>
            <button onclick="toggleTheme()" class="p-3 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors shadow-sm border border-gray-200 dark:border-gray-600">
                <i id="themeIcon" class="fas fa-sun text-xl text-yellow-500"></i>
            </button>
        </div>

        <div class="theme-card p-6 rounded-2xl shadow-lg text-center">
            <p class="text-gray-500 dark:text-gray-400 mb-6 font-medium">أرسل رابط فيديو أو ابحث عن مقطعك المفضل بسهولة 🔎</p>
            <div class="relative mb-4"><input type="text" id="url" placeholder="الرابط أو كلمة البحث..." class="w-full theme-input p-4 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary transition-all"></div>
            <button onclick="processInput()" id="mainBtn" class="bg-blue-600 text-white px-8 py-3 rounded-xl font-bold hover:bg-blue-700 transition-all shadow-md w-full md:w-auto"><i class="fas fa-search ml-2"></i> ابحث الآن</button>
            <div id="searchResults" class="hidden mt-6 text-right space-y-2"></div>
            <div id="previewBox" class="hidden mt-6 p-4 theme-card rounded-xl text-right flex flex-col md:flex-row gap-4 items-center border">
                <img id="thumb" class="w-full md:w-32 rounded-lg shadow-sm">
                <div class="flex-1 w-full">
                    <h3 id="title" class="font-bold text-lg text-primary truncate mb-1"></h3>
                    <p id="duration" class="text-sm text-gray-500 dark:text-gray-400 mb-4"></p>
                    <div id="adGate" class="bg-green-100 dark:bg-green-900/30 border border-primary p-4 rounded-xl text-center mb-4">
                        <p class="text-sm mb-3 font-medium">لفتح قفل التحميل، يرجى تصفح الإعلان لثوانٍ ❤️</p>
                        <a href="{{ ad_link }}" target="_blank" onclick="startAdTimer()" class="bg-primary text-gray-900 px-5 py-2 rounded-lg font-bold text-sm inline-block shadow-sm"><i class="fas fa-eye ml-1"></i> تصفح الإعلان</a>
                        <button id="verifyBtn" onclick="unlockDownload()" disabled class="bg-gray-300 dark:bg-gray-700 text-gray-500 px-5 py-2 rounded-lg font-bold text-sm inline-block mr-2 cursor-not-allowed"><i class="fas fa-lock ml-1"></i> مقفول</button>
                    </div>
                    <div id="dlOptions" class="hidden space-y-3">
                        <div class="grid grid-cols-2 gap-3">
                            <select id="mode" onchange="toggleRes()" class="theme-input p-3 rounded-xl text-sm font-medium w-full"><option value="video">🎬 مقطع مرئي (فيديو)</option><option value="audio">🎵 مقطع صوتي فقط</option></select>
                            <select id="resolution" class="theme-input p-3 rounded-xl text-sm font-medium w-full"><option value="360">جودة عادية</option><option value="720" selected>جودة عالية (HD)</option><option value="best">أفضل جودة متاحة</option></select>
                        </div>
                        <button onclick="downloadMedia()" class="w-full bg-primary text-gray-900 font-bold py-3 rounded-xl hover:bg-green-500 shadow-md text-lg"><i class="fas fa-cloud-download-alt ml-2"></i> حمل الآن</button>
                    </div>
                </div>
            </div>
            <div id="progressBox" class="hidden mt-8 text-right bg-gray-100 dark:bg-gray-800 p-5 rounded-xl border border-gray-200 dark:border-gray-700">
                <div class="flex justify-between text-sm mb-2 font-medium"><span id="progStatus" class="text-blue-600 dark:text-blue-400">جاري التجهيز...</span><span id="progPercent" class="font-bold">0%</span></div>
                <div class="w-full bg-gray-300 dark:bg-gray-700 rounded-full h-4 mb-2 shadow-inner overflow-hidden"><div id="progBar" class="bg-primary h-4 rounded-full transition-all duration-300" style="width: 0%"></div></div>
                <div class="flex justify-between text-xs text-gray-500"><span id="progSize">0 / 0</span><span id="progSpeed">0</span></div>
            </div>
        </div>

        <div class="theme-card p-6 rounded-2xl shadow-lg">
            <div class="flex justify-between items-center mb-6 border-b border-gray-200 dark:border-gray-700 pb-4">
                <h2 class="text-xl md:text-2xl font-bold text-blue-600 dark:text-blue-400"><i class="fas fa-play-circle ml-2"></i> مكتبتي السحابية</h2>
                <div class="flex gap-2">
                    <button onclick="location.reload()" class="px-4 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 font-bold text-sm"><i class="fas fa-sync ml-1"></i> تحديث</button>
                    <a href="/admin" class="px-4 py-2 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded-lg font-bold text-sm hover:bg-blue-200 dark:hover:bg-blue-800"><i class="fas fa-cog ml-1"></i> الإدارة</a>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
                {% for file in files %}
                <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-primary transition-all shadow-sm">
                    <div class="flex justify-between items-start mb-3">
                        <p class="text-sm font-bold truncate w-3/4" dir="ltr" title="{{ file.name }}">{{ file.name }}</p>
                        <span class="text-xs px-2 py-1 bg-gray-200 dark:bg-gray-700 rounded text-gray-600 dark:text-gray-300">{% if file.is_audio %}صوت{% else %}فيديو{% endif %}</span>
                    </div>
                    {% if file.is_audio %}<audio controls class="w-full h-12 rounded-lg outline-none"><source src="{{ file.url }}" type="audio/mpeg"></audio>
                    {% else %}<video controls class="w-full h-40 bg-black rounded-lg outline-none"><source src="{{ file.url }}" type="video/mp4"></video>{% endif %}
                    <div class="flex gap-2 mt-4">
                        <a href="{{ file.url }}" download class="flex-1 text-center bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 py-2 rounded-lg text-sm font-bold"><i class="fas fa-download ml-1"></i> تحميل للجهاز</a>
                        <button onclick="shareLink('{{ file.url }}')" class="flex-none px-4 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded-lg"><i class="fas fa-link"></i></button>
                    </div>
                </div>
                {% endfor %}
                {% if not files %}
                <div class="col-span-1 md:col-span-2 text-center py-10 text-gray-500">
                    <i class="fas fa-folder-open text-4xl mb-3 opacity-50"></i>
                    <p>المكتبة فارغة حالياً. ابدأ التحميل الآن لتظهر مقاطعك هنا!</p>
                </div>
                {% endif %}
            </div>
        </div>
    </div>
    
    <script>
        function toggleTheme() { document.documentElement.classList.toggle('dark'); const isDark = document.documentElement.classList.contains('dark'); localStorage.setItem('theme', isDark ? 'dark' : 'light'); updateThemeIcon(isDark); }
        function updateThemeIcon(isDark) { const icon = document.getElementById('themeIcon'); if(isDark) icon.className = 'fas fa-sun text-xl text-yellow-400'; else icon.className = 'fas fa-moon text-xl text-gray-600'; }
        if (localStorage.getItem('theme') === 'light') { document.documentElement.classList.remove('dark'); updateThemeIcon(false); } else updateThemeIcon(true);
        function showToast(msg) { const toast = document.getElementById("toast"); toast.innerText = msg; toast.className = "show"; setTimeout(() => { toast.className = toast.className.replace("show", ""); }, 3500); }
        function shareLink(url) { const fullUrl = window.location.origin + url; navigator.clipboard.writeText(fullUrl).then(() => showToast("🔗 تم نسخ رابط المقطع بنجاح!")); }
        
        let currentUrl = "", adWatched = false;
        async function processInput() {
            const input = document.getElementById('url').value; if(!input) return showToast("⚠️ يرجى إدخال رابط أو كلمة للبحث");
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-spinner fa-spin ml-2"></i> لحظات...';
            if (input.startsWith('http')) await fetchPreview(input);
            else {
                const res = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                const data = await res.json();
                if(data.success) {
                    const box = document.getElementById('searchResults'); box.innerHTML = '<h4 class="text-primary font-bold mb-3">🔎 اختر المقطع المناسب:</h4>';
                    data.entries.forEach(v => box.innerHTML += `<div onclick="fetchPreview('https://youtube.com/watch?v=${v.id}')" class="bg-gray-50 dark:bg-gray-800 p-3 rounded-xl border border-gray-200 dark:border-gray-700 cursor-pointer mt-2 hover:border-primary flex items-center justify-between"><p class="font-bold text-sm truncate flex-1 ml-2">${v.title}</p><span class="text-xs bg-gray-200 dark:bg-gray-700 px-2 py-1 rounded">⏱ ${v.duration} ث</span></div>`);
                    box.classList.remove('hidden');
                } else showToast("❌ حدث خطأ في محرك البحث");
            }
            btn.innerHTML = '<i class="fas fa-search ml-2"></i> ابحث الآن';
        }
        async function fetchPreview(url) {
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden');
            const res = await fetch('/api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
            const data = await res.json();
            if(data.success) {
                document.getElementById('previewBox').classList.remove('hidden');
                document.getElementById('thumb').src = data.thumb; document.getElementById('title').innerText = data.title;
                document.getElementById('adGate').classList.remove('hidden'); document.getElementById('dlOptions').classList.add('hidden');
                const vBtn = document.getElementById('verifyBtn'); vBtn.disabled = true; vBtn.className = "bg-gray-300 dark:bg-gray-700 text-gray-500 px-5 py-2 rounded-lg font-bold text-sm inline-block mr-2 cursor-not-allowed"; vBtn.innerHTML = '<i class="fas fa-lock ml-1"></i> مقفول'; adWatched = false;
            } else showToast("❌ تعذر قراءة الرابط المدخل");
        }
        function startAdTimer() { showToast("⏳ يرجى تصفح الإعلان 12 ثانية لتفعيل الزر"); setTimeout(()=>{adWatched=true; const vBtn = document.getElementById('verifyBtn'); vBtn.disabled=false; vBtn.className="bg-blue-600 text-white px-5 py-2 rounded-lg font-bold text-sm inline-block mr-2 hover:bg-blue-700"; vBtn.innerHTML="<i class='fas fa-unlock ml-1'></i> افتح القفل"; showToast("🔓 اكتمل الوقت، يمكنك الآن فتح القفل!");}, 12000); }
        function unlockDownload() { if(!adWatched) return showToast('⚠️ يرجى تصفح الإعلان أولاً!'); document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden'); }
        function toggleRes() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }
        
        async function downloadMedia() {
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            const res = await fetch('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:currentUrl, mode:document.getElementById('mode').value, resolution:document.getElementById('resolution').value})});
            const data = await res.json();
            if(data.success) {
                const interval = setInterval(async ()=>{
                    const progRes = await fetch(`/api/progress/${data.job_id}`); const prog = await progRes.json();
                    if(prog.status === 'downloading') { document.getElementById('progPercent').innerText=prog.percent+'%'; document.getElementById('progBar').style.width=prog.percent+'%'; document.getElementById('progSize').innerText=prog.downloaded+' / '+prog.total; document.getElementById('progSpeed').innerText=prog.speed; }
                    else if(prog.status === 'converting') { document.getElementById('progStatus').innerHTML='⚙️ يتم الآن معالجة الملف...'; document.getElementById('progPercent').innerText='100%'; document.getElementById('progBar').style.width='100%'; }
                    else if(prog.status === 'completed') { clearInterval(interval); document.getElementById('progStatus').innerHTML='✅ تمت الإضافة للمكتبة بنجاح!'; showToast("🎉 اكتمل التحميل!"); setTimeout(()=>location.reload(), 2000); }
                    else if(prog.status === 'error') { clearInterval(interval); document.getElementById('progStatus').innerText='❌ فشل التحميل!'; showToast("❌ حدث خطأ غير متوقع"); }
                }, 1500);
            }
        }
    </script>
</body>
</html>
"""

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
        <div class="flex justify-between items-center"><h1 class="text-3xl font-bold text-primary"><i class="fas fa-server"></i> الإدارة</h1><a href="/" class="bg-gray-700 px-4 py-2 rounded text-sm">رجوع للموقع</a></div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-blue-500 text-center"><p class="text-gray-400">مستخدمين</p><h2 class="text-3xl font-bold">{{ total_users }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-green-500 text-center"><p class="text-gray-400">نجاح</p><h2 class="text-3xl font-bold">{{ stats.success }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-red-500 text-center"><p class="text-gray-400">فشل</p><h2 class="text-3xl font-bold">{{ stats.failed }}</h2></div>
            <div class="bg-cardbg p-6 rounded-xl border-t-4 border-purple-500 text-center"><p class="text-gray-400">نشط (48h)</p><h2 class="text-3xl font-bold">{{ active }}</h2></div>
        </div>
    </div>
</body></html>
"""

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

    # الترقية المؤسسية: إضافة إعدادات تخطي الحماية واستخدام الكوكيز مثل البوت تماماً
    opts = {
        'outtmpl': str(WEB_DIR / f'{job_id}_%(title)s.%(ext)s'), 
        'quiet': True, 
        'progress_hooks': [hook],
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'tv'], 'player_skip': ['web', 'mweb']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    
    # تفعيل ملف الكوكيز لتخطي حظر يوتيوب إذا كان صالحاً
    if cookie_file_is_usable(COOKIES_FILE):
        opts['cookiefile'] = str(COOKIES_FILE)

    if mode == 'audio': opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]})
    else: opts.update({'format': f'bestvideo[height<={res}]+bestaudio/best', 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        PROGRESS_CACHE[job_id] = {"status": "completed"}
    except Exception as e: 
        print(f"Error downloading: {e}")
        PROGRESS_CACHE[job_id] = {"status": "error"}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting"}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(admin: str = Depends(check_admin)):
    return Template(ADMIN_HTML).render(stats=load_stats_sync(), total_users=len(all_user_ids()), active=len(get_active_users_48h()))
