import os, threading, uuid, time, requests, json
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

# --- إعدادات البوت والبيئة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot" # اليوزر الثابت اليدوي للبوت الخاص بك
# -------------------------------------------------------------

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

app = FastAPI(title="PlayZone Cloud Dashboard")
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

# نظام التنظيف الذاتي لحماية مساحة السيرفر
def cleanup_daemon():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            expired_jobs = [jid for jid, data in PROGRESS_CACHE.items() if now - data.get("timestamp", now) > 86400]
            for jid in expired_jobs:
                del PROGRESS_CACHE[jid]
        except Exception as e:
            pass
        time.sleep(3600)

threading.Thread(target=cleanup_daemon, daemon=True).start()

class URLRequest(BaseModel):
    url: str; mode: str = "video"; resolution: str = "720"

class SearchRequest(BaseModel):
    query: str

class TelegramRequest(BaseModel):
    file_url: str
    chat_id: str
    is_audio: bool
    title: str = "مقطع"
    performer: str = "PlayZone"
    duration: int = 0
    thumb: str = ""

def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "ar-SA,ar;q=0.9"}
    }
    if cookie_file_is_usable(COOKIES_FILE): opts["cookiefile"] = str(COOKIES_FILE)
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 25):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

# ==========================================
# الواجهة المتجاوبة والمصححة كلياً للهواتف
# ==========================================
INDEX_HTML = f"""
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>PlayZone | السحابة الشخصية</title>
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
                    colors:{{ 
                        accent:'#8b5cf6', accentHover:'#7c3aed', 
                        bgDark:'#09090b', panel:'#18181b', panelBorder:'#27272a',
                        textMuted:'#a1a1aa', tgBlue: '#3b82f6'
                    }}
                }}
            }}
        }}
    </script>
    <style>
        body {{ background-color: #09090b; color: #f4f4f5; transition: all 0.3s ease; overflow: hidden; }}
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: #3f3f46; border-radius: 4px; }}
        
        .modern-input {{ background: #09090b; border: 1px solid #27272a; color: white; border-radius: 0.75rem; padding: 0.8rem 1rem; outline: none; transition: border-color 0.3s, box-shadow 0.3s; width: 100%; }}
        .modern-input:focus {{ border-color: #8b5cf6; box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.15); }}
        
        .btn {{ padding: 0.8rem 1.5rem; border-radius: 0.75rem; font-weight: bold; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; cursor: pointer; user-select: none; position: relative; overflow: hidden; }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; transform: none !important; }}
        .btn:active:not(:disabled) {{ transform: scale(0.96); }}
        
        @keyframes shimmer {{ 0% {{ transform: translateX(100%); }} 100% {{ transform: translateX(-100%); }} }}
        .shimmer-bg {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent); animation: shimmer 1.5s infinite linear; }}

        .view-section {{ display: none; animation: fadeIn 0.3s ease-out; }}
        .view-section.active {{ display: block; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        
        #musicPlayer {{ position: fixed; bottom: 0; left: 0; right: 0; z-index: 50; transform: translateY(100%); transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1); border-top: 1px solid #27272a; background: #18181b; }}
        #musicPlayer.active {{ transform: translateY(0); }}
        .progress-container {{ width: 100%; height: 4px; background: #27272a; cursor: pointer; position: absolute; top: -2px; left: 0; transition: height 0.2s; }}
        .progress-container:hover {{ height: 8px; top: -4px; }}
        .progress-bar {{ height: 100%; background: #8b5cf6; width: 0%; position: relative; transition: width 0.3s ease-out; }}
        
        #toast {{ position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-100%); opacity: 0; z-index: 1000; padding: 12px 24px; border-radius: 50px; font-weight: bold; color: white; transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55); box-shadow: 0 10px 25px rgba(0,0,0,0.5); pointer-events: none; }}
        #toast.show {{ transform: translateX(-50%) translateY(0); opacity: 1; }}
    </style>
</head>
<body class="antialiased flex h-screen w-full">
    <div id="toast"></div>

    <aside class="w-20 md:w-64 bg-panel border-l border-panelBorder flex flex-col justify-between h-full z-40">
        <div>
            <div class="h-20 flex items-center justify-center md:justify-start md:px-6 border-b border-panelBorder">
                <div class="w-10 h-10 bg-accent/20 rounded-xl flex items-center justify-center text-accent text-xl flex-shrink-0">
                    <i class="fas fa-play"></i>
                </div>
                <h1 class="text-xl font-black text-white mr-3 hidden md:block">Play<span class="text-accent">Zone</span></h1>
            </div>

            <nav class="mt-6 px-3 space-y-2">
                <button onclick="switchView('searchView')" id="nav-searchView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 px-4 py-3 rounded-xl bg-panelBorder text-accent font-bold">
                    <i class="fas fa-search text-xl"></i><span class="hidden md:block">البحث والتحميل</span>
                </button>
                <button onclick="switchView('libraryView')" id="nav-libraryView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 px-4 py-3 rounded-xl text-textMuted hover:bg-panelBorder hover:text-white bg-transparent">
                    <i class="fas fa-folder text-xl"></i><span class="hidden md:block">ملفاتي المحفوظة</span>
                </button>
                <button onclick="switchView('settingsView')" id="nav-settingsView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 px-4 py-3 rounded-xl text-textMuted hover:bg-panelBorder hover:text-white bg-transparent">
                    <i class="fas fa-cog text-xl"></i><span class="hidden md:block">الإعدادات</span>
                </button>
            </nav>
        </div>
        <div class="p-4 border-t border-panelBorder">
            <a href="https://t.me/{BOT_USERNAME}" target="_blank" class="btn w-full flex items-center justify-center md:justify-start gap-3 px-4 py-3 rounded-xl bg-tgBlue/10 text-tgBlue hover:bg-tgBlue/20">
                <i class="fab fa-telegram-plane text-xl"></i><span class="hidden md:block font-bold text-sm" dir="ltr">@{BOT_USERNAME}</span>
            </a>
        </div>
    </aside>

    <main class="flex-1 h-full overflow-y-auto pb-28 relative scroll-smooth">
        
        <!-- قسم البحث والنتائج -->
        <section id="searchView" class="view-section active p-4 md:p-8 max-w-4xl mx-auto">
            <div class="bg-panel rounded-3xl p-6 md:p-8 border border-panelBorder mb-6 relative overflow-hidden">
                <div class="absolute top-0 left-0 w-32 h-32 bg-accent/5 rounded-full blur-3xl"></div>
                <h2 class="text-2xl font-bold mb-2 text-white relative z-10">البحث السريع ⚡</h2>
                <p class="text-textMuted mb-6 text-sm relative z-10">الصق رابط المقطع هنا أو ابحث باسمه مباشرة.</p>
                
                <div class="flex flex-col md:flex-row gap-4 relative z-10">
                    <input type="text" id="url" placeholder="الرابط أو الكلمة البحثية..." class="modern-input flex-1">
                    <button onclick="processInput()" id="mainBtn" class="btn bg-accent hover:bg-accentHover text-white md:w-32 shadow-lg shadow-accent/20"><i class="fas fa-search"></i> بحث</button>
                </div>
                
                <!-- حاوية نتائج البحث المُعاد برمجتها (إظهار 5 خيارات تحميل عمودية بالكامل) -->
                <div id="searchResults" class="hidden mt-8 bg-[#18181b] border border-panelBorder rounded-3xl p-4 md:p-5 shadow-xl">
                    <div class="mb-4 pb-3 border-b border-panelBorder flex justify-between items-center">
                        <h3 class="text-white font-bold text-base md:text-lg flex items-center gap-2">🎬 اختر المقطع المطلوب:</h3>
                        <button onclick="document.getElementById('searchResults').classList.add('hidden')" class="text-textMuted hover:text-red-400 p-2 bg-bgDark rounded-full transition-colors flex-shrink-0"><i class="fas fa-times"></i></button>
                    </div>
                    <!-- قائمة الـ 5 مقاطع المحمية من الانضغاط وبدون أرقام -->
                    <div id="searchResultsList" class="flex flex-col gap-3 w-full"></div>
                </div>
            </div>

            <!-- بطاقة المعاينة والتحكم بعد اختيار مقطع -->
            <div id="previewBox" class="hidden bg-panel rounded-3xl p-6 border border-panelBorder">
                <div class="flex flex-col md:flex-row gap-6 items-center">
                    <div class="w-full md:w-1/3">
                        <img id="thumb" class="w-full rounded-xl object-cover aspect-video shadow-md border border-panelBorder">
                    </div>
                    <div class="w-full md:w-2/3 space-y-4">
                        <h3 id="title" class="font-bold text-lg text-white line-clamp-2"></h3>
                        
                        <div id="adGate" class="bg-bgDark border border-panelBorder p-4 rounded-xl text-center">
                            <p class="text-sm mb-3 text-textMuted font-medium"><i class="fas fa-info-circle text-accent ml-1"></i> للتحميل، يجب مشاهدة الإعلان لعدة ثوانٍ ثم الرجوع هنا للتحقق.</p>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <a href="{AD_LINK}" target="_blank" onclick="startAdTimer()" class="btn bg-tgBlue text-white flex-1 hover:bg-blue-500"><i class="fas fa-eye"></i> 1. مشاهدة الإعلان</a>
                                <button id="verifyBtn" disabled class="btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder"><i class="fas fa-lock"></i> 2. التحقق للتحميل</button>
                            </div>
                        </div>

                        <div id="dlOptions" class="hidden space-y-4">
                            <div class="grid grid-cols-2 gap-3">
                                <select id="mode" onchange="toggleRes()" class="modern-input bg-bgDark py-2 text-sm"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                                <select id="resolution" class="modern-input bg-bgDark py-2 text-sm"><option value="480">جودة 480p</option><option value="720" selected>جودة 720p</option></select>
                            </div>
                            <button onclick="startDownload()" class="btn bg-accent text-white w-full hover:bg-accentHover shadow-lg shadow-accent/30"><i class="fas fa-download"></i> بدء التحميل الآن</button>
                        </div>

                        <div id="progressBox" class="hidden bg-bgDark p-5 rounded-xl border border-panelBorder">
                            <div class="flex justify-between items-center mb-3">
                                <span id="progStatus" class="text-accent font-bold text-sm flex items-center gap-2"><i class="fas fa-circle-notch fa-spin"></i> جاري التجهيز...</span>
                                <span id="progPercent" class="font-mono font-bold text-white text-lg">0%</span>
                            </div>
                            <div class="w-full bg-panel rounded-full h-3 mb-2 overflow-hidden relative">
                                <div id="progBar" class="bg-gradient-to-r from-accentHover to-accent h-full relative" style="width: 0%">
                                    <div class="shimmer-bg"></div>
                                </div>
                            </div>
                            <div class="flex justify-between text-xs text-textMuted font-mono mt-1">
                                <span id="progSize">-- MB / -- MB</span>
                                <span id="progSpeed">-- MB/s</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- الأقسام الأخرى المحفوظة -->
        <section id="libraryView" class="view-section p-4 md:p-8 max-w-6xl mx-auto h-full flex flex-col">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
                <h2 class="text-2xl font-bold text-white">ملفاتي المحفوظة</h2>
                <div class="flex flex-wrap gap-2 w-full md:w-auto">
                    <div class="relative flex-1 md:w-56">
                        <i class="fas fa-search absolute right-3 top-1/2 transform -translate-y-1/2 text-textMuted"></i>
                        <input type="text" id="libSearch" oninput="applyFilters()" placeholder="بحث سريع..." class="modern-input pl-3 pr-10 py-2 bg-panel text-sm">
                    </div>
                    <select id="libFilter" onchange="applyFilters()" class="modern-input py-2 px-3 w-auto bg-panel text-accent font-bold text-sm">
                        <option value="all">الكل</option><option value="favorites">❤️ المفضلة</option><option value="audio">🎵 صوتيات</option><option value="video">🎬 فيديوهات</option>
                    </select>
                </div>
            </div>
            <div id="libraryContainer" class="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 content-start"></div>
            <div id="pagination" class="mt-8 flex justify-center items-center gap-3 pb-8"></div>
        </section>

        <section id="settingsView" class="view-section p-4 md:p-8 max-w-3xl mx-auto">
            <h2 class="text-2xl font-bold mb-6 text-white">الإعدادات</h2>
            <div class="space-y-6">
                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-sm">
                    <h3 class="text-lg font-bold text-white mb-3 flex items-center gap-2"><i class="fab fa-telegram text-tgBlue"></i> حساب تيليجرام</h3>
                    <p class="text-textMuted text-sm mb-4">اربط حسابك لاستلام الملفات عبر البوت.</p>
                    <div class="flex gap-3 mb-5">
                        <input type="text" id="settingTgId" placeholder="الآي دي (ID)" class="modern-input font-mono bg-bgDark">
                        <button onclick="updateTgId()" class="btn bg-tgBlue hover:bg-blue-500 text-white px-6">حفظ</button>
                    </div>
                    <div class="flex items-center justify-between p-4 bg-bgDark rounded-xl border border-panelBorder">
                        <div>
                            <p class="font-bold text-white text-sm">إرسال تلقائي (أتمتة)</p>
                            <p class="text-xs text-textMuted mt-1">إرسال الملف للبوت فور انتهاء تحميله.</p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="autoForwardToggle" onchange="toggleAutoForward()" class="sr-only peer" checked>
                            <div class="w-11 h-6 bg-panelBorder rounded-full peer peer-checked:after:-translate-x-full peer-checked:bg-accent after:content-[''] after:absolute after:top-[2px] after:right-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all"></div>
                        </label>
                    </div>
                </div>

                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-sm">
                    <h3 class="text-lg font-bold text-white mb-3 flex items-center gap-2"><i class="fas fa-database text-red-400"></i> إدارة البيانات</h3>
                    <div class="flex justify-between items-center p-4 bg-bgDark rounded-xl border border-panelBorder">
                        <div><p class="font-bold text-white text-sm" id="libCountStatus">السجل (0)</p></div>
                        <button onclick="clearAllLibrary()" class="btn bg-red-500/10 text-red-500 hover:bg-red-500/20 text-sm px-4">مسح السجل بالكامل</button>
                    </div>
                </div>
            </div>
        </section>
    </main>

    <!-- مشغل الموسيقى السفلي -->
    <div id="musicPlayer" class="pb-safe">
        <div class="progress-container" id="progressContainer" onclick="seekAudio(event)"><div class="progress-bar" id="audioProgressBar"></div></div>
        <div class="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4 p-3 md:px-6">
            <div class="flex items-center gap-3 w-full md:w-1/3 overflow-hidden">
                <div class="w-12 h-12 rounded-full bg-accent/20 flex items-center justify-center text-accent text-xl flex-shrink-0 border border-accent/30" id="playerThumbPlaceholder"><i class="fas fa-music"></i></div>
                <div class="overflow-hidden">
                    <p id="playerTitle" class="font-bold text-sm text-white truncate">جاهز للتشغيل</p>
                    <p id="playerTime" class="text-xs text-textMuted font-mono mt-0.5">0:00 / 0:00</p>
                </div>
            </div>
            <div class="flex items-center justify-center gap-6 w-full md:w-1/3">
                <button onclick="toggleShuffle()" id="shuffleBtn" class="text-textMuted hover:text-white transition-colors text-lg active:scale-90"><i class="fas fa-random"></i></button>
                <button onclick="playPrev()" class="text-white hover:text-accent transition-colors text-xl active:scale-90"><i class="fas fa-step-backward"></i></button>
                <button onclick="togglePlay()" id="playPauseBtn" class="w-12 h-12 rounded-full bg-accent text-white flex items-center justify-center text-lg active:scale-90 transition-transform"><i class="fas fa-play ml-1"></i></button>
                <button onclick="playNext()" class="text-white hover:text-accent transition-colors text-xl active:scale-90"><i class="fas fa-step-forward"></i></button>
                <button onclick="toggleRepeat()" id="repeatBtn" class="text-textMuted hover:text-white transition-colors relative text-lg active:scale-90"><i class="fas fa-redo"></i></button>
            </div>
            <div class="flex items-center justify-end gap-4 w-full md:w-1/3 hidden md:flex">
                <button onclick="changeSpeed()" id="speedBtn" class="btn px-2 py-1 bg-transparent border border-panelBorder text-xs font-mono text-textMuted">1x</button>
                <i class="fas fa-volume-up text-textMuted text-xs"></i>
                <input type="range" id="volumeSlider" min="0" max="1" step="0.05" value="1" oninput="changeVolume()" class="w-20 accent-accent">
                <button onclick="closePlayer()" class="text-textMuted hover:text-red-400 p-2 ml-2 active:scale-90"><i class="fas fa-times text-lg"></i></button>
            </div>
        </div>
        <audio id="globalAudioElement" ontimeupdate="updatePlayerProgress()" onended="handleAudioEnd()"></audio>
    </div>

    <div id="tgModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-[200] hidden flex-col items-center justify-center p-4">
        <div class="bg-panel border border-panelBorder p-6 rounded-3xl max-w-sm w-full text-center shadow-2xl" id="tgModalContent">
            <div class="w-14 h-14 bg-tgBlue/20 text-tgBlue rounded-full flex items-center justify-center mx-auto mb-4 text-2xl"><i class="fab fa-telegram-plane"></i></div>
            <h3 class="text-lg font-bold mb-2 text-white">يرجى ربط حسابك</h3>
            <p class="text-textMuted text-sm mb-5">أدخل الـ ID الخاص بك لمرة واحدة للتمكن من الإرسال.</p>
            <button onclick="window.open('https://t.me/{BOT_USERNAME}', '_blank')" class="btn bg-tgBlue text-white w-full mb-3 text-sm"><i class="fas fa-robot"></i> 1. نسخ الـ ID من البوت</button>
            <input type="text" id="tgIdInput" placeholder="2. الصق الـ ID هنا" class="modern-input bg-bgDark text-center text-base mb-4 font-mono py-2">
            <div class="flex gap-2">
                <button onclick="saveTgIdFromModal()" class="btn bg-accent text-white flex-1 text-sm">حفظ ومتابعة</button>
                <button onclick="closeTgModal()" class="btn bg-panelBorder text-textMuted flex-1 hover:text-white text-sm">إلغاء</button>
            </div>
        </div>
    </div>

    <script>
        function formatTime(secs) {{ if(isNaN(secs)) return "0:00"; const m = Math.floor(secs / 60), s = Math.floor(secs % 60); return `${{m}}:${{s < 10 ? '0'+s : s}}`; }}

        async function processInput() {{
            const input = document.getElementById('url').value.trim(); 
            if(!input) return;
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري البحث...'; btn.disabled = true;
            
            document.getElementById('previewBox').classList.add('hidden');
            
            if (input.startsWith('http')) await renderPreview(input);
            else {{
                try {{
                    const res = await fetch('/api/search', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{query:input}})}});
                    if(!res.ok) throw new Error();
                    const data = await res.json();
                    
                    if(data.success && data.entries.length) {{
                        let box = document.getElementById('searchResultsList'); box.innerHTML = '';
                        
                        data.entries.forEach((v) => {{
                            const duration = formatTime(v.duration || 0);
                            
                            // تعديل الـ HTML ليعرض صورة المعاينة مع الوقت بداخلها وبدون ترقيم، وبحماية تامة ضد التداخل
                            box.innerHTML += `
                            <div onclick="renderPreview('https://youtube.com/watch?v=${{v.id}}')" class="w-full flex items-center p-3 bg-panel rounded-2xl border border-panelBorder cursor-pointer hover:border-accent/50 transition-all active:scale-[0.98] shadow-sm mb-1">
                                <div class="flex-shrink-0 w-24 h-14 rounded-xl overflow-hidden border border-panelBorder shadow-sm relative ml-3">
                                    <img src="${{v.thumbnail}}" class="w-full h-full object-cover">
                                    <div class="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono">${{duration}}</div>
                                </div>
                                <div class="flex-1 min-w-0 flex flex-col justify-center text-right">
                                    <h4 class="text-white font-bold text-sm truncate w-full mb-1" dir="auto">${{v.title}}</h4>
                                    <p class="text-textMuted text-xs truncate w-full" dir="auto">
                                        <i class="fas fa-user-circle text-accent/70"></i> ${{v.uploader}}
                                    </p>
                                </div>
                                <div class="flex-shrink-0 w-8 h-8 rounded-full bg-bgDark border border-panelBorder flex items-center justify-center text-accent mr-2">
                                    <i class="fas fa-download text-xs"></i>
                                </div>
                            </div>`;
                        }});
                        document.getElementById('searchResults').classList.remove('hidden');
                    }} else showToast("لم يتم العثور على نتائج متوافقة", "error");
                }} catch(e) {{ showToast("خطأ في الاتصال بالسيرفر", "error"); }}
            }}
            btn.innerHTML = '<i class="fas fa-search"></i> بحث'; btn.disabled = false;
        }}

        async function renderPreview(url) {{
            currentUrl = url; 
            document.getElementById('searchResults').classList.add('hidden');
            document.getElementById('previewBox').classList.add('hidden'); 
            document.getElementById('progressBox').classList.add('hidden');
            document.getElementById('dlOptions').classList.remove('hidden'); 
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
                    vBtn.className = "btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder";
                    vBtn.innerHTML = '<i class="fas fa-lock"></i> 2. التحقق للتحميل'; adWatched = false;
                }} else showToast("الرابط محمي", "error");
            }} catch(e) {{ showToast("حدث خطأ", "error"); }}
        }}

        function toggleRes() {{ document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }}

        function startAdTimer() {{
            if(adWatched) return;
            let btn = document.getElementById('verifyBtn'); let timeLeft = 5;
            btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${{timeLeft}})...`;
            let timer = setInterval(() => {{
                timeLeft--;
                if(timeLeft > 0) btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${{timeLeft}})...`; 
                else {{
                    clearInterval(timer); btn.disabled = false; btn.onclick = unlockDownload; 
                    btn.className = "btn bg-green-600 text-white hover:bg-green-500 shadow-lg shadow-green-500/30 flex-1";
                    btn.innerHTML = "<i class='fas fa-unlock-alt'></i> 2. تحقق وفك القفل"; adWatched = true;
                }}
            }}, 1000);
        }}

        function unlockDownload() {{
            if(!adWatched) return;
            document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden');
        }}

        async function startDownload() {{
            const btn = event.currentTarget; const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري البدء...'; btn.disabled = true;
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            
            document.getElementById('progPercent').innerText = '0%';
            document.getElementById('progBar').style.width = '0%';
            document.getElementById('progSize').innerText = '-- / --';
            document.getElementById('progSpeed').innerText = '--';
            document.getElementById('progStatus').innerHTML = '<i class="fas fa-cloud-download-alt"></i> جاري الاتصال...';

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
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري تحميل الملف...';
                                document.getElementById('progPercent').innerText = prog.percent + '%';
                                document.getElementById('progBar').style.width = prog.percent + '%';
                                document.getElementById('progSize').innerText = prog.dl_mb + ' / ' + prog.total_mb;
                                document.getElementById('progSpeed').innerText = prog.spd_mb;
                            }} 
                            else if(prog.status === 'converting') {{ 
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-cog fa-spin"></i> جاري دمج وتجهيز الملف النهائي...'; 
                                document.getElementById('progBar').style.width = '100%'; 
                            }} 
                            else if(prog.status === 'completed') {{
                                clearInterval(interval); 
                                document.getElementById('progStatus').innerHTML = '<span class="text-green-400"><i class="fas fa-check-circle"></i> اكتمل التحميل بنجاح</span>';
                                
                                myLibrary.unshift({{ 
                                    id: Date.now().toString(), title: prog.title, url: prog.url, thumb: prog.thumb, 
                                    uploader: prog.uploader, duration: prog.duration,
                                    is_audio: prog.is_audio, timestamp: Date.now(), favorite: false 
                                }});
                                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                                
                                if(document.getElementById('libraryView').classList.contains('active')) applyFilters();
                                showToast("أضيف إلى ملفاتي", "success");

                                if(localStorage.getItem('pz_auto_tg') !== 'false') sendToTelegram(prog.url, prog.is_audio, true);
                            }} 
                            else if(prog.status === 'error') {{ clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">فشل التحميل</span>'; }}
                        }} catch(err) {{}}
                    }}, 800);
                }}
            }} catch(e) {{ showToast("فشل الاتصال", "error"); }}
            btn.innerHTML = original; btn.disabled = false;
        }}
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=INDEX_HTML)

# هنا التحديث الجوهري والمصلح بالكامل لتجميع الـ 5 خيارات دون الاعتماد على شروط صارمة تعطل البحث
@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        raw_results = search_youtube(req.query, limit=25)
        entries = raw_results.get("entries", [])
        
        valid_videos = []
        for entry in entries:
            if not entry:
                continue
                
            video_id = entry.get("id")
            title = entry.get("title")
            
            # فحص مرن وآمن: نكتفي بالـ ID والـ Title لضمان عدم إقصاء أي نتيجة من نتائج يوتيوب الـ 5 المطلوبة
            if video_id and title:
                thumb_url = entry.get("thumbnail")
                if not thumb_url and entry.get("thumbnails"):
                    thumb_url = entry.get("thumbnails")[0].get("url")
                if not thumb_url:
                    thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    
                clean_entry = {
                    "id": video_id,
                    "title": title,
                    "duration": entry.get("duration") or 0,
                    "uploader": entry.get("uploader") or entry.get("channel") or "غير معروف",
                    "thumbnail": thumb_url
                }
                valid_videos.append(clean_entry)
            
            # التوقف الفوري بمجرد تجميع 5 خيارات تحميل صالحة تماماً
            if len(valid_videos) == 5:
                break
                
        return {"success": True, "entries": valid_videos}
    except Exception as e: 
        return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"success": True, "title": info.get("title", "بدون عنوان"), "thumb": info.get("thumbnail", "")}
    except Exception as e: return {"success": False, "error": str(e)}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            
            percent = round((downloaded / total) * 100, 1)
            total_mb = f"{total / 1048576:.1f} MB"
            dl_mb = f"{downloaded / 1048576:.1f} MB"
            spd_mb = f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s"
            
            PROGRESS_CACHE[job_id] = {
                "status": "downloading", "percent": percent,
                "total_mb": total_mb, "dl_mb": dl_mb, "spd_mb": spd_mb,
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
            PROGRESS_CACHE[job_id] = {
                "status": "completed", 
                "url": f"/files/{filename}", 
                "title": info.get('title', 'مقطع'), 
                "thumb": info.get('thumbnail', ''), 
                "uploader": info.get('uploader', 'غير معروف'),
                "duration": info.get('duration', 0),
                "is_audio": mode == 'audio', 
                "timestamp": time.time()
            }
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
            return {"success": False, "error": "الملف مسح من السيرفر. يرجى إعادة التحميل."}
        if not TELEGRAM_TOKEN:
            return {"success": False, "error": "البوت غير مفعل حالياً."}

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 49.5:
            return {"success": False, "error": "حجم الملف يتجاوز الحد المسموح للإرسال في تيليجرام (50MB)."}

        api_method = "sendAudio" if req.is_audio else "sendVideo"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        
        dur = int(req.duration) if req.duration else 0
        if dur > 0:
            m, s = divmod(dur, 60)
            h, m = divmod(m, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            caption = f"- @{BOT_USERNAME} , {time_str}"
        else:
            caption = f"- @{BOT_USERNAME}"
            
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🌟 أعجبك البوت؟ شاركه", "url": f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"}]
            ]
        }
        
        data = {
            'chat_id': req.chat_id,
            'caption': caption,
            'reply_markup': json.dumps(reply_markup)
        }
        
        if req.is_audio:
            data['title'] = req.title
            data['performer'] = req.performer
            data['duration'] = req.duration
        else:
            data['supports_streaming'] = True
            data['duration'] = req.duration

        files = {}
        with open(file_path, 'rb') as f:
            file_content = f.read()
        files['audio' if req.is_audio else 'video'] = (filename, file_content)
        
        if req.thumb:
            try:
                thumb_res = requests.get(req.thumb, timeout=5)
                if thumb_res.status_code == 200:
                    files['thumb'] = ('thumb.jpg', thumb_res.content, 'image/jpeg')
            except: pass
                
        response = requests.post(url, data=data, files=files)
        res_data = response.json()
        
        if response.status_code == 200 and res_data.get("ok"): return {"success": True}
        else: return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة مع البوت.")}
            
    except Exception as e:
        return {"success": False, "error": str(e)}
