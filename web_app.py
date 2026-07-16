import os, threading, uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Template
import yt_dlp

# استيراد إعدادات البوت وقاعدة البيانات
from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
from database.connection import init_db
from services.downloader import extract_metadata, search_youtube
from utils.helpers import cookie_file_is_usable

app = FastAPI(title="PlayZone Cloud")
init_db()

# مجلد المكتبة الدائم
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

# واجهة الموقع الحديثة والمطورة (UI/UX)
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>PlayZone | السحابة الذكية</title>
    <script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script>tailwind.config={darkMode:'class',theme:{extend:{colors:{primary:'#10b981',primaryDark:'#059669',bgDark:'#0f172a',cardDark:'#1e293b',bgLight:'#f8fafc',cardLight:'#ffffff'}}}}</script>
    <style>
        body { transition: background-color 0.3s, color 0.3s; }
        .glass-panel { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); }
        .light .glass-panel { background: rgba(255, 255, 255, 0.8); border: 1px solid rgba(0,0,0,0.05); }
        .modern-input { width: 100%; padding: 1rem 1.5rem; border-radius: 1rem; outline: none; transition: all 0.3s; }
        .dark .modern-input { background: #0f172a; border: 1px solid #334155; color: white; }
        .light .modern-input { background: #f1f5f9; border: 1px solid #e2e8f0; color: #0f172a; }
        .modern-input:focus { border-color: #10b981; box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.1); }
        .modern-btn { padding: 1rem 2rem; border-radius: 1rem; font-weight: bold; transition: all 0.3s; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; }
        .btn-primary { background: #10b981; color: white; box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3); }
        .btn-primary:hover { background: #059669; transform: translateY(-2px); }
        .btn-secondary { background: #3b82f6; color: white; box-shadow: 0 4px 14px rgba(59, 130, 246, 0.3); }
        .btn-secondary:hover { background: #2563eb; transform: translateY(-2px); }
        
        #toast { visibility: hidden; min-width: 250px; background: #10b981; color: white; text-align: center; border-radius: 99px; padding: 12px 24px; position: fixed; z-index: 50; left: 50%; bottom: 40px; font-size: 15px; font-weight: bold; transform: translateX(-50%) translateY(20px); transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55); opacity: 0; box-shadow: 0 10px 25px rgba(16,185,129,0.4); }
        #toast.show { visibility: visible; opacity: 1; transform: translateX(-50%) translateY(0); }
        .fade-in { animation: fadeIn 0.4s ease-in-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body class="bg-bgDark text-white dark:bg-bgDark dark:text-white light:bg-bgLight light:text-slate-900 min-h-screen pb-10">
    <div id="toast"></div>

    <!-- النافذة العلوية -->
    <header class="p-6 flex justify-between items-center max-w-5xl mx-auto">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-blue-500 flex items-center justify-center shadow-lg">
                <i class="fas fa-play text-white text-lg ml-1"></i>
            </div>
            <h1 class="text-2xl font-extrabold tracking-tight">Play<span class="text-primary">Zone</span></h1>
        </div>
        <button onclick="toggleTheme()" class="w-12 h-12 rounded-full glass-panel flex items-center justify-center hover:scale-110 transition-transform">
            <i id="themeIcon" class="fas fa-moon text-xl"></i>
        </button>
    </header>

    <main class="max-w-5xl mx-auto px-4 space-y-8">
        
        <!-- منطقة البحث الذكية -->
        <section class="glass-panel rounded-3xl p-6 md:p-10 text-center shadow-2xl relative overflow-hidden">
            <div class="absolute top-0 right-0 w-64 h-64 bg-primary rounded-full mix-blend-multiply filter blur-3xl opacity-10 animate-blob"></div>
            
            <h2 class="text-3xl md:text-4xl font-bold mb-3">ما الذي تود تحميله اليوم؟</h2>
            <p class="text-slate-400 dark:text-slate-400 light:text-slate-500 mb-8 font-medium">ضع رابط المقطع هنا، أو ابحث باسم الأغنية مباشرة 🎧</p>
            
            <div class="flex flex-col md:flex-row gap-4 max-w-3xl mx-auto relative z-10">
                <input type="text" id="url" placeholder="https://youtube.com/... أو اكتب كلمة للبحث" class="modern-input flex-1 text-lg">
                <button onclick="processInput()" id="mainBtn" class="modern-btn btn-secondary w-full md:w-auto text-lg px-8">
                    <i class="fas fa-search"></i> ابدأ الآن
                </button>
            </div>
        </section>

        <!-- منطقة العمليات الديناميكية (نتائج، معاينة، إعلان، تحميل) -->
        <section id="dynamicArea" class="max-w-3xl mx-auto"></section>

        <!-- مكتبة الوسائط -->
        <section class="pt-8">
            <div class="flex justify-between items-center mb-6 px-2">
                <h2 class="text-2xl font-bold flex items-center gap-2"><i class="fas fa-cloud text-primary"></i> ملفاتي المحفوظة</h2>
                <button onclick="location.reload()" class="text-sm font-bold text-slate-400 hover:text-primary transition-colors"><i class="fas fa-sync-alt ml-1"></i> تحديث القائمة</button>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {% for file in files %}
                <div class="glass-panel rounded-2xl p-5 hover:border-primary transition-all group shadow-lg flex flex-col h-full">
                    <div class="flex gap-3 mb-4 items-start">
                        <div class="w-12 h-12 rounded-full bg-slate-800 dark:bg-slate-800 light:bg-slate-200 flex items-center justify-center flex-shrink-0 text-primary">
                            <i class="fas {% if file.is_audio %}fa-music{% else %}fa-video{% endif %} text-xl"></i>
                        </div>
                        <p class="font-bold text-sm line-clamp-2 pt-1 flex-1" dir="ltr" title="{{ file.name }}">{{ file.name }}</p>
                    </div>
                    
                    <div class="mt-auto">
                        {% if file.is_audio %}
                        <audio controls class="w-full h-12 rounded-lg mb-4 opacity-90 hover:opacity-100 transition-opacity"><source src="{{ file.url }}" type="audio/mpeg"></audio>
                        {% else %}
                        <video controls class="w-full h-40 bg-black rounded-xl mb-4 object-cover"><source src="{{ file.url }}" type="video/mp4"></video>
                        {% endif %}
                        
                        <div class="flex gap-2">
                            <a href="{{ file.url }}" download class="modern-btn btn-primary flex-1 py-2 text-sm"><i class="fas fa-download"></i> حفظ</a>
                            <button onclick="shareLink('{{ file.url }}')" class="modern-btn bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 text-sm"><i class="fas fa-share-alt"></i></button>
                        </div>
                    </div>
                </div>
                {% endfor %}
                
                {% if not files %}
                <div class="col-span-full text-center py-16 glass-panel rounded-3xl">
                    <div class="w-24 h-24 bg-slate-800 dark:bg-slate-800 light:bg-slate-200 rounded-full flex items-center justify-center mx-auto mb-4 text-slate-400">
                        <i class="fas fa-box-open text-4xl"></i>
                    </div>
                    <h3 class="text-xl font-bold mb-2">المكتبة فارغة حالياً</h3>
                    <p class="text-slate-400">أي مقطع تقوم بتحميله سيتم حفظه هنا لتتمكن من الرجوع إليه لاحقاً.</p>
                </div>
                {% endif %}
            </div>
        </section>
    </main>
    
    <script>
        // نظام الوضع الليلي/النهاري الذكي
        const body = document.body;
        function toggleTheme() {
            body.classList.toggle('light'); document.documentElement.classList.toggle('light');
            const isLight = body.classList.contains('light');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            document.getElementById('themeIcon').className = isLight ? 'fas fa-sun text-yellow-500 text-xl' : 'fas fa-moon text-slate-300 text-xl';
        }
        if (localStorage.getItem('theme') === 'light') { toggleTheme(); }

        function showToast(msg) { const t = document.getElementById("toast"); t.innerText = msg; t.className = "show"; setTimeout(() => t.className = "", 3000); }
        function shareLink(url) { navigator.clipboard.writeText(window.location.origin + url).then(() => showToast("🔗 تم نسخ الرابط للحافظة")); }
        
        let currentUrl = "";
        const dynamicArea = document.getElementById('dynamicArea');

        async function processInput() {
            const input = document.getElementById('url').value.trim(); 
            if(!input) return showToast("⚠️ يرجى إدخال الرابط أولاً");
            
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            dynamicArea.innerHTML = ''; // تنظيف المنطقة
            
            if (input.startsWith('http')) {
                await renderPreview(input);
            } else {
                const res = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                const data = await res.json();
                if(data.success) {
                    let html = `<div class="glass-panel rounded-2xl p-4 fade-in"><h3 class="font-bold mb-4 text-primary"><i class="fas fa-list-ul ml-2"></i> نتائج البحث:</h3><div class="space-y-2">`;
                    data.entries.forEach(v => {
                        html += `<div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="p-3 bg-slate-800/50 hover:bg-slate-800 rounded-xl cursor-pointer flex justify-between items-center border border-transparent hover:border-primary transition-all">
                            <p class="font-bold text-sm truncate flex-1 ml-3">${v.title}</p>
                            <span class="text-xs bg-slate-700 px-3 py-1 rounded-full whitespace-nowrap">⏱ ${v.duration} ثانية</span>
                        </div>`;
                    });
                    html += `</div></div>`;
                    dynamicArea.innerHTML = html;
                } else showToast("❌ لا توجد نتائج");
            }
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-search"></i> ابدأ الآن';
        }

        async function renderPreview(url) {
            currentUrl = url;
            dynamicArea.innerHTML = `<div class="text-center py-10 fade-in"><i class="fas fa-circle-notch fa-spin text-4xl text-primary mb-4"></i><p>جاري جلب بيانات المقطع...</p></div>`;
            
            const res = await fetch('/api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
            const data = await res.json();
            
            if(data.success) {
                dynamicArea.innerHTML = `
                <div class="glass-panel rounded-3xl p-6 fade-in shadow-xl">
                    <div class="flex flex-col md:flex-row gap-6 items-center">
                        <img src="${data.thumb}" class="w-full md:w-48 rounded-2xl shadow-md object-cover aspect-video">
                        <div class="flex-1 w-full text-center md:text-right">
                            <h3 class="font-bold text-xl mb-2 line-clamp-2">${data.title}</h3>
                            <p class="text-slate-400 text-sm mb-6"><i class="far fa-clock ml-1"></i> المدة: ${data.duration || 'غير معروف'} ثانية</p>
                            
                            <div class="grid grid-cols-2 gap-3 mb-6">
                                <select id="mode" onchange="toggleRes()" class="modern-input py-3 text-sm">
                                    <option value="video">🎬 مقطع فيديو (مرئي)</option>
                                    <option value="audio">🎵 مقطع صوتي فقط (MP3)</option>
                                </select>
                                <select id="resolution" class="modern-input py-3 text-sm">
                                    <option value="480">دقة عادية</option>
                                    <option value="720" selected>دقة عالية (HD)</option>
                                    <option value="best">أفضل دقة</option>
                                </select>
                            </div>
                            <button onclick="requestDownload()" class="modern-btn btn-primary w-full text-lg"><i class="fas fa-magic"></i> تجهيز الملف</button>
                        </div>
                    </div>
                </div>`;
            } else {
                dynamicArea.innerHTML = ''; showToast("❌ الرابط غير مدعوم أو المقطع محذوف");
            }
        }

        function toggleRes() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }

        // نظام البوابة الإعلانية السلس (Ad-Gate)
        function requestDownload() {
            const adLink = "{{ ad_link }}";
            dynamicArea.innerHTML = `
            <div class="glass-panel rounded-3xl p-8 text-center fade-in border border-primary/50 bg-primary/5 shadow-2xl">
                <div class="w-16 h-16 bg-primary/20 text-primary rounded-full flex items-center justify-center mx-auto mb-4 text-2xl"><i class="fas fa-heart"></i></div>
                <h3 class="text-2xl font-bold mb-2">خطوة واحدة فقط!</h3>
                <p class="text-slate-400 mb-6 font-medium leading-relaxed">لضمان استمرار خدمتنا مجانية وسريعة، يرجى دعمنا عبر زيارة الراعي الإعلاني لمدة 10 ثوانٍ فقط وسيبدأ التحميل تلقائياً.</p>
                
                <div class="flex flex-col gap-3 max-w-sm mx-auto">
                    <a href="${adLink}" target="_blank" onclick="startTimer()" class="modern-btn bg-blue-600 hover:bg-blue-500 text-white w-full"><i class="fas fa-external-link-alt"></i> 1. زيارة الراعي الإعلاني</a>
                    <button id="verifyBtn" disabled class="modern-btn bg-slate-700 text-slate-400 w-full cursor-not-allowed border border-slate-600"><i class="fas fa-lock"></i> 2. التحقق من الزيارة</button>
                </div>
            </div>`;
        }

        function startTimer() {
            showToast("⏳ يرجى البقاء في صفحة الإعلان 10 ثوانٍ...");
            let btn = document.getElementById('verifyBtn');
            let timeLeft = 10;
            btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`;
            
            let timer = setInterval(() => {
                timeLeft--;
                if(timeLeft > 0) { btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`; }
                else {
                    clearInterval(timer);
                    btn.disabled = false;
                    btn.className = "modern-btn btn-primary w-full";
                    btn.innerHTML = "<i class="fas fa-check-circle"></i> بدء التحميل الفعلي";
                    btn.onclick = startActualDownload;
                    showToast("🔓 شكراً لك! يمكنك بدء التحميل الآن.");
                }
            }, 1000);
        }

        async function startActualDownload() {
            const mode = document.getElementById('mode') ? document.getElementById('mode').value : 'video';
            const resVal = document.getElementById('resolution') ? document.getElementById('resolution').value : '720';
            
            dynamicArea.innerHTML = `
            <div class="glass-panel rounded-3xl p-8 fade-in shadow-xl text-center">
                <h3 id="progStatus" class="text-xl font-bold mb-6 text-primary"><i class="fas fa-cloud-download-alt animate-bounce"></i> جاري سحب الملف...</h3>
                <div class="flex justify-between text-sm mb-2 font-bold px-1"><span id="progSpeed" class="text-slate-400">يتم الحساب</span><span id="progPercent" class="text-xl">0%</span></div>
                <div class="w-full bg-slate-800 rounded-full h-5 mb-3 overflow-hidden shadow-inner p-1">
                    <div id="progBar" class="bg-gradient-to-r from-primary to-blue-500 h-full rounded-full transition-all duration-300 relative" style="width: 0%">
                        <div class="absolute inset-0 bg-white/20 w-full h-full animate-pulse"></div>
                    </div>
                </div>
                <p id="progSize" class="text-slate-400 text-sm font-medium">0 MB / 0 MB</p>
            </div>`;

            const res = await fetch('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:currentUrl, mode:mode, resolution:resVal})});
            const data = await res.json();
            
            if(data.success) {
                const interval = setInterval(async ()=>{
                    const progRes = await fetch(`/api/progress/${data.job_id}`); const prog = await progRes.json();
                    
                    if(prog.status === 'downloading') {
                        document.getElementById('progPercent').innerText = prog.percent + '%';
                        document.getElementById('progBar').style.width = prog.percent + '%';
                        document.getElementById('progSize').innerText = prog.downloaded + ' من ' + prog.total;
                        document.getElementById('progSpeed').innerText = prog.speed;
                    } 
                    else if(prog.status === 'converting') {
                        document.getElementById('progStatus').innerHTML = '⚙️ جاري دمج الصوت والصورة (FFmpeg)...';
                        document.getElementById('progBar').style.width = '100%'; document.getElementById('progPercent').innerText = '100%';
                    } 
                    else if(prog.status === 'completed') {
                        clearInterval(interval);
                        document.getElementById('progStatus').innerHTML = '✅ تم الحفظ في مكتبتك بنجاح!';
                        showToast("🎉 اكتمل التحميل!");
                        setTimeout(() => location.reload(), 2000);
                    } 
                    else if(prog.status === 'error') {
                        clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">❌ تعذر التحميل</span>'; showToast("❌ حدث خطأ من المصدر");
                    }
                }, 1000);
            }
        }
    </script>
</body>
</html>
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
        return {"success": True, "title": info.get("title"), "thumb": info.get("thumbnail"), "duration": info.get("duration")}
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
    
    if cookie_file_is_usable(COOKIES_FILE):
        opts['cookiefile'] = str(COOKIES_FILE)

    if mode == 'audio': opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]})
    else: opts.update({'format': f'bestvideo[height<={res}]+bestaudio/best', 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        PROGRESS_CACHE[job_id] = {"status": "completed"}
    except Exception as e: 
        PROGRESS_CACHE[job_id] = {"status": "error"}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting"}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})
