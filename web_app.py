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
BOT_USERNAME = "MusicPlayZoneBot"
# -------------------------------------------------------------

try:
    from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
    from database.connection import init_db
    from utils.helpers import get_cookie_file_for_url
except ImportError:
    BASE_DOWNLOAD_DIR = Path("./downloads")
    HILLTOPADS_LINK = "https://example.com/ad"
    ADSTERRA_LINK = None
    COOKIES_FILE = Path("cookies.txt")
    
    def init_db(): pass
    def get_cookie_file_for_url(url: str): return COOKIES_FILE if COOKIES_FILE.exists() else None

app = FastAPI(title="PlayZone Cloud Dashboard")
init_db()

try:
    from database.operations import load_all_cookies_from_db
    load_all_cookies_from_db()
except ImportError:
    pass

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

def cleanup_daemon():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            expired_jobs = [jid for jid, data in list(PROGRESS_CACHE.items()) if now - data.get("timestamp", now) > 86400]
            for jid in expired_jobs:
                PROGRESS_CACHE.pop(jid, None)
        except Exception as e:
            pass
        time.sleep(3600)

threading.Thread(target=cleanup_daemon, daemon=True).start()

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
    title: str = "مقطع"
    performer: str = "PlayZone"
    duration: int = 0
    thumb: str = ""

def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None, url=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "ar-SA,ar;q=0.9"}
    }
    
    # 🌟 الاستعانة بالفاحص الذكي للكوكيز 🌟
    cookie_path = get_cookie_file_for_url(url) if url else None
    if cookie_path:
        opts["cookiefile"] = str(cookie_path)
        
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 25):
    opts = get_hardened_ydl_options(url="https://youtube.com")
    opts['extract_flat'] = True
    
    if 'playlist_items' in opts:
        del opts['playlist_items']
    if 'noplaylist' in opts:
        del opts['noplaylist']
        
    with yt_dlp.YoutubeDL(opts) as ydl: 
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

# ==========================================
# الواجهة الاحترافية (HTML) كنص خام (Raw) لمنع أي خطأ فني
# ==========================================
INDEX_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="theme-color" content="#09090b">
    <title>PlayZone | سحابة الترفيه</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800;900&display=swap" rel="stylesheet">
    <script>
        tailwind.config={
            darkMode:'class',
            theme:{
                extend:{
                    fontFamily: { sans: ['Tajawal', 'sans-serif'] }, 
                    colors:{ 
                        brand: '#7c3aed',
                        brandHover: '#6d28d9',
                        bgBase: '#09090b',
                        surface: 'rgba(24, 24, 27, 0.6)',
                        surfaceSolid: '#18181b',
                        surfaceBorder: 'rgba(255, 255, 255, 0.08)',
                        textMuted: '#a1a1aa',
                        tgBlue: '#3b82f6'
                    },
                    animation: {
                        'spin-slow': 'spin 3s linear infinite',
                        'pulse-glow': 'pulseGlow 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                    },
                    keyframes: {
                        pulseGlow: {
                            '0%, 100%': { boxShadow: '0 0 15px rgba(124, 58, 237, 0.3)' },
                            '50%': { boxShadow: '0 0 25px rgba(124, 58, 237, 0.6)' },
                        }
                    }
                }
            }
        }
    </script>
    <style>
        :root { --safe-bottom: env(safe-area-inset-bottom, 0px); }
        body { 
            background-color: #09090b; 
            color: #f4f4f5; 
            overflow: hidden;
            background-image: 
                radial-gradient(circle at 15% 50%, rgba(124, 58, 237, 0.08), transparent 25%),
                radial-gradient(circle at 85% 30%, rgba(59, 130, 246, 0.08), transparent 25%);
        }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
        
        .glass-panel {
            background: var(--surface);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--surfaceBorder);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        }
        
        .modern-input { 
            background: rgba(0,0,0,0.4); 
            border: 1px solid var(--surfaceBorder); 
            color: white; 
            border-radius: 1rem; 
            padding: 1rem 1.25rem; 
            outline: none; 
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
            width: 100%; 
            font-weight: 500;
        }
        .modern-input:focus { 
            border-color: #7c3aed; 
            box-shadow: 0 0 0 4px rgba(124, 58, 237, 0.15); 
            background: rgba(0,0,0,0.6);
        }
        
        .btn-gradient {
            background: linear-gradient(135deg, #7c3aed, #4f46e5);
            color: white;
            border: none;
            position: relative;
            z-index: 1;
        }
        .btn-gradient::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: linear-gradient(135deg, #6d28d9, #4338ca);
            border-radius: inherit; z-index: -1; opacity: 0; transition: opacity 0.3s;
        }
        .btn-gradient:hover::before { opacity: 1; }

        .btn { 
            padding: 1rem 1.5rem; 
            border-radius: 1rem; 
            font-weight: 700; 
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); 
            display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; 
            cursor: pointer; user-select: none;
        }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
        .btn:active:not(:disabled) { transform: scale(0.97); }

        .view-section { 
            display: none; 
            opacity: 0;
            transition: opacity 0.4s ease, transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            transform: translateY(15px) scale(0.98);
        }
        .view-section.active { 
            display: block; 
            opacity: 1;
            transform: translateY(0) scale(1);
        }
        
        .bottom-nav {
            position: fixed; bottom: 0; left: 0; right: 0;
            z-index: 40;
            padding-bottom: var(--safe-bottom);
        }
        
        #musicPlayer { 
            position: fixed; 
            bottom: calc(5rem + var(--safe-bottom)); 
            left: 1rem; right: 1rem;
            z-index: 45; 
            transform: translateY(150%); 
            transition: all 0.5s cubic-bezier(0.34, 1.56, 0.64, 1); 
            border-radius: 1.5rem;
            opacity: 0;
        }
        #musicPlayer.active {
            transform: translateY(0);
            opacity: 1;
        }
        @media (min-width: 768px) {
            .bottom-nav { display: none; }
            #musicPlayer { left: auto; right: 2rem; bottom: 2rem; width: 400px; }
        }
        
        .nav-item {
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            color: var(--textMuted); transition: all 0.3s;
            padding: 0.5rem; border-radius: 1rem;
        }
        .nav-item.active { color: #7c3aed; }
        .nav-item.active i { transform: translateY(-3px) scale(1.1); text-shadow: 0 4px 12px rgba(124, 58, 237, 0.4); }
        .nav-item i { transition: transform 0.3s; font-size: 1.25rem; margin-bottom: 0.25rem; }
        .nav-item span { font-size: 0.7rem; font-weight: 700; }

        .progress-track { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; cursor: pointer; position: relative; overflow: hidden; }
        .progress-fill { height: 100%; background: #7c3aed; width: 0%; border-radius: 3px; transition: width 0.1s linear; position: relative; }
        .progress-fill::after { content: ''; position: absolute; right: 0; top: -2px; width: 10px; height: 10px; background: white; border-radius: 50%; box-shadow: 0 0 8px rgba(124,58,237,0.8); }

        #toast { position: fixed; top: 1.5rem; left: 50%; transform: translateX(-50%) translateY(-150%); opacity: 0; z-index: 1000; padding: 1rem 1.5rem; border-radius: 1rem; font-weight: 700; color: white; transition: all 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.2); pointer-events: none; text-align: center; min-width: 250px; }
        #toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
        
        .card-hover { transition: all 0.3s ease; }
        .card-hover:hover { transform: translateY(-4px); border-color: rgba(124, 58, 237, 0.4); box-shadow: 0 10px 30px -10px rgba(124, 58, 237, 0.2); }
    </style>
</head>
<body class="antialiased flex h-[100dvh] w-full">
    <div id="toast"></div>

    <aside class="hidden md:flex w-72 glass-panel border-l border-surfaceBorder flex-col justify-between h-[100dvh] z-40 flex-shrink-0">
        <div>
            <div class="h-24 flex items-center justify-center border-b border-surfaceBorder px-8 relative overflow-hidden">
                <div class="absolute inset-0 bg-gradient-to-br from-brand/10 to-transparent"></div>
                <div class="w-12 h-12 bg-gradient-to-br from-brand to-indigo-600 rounded-2xl flex items-center justify-center text-white text-xl shadow-lg shadow-brand/30 relative z-10">
                    <i class="fas fa-play ml-1"></i>
                </div>
                <h1 class="text-2xl font-black text-white mr-4 relative z-10 tracking-tight">Play<span class="text-brand">Zone</span></h1>
            </div>

            <nav class="mt-8 px-4 space-y-3">
                <button onclick="switchView('searchView')" id="nav-desktop-searchView" class="desk-nav btn w-full flex items-center justify-start gap-4 p-4 rounded-2xl bg-brand/10 text-brand font-bold border border-brand/20">
                    <i class="fas fa-compass text-xl w-6"></i><span>اكتشف و حمّل</span>
                </button>
                <button onclick="switchView('libraryView')" id="nav-desktop-libraryView" class="desk-nav btn w-full flex items-center justify-start gap-4 p-4 rounded-2xl text-textMuted hover:bg-white/5 hover:text-white bg-transparent border border-transparent">
                    <i class="fas fa-layer-group text-xl w-6"></i><span>مكتبتي</span>
                </button>
                <button onclick="switchView('settingsView')" id="nav-desktop-settingsView" class="desk-nav btn w-full flex items-center justify-start gap-4 p-4 rounded-2xl text-textMuted hover:bg-white/5 hover:text-white bg-transparent border border-transparent">
                    <i class="fas fa-sliders-h text-xl w-6"></i><span>الإعدادات</span>
                </button>
            </nav>
        </div>
        
        <div class="p-6 border-t border-surfaceBorder">
            <div class="bg-bgBase rounded-2xl p-4 border border-surfaceBorder text-center">
                <p class="text-xs text-textMuted mb-3">متصل عبر تيليجرام</p>
                <a href="https://t.me/{{BOT_USERNAME}}" target="_blank" class="btn w-full flex items-center justify-center gap-2 p-3 rounded-xl bg-tgBlue/10 text-tgBlue hover:bg-tgBlue/20">
                    <i class="fab fa-telegram-plane"></i><span dir="ltr">@{{BOT_USERNAME}}</span>
                </a>
            </div>
        </div>
    </aside>

    <main class="flex-1 h-[100dvh] overflow-y-auto pb-24 md:pb-8 relative scroll-smooth">
        
        <section id="searchView" class="view-section active p-4 md:p-10 max-w-5xl mx-auto mt-4 md:mt-8">
            <div class="text-center mb-10">
                <h2 class="text-3xl md:text-5xl font-black text-white mb-4 tracking-tight">ماذا تريد أن تُحمل <span class="text-brand">اليوم؟</span></h2>
                <p class="text-textMuted text-sm md:text-base max-w-lg mx-auto">الصق رابط المقطع المفضل لديك أو ابحث باسم الأغنية مباشرة للتحميل بأعلى جودة متوفرة.</p>
            </div>

            <div class="glass-panel rounded-[2rem] p-3 md:p-4 mb-8 relative flex flex-col md:flex-row gap-3">
                <div class="relative flex-1">
                    <i class="fas fa-link absolute right-5 top-1/2 transform -translate-y-1/2 text-brand text-lg"></i>
                    <input type="text" id="url" placeholder="الرابط أو الكلمة البحثية..." class="modern-input pl-4 pr-12 h-14 bg-bgBase border-none">
                </div>
                <button onclick="processInput()" id="mainBtn" class="btn btn-gradient h-14 w-full md:w-40 rounded-xl shadow-lg shadow-brand/25 text-lg">
                    <i class="fas fa-search"></i> بحث
                </button>
            </div>
            
            <div id="searchResults" class="hidden mt-4 glass-panel rounded-[2rem] p-5 shadow-2xl">
                <div class="mb-5 pb-4 border-b border-surfaceBorder flex justify-between items-center px-2">
                    <h3 class="text-white font-bold text-lg flex items-center gap-2"><i class="fas fa-list-ul text-brand"></i> نتائج البحث:</h3>
                    <button onclick="document.getElementById('searchResults').classList.add('hidden')" class="text-textMuted hover:text-white bg-bgBase w-8 h-8 rounded-full flex items-center justify-center transition-colors"><i class="fas fa-times"></i></button>
                </div>
                <div id="searchResultsList" class="flex flex-col gap-3"></div>
            </div>

            <div id="previewBox" class="hidden glass-panel rounded-[2rem] p-6 mt-6">
                <div class="flex flex-col md:flex-row gap-8 items-center">
                    <div class="w-full md:w-5/12 relative group rounded-2xl overflow-hidden shadow-2xl">
                        <img id="thumb" class="w-full aspect-video object-cover transition-transform duration-500 group-hover:scale-105">
                        <div class="absolute inset-0 bg-gradient-to-t from-bgBase via-transparent to-transparent opacity-60"></div>
                    </div>
                    <div class="w-full md:w-7/12 space-y-6">
                        <h3 id="title" class="font-black text-xl md:text-2xl text-white leading-snug"></h3>
                        
                        <div id="adGate" class="bg-brand/5 border border-brand/20 p-5 rounded-2xl text-center">
                            <div class="w-12 h-12 bg-brand/20 text-brand rounded-full flex items-center justify-center mx-auto mb-3 text-xl"><i class="fas fa-shield-alt"></i></div>
                            <p class="text-sm mb-4 text-white font-medium">للتحميل مجاناً، يرجى مشاهدة إعلان قصير لفتح القفل.</p>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <a href="{{AD_LINK}}" target="_blank" onclick="startAdTimer()" class="btn bg-white text-bgBase hover:bg-gray-200 flex-1 shadow-lg"><i class="fas fa-external-link-alt"></i> 1. فتح الإعلان</a>
                                <button id="verifyBtn" disabled class="btn bg-surfaceSolid text-textMuted flex-1 cursor-not-allowed border border-surfaceBorder"><i class="fas fa-lock"></i> 2. التحقق</button>
                            </div>
                        </div>

                        <div id="dlOptions" class="hidden space-y-5">
                            <div class="grid grid-cols-2 gap-4">
                                <div class="space-y-2">
                                    <label class="text-xs font-bold text-textMuted px-1">نوع التحميل</label>
                                    <select id="mode" onchange="toggleRes()" class="modern-input h-12"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                                </div>
                                <div class="space-y-2" id="resContainer">
                                    <label class="text-xs font-bold text-textMuted px-1">الجودة</label>
                                    <select id="resolution" class="modern-input h-12"><option value="480">عادية 480p</option><option value="720" selected>عالية 720p</option></select>
                                </div>
                            </div>
                            <button onclick="startDownload()" class="btn btn-gradient w-full h-14 shadow-xl shadow-brand/30 text-lg"><i class="fas fa-cloud-download-alt"></i> ابدأ التحميل الآن</button>
                        </div>

                        <div id="progressBox" class="hidden bg-bgBase p-6 rounded-2xl border border-surfaceBorder relative overflow-hidden">
                            <div class="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-brand to-indigo-500 animate-pulse"></div>
                            <div class="flex justify-between items-center mb-4">
                                <span id="progStatus" class="text-brand font-bold text-sm flex items-center gap-2"><i class="fas fa-circle-notch fa-spin"></i> جاري التجهيز...</span>
                                <span id="progPercent" class="font-black text-white text-xl">0%</span>
                            </div>
                            <div class="w-full bg-surfaceSolid rounded-full h-4 mb-3 overflow-hidden p-0.5">
                                <div id="progBar" class="bg-gradient-to-r from-indigo-500 to-brand h-full rounded-full relative" style="width: 0%">
                                    <div class="shimmer-bg rounded-full"></div>
                                </div>
                            </div>
                            <div class="flex justify-between text-xs text-textMuted font-mono">
                                <span id="progSize" class="bg-surface px-2 py-1 rounded-md">-- / --</span>
                                <span id="progSpeed" class="bg-surface px-2 py-1 rounded-md">--</span>
                            </div>
                            
                            <div id="directDownloadArea" class="hidden mt-5 pt-5 border-t border-surfaceBorder">
                                <a id="directDownloadBtn" href="#" download class="btn bg-green-500 text-bgBase hover:bg-green-400 w-full shadow-lg shadow-green-500/20 text-base"><i class="fas fa-arrow-down"></i> حفظ في الهاتف 💾</a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <section id="libraryView" class="view-section p-4 md:p-10 max-w-6xl mx-auto h-full flex flex-col mt-4 md:mt-8">
            <div class="flex flex-col md:flex-row justify-between items-center gap-6 mb-10 bg-bgBase/50 p-4 rounded-2xl border border-surfaceBorder">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-brand/20 text-brand rounded-xl flex items-center justify-center"><i class="fas fa-layer-group"></i></div>
                    <h2 class="text-2xl font-black text-white">مكتبتي</h2>
                </div>
                <div class="flex flex-wrap gap-3 w-full md:w-auto">
                    <div class="relative flex-1 md:w-64">
                        <i class="fas fa-search absolute right-4 top-1/2 transform -translate-y-1/2 text-textMuted"></i>
                        <input type="text" id="libSearch" oninput="applyFilters()" placeholder="بحث في ملفاتي..." class="modern-input pl-4 pr-11 h-12 bg-surfaceSolid">
                    </div>
                    <select id="libFilter" onchange="applyFilters()" class="modern-input h-12 px-4 w-auto bg-surfaceSolid text-white font-bold">
                        <option value="all">الكل</option><option value="favorites">❤️ المفضلة</option><option value="audio">🎵 صوتيات</option><option value="video">🎬 فيديوهات</option>
                    </select>
                </div>
            </div>
            <div id="libraryContainer" class="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1 content-start"></div>
            <div id="pagination" class="mt-10 flex justify-center items-center gap-2 pb-10"></div>
        </section>

        <section id="settingsView" class="view-section p-4 md:p-10 max-w-3xl mx-auto mt-4 md:mt-8">
            <div class="text-center mb-10">
                <h2 class="text-3xl font-black text-white mb-2">إعدادات النظام</h2>
                <p class="text-textMuted">تحكم في خيارات الربط والتخزين الخاص بك.</p>
            </div>

            <div class="space-y-6">
                <div class="glass-panel rounded-[2rem] p-6 md:p-8">
                    <div class="flex items-center gap-4 mb-6">
                        <div class="w-12 h-12 bg-tgBlue/20 text-tgBlue rounded-2xl flex items-center justify-center text-2xl"><i class="fab fa-telegram-plane"></i></div>
                        <div>
                            <h3 class="text-xl font-bold text-white">الربط مع تيليجرام</h3>
                            <p class="text-textMuted text-sm">استلم تحميلاتك مباشرة داخل المحادثة.</p>
                        </div>
                    </div>
                    <div class="flex flex-col md:flex-row gap-3 mb-6">
                        <input type="text" id="settingTgId" placeholder="أدخل الآي دي (ID) الخاص بك" class="modern-input h-14 font-mono text-center md:text-right bg-bgBase">
                        <button onclick="updateTgId()" class="btn bg-tgBlue hover:bg-blue-500 text-white h-14 md:w-32 shadow-lg shadow-tgBlue/20">حفظ الربط</button>
                    </div>
                    <div class="flex items-center justify-between p-5 bg-bgBase rounded-2xl border border-surfaceBorder">
                        <div>
                            <p class="font-bold text-white">إرسال تلقائي (أتمتة)</p>
                            <p class="text-sm text-textMuted mt-1">توجيه الملف للبوت فور اكتماله السحابة.</p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="autoForwardToggle" onchange="toggleAutoForward()" class="sr-only peer" checked>
                            <div class="w-14 h-7 bg-surfaceSolid border border-surfaceBorder rounded-full peer peer-checked:after:-translate-x-[120%] peer-checked:bg-brand after:content-[''] after:absolute after:top-[2px] after:right-[2px] after:bg-white after:rounded-full after:h-6 after:w-6 after:transition-all shadow-inner"></div>
                        </label>
                    </div>
                </div>

                <div class="glass-panel rounded-[2rem] p-6 md:p-8">
                    <div class="flex items-center gap-4 mb-6">
                        <div class="w-12 h-12 bg-red-500/20 text-red-400 rounded-2xl flex items-center justify-center text-2xl"><i class="fas fa-hdd"></i></div>
                        <div>
                            <h3 class="text-xl font-bold text-white">التخزين المحلي</h3>
                            <p class="text-textMuted text-sm">إدارة السجل المحفوظ على هذا المتصفح.</p>
                        </div>
                    </div>
                    <div class="flex flex-col sm:flex-row justify-between items-center gap-4 p-5 bg-bgBase rounded-2xl border border-surfaceBorder">
                        <div class="text-center sm:text-right">
                            <p class="font-bold text-white text-lg" id="libCountStatus">ملفات السجل: 0</p>
                            <p class="text-xs text-textMuted mt-1">لا يتم حذف الملفات من سيرفر البوت.</p>
                        </div>
                        <button onclick="clearAllLibrary()" class="btn bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white w-full sm:w-auto transition-colors border border-red-500/20"><i class="fas fa-trash-alt"></i> مسح السجل</button>
                    </div>
                </div>
            </div>
        </section>
    </main>

    <nav class="bottom-nav glass-panel border-t border-surfaceBorder">
        <div class="flex justify-around items-center h-16 px-2 relative">
            <button onclick="switchView('searchView')" id="nav-mobile-searchView" class="mob-nav nav-item active w-1/3">
                <i class="fas fa-search"></i><span>بحث</span>
            </button>
            <button onclick="switchView('libraryView')" id="nav-mobile-libraryView" class="mob-nav nav-item w-1/3">
                <i class="fas fa-layer-group"></i><span>مكتبتي</span>
            </button>
            <button onclick="switchView('settingsView')" id="nav-mobile-settingsView" class="mob-nav nav-item w-1/3">
                <i class="fas fa-cog"></i><span>الإعدادات</span>
            </button>
        </div>
    </nav>

    <div id="musicPlayer" class="glass-panel border border-surfaceBorder shadow-2xl p-3">
        <div class="flex flex-col gap-3">
            <div class="flex items-center gap-3 w-full">
                <div class="w-12 h-12 rounded-xl overflow-hidden flex-shrink-0 relative group" id="playerThumbPlaceholder">
                    <div class="w-full h-full bg-brand/20 flex items-center justify-center text-brand"><i class="fas fa-music"></i></div>
                </div>
                <div class="flex-1 overflow-hidden min-w-0 pr-1">
                    <p id="playerTitle" class="font-bold text-sm text-white truncate marquee-if-long">اختر مقطعاً للتشغيل</p>
                    <p id="playerTime" class="text-xs text-brand font-mono mt-1 opacity-80">0:00 / 0:00</p>
                </div>
                
                <div class="flex items-center justify-end gap-2 md:hidden">
                    <button onclick="togglePlay()" id="playPauseBtnMob" class="w-10 h-10 rounded-full bg-white text-bgBase flex items-center justify-center text-sm shadow-md active:scale-90 transition-transform"><i class="fas fa-play ml-0.5"></i></button>
                    <button onclick="closePlayer()" class="w-8 h-8 rounded-full bg-surfaceSolid text-textMuted flex items-center justify-center text-xs active:scale-90 transition-transform border border-surfaceBorder"><i class="fas fa-times"></i></button>
                </div>
                
                <div class="hidden md:flex items-center gap-4 flex-shrink-0 bg-bgBase p-1.5 rounded-2xl border border-surfaceBorder">
                    <button onclick="toggleShuffle()" id="shuffleBtn" class="w-8 h-8 text-textMuted hover:text-white transition-colors active:scale-90 rounded-lg"><i class="fas fa-random text-sm"></i></button>
                    <button onclick="playPrev()" class="w-8 h-8 text-white hover:text-brand transition-colors active:scale-90 rounded-lg"><i class="fas fa-step-backward"></i></button>
                    <button onclick="togglePlay()" id="playPauseBtnDesk" class="w-10 h-10 rounded-full bg-brand text-white flex items-center justify-center shadow-lg shadow-brand/40 active:scale-90 transition-transform"><i class="fas fa-play ml-0.5"></i></button>
                    <button onclick="playNext()" class="w-8 h-8 text-white hover:text-brand transition-colors active:scale-90 rounded-lg"><i class="fas fa-step-forward"></i></button>
                    <button onclick="toggleRepeat()" id="repeatBtn" class="w-8 h-8 text-textMuted hover:text-white transition-colors active:scale-90 rounded-lg"><i class="fas fa-redo text-sm"></i></button>
                </div>
            </div>
            
            <div class="flex items-center gap-3">
                <span class="text-[10px] text-textMuted font-mono hidden md:block w-8 text-right" id="currTimeTxt">0:00</span>
                <div class="progress-track flex-1" onclick="seekAudio(event)" ontouchstart="seekAudio(event)">
                    <div class="progress-fill" id="audioProgressBar"></div>
                </div>
                <span class="text-[10px] text-textMuted font-mono hidden md:block w-8" id="durTimeTxt">0:00</span>
            </div>
        </div>
        <audio id="globalAudioElement" ontimeupdate="updatePlayerProgress()" onended="handleAudioEnd()" onplay="handleAudioPlay()" onpause="handleAudioPause()"></audio>
    </div>

    <div id="tgModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-[200] hidden flex-col items-center justify-center p-4">
        <div class="glass-panel border border-surfaceBorder p-8 rounded-[2rem] max-w-sm w-full text-center shadow-2xl" id="tgModalContent">
            <div class="w-16 h-16 bg-gradient-to-br from-blue-400 to-blue-600 text-white rounded-2xl flex items-center justify-center mx-auto mb-5 text-3xl shadow-lg shadow-blue-500/30"><i class="fab fa-telegram-plane pr-1"></i></div>
            <h3 class="text-xl font-black mb-2 text-white">اربط حسابك للتحميل</h3>
            <p class="text-textMuted text-sm mb-6">نحتاج معرفك (ID) لمرة واحدة فقط لكي نتمكن من إرسال الملفات إلى محادثتك.</p>
            <button onclick="window.open('https://t.me/{{BOT_USERNAME}}', '_blank')" class="btn bg-surfaceSolid text-white w-full mb-4 text-sm border border-surfaceBorder hover:bg-surface"><i class="fas fa-robot text-brand"></i> 1. جلب الآي دي من البوت</button>
            <input type="text" id="tgIdInput" placeholder="2. الصق الآي دي (ID) هنا" class="modern-input text-center text-lg mb-6 font-mono bg-bgBase">
            <div class="flex gap-3">
                <button onclick="saveTgIdFromModal()" class="btn btn-gradient flex-1 text-sm">حفظ ومتابعة</button>
                <button onclick="closeTgModal()" class="btn bg-transparent text-textMuted flex-1 hover:text-white hover:bg-surfaceSolid text-sm border border-surfaceBorder">إلغاء</button>
            </div>
        </div>
    </div>

    <script>
        let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
        let currentUrl = "";
        let adWatched = false;
        let currentPlayingIndex = -1;
        let isShuffle = false;
        let isRepeat = false;
        let libraryPage = 1;
        const itemsPerPage = 6;
        const audioEl = document.getElementById('globalAudioElement');

        window.addEventListener('DOMContentLoaded', () => {
            if (window.Telegram && window.Telegram.WebApp) {
                window.Telegram.WebApp.ready();
                window.Telegram.WebApp.expand();
                window.Telegram.WebApp.setHeaderColor('#09090b');
                const tgUser = window.Telegram.WebApp.initDataUnsafe.user;
                if (tgUser && tgUser.id) {
                    localStorage.setItem('pz_tg_id', tgUser.id);
                    showToast("تم مزامنة حساب تيليجرام تلقائياً 🛡️", "success");
                }
            }
            
            const savedId = localStorage.getItem('pz_tg_id') || "";
            document.getElementById('settingTgId').value = savedId;
            document.getElementById('tgIdInput').value = savedId;
            
            const autoFwd = localStorage.getItem('pz_auto_tg') !== 'false';
            document.getElementById('autoForwardToggle').checked = autoFwd;

            updateLibraryCount();
            switchView('searchView');
        });

        function formatTime(secs) { 
            if(isNaN(secs) || secs === null) return "0:00"; 
            const m = Math.floor(secs / 60), s = Math.floor(secs % 60); 
            return m + ":" + (s < 10 ? '0' + s : s); 
        }

        function showToast(message, type = "success") {
            const toast = document.getElementById('toast');
            toast.innerText = message;
            toast.className = "show";
            if (type === "error") {
                toast.style.background = "rgba(220, 38, 38, 0.9)";
                toast.style.borderColor = "rgba(239, 68, 68, 0.5)";
            } else {
                toast.style.background = "rgba(124, 58, 237, 0.9)";
                toast.style.borderColor = "rgba(139, 92, 246, 0.5)";
            }
            setTimeout(() => { toast.className = ""; }, 3000);
        }

        function switchView(viewId) {
            document.querySelectorAll('.view-section').forEach(el => {
                el.classList.remove('active');
            });
            
            setTimeout(() => {
                document.getElementById(viewId).classList.add('active');
            }, 50);
            
            document.querySelectorAll('.desk-nav').forEach(btn => {
                btn.classList.remove('bg-brand/10', 'text-brand', 'border-brand/20');
                btn.classList.add('text-textMuted', 'bg-transparent', 'border-transparent');
            });
            const deskBtn = document.getElementById('nav-desktop-' + viewId);
            if (deskBtn) {
                deskBtn.classList.remove('text-textMuted', 'bg-transparent', 'border-transparent');
                deskBtn.classList.add('bg-brand/10', 'text-brand', 'border-brand/20');
            }
            
            document.querySelectorAll('.mob-nav').forEach(btn => btn.classList.remove('active'));
            const mobBtn = document.getElementById('nav-mobile-' + viewId);
            if(mobBtn) mobBtn.classList.add('active');
            
            if (viewId === 'libraryView') applyFilters();
        }

        function applyFilters() {
            const query = document.getElementById('libSearch').value.toLowerCase();
            const filter = document.getElementById('libFilter').value;
            
            let filtered = myLibrary.filter(item => {
                const matchesSearch = item.title.toLowerCase().includes(query) || (item.uploader && item.uploader.toLowerCase().includes(query));
                let matchesType = true;
                if (filter === 'favorites') matchesType = item.favorite;
                else if (filter === 'audio') matchesType = item.is_audio;
                else if (filter === 'video') matchesType = !item.is_audio;
                return matchesSearch && matchesType;
            });

            const totalItems = filtered.length;
            const totalPages = Math.ceil(totalItems / itemsPerPage);
            if (libraryPage > totalPages) libraryPage = Math.max(1, totalPages);
            
            const start = (libraryPage - 1) * itemsPerPage;
            const pageItems = filtered.slice(start, start + itemsPerPage);
            
            const container = document.getElementById('libraryContainer');
            container.innerHTML = "";
            
            if (pageItems.length === 0) {
                container.innerHTML = `
                    <div class="col-span-full py-16 text-center text-textMuted bg-bgBase rounded-[2rem] border border-surfaceBorder border-dashed">
                        <div class="w-20 h-20 bg-surfaceSolid rounded-full flex items-center justify-center mx-auto mb-4 text-3xl"><i class="fas fa-ghost"></i></div>
                        <h3 class="text-white font-bold text-lg mb-1">المكتبة فارغة</h3>
                        <p class="text-sm">لا توجد ملفات مطابقة للبحث أو التصفية الحالية.</p>
                    </div>
                `;
                document.getElementById('pagination').innerHTML = "";
                return;
            }

            pageItems.forEach((item) => {
                const actualIndex = myLibrary.findIndex(i => i.id === item.id);
                const isCurrentPlaying = (currentPlayingIndex !== -1 && myLibrary[currentPlayingIndex] && myLibrary[currentPlayingIndex].id === item.id);
                const activeBorder = isCurrentPlaying ? 'border-brand shadow-lg shadow-brand/20 bg-brand/5' : 'border-surfaceBorder bg-bgBase';
                const bounceIcon = isCurrentPlaying ? '<i class="fas fa-volume-up text-brand text-2xl animate-pulse"></i>' : (item.is_audio ? '<i class="fas fa-play text-white text-2xl drop-shadow-md"></i>' : '<i class="fas fa-external-link-alt text-white text-2xl drop-shadow-md"></i>');
                const titleColor = isCurrentPlaying ? 'text-brand' : 'text-white';
                
                const durationStr = formatTime(item.duration || 0);
                const favClass = item.favorite ? 'fas fa-heart text-red-500 drop-shadow-[0_0_8px_rgba(239,68,68,0.5)]' : 'far fa-heart';
                const icon = item.is_audio ? '<i class="fas fa-music text-brand"></i>' : '<i class="fas fa-video text-tgBlue"></i>';
                const fileExt = item.is_audio ? 'mp3' : 'mp4';
                
                const onclickAction = item.is_audio ? "playAudioTrack(" + actualIndex + ")" : "watchVideo('" + item.url + "')";
                const safeThumb = item.thumb || 'https://via.placeholder.com/150';
                const safeTitle = item.title.replace(/'/g, "&apos;").replace(/"/g, "&quot;");
                const safeUploader = (item.uploader || 'غير معروف').replace(/'/g, "&apos;").replace(/"/g, "&quot;");

                container.innerHTML += `
                    <div class="rounded-2xl p-4 border ${activeBorder} flex gap-4 items-center relative card-hover">
                        <div class="relative w-28 h-20 rounded-xl overflow-hidden flex-shrink-0 cursor-pointer group shadow-md" onclick="${onclickAction}">
                            <img src="${safeThumb}" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110" onerror="this.src='https://via.placeholder.com/150'">
                            <div class="absolute inset-0 bg-black/50 flex items-center justify-center ${isCurrentPlaying ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} transition-all duration-300">
                                ${bounceIcon}
                            </div>
                            <div class="absolute bottom-1.5 right-1.5 bg-black/80 backdrop-blur text-[10px] px-1.5 py-0.5 font-bold font-mono rounded text-white">${durationStr}</div>
                        </div>
                        <div class="flex-1 min-w-0 text-right py-1">
                            <h4 class="${titleColor} font-bold text-sm md:text-base leading-tight mb-1 truncate cursor-pointer" onclick="${onclickAction}">${safeTitle}</h4>
                            <p class="text-textMuted text-xs truncate">${icon} ${safeUploader}</p>
                        </div>
                        <div class="flex flex-col sm:flex-row items-center gap-2 flex-shrink-0 pl-1">
                            <button onclick="toggleFavorite('${item.id}')" class="w-10 h-10 bg-surfaceSolid rounded-full border border-surfaceBorder text-textMuted hover:text-red-500 active:scale-90 transition-all flex items-center justify-center" title="مفضلة">
                                <i class="${favClass} text-lg"></i>
                            </button>
                            <button onclick="triggerSendToTelegram('${item.id}')" class="w-10 h-10 bg-tgBlue/10 text-tgBlue rounded-full border border-tgBlue/20 hover:bg-tgBlue hover:text-white active:scale-90 transition-all flex items-center justify-center" title="إرسال لتيليجرام">
                                <i class="fab fa-telegram-plane text-lg -ml-0.5"></i>
                            </button>
                            <div class="relative group dropdown-container">
                                <button class="w-10 h-10 bg-surfaceSolid rounded-full border border-surfaceBorder text-textMuted hover:text-white active:scale-90 transition-all flex items-center justify-center">
                                    <i class="fas fa-ellipsis-v"></i>
                                </button>
                                <div class="absolute left-0 bottom-full mb-2 hidden group-hover:flex flex-col bg-surfaceSolid border border-surfaceBorder rounded-xl shadow-xl overflow-hidden z-20 w-36">
                                    <a href="${item.url}" download="${safeTitle}.${fileExt}" class="px-4 py-3 text-xs font-bold text-white hover:bg-white/10 flex items-center gap-2 border-b border-surfaceBorder">
                                        <i class="fas fa-download text-green-400"></i> تحميل
                                    </a>
                                    <button onclick="deleteFromLibrary('${item.id}')" class="px-4 py-3 text-xs font-bold text-red-400 hover:bg-red-500/10 flex items-center gap-2 text-right">
                                        <i class="fas fa-trash-alt"></i> حذف
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            });

            renderPagination(totalPages);
        }

        function renderPagination(totalPages) {
            const pagBox = document.getElementById('pagination');
            pagBox.innerHTML = "";
            if (totalPages <= 1) return;

            let html = '<button onclick="changePage(' + (libraryPage - 1) + ')" ' + (libraryPage === 1 ? 'disabled' : '') + ' class="btn w-10 h-10 p-0 bg-surfaceSolid border border-surfaceBorder text-textMuted hover:text-white disabled:opacity-30 rounded-full"><i class="fas fa-chevron-right"></i></button>';
            for (let i = 1; i <= totalPages; i++) {
                const activeClass = (libraryPage === i) ? 'bg-brand text-white border-brand shadow-lg shadow-brand/30' : 'bg-surfaceSolid border-surfaceBorder text-textMuted hover:text-white';
                html += '<button onclick="changePage(' + i + ')" class="btn w-10 h-10 p-0 rounded-full border ' + activeClass + ' font-mono text-sm">' + i + '</button>';
            }
            html += '<button onclick="changePage(' + (libraryPage + 1) + ')" ' + (libraryPage === totalPages ? 'disabled' : '') + ' class="btn w-10 h-10 p-0 bg-surfaceSolid border border-surfaceBorder text-textMuted hover:text-white disabled:opacity-30 rounded-full"><i class="fas fa-chevron-left"></i></button>';
            pagBox.innerHTML = html;
        }

        function changePage(page) {
            libraryPage = page;
            applyFilters();
            document.getElementById('libraryView').scrollIntoView({behavior: 'smooth'});
        }

        function toggleFavorite(id) {
            const index = myLibrary.findIndex(i => i.id === id);
            if (index !== -1) {
                myLibrary[index].favorite = !myLibrary[index].favorite;
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
            }
        }

        function deleteFromLibrary(id) {
            if (confirm("هل أنت متأكد من حذف هذا الملف نهائياً من قائمتك؟")) {
                myLibrary = myLibrary.filter(i => i.id !== id);
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
                updateLibraryCount();
                showToast("تم الحذف بنجاح", "success");
            }
        }

        function updateLibraryCount() {
            const count = myLibrary.length;
            document.getElementById('libCountStatus').innerText = "ملفات السجل: " + count;
        }

        function clearAllLibrary() {
            if (confirm("تحذير: سيتم مسح السجل المحلي بالكامل! هل ترغب بالمتابعة؟")) {
                myLibrary = [];
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
                updateLibraryCount();
                showToast("تم تهيئة السجل بسلام", "success");
            }
        }

        function updateTgId() {
            const tgId = document.getElementById('settingTgId').value.trim();
            if (!tgId) {
                localStorage.removeItem('pz_tg_id');
                showToast("تم إزالة معرف تيليجرام", "success");
            } else {
                localStorage.setItem('pz_tg_id', tgId);
                showToast("تم حفظ معرف تيليجرام بنجاح", "success");
            }
        }

        function toggleAutoForward() {
            const val = document.getElementById('autoForwardToggle').checked;
            localStorage.setItem('pz_auto_tg', val ? 'true' : 'false');
        }

        let pendingTgItem = null;
        function triggerSendToTelegram(id) {
            const item = myLibrary.find(i => i.id === id);
            if (!item) return;
            
            const tgId = localStorage.getItem('pz_tg_id');
            if (!tgId) {
                pendingTgItem = item;
                document.getElementById('tgIdInput').value = "";
                document.getElementById('tgModal').classList.remove('hidden');
                document.getElementById('tgModal').classList.add('flex');
            } else {
                sendToTelegram(item.url, item.is_audio, false, item.title, item.uploader, item.duration, item.thumb);
            }
        }

        function closeTgModal() {
            document.getElementById('tgModal').classList.add('hidden');
            document.getElementById('tgModal').classList.remove('flex');
            pendingTgItem = null;
        }

        function saveTgIdFromModal() {
            const val = document.getElementById('tgIdInput').value.trim();
            if (!val) return showToast("الرجاء إدخال ID صالح", "error");
            
            localStorage.setItem('pz_tg_id', val);
            document.getElementById('settingTgId').value = val;
            closeTgModal();
            showToast("تم الحفظ بنجاح", "success");
            
            if (pendingTgItem) {
                sendToTelegram(pendingTgItem.url, pendingTgItem.is_audio, false, pendingTgItem.title, pendingTgItem.uploader, pendingTgItem.duration, pendingTgItem.thumb);
            }
        }

        async function sendToTelegram(fileUrl, isAudio, auto = false, title = "مقطع", performer = "PlayZone", duration = 0, thumb = "") {
            const chatId = localStorage.getItem('pz_tg_id');
            if (!chatId) {
                if (auto) showToast("لم يتم إعداد تيليجرام للأتمتة", "error");
                return;
            }

            showToast("🚀 جاري الإرسال لتيليجرام...", "success");

            try {
                const payload = {
                    file_url: fileUrl,
                    chat_id: chatId.toString(),
                    is_audio: isAudio,
                    title: title,
                    performer: performer,
                    duration: duration || 0,
                    thumb: thumb || ""
                };

                const res = await fetch('/api/send_telegram', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    showToast("🎉 تم الإرسال لمحرك البوت بنجاح!", "success");
                } else {
                    showToast("❌ خطأ: " + data.error, "error");
                }
            } catch(e) {
                showToast("حدث خطأ في الاتصال بالخادم", "error");
            }
        }

        function watchVideo(url) {
            window.open(url, '_blank');
        }

        async function processInput() {
            const input = document.getElementById('url').value.trim(); 
            if(!input) return;
            const btn = document.getElementById('mainBtn'); 
            const origHtml = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري...'; 
            btn.disabled = true;
            
            document.getElementById('previewBox').classList.add('hidden');
            
            if (input.startsWith('http')) {
                await renderPreview(input);
            } else {
                try {
                    const res = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                    if(!res.ok) throw new Error();
                    const data = await res.json();
                    
                    if(data.success && data.entries.length) {
                        let box = document.getElementById('searchResultsList'); box.innerHTML = '';
                        
                        data.entries.forEach((v) => {
                            const duration = formatTime(v.duration || 0);
                            const safeThumb = v.thumbnail || 'https://via.placeholder.com/150';
                            const safeTitle = v.title.replace(/'/g, "&apos;").replace(/"/g, "&quot;");
                            const safeUploader = (v.uploader || 'غير معروف').replace(/'/g, "&apos;").replace(/"/g, "&quot;");
                            
                            box.innerHTML += `
                            <div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="w-full flex items-center p-3 bg-surfaceSolid rounded-2xl border border-surfaceBorder cursor-pointer hover:border-brand/50 hover:bg-brand/5 transition-all active:scale-[0.98] mb-2 group">
                                <div class="flex-shrink-0 w-28 h-16 rounded-xl overflow-hidden relative shadow-md">
                                    <img src="${safeThumb}" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500">
                                    <div class="absolute bottom-1 right-1 bg-black/80 backdrop-blur text-white text-[10px] px-1.5 py-0.5 rounded font-mono font-bold">${duration}</div>
                                </div>
                                <div class="flex-1 min-w-0 flex flex-col justify-center text-right pr-4">
                                    <h4 class="text-white font-bold text-sm md:text-base truncate w-full mb-1 group-hover:text-brand transition-colors" dir="auto">${safeTitle}</h4>
                                    <p class="text-textMuted text-xs truncate w-full" dir="auto">
                                        <i class="fas fa-user-circle text-brand/70 mr-1"></i> ${safeUploader}
                                    </p>
                                </div>
                                <div class="flex-shrink-0 w-10 h-10 rounded-full bg-brand/10 border border-brand/20 flex items-center justify-center text-brand mr-2 opacity-50 group-hover:opacity-100 group-hover:bg-brand group-hover:text-white transition-all">
                                    <i class="fas fa-download text-sm -ml-0.5"></i>
                                </div>
                            </div>`;
                        });
                        document.getElementById('searchResults').classList.remove('hidden');
                        document.getElementById('searchResults').scrollIntoView({behavior: 'smooth', block: 'start'});
                    } else showToast("لم يتم العثور على نتائج متوافقة", "error");
                } catch(e) { showToast("خطأ في الاتصال بالسيرفر", "error"); }
            }
            btn.innerHTML = origHtml; btn.disabled = false;
        }

        async function renderPreview(url) {
            currentUrl = url; 
            document.getElementById('searchResults').classList.add('hidden');
            document.getElementById('previewBox').classList.add('hidden'); 
            document.getElementById('progressBox').classList.add('hidden');
            document.getElementById('dlOptions').classList.remove('hidden'); 
            try {
                const res = await fetch('/api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
                if(!res.ok) throw new Error();
                const data = await res.json();
                if(data.success) {
                    document.getElementById('previewBox').classList.remove('hidden');
                    document.getElementById('thumb').src = data.thumb;
                    document.getElementById('title').innerText = data.title;
                    document.getElementById('adGate').classList.remove('hidden');
                    document.getElementById('dlOptions').classList.add('hidden');
                    let vBtn = document.getElementById('verifyBtn');
                    vBtn.disabled = true; vBtn.onclick = null;
                    vBtn.className = "btn bg-surfaceSolid text-textMuted flex-1 cursor-not-allowed border border-surfaceBorder";
                    vBtn.innerHTML = '<i class="fas fa-lock"></i> 2. التحقق'; adWatched = false;
                    document.getElementById('previewBox').scrollIntoView({behavior: 'smooth', block: 'center'});
                } else showToast("الرابط محمي أو غير متاح حالياً", "error");
            } catch(e) { showToast("حدث خطأ أثناء جلب الرابط", "error"); }
        }

        function toggleRes() { 
            document.getElementById('resContainer').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; 
        }

        function startAdTimer() {
            if(adWatched) return;
            let btn = document.getElementById('verifyBtn'); let timeLeft = 6;
            btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`;
            let timer = setInterval(() => {
                timeLeft--;
                if(timeLeft > 0) btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`; 
                else {
                    clearInterval(timer); btn.disabled = false; btn.onclick = unlockDownload; 
                    btn.className = "btn bg-green-500 text-bgBase hover:bg-green-400 shadow-lg shadow-green-500/30 flex-1";
                    btn.innerHTML = "<i class='fas fa-unlock-alt'></i> 2. فك القفل للتحميل"; adWatched = true;
                }
            }, 1000);
        }

        function unlockDownload() {
            if(!adWatched) return;
            document.getElementById('adGate').classList.add('hidden'); 
            document.getElementById('dlOptions').classList.remove('hidden');
        }

        async function startDownload() {
            const btn = event.currentTarget; const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التجهيز...'; btn.disabled = true;
            document.getElementById('dlOptions').classList.add('hidden'); 
            document.getElementById('progressBox').classList.remove('hidden');
            
            document.getElementById('directDownloadArea').classList.add('hidden');
            
            document.getElementById('progPercent').innerText = '0%';
            document.getElementById('progBar').style.width = '0%';
            document.getElementById('progSize').innerText = '-- / --';
            document.getElementById('progSpeed').innerText = '--';
            document.getElementById('progStatus').innerHTML = '<i class="fas fa-cloud-download-alt"></i> جاري الاتصال بالسيرفر...';

            const mode = document.getElementById('mode').value, resVal = document.getElementById('resolution').value;
            try {
                const res = await fetch('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:currentUrl, mode:mode, resolution:resVal})});
                if(!res.ok) throw new Error();
                const data = await res.json();
                if(data.success) {
                    const interval = setInterval(async ()=>{
                        try {
                            const progRes = await fetch(`/api/progress/${data.job_id}`); const prog = await progRes.json();
                            if(prog.status === 'downloading') {
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التحميل...';
                                document.getElementById('progPercent').innerText = prog.percent + '%';
                                document.getElementById('progBar').style.width = prog.percent + '%';
                                document.getElementById('progSize').innerText = prog.dl_mb + ' / ' + prog.total_mb;
                                document.getElementById('progSpeed').innerText = prog.spd_mb;
                            } 
                            else if(prog.status === 'converting') { 
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-cog fa-spin"></i> جاري دمج ومعالجة الملف...'; 
                                document.getElementById('progBar').style.width = '100%'; 
                                document.getElementById('progPercent').innerText = '99%';
                            } 
                            else if(prog.status === 'completed') {
                                clearInterval(interval); 
                                document.getElementById('progStatus').innerHTML = '<span class="text-green-400"><i class="fas fa-check-circle"></i> اكتمل التحميل بنجاح!</span>';
                                document.getElementById('progPercent').innerText = '100%';
                                
                                const dlArea = document.getElementById('directDownloadArea');
                                const dlBtn = document.getElementById('directDownloadBtn');
                                dlBtn.href = prog.url;
                                const extension = prog.is_audio ? '.mp3' : '.mp4';
                                dlBtn.setAttribute('download', prog.title + extension);
                                dlArea.classList.remove('hidden');
                                
                                myLibrary.unshift({ 
                                    id: Date.now().toString(), title: prog.title, url: prog.url, thumb: prog.thumb, 
                                    uploader: prog.uploader, duration: prog.duration,
                                    is_audio: prog.is_audio, timestamp: Date.now(), favorite: false 
                                });
                                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                                
                                if(document.getElementById('libraryView').classList.contains('active')) applyFilters();
                                showToast("أضيف الملف إلى مكتبتك بنجاح 📁", "success");

                                if(localStorage.getItem('pz_auto_tg') !== 'false') {
                                    sendToTelegram(prog.url, prog.is_audio, true, prog.title, prog.uploader, prog.duration, prog.thumb);
                                }
                            } 
                            else if(prog.status === 'error') { 
                                clearInterval(interval); 
                                document.getElementById('progStatus').innerHTML = '<span class="text-red-500"><i class="fas fa-exclamation-triangle"></i> فشل التحميل. الرابط غير مدعوم أو محمي.</span>'; 
                            }
                        } catch(err) {}
                    }, 800);
                }
            } catch(e) { showToast("فقدان الاتصال بالخادم الرئيسي", "error"); }
            btn.innerHTML = original; btn.disabled = false;
        }

        function playAudioTrack(index) {
            currentPlayingIndex = index;
            const track = myLibrary[index];
            if (!track || !track.is_audio) return;
            
            audioEl.src = track.url;
            audioEl.play().catch(e => {
                showToast("عذراً، الملف الصوتي غير متاح للتشغيل حالياً", "error");
            });
            
            document.getElementById('playerTitle').innerText = track.title;
            const safeThumb = track.thumb || 'https://via.placeholder.com/150';
            document.getElementById('playerThumbPlaceholder').innerHTML = `<img src="${safeThumb}" class="w-full h-full object-cover">`;
            
            document.getElementById('musicPlayer').classList.add('active');
            
            const btnMob = document.getElementById('playPauseBtnMob');
            const btnDesk = document.getElementById('playPauseBtnDesk');
            if(btnMob) btnMob.innerHTML = '<i class="fas fa-pause text-brand"></i>';
            if(btnDesk) btnDesk.innerHTML = '<i class="fas fa-pause"></i>';
            
            applyFilters();
        }

        function togglePlay() {
            if (audioEl.paused) {
                audioEl.play().catch(e => {});
            } else {
                audioEl.pause();
            }
        }

        function handleAudioPlay() {
            const btnMob = document.getElementById('playPauseBtnMob');
            const btnDesk = document.getElementById('playPauseBtnDesk');
            if(btnMob) btnMob.innerHTML = '<i class="fas fa-pause text-brand"></i>';
            if(btnDesk) btnDesk.innerHTML = '<i class="fas fa-pause"></i>';
            document.getElementById('playerThumbPlaceholder').classList.add('animate-pulse-glow');
            applyFilters();
        }

        function handleAudioPause() {
            const btnMob = document.getElementById('playPauseBtnMob');
            const btnDesk = document.getElementById('playPauseBtnDesk');
            if(btnMob) btnMob.innerHTML = '<i class="fas fa-play ml-0.5 text-brand"></i>';
            if(btnDesk) btnDesk.innerHTML = '<i class="fas fa-play ml-0.5"></i>';
            document.getElementById('playerThumbPlaceholder').classList.remove('animate-pulse-glow');
            applyFilters();
        }

        function playNext() {
            const audioTracks = myLibrary.map((t, idx) => ({t, idx})).filter(x => x.t.is_audio);
            if (audioTracks.length === 0) return;

            if (isShuffle) {
                const rand = Math.floor(Math.random() * audioTracks.length);
                playAudioTrack(audioTracks[rand].idx);
            } else {
                const currentPosition = audioTracks.findIndex(x => x.idx === currentPlayingIndex);
                if (currentPosition !== -1 && currentPosition < audioTracks.length - 1) {
                    playAudioTrack(audioTracks[currentPosition + 1].idx);
                } else if (audioTracks.length > 0) {
                    playAudioTrack(audioTracks[0].idx);
                }
            }
        }

        function playPrev() {
            const audioTracks = myLibrary.map((t, idx) => ({t, idx})).filter(x => x.t.is_audio);
            if (audioTracks.length === 0) return;

            const currentPosition = audioTracks.findIndex(x => x.idx === currentPlayingIndex);
            if (currentPosition > 0) {
                playAudioTrack(audioTracks[currentPosition - 1].idx);
            } else if (audioTracks.length > 0) {
                playAudioTrack(audioTracks[audioTracks.length - 1].idx);
            }
        }

        function toggleShuffle() {
            isShuffle = !isShuffle;
            const btn = document.getElementById('shuffleBtn');
            if (isShuffle) {
                btn.classList.remove('text-textMuted');
                btn.classList.add('text-brand', 'bg-brand/10');
            } else {
                btn.classList.remove('text-brand', 'bg-brand/10');
                btn.classList.add('text-textMuted');
            }
        }

        function toggleRepeat() {
            isRepeat = !isRepeat;
            const btn = document.getElementById('repeatBtn');
            if (isRepeat) {
                btn.classList.remove('text-textMuted');
                btn.classList.add('text-brand', 'bg-brand/10');
            } else {
                btn.classList.remove('text-brand', 'bg-brand/10');
                btn.classList.add('text-textMuted');
            }
        }

        function updatePlayerProgress() {
            const cur = audioEl.currentTime;
            const dur = audioEl.duration;
            if (isNaN(dur)) return;
            
            const pct = (cur / dur) * 100;
            document.getElementById('audioProgressBar').style.width = pct + '%';
            
            const fCur = formatTime(cur);
            const fDur = formatTime(dur);
            
            document.getElementById('playerTime').innerText = fCur + ' / ' + fDur;
            
            const curTxt = document.getElementById('currTimeTxt');
            const durTxt = document.getElementById('durTimeTxt');
            if(curTxt) curTxt.innerText = fCur;
            if(durTxt) durTxt.innerText = fDur;
        }

        function seekAudio(e) {
            const container = document.getElementById('progressContainer');
            const rect = container.getBoundingClientRect();
            let clientX = e.clientX;
            if (e.touches && e.touches.length > 0) {
                clientX = e.touches[0].clientX;
            }
            const clickX = clientX - rect.left;
            const pct = Math.max(0, Math.min(1, clickX / rect.width));
            if (!isNaN(audioEl.duration)) {
                audioEl.currentTime = pct * audioEl.duration;
            }
        }
        
        function handleAudioEnd() {
            if (isRepeat) {
                audioEl.currentTime = 0;
                audioEl.play().catch(e => {});
            } else {
                playNext();
            }
        }

        function closePlayer() {
            audioEl.pause();
            currentPlayingIndex = -1;
            document.getElementById('musicPlayer').classList.remove('active');
            applyFilters();
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    # حقن المتغيرات الخاصة بالبايثون داخل قالب الـ HTML بأمان كامل
    html_content = INDEX_HTML_TEMPLATE.replace("{{BOT_USERNAME}}", BOT_USERNAME).replace("{{AD_LINK}}", AD_LINK)
    return HTMLResponse(content=html_content)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        raw_results = search_youtube(req.query, limit=25) or {}
        entries = raw_results.get("entries") or []
        
        valid_videos = []
        for entry in entries:
            if not entry:
                continue
                
            video_id = entry.get("id")
            title = entry.get("title")
            
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
            
            if len(valid_videos) == 5:
                break
                
        return {"success": True, "entries": valid_videos}
    except Exception as e: 
        return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        opts = get_hardened_ydl_options(url=req.url)
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

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook, url=url)
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
