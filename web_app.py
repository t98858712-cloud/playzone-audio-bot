import os, threading, uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp

# استيراد إعدادات البوت الأساسية لتخطي الحظر والسرعة
from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
from database.connection import init_db
from services.downloader import extract_metadata, search_youtube
from utils.helpers import cookie_file_is_usable

app = FastAPI(title="PlayZone Cloud")
init_db()

# مجلد المكتبة السحابية في السيرفر
WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")

PROGRESS_CACHE = {}
# استخدام الرابط المعتمد من الإعدادات
AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else ADSTERRA_LINK

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"

class SearchRequest(BaseModel):
    query: str

# ==========================================
# الواجهة الحديثة (مدمجة هنا لتفادي أخطاء السيرفر)
# تعتمد بالكامل على متصفح المستخدم (Local Storage)
# ==========================================
INDEX_HTML = f"""
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>PlayZone | السحابة الذكية</title>
    <script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <script>tailwind.config={{darkMode:'class',theme:{{extend:{{colors:{{primary:'#10b981',bgDark:'#0f172a',cardDark:'#1e293b'}}}}}}}}</script>
    <style>
        body {{ background-color: #0f172a; color: white; transition: background-color 0.3s; }}
        .glass-panel {{ background: #1e293b; border: 1px solid #334155; }}
        .modern-input {{ background: #0f172a; border: 1px solid #334155; color: white; width: 100%; padding: 1rem; border-radius: 1rem; outline: none; transition: all 0.3s; }}
        .modern-input:focus {{ border-color: #10b981; box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2); }}
        .modern-btn {{ padding: 1rem; border-radius: 1rem; font-weight: bold; transition: all 0.3s; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 0.5rem; }}
        #toast {{ visibility: hidden; min-width: 250px; background: #10b981; color: #000; text-align: center; border-radius: 99px; padding: 12px 24px; position: fixed; z-index: 50; left: 50%; bottom: 30px; font-weight: bold; transform: translateX(-50%) translateY(20px); transition: all 0.4s; opacity: 0; box-shadow: 0 10px 25px rgba(16,185,129,0.3); }}
        #toast.show {{ visibility: visible; opacity: 1; transform: translateX(-50%) translateY(0); }}
    </style>
</head>
<body class="font-sans p-4 pb-10">
    <div id="toast"></div>

    <div class="max-w-4xl mx-auto space-y-8 mt-4">
        <!-- قسم البحث والتحميل -->
        <section class="glass-panel rounded-3xl p-6 md:p-8 text-center shadow-xl">
            <h1 class="text-3xl font-extrabold mb-2">Play<span class="text-primary">Zone</span> 📥</h1>
            <p class="text-slate-400 mb-6 font-medium">أرسل رابط المقطع، أو ابحث عن الأغنية مباشرة 🎧</p>
            
            <div class="flex flex-col md:flex-row gap-3">
                <input type="text" id="url" placeholder="الرابط أو اسم المقطع..." class="modern-input">
                <button onclick="processInput()" id="mainBtn" class="modern-btn bg-blue-600 hover:bg-blue-500 text-white md:w-48"><i class="fas fa-search"></i> فحص / بحث</button>
            </div>
            
            <div id="searchResults" class="hidden mt-6 text-right space-y-2"></div>

            <div id="previewBox" class="hidden mt-6 p-4 bg-slate-800 rounded-2xl text-right flex flex-col md:flex-row gap-4 items-center border border-slate-700">
                <img id="thumb" class="w-full md:w-40 rounded-xl object-cover shadow-md">
                <div class="flex-1 w-full">
                    <h3 id="title" class="font-bold text-lg text-primary truncate mb-2"></h3>
                    
                    <div id="adGate" class="bg-primary/10 border border-primary/30 p-4 rounded-xl text-center mb-4">
                        <p class="text-sm mb-3">لدعمنا، يرجى تصفح الإعلان لثوانٍ لفك القفل ❤️</p>
                        <div class="flex flex-col sm:flex-row gap-2 justify-center">
                            <a href="{AD_LINK}" target="_blank" onclick="startAdTimer()" class="modern-btn bg-primary text-slate-900 py-2 px-4 text-sm"><i class="fas fa-eye"></i> زيارة الراعي</a>
                            <button id="verifyBtn" disabled class="modern-btn bg-slate-700 text-slate-500 py-2 px-4 text-sm cursor-not-allowed border border-slate-600"><i class="fas fa-lock"></i> مقفول</button>
                        </div>
                    </div>

                    <div id="dlOptions" class="hidden space-y-3">
                        <div class="grid grid-cols-2 gap-2">
                            <select id="mode" onchange="toggleRes()" class="modern-input py-2 px-3 text-sm"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                            <select id="resolution" class="modern-input py-2 px-3 text-sm"><option value="480">عادية 480p</option><option value="720" selected>عالية 720p</option><option value="best">أفضل جودة</option></select>
                        </div>
                        <button onclick="startDownload()" class="modern-btn bg-primary text-slate-900 w-full hover:bg-green-500"><i class="fas fa-cloud-download-alt"></i> ابدأ التحميل الآن</button>
                    </div>
                </div>
            </div>

            <!-- شريط التقدم -->
            <div id="progressBox" class="hidden mt-6 text-right bg-slate-800 p-5 rounded-2xl border border-slate-700">
                <div class="flex justify-between text-sm mb-2 font-bold"><span id="progStatus" class="text-primary">جاري التجهيز...</span><span id="progPercent">0%</span></div>
                <div class="w-full bg-slate-700 rounded-full h-3 mb-2 overflow-hidden"><div id="progBar" class="bg-gradient-to-r from-primary to-blue-500 h-full transition-all duration-300" style="width: 0%"></div></div>
                <div class="flex justify-between text-xs text-slate-400"><span id="progSize">0 MB</span><span id="progSpeed">0 MB/s</span></div>
            </div>
        </section>

        <!-- مكتبة الوسائط الشخصية (تعتمد على المتصفح) -->
        <section class="glass-panel rounded-3xl p-6 shadow-xl">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-xl font-bold flex items-center gap-2"><i class="fas fa-history text-primary"></i> سجل تحميلاتي</h2>
                <button onclick="renderLibrary()" class="text-sm font-bold text-slate-400 hover:text-white"><i class="fas fa-sync-alt"></i> تحديث</button>
            </div>
            
            <!-- حاوية الملفات المحفوظة -->
            <div id="libraryContainer" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>
    </div>
    
    <script>
        function showToast(msg) {{ const t = document.getElementById("toast"); t.innerText = msg; t.className = "show"; setTimeout(() => t.className = "", 3000); }}
        
        // ==========================================
        // نظام المكتبة السحابية الآمن (حفظ في المتصفح)
        // ==========================================
        let myLibrary = JSON.parse(localStorage.getItem('pz_library')) || [];

        function renderLibrary() {{
            const container = document.getElementById('libraryContainer');
            if(myLibrary.length === 0) {{
                container.innerHTML = '<div class="col-span-full text-center py-10 text-slate-500">لا توجد ملفات في سجلك. ابدأ التحميل لتظهر مقاطعك هنا!</div>';
                return;
            }}
            let html = '';
            // عرض الملفات الأحدث أولاً
            myLibrary.slice().reverse().forEach(file => {{
                const mediaTag = file.is_audio ?
                    `<audio controls class="w-full h-12 mt-3 outline-none rounded-lg"><source src="${{file.url}}" type="audio/mpeg"></audio>` :
                    `<video controls class="w-full h-40 mt-3 bg-black rounded-xl outline-none object-cover"><source src="${{file.url}}" type="video/mp4"></video>`;

                html += `
                <div class="bg-slate-800 p-4 rounded-2xl shadow-lg border border-slate-700">
                    <div class="flex justify-between items-start">
                        <div class="flex gap-3 items-center w-4/5">
                            <img src="${{file.thumb}}" class="w-12 h-12 rounded-lg object-cover">
                            <p class="font-bold text-sm truncate" dir="ltr" title="${{file.title}}">${{file.title}}</p>
                        </div>
                        <button onclick="removeFile('${{file.id}}')" class="text-red-500 p-2 hover:bg-slate-700 rounded-lg"><i class="fas fa-trash"></i></button>
                    </div>
                    ${{mediaTag}}
                    <button onclick="forceDownload('${{file.url}}', '${{file.title}}')" class="w-full mt-4 modern-btn bg-slate-700 hover:bg-slate-600 text-white py-2 text-sm"><i class="fas fa-download"></i> حفظ في جهازي مجدداً</button>
                </div>`;
            }});
            container.innerHTML = html;
        }}
        
        function removeFile(id) {{
            myLibrary = myLibrary.filter(f => f.id !== id);
            localStorage.setItem('pz_library', JSON.stringify(myLibrary));
            renderLibrary();
            showToast("🗑️ تم الحذف من السجل");
        }}

        // دالة التنزيل الإجباري لهاتف المستخدم
        function forceDownload(url, title) {{
            const a = document.createElement('a');
            a.href = url;
            a.download = title;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            showToast("📥 جاري الحفظ في هاتفك...");
        }}

        // تشغيل المكتبة فور فتح الموقع
        window.onload = renderLibrary;

        // ==========================================
        // عمليات البحث والتحميل
        // ==========================================
        let currentUrl = "", adWatched = false;

        async function processInput() {{
            const input = document.getElementById('url').value.trim(); 
            if(!input) return showToast("⚠️ أدخل الرابط أو الكلمة");
            
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            if (input.startsWith('http')) {{
                await renderPreview(input);
            }} else {{
                const res = await fetch('/api/search', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{query:input}})}});
                const data = await res.json();
                if(data.success) {{
                    let box = document.getElementById('searchResults');
                    box.innerHTML = '<h3 class="font-bold mb-3 text-primary">🔎 اختر המقطع:</h3><div class="space-y-2">';
                    data.entries.forEach(v => {{
                        box.innerHTML += `<div onclick="renderPreview('https://youtube.com/watch?v=${{v.id}}')" class="p-3 bg-slate-800 hover:bg-slate-700 rounded-xl cursor-pointer flex justify-between items-center border border-slate-700">
                            <p class="font-bold text-sm truncate flex-1 ml-3">${{v.title}}</p><span class="text-xs bg-slate-900 px-3 py-1 rounded-full">⏱ ${{v.duration}} ث</span>
                        </div>`;
                    }});
                    box.innerHTML += '</div>';
                    box.classList.remove('hidden');
                }} else showToast("❌ لا توجد نتائج");
            }}
            document.getElementById('mainBtn').innerHTML = '<i class="fas fa-search"></i> فحص / بحث';
        }}

        async function renderPreview(url) {{
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden');
            const res = await fetch('/api/preview', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{url:url}})}});
            const data = await res.json();
            
            if(data.success) {{
                document.getElementById('previewBox').classList.remove('hidden');
                document.getElementById('thumb').src = data.thumb;
                document.getElementById('title').innerText = data.title;
                
                document.getElementById('adGate').classList.remove('hidden');
                document.getElementById('dlOptions').classList.add('hidden');
                const vBtn = document.getElementById('verifyBtn');
                vBtn.disabled = true; vBtn.className = "modern-btn bg-slate-700 text-slate-500 py-2 px-4 text-sm cursor-not-allowed border border-slate-600";
                vBtn.innerHTML = '<i class="fas fa-lock"></i> مقفول'; adWatched = false;
            }} else showToast("❌ تعذر قراءة الرابط");
        }}

        function toggleRes() {{ document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }}

        function startAdTimer() {{
            showToast("⏳ يرجى البقاء في الإعلان 10 ثوانٍ");
            let btn = document.getElementById('verifyBtn');
            let timeLeft = 10;
            let timer = setInterval(() => {{
                timeLeft--;
                if(timeLeft > 0) {{ btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> تحقق (${{timeLeft}})...`; }}
                else {{
                    clearInterval(timer); btn.disabled = false;
                    btn.className = "modern-btn bg-blue-600 hover:bg-blue-500 text-white py-2 px-4 text-sm";
                    btn.innerHTML = "<i class='fas fa-unlock'></i> افتح القفل"; adWatched = true;
                }}
            }}, 1000);
        }}

        function unlockDownload() {{
            if(!adWatched) return showToast("⚠️ زر الراعي الإعلاني أولاً");
            document.getElementById('adGate').classList.add('hidden');
            document.getElementById('dlOptions').classList.remove('hidden');
        }}

        async function startDownload() {{
            document.getElementById('dlOptions').classList.add('hidden');
            document.getElementById('progressBox').classList.remove('hidden');
            
            const mode = document.getElementById('mode').value;
            const resVal = document.getElementById('resolution').value;

            const res = await fetch('/api/download', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{url:currentUrl, mode:mode, resolution:resVal}})}});
            const data = await res.json();
            
            if(data.success) {{
                const interval = setInterval(async ()=>{{
                    const progRes = await fetch(`/api/progress/${{data.job_id}}`); const prog = await progRes.json();
                    
                    if(prog.status === 'downloading') {{
                        document.getElementById('progPercent').innerText = prog.percent + '%';
                        document.getElementById('progBar').style.width = prog.percent + '%';
                        document.getElementById('progSize').innerText = prog.downloaded + ' / ' + prog.total;
                        document.getElementById('progSpeed').innerText = prog.speed;
                    }} 
                    else if(prog.status === 'converting') {{
                        document.getElementById('progStatus').innerHTML = '⚙️ جاري دمج المقطع...';
                        document.getElementById('progBar').style.width = '100%'; document.getElementById('progPercent').innerText = '100%';
                    }} 
                    else if(prog.status === 'completed') {{
                        clearInterval(interval);
                        document.getElementById('progStatus').innerHTML = '✅ اكتمل التحميل!';
                        
                        // 1. الإضافة لسجل المستخدم المحلي (المكتبة)
                        myLibrary.push({{ id: Date.now().toString(), title: prog.title, url: prog.url, thumb: prog.thumb, is_audio: prog.is_audio }});
                        localStorage.setItem('pz_library', JSON.stringify(myLibrary));
                        renderLibrary(); // تحديث المكتبة فوراً في الواجهة
                        
                        // 2. الحفظ التلقائي في جهاز المستخدم مباشرة
                        forceDownload(prog.url, prog.title);
                    }} 
                    else if(prog.status === 'error') {{ clearInterval(interval); document.getElementById('progStatus').innerText = '❌ تعذر التحميل'; showToast("❌ فشل سحب المقطع"); }}
                }}, 1500);
            }}
        }}
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=INDEX_HTML)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try: return {"success": True, "entries": search_youtube(req.query, limit=5).get("entries", [])}
    except Exception as e: return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        info = extract_metadata(req.url)
        return {"success": True, "title": info.get("title", "بدون اسم"), "thumb": info.get("thumbnail", "https://via.placeholder.com/150")}
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
        elif d['status'] == 'finished':
            prev = PROGRESS_CACHE.get(job_id, {})
            prev["status"] = "converting"
            PROGRESS_CACHE[job_id] = prev

    opts = {
        'outtmpl': str(WEB_DIR / f'{job_id}_%(title)s.%(ext)s'), 
        'quiet': True, 
        'progress_hooks': [hook],
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'tv'], 'player_skip': ['web', 'mweb']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    
    if cookie_file_is_usable(COOKIES_FILE):
        opts['cookiefile'] = str(COOKIES_FILE)

    if mode == 'audio': opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]})
    else: opts.update({'format': f'bestvideo[height<={res}]+bestaudio/best', 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if mode == 'audio': filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            PROGRESS_CACHE[job_id] = {
                "status": "completed",
                "url": f"/files/{Path(filename).name}",
                "title": info.get('title', 'مقطع PlayZone'),
                "thumb": info.get('thumbnail', 'https://via.placeholder.com/150'),
                "is_audio": mode == 'audio'
            }
    except Exception as e: 
        PROGRESS_CACHE[job_id] = {"status": "error", "error": str(e)}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting"}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})
