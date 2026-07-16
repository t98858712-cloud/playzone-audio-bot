import os, threading, uuid, time, requests
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

# --- إعدادات البوت والبيئة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot" # تم التثبيت يدوياً
# -------------------------------------------------------------

# --- إعدادات البيئة الافتراضية ---
try:
    from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
    from database.connection import init_db
    from utils.helpers import cookie_file_is_usable
except ImportError:
    BASE_DOWNLOAD_DIR = Path("./downloads")
    HILLTOPADS_LINK = "https://example.com/ad"
    ADSTERRA_LINK = None
    COOKIES_FILE = Path("cookies.txt")
    def init_db(): pass
    def cookie_file_is_usable(f): return False
# -------------------------------------------------------------

app = FastAPI(title="PlayZone Cloud Enterprise", description="منصة التحميل الذكية المربوطة بتيليجرام")
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = BASE_DOWNLOAD_DIR / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=WEB_DIR), name="files")

PROGRESS_CACHE = {}
AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "#")

# ==========================================
# نظام التنظيف الذاتي لحماية السيرفر من الامتلاء (يعمل 24/7)
# ==========================================
def cleanup_daemon():
    while True:
        try:
            now = time.time()
            # 1. مسح الملفات الأقدم من 24 ساعة (86400 ثانية)
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            # 2. تنظيف الذاكرة المؤقتة للتقدم
            expired_jobs = [jid for jid, data in PROGRESS_CACHE.items() if now - data.get("timestamp", now) > 86400]
            for jid in expired_jobs:
                del PROGRESS_CACHE[jid]
                
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600) # الفحص كل ساعة

# تشغيل منظف السيرفر في الخلفية عند بدء التشغيل
threading.Thread(target=cleanup_daemon, daemon=True).start()

# ==========================================

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"

class SearchRequest(BaseModel):
    query: str

class TelegramRequest(BaseModel):
    file_url: str
    chat_id: str
    is_audio: bool

def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "ar-SA,ar;q=0.9"}
    }
    if cookie_file_is_usable(COOKIES_FILE): opts["cookiefile"] = str(COOKIES_FILE)
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 5):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

INDEX_HTML = f"""
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlayZone | السحابة الذكية</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap" rel="stylesheet">
    <script>
        tailwind.config={{
            darkMode:'class',
            theme:{{
                extend:{{
                    fontFamily: {{ sans: ['Tajawal', 'sans-serif'] }},
                    colors:{{ primary:'#10b981', darkBg:'#090f1a', panelBg:'#111827', accent:'#3b82f6' }}
                }}
            }}
        }}
    </script>
    <style>
        body {{ background-color: #090f1a; color: #f8fafc; transition: all 0.3s ease; padding-bottom: 100px; }}
        .glass {{ background: rgba(17, 24, 39, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.05); }}
        .modern-input {{ background: #1e293b; border: 1px solid #334155; color: white; border-radius: 0.75rem; padding: 0.8rem 1rem; outline: none; transition: all 0.3s; width: 100%; }}
        .modern-input:focus {{ border-color: #10b981; box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2); }}
        .btn {{ padding: 0.8rem 1.5rem; border-radius: 0.75rem; font-weight: bold; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; cursor: pointer; }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .btn:active:not(:disabled) {{ transform: scale(0.97); }}
        ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
        ::-webkit-scrollbar-track {{ background: #0f172a; }}
        ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #10b981; }}
        
        #musicPlayer {{ position: fixed; bottom: 0; left: 0; right: 0; z-index: 100; transform: translateY(100%); transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1); border-top: 1px solid rgba(255,255,255,0.1); }}
        #musicPlayer.active {{ transform: translateY(0); }}
        .progress-container {{ width: 100%; height: 6px; background: #334155; border-radius: 4px; cursor: pointer; position: absolute; top: -3px; left: 0; }}
        .progress-bar {{ height: 100%; background: #10b981; width: 0%; border-radius: 4px; transition: width 0.1s linear; position: relative; }}
        
        #toast {{ position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-100%); opacity: 0; z-index: 1000; padding: 12px 24px; border-radius: 50px; font-weight: bold; color: white; transition: all 0.4s; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }}
        #toast.show {{ transform: translateX(-50%) translateY(0); opacity: 1; }}
        .toast-success {{ background: #10b981; }} .toast-error {{ background: #ef4444; }} .toast-info {{ background: #3b82f6; }} .toast-warning {{ background: #f59e0b; color: #fff; }}
    </style>
</head>
<body class="antialiased">
    <div id="toast"></div>

    <div id="tgModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-[200] hidden flex-col items-center justify-center p-4">
        <div class="bg-slate-800 border border-slate-700 p-6 rounded-3xl max-w-sm w-full text-center shadow-2xl transform transition-all scale-95 opacity-0" id="tgModalContent">
            <div class="w-16 h-16 bg-blue-500/20 text-blue-400 rounded-full flex items-center justify-center mx-auto mb-4 text-3xl shadow-[0_0_15px_rgba(59,130,246,0.5)]">
                <i class="fab fa-telegram-plane"></i>
            </div>
            <h3 class="text-xl font-bold mb-2 text-white">ربط حساب تيليجرام</h3>
            <p class="text-slate-400 text-sm mb-6">لإرسال الملفات مباشرة لهاتفك، نحتاج للـ ID الخاص بك لمرة واحدة فقط.</p>
            <button onclick="window.open('https://t.me/{BOT_USERNAME}', '_blank')" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full mb-4 shadow-lg"><i class="fas fa-robot"></i> 1. افتح البوت لنسخ الـ ID</button>
            <input type="text" id="tgIdInput" placeholder="2. الصق الـ ID هنا..." class="modern-input text-center text-lg tracking-widest mb-4 font-mono">
            <div class="flex gap-3">
                <button onclick="saveTgId()" class="btn bg-primary text-slate-900 flex-1 hover:bg-emerald-500 font-bold">تأكيد وربط</button>
                <button onclick="closeTgModal()" class="btn bg-slate-700 text-slate-300 flex-1 hover:text-white hover:bg-slate-600">إلغاء</button>
            </div>
        </div>
    </div>

    <div class="max-w-6xl mx-auto p-4 space-y-6">
        <header class="glass p-4 rounded-2xl flex justify-between items-center shadow-lg">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-primary/20 rounded-xl flex items-center justify-center text-primary text-xl"><i class="fas fa-bolt"></i></div>
                <h1 class="text-2xl font-black tracking-wide text-white">Play<span class="text-primary">Zone</span></h1>
            </div>
            <div class="flex gap-2">
                <a href="https://t.me/{BOT_USERNAME}" target="_blank" class="w-10 h-10 rounded-xl bg-blue-500/20 hover:bg-blue-500/40 text-blue-400 flex items-center justify-center transition-colors tooltip" title="التواصل مع البوت"><i class="fab fa-telegram-plane"></i></a>
            </div>
        </header>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div class="lg:col-span-1 space-y-6">
                <section class="glass rounded-3xl p-6 shadow-xl relative overflow-hidden">
                    <div class="absolute top-0 right-0 w-32 h-32 bg-primary/10 rounded-full blur-3xl"></div>
                    <h2 class="text-xl font-bold mb-4 flex items-center gap-2"><i class="fas fa-search text-primary"></i> بحث وتحميل</h2>
                    <div class="space-y-4 relative z-10">
                        <input type="text" id="url" placeholder="الرابط أو اسم المقطع..." class="modern-input">
                        <button onclick="processInput()" id="mainBtn" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full shadow-lg shadow-blue-600/30"><i class="fas fa-search"></i> إيجاد المقطع</button>
                    </div>
                    <div id="searchResults" class="hidden mt-6 space-y-2 max-h-60 overflow-y-auto pr-2"></div>
                </section>

                <section id="previewBox" class="hidden glass rounded-3xl p-6 shadow-xl relative transition-all">
                    <div class="flex flex-col items-center text-center">
                        <img id="thumb" class="w-full rounded-xl object-cover aspect-video mb-4 shadow-lg border border-slate-700">
                        <h3 id="title" class="font-bold text-lg text-white line-clamp-2 mb-4"></h3>
                    </div>

                    <div id="adGate" class="bg-emerald-900/20 border border-primary/30 p-4 rounded-xl text-center mb-4">
                        <p class="text-sm mb-3 text-slate-300">فضلاً، ادعم الخدمة بزيارة سريعة لفتح التحميل</p>
                        <a href="{AD_LINK}" target="_blank" onclick="startAdTimer()" class="btn bg-primary text-slate-900 w-full mb-2 hover:bg-emerald-400"><i class="fas fa-external-link-alt"></i> 1. زيارة الدعم</a>
                        <button id="verifyBtn" disabled class="btn bg-slate-800 text-slate-500 w-full cursor-not-allowed border border-slate-700"><i class="fas fa-lock"></i> 2. فك القفل</button>
                    </div>

                    <div id="dlOptions" class="hidden space-y-3">
                        <select id="mode" onchange="toggleRes()" class="modern-input"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                        <select id="resolution" class="modern-input"><option value="480">عادية 480p</option><option value="720" selected>عالية 720p</option><option value="best">أعلى جودة</option></select>
                        <button onclick="startDownload()" class="btn bg-primary text-slate-900 w-full hover:bg-emerald-500 shadow-lg shadow-primary/30"><i class="fas fa-cloud-download-alt"></i> بدء التحميل السحابي</button>
                    </div>

                    <div id="progressBox" class="hidden mt-4 text-center">
                        <div class="flex justify-between text-sm mb-2"><span id="progStatus" class="text-primary font-bold">جاري التجهيز...</span><span id="progPercent">0%</span></div>
                        <div class="w-full bg-slate-800 rounded-full h-3 mb-2 overflow-hidden"><div id="progBar" class="bg-primary h-full transition-all duration-300" style="width: 0%"></div></div>
                    </div>
                </section>
            </div>

            <div class="lg:col-span-2" id="librarySection">
                <section class="glass rounded-3xl p-6 md:p-8 shadow-xl min-h-[500px] flex flex-col">
                    <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6 border-b border-slate-700 pb-4">
                        <h2 class="text-2xl font-bold flex items-center gap-3"><i class="fas fa-photo-video text-primary"></i> مكتبتي السحابية</h2>
                        
                        <div class="flex flex-wrap gap-2 w-full md:w-auto">
                            <div class="relative flex-1 md:w-48">
                                <i class="fas fa-search absolute right-3 top-1/2 transform -translate-y-1/2 text-slate-500"></i>
                                <input type="text" id="libSearch" oninput="applyFilters()" placeholder="ابحث في ملفاتك..." class="modern-input pl-3 pr-9 py-2 text-sm bg-slate-800">
                            </div>
                            <select id="libFilter" onchange="applyFilters()" class="modern-input py-2 px-3 text-sm bg-slate-800 w-auto text-primary">
                                <option value="all">الكل</option><option value="favorites">❤️ المفضلة</option><option value="audio">🎵 صوتيات</option><option value="video">🎬 فيديوهات</option>
                            </select>
                        </div>
                    </div>

                    <div id="libraryContainer" class="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1 content-start"></div>
                    <div id="pagination" class="mt-8 flex justify-center items-center gap-3"></div>
                </section>
            </div>
        </div>
    </div>

    <div id="musicPlayer" class="glass pb-2 pt-3 px-4 shadow-[0_-10px_30px_rgba(0,0,0,0.5)]">
        <div class="progress-container" id="progressContainer" onclick="seekAudio(event)"><div class="progress-bar" id="audioProgressBar"></div></div>
        <div class="max-w-6xl mx-auto flex items-center justify-between gap-4 mt-2">
            <div class="flex items-center gap-3 w-1/3 overflow-hidden">
                <img id="playerThumb" src="https://via.placeholder.com/50" class="w-12 h-12 rounded-lg object-cover shadow">
                <div class="overflow-hidden"><p id="playerTitle" class="font-bold text-sm text-white truncate">لم يتم التحديد</p></div>
            </div>
            <div class="flex items-center justify-center gap-4 md:gap-6 w-1/3">
                <button onclick="playPrev()" class="text-slate-400 hover:text-white text-xl transition-colors"><i class="fas fa-step-backward"></i></button>
                <button onclick="togglePlay()" id="playPauseBtn" class="w-12 h-12 rounded-full bg-primary text-slate-900 hover:scale-105 flex items-center justify-center text-lg shadow-lg shadow-primary/30 transition-transform"><i class="fas fa-play ml-1"></i></button>
                <button onclick="playNext()" class="text-slate-400 hover:text-white text-xl transition-colors"><i class="fas fa-step-forward"></i></button>
            </div>
            <div class="flex items-center justify-end gap-3 w-1/3">
                <input type="range" id="volumeSlider" min="0" max="1" step="0.05" value="1" oninput="changeVolume()" class="w-20 accent-primary hidden md:block">
                <button onclick="closePlayer()" class="text-slate-500 hover:text-red-400 p-2 ml-2 transition-colors"><i class="fas fa-times"></i></button>
            </div>
        </div>
        <audio id="globalAudioElement" ontimeupdate="updatePlayerProgress()" onended="playNext()"></audio>
    </div>

    <script>
        document.addEventListener("DOMContentLoaded", () => {{
            const tgApp = window.Telegram?.WebApp;
            if (tgApp && tgApp.initDataUnsafe && tgApp.initDataUnsafe.user) {{
                localStorage.setItem('pz_tg_chat_id', tgApp.initDataUnsafe.user.id);
                tgApp.expand();
            }}
            applyFilters();
        }});

        let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
        let filteredLibrary = [];
        let currentPage = 1;
        const itemsPerPage = 8;
        
        const audioEl = document.getElementById('globalAudioElement');
        let currentPlaylist = [];
        let currentAudioIndex = -1;

        let pendingTgFileUrl = "";
        let pendingTgIsAudio = false;

        function showToast(msg, type = 'info') {{ 
            const t = document.getElementById("toast"); 
            t.innerHTML = msg; t.className = `show toast-${{type}}`; 
            setTimeout(() => t.className = "", type==='warning' ? 5000 : 3000); 
        }}

        function applyFilters() {{
            const query = document.getElementById('libSearch').value.toLowerCase();
            const filter = document.getElementById('libFilter').value;
            filteredLibrary = myLibrary.filter(file => {{
                const matchSearch = file.title.toLowerCase().includes(query);
                const matchType = filter === 'all' ? true : 
                                  (filter === 'audio' ? file.is_audio : 
                                  (filter === 'video' ? !file.is_audio : 
                                  (filter === 'favorites' ? file.favorite : true)));
                return matchSearch && matchType;
            }});
            filteredLibrary.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
            currentPage = 1; renderPage();
        }}

        function renderPage() {{
            const container = document.getElementById('libraryContainer');
            const paginator = document.getElementById('pagination');
            container.innerHTML = ''; paginator.innerHTML = '';

            if(filteredLibrary.length === 0) {{
                container.innerHTML = '<div class="col-span-full text-center py-16 text-slate-500"><i class="fas fa-box-open text-5xl mb-4 opacity-30 block"></i>لا توجد ملفات.</div>'; return;
            }}

            const totalPages = Math.ceil(filteredLibrary.length / itemsPerPage);
            const start = (currentPage - 1) * itemsPerPage;
            const pageItems = filteredLibrary.slice(start, start + itemsPerPage);
            currentPlaylist = filteredLibrary.filter(f => f.is_audio);

            pageItems.forEach(file => {{
                const isAudio = file.is_audio;
                const playBtn = isAudio 
                    ? `<button onclick="playGlobalAudio('${{file.id}}')" class="btn bg-primary text-slate-900 py-1.5 px-3 text-xs flex-1"><i class="fas fa-play"></i> تشغيل</button>`
                    : `<button onclick="window.open('${{file.url}}', '_blank')" class="btn bg-blue-500 text-white py-1.5 px-3 text-xs flex-1"><i class="fas fa-video"></i> مشاهدة</button>`;
                const favClass = file.favorite ? 'text-red-500' : 'text-slate-500 hover:text-red-400';

                container.innerHTML += `
                <div class="bg-slate-800/60 p-3 rounded-xl border border-slate-700 hover:border-primary/50 transition-all flex flex-col gap-3">
                    <div class="flex gap-3">
                        <div class="relative w-20 h-16 flex-shrink-0">
                            <img src="${{file.thumb}}" class="w-full h-full object-cover rounded-lg shadow">
                            <div class="absolute top-1 right-1 bg-black/70 rounded text-[10px] px-1">${{isAudio ? '🎵' : '🎬'}}</div>
                        </div>
                        <div class="flex-1 overflow-hidden pr-1 relative">
                            <p class="font-bold text-sm line-clamp-2 text-slate-200" title="${{file.title}}">${{file.title}}</p>
                            <button onclick="toggleFavorite('${{file.id}}')" class="absolute left-0 bottom-0 p-1 ${{favClass}} transition-colors text-lg"><i class="fas fa-heart"></i></button>
                        </div>
                    </div>
                    <div class="flex gap-2">
                        ${{playBtn}}
                        <button onclick="sendToTelegram('${{file.url}}', ${{isAudio}})" class="btn bg-blue-500/20 hover:bg-blue-500/50 text-blue-400 py-1.5 px-3 text-xs tooltip" title="إرسال لتيليجرام"><i class="fab fa-telegram-plane"></i></button>
                        <button onclick="forceDownload('${{file.url}}', '${{file.title}}')" class="btn bg-slate-700 hover:bg-slate-600 p-2 text-white"><i class="fas fa-download"></i></button>
                        <button onclick="removeFile('${{file.id}}')" class="btn bg-slate-700 hover:bg-red-500/80 p-2 text-slate-300 hover:text-white"><i class="fas fa-trash-alt"></i></button>
                    </div>
                </div>`;
            }});

            if (totalPages > 1) {{
                paginator.innerHTML += `<button onclick="currentPage--; renderPage()" ${{currentPage === 1 ? 'disabled' : ''}} class="w-8 h-8 rounded bg-slate-800 hover:bg-primary disabled:opacity-30"><i class="fas fa-chevron-right"></i></button>
                <span class="text-sm font-mono px-3">صفحة ${{currentPage}} من ${{totalPages}}</span>
                <button onclick="currentPage++; renderPage()" ${{currentPage === totalPages ? 'disabled' : ''}} class="w-8 h-8 rounded bg-slate-800 hover:bg-primary disabled:opacity-30"><i class="fas fa-chevron-left"></i></button>`;
            }}
        }}

        function toggleFavorite(id) {{
            const idx = myLibrary.findIndex(f => f.id === id);
            if(idx > -1) {{ myLibrary[idx].favorite = !myLibrary[idx].favorite; localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters(); }}
        }}

        function openTgModal(url, isAudio) {{
            pendingTgFileUrl = url; pendingTgIsAudio = isAudio;
            const modal = document.getElementById('tgModal');
            const content = document.getElementById('tgModalContent');
            modal.classList.remove('hidden'); modal.classList.add('flex');
            setTimeout(() => {{ content.classList.remove('scale-95', 'opacity-0'); content.classList.add('scale-100', 'opacity-100'); }}, 10);
        }}

        function closeTgModal() {{
            const modal = document.getElementById('tgModal');
            const content = document.getElementById('tgModalContent');
            content.classList.remove('scale-100', 'opacity-100'); content.classList.add('scale-95', 'opacity-0');
            setTimeout(() => {{ modal.classList.add('hidden'); modal.classList.remove('flex'); }}, 200);
        }}

        function saveTgId() {{
            const id = document.getElementById('tgIdInput').value.trim();
            if(!id) return showToast("يرجى إدخال الـ ID الخاص بك للربط", "error");
            localStorage.setItem('pz_tg_chat_id', id); closeTgModal(); sendToTelegram(pendingTgFileUrl, pendingTgIsAudio); 
        }}

        async function sendToTelegram(fileUrl, isAudio) {{
            let chatId = localStorage.getItem('pz_tg_chat_id');
            if (!chatId) {{ openTgModal(fileUrl, isAudio); return; }}

            showToast("جاري التجهيز والإرسال لتيليجرام 🚀...", "info");
            try {{
                const res = await fetch('/api/send_telegram', {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ file_url: fileUrl, chat_id: chatId, is_audio: isAudio }})
                }});
                const data = await res.json();
                
                if (data.success) {{
                    showToast("✅ تم إرسال الملف بنجاح إلى حسابك!", "success");
                }} else {{
                    const isLimitError = data.error && data.error.includes("50");
                    showToast("❌ " + (data.error || "فشل الإرسال"), isLimitError ? "warning" : "error");
                    if(data.error && data.error.includes("chat not found")) localStorage.removeItem('pz_tg_chat_id');
                }}
            }} catch(e) {{ showToast("❌ خطأ في الاتصال بالخادم", "error"); }}
        }}

        function removeFile(id) {{
            myLibrary = myLibrary.filter(f => f.id !== id);
            localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
            if(currentPlaylist[currentAudioIndex] && currentPlaylist[currentAudioIndex].id === id) closePlayer();
            applyFilters(); showToast("تم الحذف بنجاح", "success");
        }}

        function forceDownload(url, title) {{
            const a = document.createElement('a'); a.href = url; a.download = title;
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
        }}

        function playGlobalAudio(fileId) {{
            const index = currentPlaylist.findIndex(f => f.id === fileId);
            if(index === -1) return; currentAudioIndex = index; const file = currentPlaylist[index];
            document.getElementById('playerTitle').innerText = file.title;
            document.getElementById('playerThumb').src = file.thumb;
            audioEl.src = file.url; audioEl.play().catch(e => showToast("الملف لم يعد متاحاً على السيرفر (تم حذفه تلقائياً)", "warning"));
            document.getElementById('musicPlayer').classList.add('active'); updatePlayBtn(true);
        }}
        function togglePlay() {{ audioEl.paused ? (audioEl.play(), updatePlayBtn(true)) : (audioEl.pause(), updatePlayBtn(false)); }}
        function updatePlayBtn(isPlay) {{ document.querySelector('#playPauseBtn i').className = isPlay ? 'fas fa-pause' : 'fas fa-play ml-1'; }}
        function playNext() {{ if(currentPlaylist.length) playGlobalAudio(currentPlaylist[(currentAudioIndex + 1) % currentPlaylist.length].id); }}
        function playPrev() {{ if(currentPlaylist.length) playGlobalAudio(currentPlaylist[(currentAudioIndex - 1 + currentPlaylist.length) % currentPlaylist.length].id); }}
        function closePlayer() {{ audioEl.pause(); document.getElementById('musicPlayer').classList.remove('active'); }}
        function updatePlayerProgress() {{
            if(!audioEl.duration) return;
            document.getElementById('audioProgressBar').style.width = ((audioEl.currentTime / audioEl.duration) * 100) + '%';
        }}
        function seekAudio(e) {{
            const rect = document.getElementById('progressContainer').getBoundingClientRect();
            let percent = (e.clientX - rect.left) / rect.width;
            if(document.dir === 'rtl') percent = 1 - percent; 
            audioEl.currentTime = percent * audioEl.duration;
        }}
        function changeVolume() {{ audioEl.volume = document.getElementById('volumeSlider').value; }}

        let currentUrl = "", adWatched = false;
        async function processInput() {{
            const input = document.getElementById('url').value.trim(); 
            if(!input) return showToast("يرجى إدخال الرابط", "error");
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; btn.disabled = true;
            
            if (input.startsWith('http')) await renderPreview(input);
            else {{
                try {{
                    const res = await fetch('/api/search', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{query:input}})}});
                    if(!res.ok) throw new Error();
                    const data = await res.json();
                    if(data.success && data.entries.length) {{
                        let box = document.getElementById('searchResults'); box.innerHTML = '';
                        data.entries.forEach(v => {{
                            if(v.id) box.innerHTML += `<div onclick="renderPreview('https://youtube.com/watch?v=${{v.id}}')" class="p-2 bg-slate-800/50 hover:bg-slate-700 rounded-xl cursor-pointer flex gap-3 items-center border border-transparent hover:border-primary/30">
                                <img src="${{v.thumbnails && v.thumbnails.length ? v.thumbnails[0].url : 'https://via.placeholder.com/150'}}" class="w-16 h-10 rounded object-cover shadow aspect-video">
                                <p class="font-bold text-sm text-slate-200 line-clamp-2">${{v.title}}</p></div>`;
                        }});
                        box.classList.remove('hidden');
                    }} else showToast("لم يتم العثور على نتائج", "error");
                }} catch(e) {{ showToast("خطأ في الاتصال بالخادم", "error"); }}
            }}
            btn.innerHTML = '<i class="fas fa-search"></i> إيجاد المقطع'; btn.disabled = false;
        }}

        async function renderPreview(url) {{
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden');
            document.getElementById('previewBox').classList.add('hidden'); document.getElementById('progressBox').classList.add('hidden');
            try {{
                const res = await fetch('/api/preview', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{url:url}})}});
                if(!res.ok) throw new Error();
                const data = await res.json();
                if(data.success) {{
                    document.getElementById('previewBox').classList.remove('hidden');
                    document.getElementById('thumb').src = data.thumb;
                    document.getElementById('title').innerText = data.title;
                    document.getElementById('adGate').classList.remove('hidden');
                    document.getElementById('dlOptions').classList.add('hidden');
                    let vBtn = document.getElementById('verifyBtn');
                    vBtn.disabled = true; vBtn.onclick = null;
                    vBtn.className = "btn bg-slate-800 text-slate-500 w-full cursor-not-allowed border border-slate-700";
                    vBtn.innerHTML = '<i class="fas fa-lock"></i> 2. فك القفل'; adWatched = false;
                }} else showToast("تعذر قراءة المقطع (قد يكون محمي)", "error");
            }} catch(e) {{ showToast("حدث خطأ بالاتصال", "error"); }}
        }}

        function toggleRes() {{ document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }}

        function startAdTimer() {{
            if(adWatched) return;
            let btn = document.getElementById('verifyBtn'); let timeLeft = 5;
            let timer = setInterval(() => {{
                timeLeft--;
                if(timeLeft > 0) btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> فحص (${{timeLeft}})...`; 
                else {{
                    clearInterval(timer); btn.disabled = false; btn.onclick = unlockDownload; 
                    btn.className = "btn bg-blue-600 hover:bg-blue-500 text-white w-full animate-pulse";
                    btn.innerHTML = "<i class='fas fa-unlock-alt'></i> 2. افتح التحميل"; adWatched = true;
                }}
            }}, 1000);
        }}

        function unlockDownload() {{
            if(!adWatched) return;
            document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden');
        }}

        async function startDownload() {{
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            const mode = document.getElementById('mode').value, resVal = document.getElementById('resolution').value;
            try {{
                const res = await fetch('/api/download', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{url:currentUrl, mode:mode, resolution:resVal}})}});
                if(!res.ok) throw new Error();
                const data = await res.json();
                if(data.success) {{
                    const interval = setInterval(async ()=>{{
                        try {{
                            const progRes = await fetch(`/api/progress/${{data.job_id}}`); const prog = await progRes.json();
                            if(prog.status === 'downloading') {{
                                document.getElementById('progPercent').innerText = prog.percent + '%';
                                document.getElementById('progBar').style.width = prog.percent + '%';
                            }} 
                            else if(prog.status === 'converting') {{ document.getElementById('progStatus').innerText = 'جاري دمج وضغط الملفات...'; document.getElementById('progBar').style.width = '100%'; }} 
                            else if(prog.status === 'completed') {{
                                clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-primary">اكتمل التحميل بنجاح!</span>';
                                myLibrary.unshift({{ id: Date.now().toString(), title: prog.title, url: prog.url, thumb: prog.thumb, is_audio: prog.is_audio, timestamp: Date.now(), favorite: false }});
                                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                                applyFilters(); showToast("تم الحفظ في مكتبتك السحابية", "success");
                            }} 
                            else if(prog.status === 'error') {{ clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">حدث خطأ داخلي!</span>'; }}
                        }} catch(err) {{}}
                    }}, 1500);
                }}
            }} catch(e) {{ showToast("تأكد من اتصالك بالإنترنت", "error"); }}
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
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"success": True, "title": info.get("title", "بدون عنوان"), "thumb": info.get("thumbnail", "https://via.placeholder.com/150")}
    except Exception as e: return {"success": False, "error": str(e)}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            # إضافة Timestamp هنا لتشغيل التنظيف التلقائي
            PROGRESS_CACHE[job_id] = {
                "status": "downloading", 
                "percent": round((d.get('downloaded_bytes',0)/total)*100, 1),
                "timestamp": time.time() 
            }
        elif d['status'] == 'finished': 
            PROGRESS_CACHE[job_id] = {"status": "converting", "timestamp": time.time()}

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook)
    if mode == 'audio': opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    else: opts.update({'format': f'bestvideo[height<={res}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'merge_output_format': 'mp4'})
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url, download=True)
            filename = f"{job_id}.mp3" if mode == 'audio' else f"{job_id}.mp4"
            PROGRESS_CACHE[job_id] = {"status": "completed", "url": f"/files/{filename}", "title": info.get('title', 'مقطع PlayZone'), "thumb": info.get('thumbnail', ''), "is_audio": mode == 'audio', "timestamp": time.time()}
    except Exception as e: PROGRESS_CACHE[job_id] = {"status": "error", "error": str(e), "timestamp": time.time()}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting", "timestamp": time.time()}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.post("/api/send_telegram")
async def send_to_telegram(req: TelegramRequest):
    try:
        filename = req.file_url.split("/")[-1]
        file_path = WEB_DIR / filename
        
        if not file_path.exists():
            return {"success": False, "error": "عذراً، يبدو أن الملف قد تم مسحه من السيرفر. يرجى تحميله مجدداً."}

        if not TELEGRAM_TOKEN:
            return {"success": False, "error": "عذراً، خدمة البوت غير مفعلة حالياً في السيرفر."}

        # --- حماية قيود تيليجرام (أهم فحص لمنع الانهيار) ---
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 49.5:
            return {"success": False, "error": f"حجم الملف ({file_size_mb:.1f}MB) يتجاوز الحد المسموح في تيليجرام (50MB). يرجى الحفظ في جهازك بدلاً من ذلك."}
        # ----------------------------------------------------

        api_method = "sendAudio" if req.is_audio else "sendVideo"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        
        with open(file_path, 'rb') as f:
            files = {'audio' if req.is_audio else 'video': f}
            data = {'chat_id': req.chat_id, 'caption': "تم التحميل والمشاركة عبر PlayZone Cloud ⚡️"}
            response = requests.post(url, data=data, files=files)
            
        res_data = response.json()
        
        if response.status_code == 200 and res_data.get("ok"):
            return {"success": True}
        else:
            return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة مع البوت أولاً.")}
            
    except Exception as e:
        return {"success": False, "error": str(e)}
