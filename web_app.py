import os, threading, uuid, time, requests, json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

# --- إعدادات البوت والبيئة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = "MusicPlayZoneBot"  # اليوزر الثابت اليدوي للبوت الخاص بك
# -------------------------------------------------------------

try:
    from core.config import BASE_DOWNLOAD_DIR, HILLTOPADS_LINK, ADSTERRA_LINK, COOKIES_FILE
except ImportError:
    BASE_DOWNLOAD_DIR = Path("./downloads")
    HILLTOPADS_LINK = "https://bony-teaching.com/TwZD7z"
    ADSTERRA_LINK = "https://www.effectivecpmnetwork.com/jgv39bh2p?key=8ffb7ed8cb605d90c6d07e1f7a698646"
    COOKIES_FILE = Path("cookies.txt")
    def init_db(): pass
    def cookie_file_is_usable(f): return False

app = FastAPI(title="PlayZone Dashboard")

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
AD_VERIFICATIONS = {} # ذاكرة مؤقتة لتتبع حالة التحقق من الإعلانات الحقيقية

AD_LINK = HILLTOPADS_LINK if HILLTOPADS_LINK else (ADSTERRA_LINK or "https://example.com/ad")

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
                
            # تنظيف سجل الجلسات الإعلانية منتهي الصلاحية (أقدم من ساعة)
            expired_ads = [cid for cid, data in list(AD_VERIFICATIONS.items()) if now - data.get("created_at", now) > 3600]
            for cid in expired_ads:
                AD_VERIFICATIONS.pop(cid, None)
        except Exception as e:
            pass
        time.sleep(3600)

threading.Thread(target=cleanup_daemon, daemon=True).start()

class URLRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "720"
    click_id: str = ""

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
    try:
        from utils.helpers import cookie_file_is_usable
        if cookie_file_is_usable(COOKIES_FILE): opts["cookiefile"] = str(COOKIES_FILE)
    except: pass
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 25):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    if 'playlist_items' in opts: del opts['playlist_items']
    if 'noplaylist' in opts: del opts['noplaylist']
    with yt_dlp.YoutubeDL(opts) as ydl: 
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

def formatTime(secs):
    if not secs: return "0:00"
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}:{s:02d}"

# ==========================================================
# واجهة الـ HTML المصلحة بالكامل بجافا سكريبت سليمة 100%
# ==========================================================
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>PlayZone | Music </title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap" rel="stylesheet">
    <script>
        tailwind.config={
            darkMode:'class',
            theme:{
                extend:{
                    fontFamily: { sans: ['Tajawal', 'sans-serif'] }, 
                    colors:{ 
                        accent:'#a855f7', accentHover:'#9333ea', 
                        bgDark:'#030303', panel:'#0b0b0f', panelBorder:'#1f1f2e',
                        textMuted:'#8e8e9f', tgBlue: '#0088cc'
                    }
                }
            }
        }
    </script>
    <style>
        body { background-color: #030303; color: #f4f4f5; transition: all 0.3s ease; overflow: hidden; font-family: 'Tajawal', sans-serif; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #1f1f2e; border-radius: 10px; }
        
        .modern-input { background: #07070a; border: 1px solid #1f1f2e; color: white; border-radius: 1rem; padding: 0.8rem 1.2rem; outline: none; transition: all 0.3s; width: 100%; }
        .modern-input:focus { border-color: #a855f7; box-shadow: 0 0 15px rgba(168, 85, 247, 0.2); }
        
        .btn { padding: 0.8rem 1.5rem; border-radius: 1rem; font-weight: bold; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; cursor: pointer; user-select: none; position: relative; overflow: hidden; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
        .btn:active:not(:disabled) { transform: scale(0.95); }
        
        @keyframes shimmer { 0% { transform: translateX(100%); } 100% { transform: translateX(-100%); } }
        .shimmer-bg { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.25), transparent); animation: shimmer 1.2s infinite linear; }

        .view-section { display: none !important; }
        .view-section.active { display: block !important; }
        .view-section.active.flex-layout { display: flex !important; }
        
        #progBar {
            transition: width 0.6s cubic-bezier(0.1, 0.8, 0.25, 1);
            box-shadow: 0 0 12px rgba(168, 85, 247, 0.45);
        }
        
        #floatingPlayer {
            position: fixed;
            bottom: 25px;
            left: 25px;
            width: 360px;
            max-width: calc(100vw - 32px);
            background: rgba(11, 11, 15, 0.82);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 24px;
            box-shadow: 0 35px 70px rgba(0,0,0,0.85), 0 0 25px rgba(168, 85, 247, 0.15);
            z-index: 100;
            overflow: hidden;
            display: none;
            opacity: 0;
            transform: scale(0.95);
            transition: opacity 0.2s ease, transform 0.2s ease;
        }
        #floatingPlayer.active-player { display: block !important; opacity: 1; transform: scale(1); }
        #floatingPlayer.dragging-player { transform: scale(0.97) !important; opacity: 0.9 !important; border-color: rgba(168, 85, 247, 0.4) !important; box-shadow: 0 45px 90px rgba(0,0,0,0.9), 0 0 35px rgba(168, 85, 247, 0.3) !important; }
        
        .drag-handle { cursor: grab; user-select: none; touch-action: none; }
        .drag-handle:active { cursor: grabbing; }

        @keyframes spinDisk { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .album-spin { animation: spinDisk 18s linear infinite; }
        
        @keyframes bounceGpu { 0% { transform: scaleY(0.2); } 100% { transform: scaleY(1.0); } }
        .wave-bar { width: 3px; height: 24px; background-color: #a855f7; border-radius: 3px; transform-origin: bottom; transform: scaleY(0.2); transition: transform 0.15s ease; }
        .playing-visualizer .wave-bar { animation: bounceGpu 0.7s ease-in-out infinite alternate; }
        .playing-visualizer .wave-bar:nth-child(1) { animation-delay: 0.1s; animation-duration: 0.5s; }
        .playing-visualizer .wave-bar:nth-child(2) { animation-delay: 0.25s; animation-duration: 0.65s; }
        .playing-visualizer .wave-bar:nth-child(3) { animation-delay: 0.05s; animation-duration: 0.55s; }
        .playing-visualizer .wave-bar:nth-child(4) { animation-delay: 0.35s; animation-duration: 0.75s; }
        .playing-visualizer .wave-bar:nth-child(5) { animation-delay: 0.15s; animation-duration: 0.45s; }
        .playing-visualizer .wave-bar:nth-child(6) { animation-delay: 0.45s; animation-duration: 0.7s; }
        .playing-visualizer .wave-bar:nth-child(7) { animation-delay: 0.2s; animation-duration: 0.6s; }
        
        #floatingPlayer.compact-mode { width: 340px !important; height: auto !important; }
        #floatingPlayer.compact-mode #playerBody { display: none; }

        #toast { position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-100%); opacity: 0; z-index: 1000; padding: 12px 24px; border-radius: 50px; font-weight: bold; color: white; transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55); box-shadow: 0 10px 25px rgba(0,0,0,0.5); pointer-events: none; }
        #toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
    </style>
</head>
<body class="antialiased flex h-[100dvh] w-full select-none">
    <div id="toast"></div>

    <aside class="w-16 md:w-20 bg-panel border-l border-panelBorder flex flex-col justify-between h-[100dvh] z-40 flex-shrink-0">
        <div>
            <div class="h-20 flex items-center justify-center border-b border-panelBorder">
                <div class="w-10 h-10 bg-accent/10 rounded-2xl flex items-center justify-center text-accent text-xl relative group cursor-pointer">
                    <div class="absolute inset-0 bg-accent/20 rounded-2xl blur-md opacity-50"></div>
                    <i class="fas fa-play z-10 text-xs"></i>
                </div>
            </div>
            <nav class="mt-8 px-2 space-y-4">
                <button onclick="switchView('searchView')" id="nav-searchView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl bg-panelBorder text-accent transition-all duration-300" title="البحث والتحميل">
                    <i class="fas fa-search text-base"></i>
                    <span class="text-[9px] mt-1 font-bold hidden md:block">البحث</span>
                </button>
                <button onclick="switchView('libraryView')" id="nav-libraryView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl text-textMuted hover:bg-panelBorder/50 hover:text-white transition-all duration-300" title="ملفاتي">
                    <i class="fas fa-folder text-base"></i>
                    <span class="text-[9px] mt-1 font-medium hidden md:block">ملفاتي</span>
                </button>
                <button onclick="switchView('settingsView')" id="nav-settingsView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl text-textMuted hover:bg-panelBorder/50 hover:text-white transition-all duration-300" title="الإعدادات">
                    <i class="fas fa-cog text-base"></i>
                    <span class="text-[9px] mt-1 font-medium hidden md:block">الإعدادات</span>
                </button>
            </nav>
        </div>
        <div class="p-2 border-t border-panelBorder flex justify-center">
            <a href="https://t.me/{BOT_USERNAME}" target="_blank" class="w-12 h-12 rounded-2xl bg-tgBlue/10 text-tgBlue hover:bg-tgBlue/25 flex items-center justify-center transition-colors">
                <i class="fab fa-telegram-plane text-lg"></i>
            </a>
        </div>
    </aside>

    <main class="flex-1 h-[100dvh] overflow-y-auto pb-24 relative scroll-smooth bg-[#030305]">
        <section id="searchView" class="view-section active p-4 md:p-8 max-w-4xl mx-auto">
            <div class="bg-panel/60 backdrop-blur-md rounded-3xl p-6 md:p-8 border border-panelBorder/60 mb-6 relative overflow-hidden shadow-2xl mt-4">
                <h2 class="text-2xl md:text-3xl font-bold mb-2 text-white flex items-center gap-2"><span>Music</span> ⚡</h2>
                <p class="text-textMuted mb-6 text-xs md:text-sm">ابحث عن مقاطع الفيديو أو الأغاني لتحميلها وتشغيلها مباشرة.</p>
                <div class="flex flex-col md:flex-row gap-3">
                    <input type="text" id="url" placeholder="أدخل كلمة البحث أو رابط المقطع..." class="modern-input flex-1">
                    <button onclick="processInput()" id="mainBtn" class="btn bg-accent hover:bg-accentHover text-white md:w-36 shadow-lg shadow-accent/25"><i class="fas fa-search"></i> بحث</button>
                </div>
                <div id="searchResults" class="hidden mt-8 bg-black/40 border border-panelBorder rounded-2xl p-4">
                    <div class="mb-4 pb-2 border-b border-panelBorder/60 flex justify-between items-center">
                        <h3 class="text-white font-bold text-xs md:text-sm flex items-center gap-2">🎬 نتائج البحث:</h3>
                        <button onclick="document.getElementById('searchResults').classList.add('hidden')" class="text-textMuted hover:text-red-400 p-1"><i class="fas fa-times"></i></button>
                    </div>
                    <div id="searchResultsList" class="flex flex-col gap-3 w-full"></div>
                </div>
            </div>

            <!-- بطاقة المعاينة والتحميل -->
            <div id="previewBox" class="hidden bg-panel/60 backdrop-blur-md rounded-3xl p-6 border border-panelBorder/60 shadow-2xl">
                <div class="flex flex-col md:flex-row gap-6 items-center">
                    <div class="w-full md:w-1/3">
                        <img id="thumb" class="w-full rounded-2xl object-cover aspect-video shadow-lg border border-panelBorder">
                    </div>
                    <div class="w-full md:w-2/3 space-y-4">
                        <h3 id="title" class="font-bold text-sm md:text-base text-white line-clamp-2"></h3>
                        
                        <!-- نظام جدار الإعلانات الهجين المحدث -->
                        <div id="adGate" class="bg-black/50 border border-panelBorder p-4 rounded-2xl text-center">
                            <p class="text-xs mb-3 text-textMuted font-medium"><i class="fas fa-lock text-accent ml-1"></i> يرجى فتح الإعلان ليفك رابط التحميل قفله تلقائياً.</p>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <a id="realAdLink" href="#" target="_blank" onclick="startAdVerificationCheck()" class="btn bg-blue-600 text-white flex-1 hover:bg-blue-500 text-xs md:text-sm"><i class="fas fa-external-link-alt"></i> 1. فتح الإعلان</a>
                                <button id="verifyBtn" onclick="manualCheckAdStatus()" class="btn bg-panel text-textMuted flex-1 border border-panelBorder text-xs md:text-sm cursor-wait"><i class="fas fa-sync fa-spin mr-1"></i> جاري الانتظار تلقائياً...</button>
                            </div>
                        </div>

                        <!-- خيارات التحميل -->
                        <div id="dlOptions" class="hidden space-y-4">
                            <div class="grid grid-cols-2 gap-3">
                                <select id="mode" onchange="toggleRes()" class="modern-input bg-black/60 py-2.5 text-xs md:text-sm"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                                <select id="resolution" class="modern-input bg-black/60 py-2.5 text-xs md:text-sm">
                                    <option value="360">جودة 360p</option>
                                    <option value="480">جودة 480p</option>
                                    <option value="720" selected>جودة 720p</option>
                                    <option value="1080">جودة 1080p</option>
                                </select>
                            </div>
                            <button onclick="startDownload()" class="btn bg-gradient-to-r from-accent to-fuchsia-600 text-white w-full hover:from-accentHover hover:to-fuchsia-700 shadow-xl shadow-accent/20 text-xs md:text-sm">
                                <i class="fas fa-download"></i> بدء التحميل
                            </button>
                        </div>

                        <!-- صندوق تقدم التحميل المطور -->
                        <div id="progressBox" class="hidden bg-black/50 p-5 rounded-2xl border border-panelBorder">
                            <div class="flex justify-between items-center mb-3">
                                <span id="progStatus" class="text-accent font-bold text-xs flex items-center gap-2"><i class="fas fa-circle-notch fa-spin"></i> جاري التحميل...</span>
                                <span id="progPercent" class="font-mono font-bold text-white text-sm transition-all duration-300 transform scale-100">0%</span>
                            </div>
                            <div class="w-full bg-zinc-900 rounded-full h-2.5 mb-2 overflow-hidden relative">
                                <div id="progBar" class="bg-gradient-to-r from-accent to-fuchsia-500 h-full relative w-0">
                                    <div class="shimmer-bg"></div>
                                </div>
                            </div>
                            <div class="flex justify-between text-[10px] text-textMuted font-mono">
                                <span id="progSize">-- MB / -- MB</span>
                                <span id="progSpeed">-- MB/s</span>
                            </div>
                            <div id="directDownloadArea" class="hidden mt-4 pt-4 border-t border-panelBorder/60">
                                <a id="directDownloadBtn" href="#" download class="btn bg-emerald-600 text-white w-full hover:bg-emerald-500 shadow-lg shadow-emerald-600/20 text-xs"><i class="fas fa-arrow-alt-circle-down"></i> تحميل إلى جعلزك 💾</a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- قسم ملفاتي المحفوظة -->
        <section id="libraryView" class="view-section flex-layout p-4 md:p-8 max-w-6xl mx-auto h-full flex flex-col">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6 mt-2">
                <div>
                    <h2 class="text-xl md:text-2xl font-bold text-white">ملفاتي المحفوظة 📁</h2>
                    <p class="text-textMuted text-xs mt-0.5">الملفات التي قمت بتحميلها مسبقاً.</p>
                </div>
                <div class="flex flex-wrap gap-2 w-full md:w-auto">
                    <div class="relative flex-1 md:w-64">
                        <i class="fas fa-search absolute right-3.5 top-1/2 transform -translate-y-1/2 text-textMuted text-xs"></i>
                        <input type="text" id="libSearch" oninput="applyFilters()" placeholder="بحث في ملفاتك..." class="modern-input pl-3 pr-10 py-1.5 bg-panel/60 text-xs">
                    </div>
                    <select id="libFilter" onchange="applyFilters()" class="modern-input py-1.5 px-4 w-auto bg-panel text-accent font-bold text-xs">
                        <option value="all">الكل</option>
                        <option value="favorites">❤️ المفضلة</option>
                        <option value="audio">🎵 الصوتيات</option>
                        <option value="video">🎬 الفيديوهات</option>
                    </select>
                </div>
            </div>
            <div id="libraryContainer" class="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 content-start"></div>
            <div id="pagination" class="mt-8 flex justify-center items-center gap-3 pb-8"></div>
        </section>

        <!-- قسم الإعدادات -->
        <section id="settingsView" class="view-section p-4 md:p-8 max-w-3xl mx-auto">
            <h2 class="text-xl md:text-2xl font-bold mb-6 text-white mt-2">الإعدادات ⚙️</h2>
            <div class="space-y-6">
                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-2xl">
                    <h3 class="text-sm font-bold text-white mb-2 flex items-center gap-2"><i class="fab fa-telegram text-tgBlue text-base"></i> ربط حساب تيليجرام</h3>
                    <p class="text-textMuted text-xs mb-4">أدخل معرف حسابك (User ID) لتلقي الملفات مباشرة عبر البوت.</p>
                    <div class="flex gap-3 mb-5">
                        <input type="text" id="settingTgId" placeholder="أدخل الـ User ID هنا..." class="modern-input font-mono bg-black/45 text-xs md:text-sm">
                        <button onclick="updateTgId()" class="btn bg-tgBlue hover:bg-opacity-90 text-white px-6 text-xs md:text-sm">حفظ</button>
                    </div>
                    <div class="flex items-center justify-between p-4 bg-black/40 rounded-2xl border border-panelBorder">
                        <div>
                            <p class="font-bold text-white text-xs md:text-sm">إرسال تلقائي للبوت</p>
                            <p class="text-[11px] text-textMuted mt-0.5">إرسال الملف إلى حسابك فور اكتمال تحميله.</p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="autoForwardToggle" onchange="toggleAutoForward()" class="sr-only peer" checked>
                            <div class="w-11 h-6 bg-zinc-800 rounded-full peer peer-checked:after:-translate-x-full peer-checked:bg-accent after:content-[''] after:absolute after:top-[2px] after:right-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all"></div>
                        </label>
                    </div>
                </div>
                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-2xl">
                    <h3 class="text-sm font-bold text-white mb-2 flex items-center gap-2"><i class="fas fa-database text-red-400"></i> إدارة الذاكرة التخزينية</h3>
                    <div class="flex justify-between items-center p-4 bg-black/40 rounded-2xl border border-panelBorder">
                        <div>
                            <p class="font-bold text-white text-xs md:text-sm" id="libCountStatus">سجل الملفات (0)</p>
                            <p class="text-[11px] text-textMuted mt-0.5">مسح سجل الملفات المحفوظة محلياً في هذا المتصفح.</p>
                        </div>
                        <button onclick="clearAllLibrary()" class="btn bg-red-500/10 text-red-500 hover:bg-red-500/20 text-xs px-4">مسح الذاكرة بالكامل</button>
                    </div>
                </div>
            </div>
        </section>
    </main>

    <!-- مشغل عائم -->
    <div id="floatingPlayer">
        <div id="playerHeader" class="drag-handle p-3 bg-black/60 border-b border-panelBorder/50 flex items-center justify-between">
            <div class="flex items-center gap-2 overflow-hidden flex-1">
                <i class="fas fa-grip-vertical text-textMuted opacity-60 cursor-grab text-[11px]"></i>
                <div class="overflow-hidden w-full">
                    <p id="playerTitle" class="font-bold text-[11px] text-white truncate w-full">جاهز للتشغيل</p>
                </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0 mr-2">
                <button onclick="toggleCompactMode(event)" class="text-textMuted hover:text-accent p-1 transition-colors transition-transform active:scale-90" title="تصغير"><i class="fas fa-compress-alt text-xs"></i></button>
                <button onclick="resetPlayerPosition()" class="text-textMuted hover:text-white p-1 transition-colors transition-transform active:scale-90" title="إعادة تعيين الموضع"><i class="fas fa-location-arrow text-[10px]"></i></button>
                <button onclick="closePlayer()" class="text-textMuted hover:text-red-400 p-1 transition-colors transition-transform active:scale-90"><i class="fas fa-times text-xs"></i></button>
            </div>
        </div>
        <div id="playerBody" class="p-4 select-none">
            <div id="videoContainer" class="hidden w-full aspect-video rounded-xl overflow-hidden bg-black mb-3 border border-panelBorder/80">
                <video id="globalVideoElement" class="w-full h-full object-contain" ontimeupdate="updatePlayerProgress()" onended="handleMediaEnd()"></video>
            </div>
            <div id="audioVisualizer" class="flex flex-col items-center justify-center py-2 mb-1">
                <div class="relative w-24 h-24 rounded-full border-2 border-zinc-800 shadow-2xl overflow-hidden mb-3 bg-zinc-950">
                    <img id="playerCoverImg" src="https://via.placeholder.com/150" class="w-full h-full object-cover album-spin">
                    <div class="absolute inset-0 bg-gradient-to-tr from-black/40 to-transparent"></div>
                    <div class="absolute w-5 h-5 bg-zinc-900 rounded-full top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 border border-zinc-700 flex items-center justify-center">
                        <div class="w-1 h-1 bg-white rounded-full"></div>
                    </div>
                </div>
                <div id="visualizerBars" class="flex gap-1 items-end h-4 justify-center mt-1">
                    <div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div>
                </div>
            </div>
            <div class="relative w-full h-1.5 bg-zinc-800 rounded-full mb-3 cursor-pointer group" id="progressContainer" dir="ltr">
                <div id="mediaProgressBar" class="absolute left-0 h-full bg-accent rounded-full w-0 pointer-events-none"></div>
                <div id="mediaProgressSlider" class="absolute w-3 h-3 bg-white border-2 border-accent rounded-full -top-[3px] -ml-[6px] scale-100 group-hover:scale-125 transition-transform pointer-events-none" style="left: 0%;"></div>
            </div>
            <div class="flex justify-between items-center text-[10px] text-textMuted font-mono mb-4">
                <span id="playerTime">0:00 / 0:00</span>
                <span id="trackSource" class="bg-accent/10 text-accent px-2 py-0.5 rounded-md text-[9px] font-bold">صوت</span>
            </div>
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-3">
                    <button onclick="toggleShuffle()" id="shuffleBtn" class="text-textMuted hover:text-white transition-colors text-xs" title="عشوائي"><i class="fas fa-random"></i></button>
                    <button onclick="playPrev()" class="text-white hover:text-accent transition-transform active:scale-90 text-sm" title="السابق"><i class="fas fa-step-backward"></i></button>
                </div>
                <button onclick="togglePlay()" id="playPauseBtn" class="w-11 h-11 rounded-full bg-accent hover:bg-accentHover text-white flex items-center justify-center text-sm shadow-md shadow-accent/30 active:scale-90 transition-all"><i class="fas fa-play ml-0.5"></i></button>
                <div class="flex items-center gap-3">
                    <button onclick="playNext()" class="text-white hover:text-accent transition-transform active:scale-90 text-sm" title="التالي"><i class="fas fa-step-forward"></i></button>
                    <button onclick="toggleRepeat()" id="repeatBtn" class="text-textMuted hover:text-white transition-colors text-xs" title="تكرار"><i class="fas fa-redo"></i></button>
                </div>
            </div>
            <div class="flex items-center justify-between mt-4 pt-3 border-t border-panelBorder/40">
                <button onclick="changeSpeed()" id="speedBtn" class="text-[9px] font-bold font-mono px-2 py-0.5 border border-panelBorder rounded-md text-textMuted hover:text-white">1.0x</button>
                <div class="flex items-center gap-2">
                    <button onclick="toggleMute()" id="muteBtn" class="text-textMuted hover:text-white"><i class="fas fa-volume-up text-xs"></i></button>
                    <input type="range" id="volumeSlider" min="0" max="1" step="0.05" value="1" oninput="changeVolume()" class="w-16 h-1 bg-zinc-800 accent-accent rounded-lg cursor-pointer">
                </div>
                <button onclick="triggerPiP()" id="pipBtn" class="text-textMuted hover:text-white hidden" title="تشغيل مصغر"><i class="fas fa-clone text-xs"></i></button>
            </div>
        </div>
    </div>

    <!-- نافذة ربط حساب تيليجرام -->
    <div id="tgModal" class="fixed inset-0 bg-black/85 backdrop-blur-md z-[200] hidden flex-col items-center justify-center p-4">
        <div class="bg-panel border border-panelBorder p-6 rounded-3xl max-w-sm w-full text-center shadow-2xl" id="tgModalContent">
            <div class="w-14 h-14 bg-tgBlue/20 text-tgBlue rounded-2xl flex items-center justify-center mx-auto mb-4 text-2xl"><i class="fab fa-telegram-plane"></i></div>
            <h3 class="text-base font-bold mb-1 text-white">ربط حساب Telegram</h3>
            <p class="text-textMuted text-xs mb-5">يرجى إدخال معرف حسابك (User ID) لتفعيل خاصية الإرسال التلقائي عبر البوت.</p>
            <button onclick="window.open('https://t.me/{BOT_USERNAME}', '_blank')" class="btn bg-tgBlue text-white w-full mb-3 text-xs"><i class="fas fa-robot"></i> 1. الدخول للبوت ونسخ المعرف</button>
            <input type="text" id="tgIdInput" placeholder="2. الصق المعرف هنا..." class="modern-input bg-black/60 text-center text-xs mb-4 font-mono py-2">
            <div class="flex gap-2">
                <button onclick="saveTgIdFromModal()" class="btn bg-accent text-white flex-1 text-xs py-2">حفظ ومتابعة</button>
                <button onclick="closeTgModal()" class="btn bg-panelBorder text-textMuted flex-1 hover:text-white text-xs py-2">إلغاء</button>
            </div>
        </div>
    </div>

    <script>
        const BACKEND_URL = window.location.origin;

        let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
        let currentUrl = "";
        let currentClickId = "";
        let adCheckInterval = null;
        let adFallbackTimeout = null; 
        let currentPlayingIndex = -1;
        let isShuffle = false;
        let isRepeat = false;
        let libraryPage = 1;
        const itemsPerPage = 6;
        let isMuted = false;
        let lastVolume = 1;
        let currentPlayingMode = 'audio';
        let isScrubbing = false;
        let lastLoggedPercent = 0;

        const mediaContainer = document.getElementById('floatingPlayer');
        const videoElement = document.getElementById('globalVideoElement');

        window.addEventListener('DOMContentLoaded', () => {
            if (window.Telegram && window.Telegram.WebApp) {
                window.Telegram.WebApp.ready();
                window.Telegram.WebApp.expand();
                const tgUser = window.Telegram.WebApp.initDataUnsafe.user;
                if (tgUser && tgUser.id) {
                    localStorage.setItem('pz_tg_id', tgUser.id);
                    showToast("تم ربط حساب تيليجرام تلقائياً 🛡️", "success");
                }
            }
            const savedId = localStorage.getItem('pz_tg_id') || "";
            document.getElementById('settingTgId').value = savedId;
            document.getElementById('tgIdInput').value = savedId;
            const autoFwd = localStorage.getItem('pz_auto_tg') !== 'false';
            document.getElementById('autoForwardToggle').checked = autoFwd;

            updateLibraryCount();
            switchView('searchView');
            setupAdvancedDraggable(mediaContainer, document.getElementById('playerHeader'));
            setupScrubbing();
        });

        function formatTime(secs) { 
            if(isNaN(secs) || secs === null) return "0:00"; 
            const m = Math.floor(secs / 60), s = Math.floor(secs % 60); 
            return m + ":" + (s < 10 ? '0' + s : s); 
        }

        function showToast(message, type = "success") {
            const toast = document.getElementById('toast');
            toast.innerText = message; toast.className = "show";
            if (type === "error") toast.style.background = "linear-gradient(135deg, #f43f5e, #e11d48)";
            else toast.style.background = "linear-gradient(135deg, #a855f7, #7c3aed)";
            setTimeout(() => { toast.className = ""; }, 3000);
        }

        function switchView(viewId) {
            document.querySelectorAll('.view-section').forEach(el => {
                el.style.setProperty('display', 'none', 'important'); el.classList.remove('active');
            });
            document.querySelectorAll('.nav-btn').forEach(btn => {
                btn.classList.remove('bg-panelBorder', 'text-accent');
                btn.classList.add('text-textMuted', 'hover:bg-panelBorder/50', 'hover:text-white');
            });
            const targetSection = document.getElementById(viewId);
            if (targetSection) {
                if (targetSection.classList.contains('flex-layout')) targetSection.style.setProperty('display', 'flex', 'important');
                else targetSection.style.setProperty('display', 'block', 'important');
                targetSection.classList.add('active');
            }
            const activeBtn = document.getElementById('nav-' + viewId);
            if (activeBtn) {
                activeBtn.classList.remove('text-textMuted', 'hover:bg-panelBorder/50', 'hover:text-white');
                activeBtn.classList.add('bg-panelBorder', 'text-accent');
            }
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
            const totalItems = filtered.length; const totalPages = Math.ceil(totalItems / itemsPerPage);
            if (libraryPage > totalPages) libraryPage = Math.max(1, totalPages);
            const start = (libraryPage - 1) * itemsPerPage; const pageItems = filtered.slice(start, start + itemsPerPage);
            const container = document.getElementById('libraryContainer'); container.innerHTML = "";
            if (pageItems.length === 0) {
                container.innerHTML = `<div class="col-span-full py-16 text-center text-textMuted"><i class="fas fa-folder-open text-4xl mb-4 text-zinc-800 block"></i> لا توجد ملفات حالياً.</div>`;
                document.getElementById('pagination').innerHTML = ""; return;
            }
            pageItems.forEach((item) => {
                const actualIndex = myLibrary.findIndex(i => i.id === item.id);
                const durationStr = formatTime(item.duration || 0);
                const favClass = item.favorite ? 'fas fa-heart text-red-500' : 'far fa-heart';
                const icon = item.is_audio ? '<i class="fas fa-music text-accent"></i>' : '<i class="fas fa-video text-fuchsia-500"></i>';
                const fileExt = item.is_audio ? 'mp3' : 'mp4';
                container.innerHTML += `
                    <div class="bg-panel/50 rounded-2xl p-4 border border-panelBorder/60 flex gap-4 items-center relative group hover:border-accent/40 transition-all duration-300">
                        <div class="relative w-24 h-16 rounded-xl overflow-hidden border border-panelBorder/60 flex-shrink-0 cursor-pointer" onclick="playMediaTrack(${actualIndex})">
                            <img src="${item.thumb || 'https://via.placeholder.com/150'}" class="w-full h-full object-cover">
                            <div class="absolute inset-0 bg-black/60 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"><i class="fas fa-play text-white text-sm"></i></div>
                            <div class="absolute bottom-1 right-1 bg-black/80 text-[9px] px-1.5 font-mono rounded text-white">${durationStr}</div>
                        </div>
                        <div class="flex-1 min-w-0 text-right">
                            <h4 class="text-white font-bold text-xs md:text-sm truncate cursor-pointer hover:text-accent transition-colors" onclick="playMediaTrack(${actualIndex})">${item.title}</h4>
                            <p class="text-textMuted text-[10px] md:text-xs mt-1 truncate">${icon} ${item.uploader || 'غير معروف'}</p>
                        </div>
                        <div class="flex items-center gap-1.5 flex-row-reverse">
                            <button onclick="deleteFromLibrary('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-red-400 active:scale-90 transition-all"><i class="fas fa-trash-alt text-xs"></i></button>
                            <a href="${item.url}" download="${item.title}.${fileExt}" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-emerald-400 active:scale-90 transition-all flex items-center justify-center"><i class="fas fa-download text-xs"></i></a>
                            <button onclick="triggerSendToTelegram('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-tgBlue active:scale-90 transition-all"><i class="fab fa-telegram-plane text-xs"></i></button>
                            <button onclick="toggleFavorite('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-red-500 active:scale-90 transition-all"><i class="${favClass} text-xs"></i></button>
                        </div>
                    </div>`;
            });
            renderPagination(totalPages);
        }

        function renderPagination(totalPages) {
            const pagBox = document.getElementById('pagination'); pagBox.innerHTML = ""; if (totalPages <= 1) return;
            let html = '<button onclick="changePage(' + (libraryPage - 1) + ')" ' + (libraryPage === 1 ? 'disabled' : '') + ' class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">السابق</button>';
            for (let i = 1; i <= totalPages; i++) {
                const activeClass = (libraryPage === i) ? 'bg-accent text-white' : 'bg-panel border border-panelBorder text-textMuted';
                html += '<button onclick="changePage(' + i + ')" class="btn px-3 py-1.5 ' + activeClass + ' text-xs font-mono">' + i + '</button>';
            }
            html += '<button onclick="changePage(' + (libraryPage + 1) + ')" ' + (libraryPage === totalPages ? 'disabled' : '') + ' class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">التالي</button>';
            pagBox.innerHTML = html;
        }

        function changePage(page) { libraryPage = page; applyFilters(); }
        
        function toggleFavorite(id) {
            const index = myLibrary.findIndex(i => i.id === id);
            if (index !== -1) {
                myLibrary[index].favorite = !myLibrary[index].favorite;
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters();
                showToast(myLibrary[index].favorite ? "تمت الإضافة للمفضلة ❤️" : "تمت الإزالة من المفضلة", "success");
            }
        }

        function deleteFromLibrary(id) {
            if (confirm("هل تريد حذف هذا الملف؟")) {
                myLibrary = myLibrary.filter(i => i.id !== id); localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters(); updateLibraryCount(); showToast("تم الحذف بنجاح", "success");
            }
        }

        function updateLibraryCount() { document.getElementById('libCountStatus').innerText = "سجل الملفات (" + myLibrary.length + ")"; }

        function clearAllLibrary() {
            if (confirm("هل تود مسح السجل بالكامل؟")) {
                myLibrary = []; localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters(); updateLibraryCount(); showToast("تم مسح السجل", "success");
            }
        }

        function updateTgId() {
            const tgId = document.getElementById('settingTgId').value.trim();
            if (!tgId) { localStorage.removeItem('pz_tg_id'); showToast("تمت إزالة المعرف", "success"); }
            else { localStorage.setItem('pz_tg_id', tgId); showToast("تم الحفظ بنجاح", "success"); }
        }

        function toggleAutoForward() {
            const val = document.getElementById('autoForwardToggle').checked;
            localStorage.setItem('pz_auto_tg', val ? 'true' : 'false');
            showToast(val ? "تم تفعيل الإرسال التلقائي" : "تم تعطيل الإرسال التلقائي", "success");
        }

        let pendingTgItem = null;
        function triggerSendToTelegram(id) {
            const item = myLibrary.find(i => i.id === id); if (!item) return;
            const tgId = localStorage.getItem('pz_tg_id');
            if (!tgId) {
                pendingTgItem = item; document.getElementById('tgIdInput').value = "";
                document.getElementById('tgModal').classList.replace('hidden', 'flex');
            } else {
                sendToTelegram(item.url, item.is_audio, false, item.title, item.uploader, item.duration, item.thumb);
            }
        }

        function closeTgModal() { document.getElementById('tgModal').classList.replace('flex', 'hidden'); pendingTgItem = null; }
        
        function saveTgIdFromModal() {
            const val = document.getElementById('tgIdInput').value.trim(); if (!val) return showToast("أدخل معرف صالح", "error");
            localStorage.setItem('pz_tg_id', val); document.getElementById('settingTgId').value = val;
            closeTgModal(); showToast("تم الحفظ بنجاح", "success");
            if (pendingTgItem) sendToTelegram(pendingTgItem.url, pendingTgItem.is_audio, false, pendingTgItem.title, pendingTgItem.uploader, pendingTgItem.duration, pendingTgItem.thumb);
        }

        async function sendToTelegram(fileUrl, isAudio, auto = false, title = "مقطع", performer = "PlayZone", duration = 0, thumb = "") {
            const chatId = localStorage.getItem('pz_tg_id'); if (!chatId) return;
            showToast(auto ? "جاري الإرسال لبوت تيليجرام تلقائياً..." : "جاري إرسال الملف إلى تيليجرام...", "success");
            try {
                const res = await fetch(`${BACKEND_URL}/api/send_telegram`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_url: fileUrl, chat_id: chatId.toString(), is_audio: isAudio, title: title, performer: performer, duration: duration || 0, thumb: thumb || "" })
                });
                const data = await res.json();
                if (data.success) showToast("تم إرسال الملف إلى حسابك بنجاح! 🎉", "success");
                else showToast("فشل الإرسال للبوت: " + data.error, "error");
            } catch(e) { showToast("فشل الاتصال بالخادم لإرسال تيليجرام", "error"); }
        }

        async function processInput() {
            const input = document.getElementById('url').value.trim(); if(!input) return;
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري...'; btn.disabled = true;
            document.getElementById('previewBox').classList.add('hidden');
            if (input.startsWith('http')) { await renderPreview(input); } 
            else {
                try {
                    const res = await fetch(`${BACKEND_URL}/api/search`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                    const data = await res.json();
                    if(data.success && data.entries.length) {
                        let box = document.getElementById('searchResultsList'); box.innerHTML = '';
                        data.entries.forEach((v) => {
                            box.innerHTML += `
                            <div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="w-full flex items-center p-3 bg-panel border border-panelBorder rounded-2xl cursor-pointer hover:border-accent/50 transition-all active:scale-[0.98] shadow-md mb-1">
                                <div class="flex-shrink-0 w-24 h-14 rounded-xl overflow-hidden border border-panelBorder relative ml-3">
                                    <img src="${v.thumbnail}" class="w-full h-full object-cover">
                                    <div class="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono">${formatTime(v.duration || 0)}</div>
                                </div>
                                <div class="flex-1 min-w-0 flex flex-col justify-center text-right">
                                    <h4 class="text-white font-bold text-xs md:text-sm truncate w-full mb-1">${v.title}</h4>
                                    <p class="text-textMuted text-[10px] md:text-xs truncate w-full"><i class="fas fa-user-circle text-accent/70 ml-1"></i> ${v.uploader}</p>
                                </div>
                                <div class="flex-shrink-0 w-8 h-8 rounded-full bg-black/40 border border-panelBorder flex items-center justify-center text-accent mr-2"><i class="fas fa-download text-xs"></i></div>
                            </div>`;
                        });
                        document.getElementById('searchResults').classList.remove('hidden');
                    } else showToast("لم يتم العثور على نتائج", "error");
                } catch(e) { showToast("حدث خطأ في البحث", "error"); }
            }
            btn.innerHTML = '<i class="fas fa-search"></i> بحث'; btn.disabled = false;
        }

        async function renderPreview(url) {
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden');
            document.getElementById('previewBox').classList.add('hidden'); document.getElementById('progressBox').classList.add('hidden');
            document.getElementById('dlOptions').classList.add('hidden'); 
            if(adCheckInterval) clearInterval(adCheckInterval);
            if(adFallbackTimeout) clearTimeout(adFallbackTimeout);
            
            try {
                const res = await fetch(`${BACKEND_URL}/api/preview`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
                const data = await res.json();
                if(data.success) {
                    document.getElementById('previewBox').classList.remove('hidden');
                    document.getElementById('thumb').src = data.thumb; document.getElementById('title').innerText = data.title;
                    
                    const sessionRes = await fetch(`${BACKEND_URL}/api/generate_ad_session`);
                    const sessionData = await sessionRes.json();
                    currentClickId = sessionData.click_id;
                    
                    document.getElementById('realAdLink').href = sessionData.ad_link;
                    document.getElementById('adGate').classList.remove('hidden');
                    
                    let vBtn = document.getElementById('verifyBtn');
                    vBtn.className = "btn bg-panel text-textMuted flex-1 border border-panelBorder text-xs cursor-wait";
                    vBtn.innerHTML = '<i class="fas fa-sync fa-spin mr-1"></i> بانتظار الفحص التلقائي...';
                } else showToast("الرابط غير صالح للتحميل", "error");
            } catch(e) { showToast("فشل تحميل المعاينة", "error"); }
        }

        function toggleRes() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }
        
        function startAdVerificationCheck() {
            showToast("تم فتح الإعلان بنجاح 🌐", "success");
            if(adCheckInterval) clearInterval(adCheckInterval);
            if(adFallbackTimeout) clearTimeout(adFallbackTimeout);
            
            adCheckInterval = setInterval(manualCheckAdStatus, 2000);
            
            // 🌟 تعديل العداد الاحتياطي بالكامل ليصبح 10 ثوانٍ (جافا سكريبت صحيحة ههنا)
            adFallbackTimeout = setTimeout(() => {
                clearInterval(adCheckInterval);
                document.getElementById('adGate').classList.add('hidden');
                document.getElementById('dlOptions').classList.remove('hidden');
                showToast("تم تجاوز الفحص وتفعيل الرابط بنجاح 🔓", "success");
            }, 10000);
        }

        async function manualCheckAdStatus() {
            if(!currentClickId) return;
            try {
                const res = await fetch(`${BACKEND_URL}/api/check_ad_status/${currentClickId}`);
                const data = await res.json();
                if(data.status === 'verified') {
                    clearInterval(adCheckInterval);
                    clearTimeout(adFallbackTimeout);
                    document.getElementById('adGate').classList.add('hidden');
                    document.getElementById('dlOptions').classList.remove('hidden');
                    showToast("تم التحقق من شبكة الإعلانات بنجاح! 🔓", "success");
                }
            } catch(e){}
        }

        function animatePercentCounter(targetPercent) {
            let start = lastLoggedPercent; let end = parseFloat(targetPercent) || 0;
            if (start === end) return;
            let duration = 500; let startTime = null; const percentEl = document.getElementById('progPercent');
            function step(timestamp) {
                if (!startTime) startTime = timestamp;
                let progress = timestamp - startTime;
                let current = start + (end - start) * Math.min(progress / duration, 1);
                percentEl.innerText = Math.floor(current) + '%';
                percentEl.style.transform = 'scale(1.08)';
                if (progress < duration) window.requestAnimationFrame(step);
                else { percentEl.innerText = end + '%'; percentEl.style.transform = 'scale(1.0)'; lastLoggedPercent = end; }
            }
            window.requestAnimationFrame(step);
        }

        async function startDownload() {
            const btn = event.currentTarget; const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري البدء...'; btn.disabled = true;
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            document.getElementById('directDownloadArea').classList.add('hidden');
            
            lastLoggedPercent = 0;
            document.getElementById('progPercent').innerText = '0%'; document.getElementById('progBar').style.width = '0%';
            document.getElementById('progSize').innerText = '-- / --'; document.getElementById('progSpeed').innerText = '--';
            document.getElementById('progStatus').innerHTML = '<i class="fas fa-cloud-download-alt"></i> جاري بدء الاتصال...';

            const mode = document.getElementById('mode').value, resVal = document.getElementById('resolution').value;
            try {
                const res = await fetch(`${BACKEND_URL}/api/download`, {
                    method:'POST', headers:{'Content-Type':'application/json'}, 
                    body:JSON.stringify({url:currentUrl, mode:mode, resolution:resVal, click_id: currentClickId})
                });
                const data = await res.json();
                if(data.success) {
                    const interval = setInterval(async ()=>{
                        try {
                            const progRes = await fetch(`${BACKEND_URL}/api/progress/${data.job_id}`); const prog = await progRes.json();
                            if(prog.status === 'downloading') {
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري تحميل الملف...';
                                document.getElementById('progBar').style.width = prog.percent + '%';
                                document.getElementById('progSize').innerText = prog.dl_mb + ' / ' + prog.total_mb;
                                document.getElementById('progSpeed').innerText = prog.spd_mb;
                                animatePercentCounter(prog.percent);
                            } 
                            else if(prog.status === 'converting') { 
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-cog fa-spin"></i> جاري معالجة الملف...'; 
                                document.getElementById('progBar').style.width = '100%'; document.getElementById('progPercent').innerText = '99%';
                            } 
                            else if(prog.status === 'completed') {
                                clearInterval(interval); 
                                document.getElementById('progStatus').innerHTML = '<span class="text-green-400"><i class="fas fa-check-circle"></i> اكتمل التحميل</span>';
                                document.getElementById('progPercent').innerText = '100%'; document.getElementById('progBar').style.width = '100%';
                                
                                const dlArea = document.getElementById('directDownloadArea'); const dlBtn = document.getElementById('directDownloadBtn');
                                dlBtn.href = BACKEND_URL + prog.url; dlBtn.setAttribute('download', prog.title + (prog.is_audio ? '.mp3' : '.mp4'));
                                dlArea.classList.remove('hidden');
                                
                                myLibrary.unshift({ id: Date.now().toString(), title: prog.title, url: BACKEND_URL + prog.url, thumb: prog.thumb, uploader: prog.uploader, duration: prog.duration, is_audio: prog.is_audio, timestamp: Date.now(), favorite: false });
                                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                                
                                if(document.getElementById('libraryView').classList.contains('active')) applyFilters();
                                showToast("تم حفظ الملف بنجاح", "success");

                                if(localStorage.getItem('pz_auto_tg') !== 'false') {
                                    sendToTelegram(BACKEND_URL + prog.url, prog.is_audio, true, prog.title, prog.uploader, prog.duration, prog.thumb);
                                }
                            } 
                            else if(prog.status === 'error') { clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">فشل التحميل</span>'; }
                        } catch(err) {}
                    }, 800);
                } else {
                    showToast(data.error, "error");
                    document.getElementById('progStatus').innerHTML = `<span class="text-red-500">${data.error}</span>`;
                }
            } catch(e) { showToast("فشل الاتصال بالخادم", "error"); }
            btn.innerHTML = original; btn.disabled = false;
        }

        function playMediaTrack(index) {
            currentPlayingIndex = index; const track = myLibrary[index]; if (!track) return;
            mediaContainer.classList.add('active-player');
            if (track.is_audio) {
                currentPlayingMode = 'audio'; document.getElementById('trackSource').innerText = '🎵 صوت';
                document.getElementById('videoContainer').classList.add('hidden'); document.getElementById('audioVisualizer').classList.remove('hidden');
                document.getElementById('pipBtn').classList.add('hidden'); document.getElementById('playerCoverImg').src = track.thumb || 'https://via.placeholder.com/150';
                document.getElementById('playerCoverImg').style.animationPlayState = 'running'; animateVisualizerBars(true);
            } else {
                currentPlayingMode = 'video'; document.getElementById('trackSource').innerText = '🎬 فيديو';
                document.getElementById('videoContainer').classList.remove('hidden'); document.getElementById('audioVisualizer').classList.add('hidden');
                document.getElementById('pipBtn').classList.remove('hidden'); document.getElementById('playerCoverImg').style.animationPlayState = 'paused';
                animateVisualizerBars(false);
            }
            videoElement.src = track.url; videoElement.load();
            const playPromise = videoElement.play();
            if (playPromise !== undefined) {
                playPromise.then(() => { document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>'; })
                .catch(error => {
                    document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-play ml-0.5"></i>';
                    document.getElementById('playerCoverImg').style.animationPlayState = 'paused'; animateVisualizerBars(false);
                    showToast("اضغط زر التشغيل للمتابعة", "success");
                });
            }
            document.getElementById('playerTitle').innerText = track.title;
            if (mediaContainer.classList.contains('compact-mode')) removeCompactLayout();
        }

        function togglePlay() {
            if (videoElement.paused) {
                videoElement.play().then(() => {
                    document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
                    if(currentPlayingMode === 'audio') { document.getElementById('playerCoverImg').style.animationPlayState = 'running'; animateVisualizerBars(true); }
                }).catch(()=>{});
            } else {
                videoElement.pause(); document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-play ml-0.5"></i>';
                document.getElementById('playerCoverImg').style.animationPlayState = 'paused'; animateVisualizerBars(false);
            }
        }

        function playNext() {
            if (myLibrary.length === 0) return;
            if (isShuffle) { playMediaTrack(Math.floor(Math.random() * myLibrary.length)); } 
            else { let idx = currentPlayingIndex + 1; if (idx >= myLibrary.length) idx = 0; playMediaTrack(idx); }
        }

        function playPrev() {
            if (myLibrary.length === 0) return;
            let idx = currentPlayingIndex - 1; if (idx < 0) idx = myLibrary.length - 1; playMediaTrack(idx);
        }

        function toggleShuffle() {
            isShuffle = !isShuffle; const btn = document.getElementById('shuffleBtn');
            btn.className = isShuffle ? "text-accent hover:text-white transition-colors text-xs" : "text-textMuted hover:text-white transition-colors text-xs";
        }

        function toggleRepeat() {
            isRepeat = !isRepeat; const btn = document.getElementById('repeatBtn');
            btn.className = isRepeat ? "text-accent hover:text-white transition-colors text-xs" : "text-textMuted hover:text-white transition-colors text-xs";
        }

        function updatePlayerProgress() {
            if (isScrubbing) return; const cur = videoElement.currentTime; const dur = videoElement.duration; if (isNaN(dur)) return;
            const pct = (cur / dur) * 100; document.getElementById('mediaProgressBar').style.width = pct + '%';
            document.getElementById('mediaProgressSlider').style.left = pct + '%'; document.getElementById('playerTime').innerText = formatTime(cur) + ' / ' + formatTime(dur);
        }

        function setupScrubbing() {
            const container = document.getElementById('progressContainer');
            container.addEventListener('pointerdown', (e) => {
                isScrubbing = true; performScrub(e); container.setPointerCapture(e.pointerId);
                container.addEventListener('pointermove', performScrub); container.addEventListener('pointerup', endScrub); container.addEventListener('pointercancel', endScrub);
            });
            function performScrub(e) {
                if (!isScrubbing) return; const rect = container.getBoundingClientRect(); const clickX = e.clientX - rect.left;
                const pct = Math.max(0, Math.min(1, clickX / rect.width)); document.getElementById('mediaProgressBar').style.width = (pct * 100) + '%';
                document.getElementById('mediaProgressSlider').style.left = (pct * 100) + '%';
                if (!isNaN(videoElement.duration)) { document.getElementById('playerTime').innerText = formatTime(pct * videoElement.duration) + ' / ' + formatTime(videoElement.duration); }
            }
            function endScrub(e) {
                if (!isScrubbing) return; isScrubbing = false; const rect = container.getBoundingClientRect(); const clickX = e.clientX - rect.left;
                const pct = Math.max(0, Math.min(1, clickX / rect.width));
                if (!isNaN(videoElement.duration)) { videoElement.currentTime = pct * videoElement.duration; }
                try { container.releasePointerCapture(e.pointerId); } catch(err) {}
                container.removeEventListener('pointermove', performScrub); container.removeEventListener('pointerup', endScrub); container.removeEventListener('pointercancel', endScrub);
            }
        }

        function changeVolume() {
            const val = document.getElementById('volumeSlider').value; videoElement.volume = val; lastVolume = val;
            const i = document.getElementById('muteBtn').querySelector('i');
            if (val == 0) { i.className = "fas fa-volume-mute text-xs"; isMuted = true; } else { i.className = "fas fa-volume-up text-xs"; isMuted = false; }
        }

        function toggleMute() {
            const i = document.getElementById('muteBtn').querySelector('i');
            if (isMuted) {
                videoElement.volume = lastVolume || 1; document.getElementById('volumeSlider').value = lastVolume || 1;
                i.className = "fas fa-volume-up text-xs"; isMuted = false;
            } else {
                videoElement.volume = 0; document.getElementById('volumeSlider').value = 0; i.className = "fas fa-volume-mute text-xs"; isMuted = true;
            }
        }

        let currentSpeed = 1;
        function changeSpeed() {
            const spds = [1, 1.25, 1.5, 1.75, 2]; let idx = spds.indexOf(currentSpeed); idx = (idx + 1) % spds.length;
            currentSpeed = spds[idx]; videoElement.playbackRate = currentSpeed; document.getElementById('speedBtn').innerText = currentSpeed + 'x';
        }

        function handleMediaEnd() { if (isRepeat) { videoElement.currentTime = 0; videoElement.play().catch(()=>{}); } else { playNext(); } }
        function closePlayer() { videoElement.pause(); mediaContainer.classList.remove('active-player'); animateVisualizerBars(false); }
        function triggerPiP() { if (document.pictureInPictureEnabled && videoElement && currentPlayingMode === 'video') { if (document.pictureInPictureElement) document.exitPictureInPicture(); else videoElement.requestPictureInPicture().catch(()=>{}); } }
        function toggleCompactMode(e) { e.stopPropagation(); const icon = e.currentTarget.querySelector('i'); if (mediaContainer.classList.contains('compact-mode')) { removeCompactLayout(); icon.className = "fas fa-compress-alt text-xs"; } else { mediaContainer.classList.add('compact-mode'); document.getElementById('playerBody').style.display = 'none'; icon.className = "fas fa-expand-alt text-xs"; } }
        function removeCompactLayout() { mediaContainer.classList.remove('compact-mode'); document.getElementById('playerBody').style.display = 'block'; }
        function resetPlayerPosition() { mediaContainer.style.top = 'auto'; mediaContainer.style.left = '25px'; mediaContainer.style.bottom = '25px'; mediaContainer.style.right = 'auto'; mediaContainer.style.transform = 'none'; }
        function animateVisualizerBars(run) { const visualizer = document.getElementById('visualizerBars'); if (run) visualizer.classList.add('playing-visualizer'); else visualizer.classList.remove('playing-visualizer'); }

        function setupAdvancedDraggable(el, handle) {
            let isDragging = false; let startX, startY, initialLeft, initialTop;
            handle.addEventListener('pointerdown', dragStart);
            function dragStart(e) {
                if (e.target.closest('button, input, a, select')) return;
                isDragging = true; handle.style.cursor = 'grabbing'; el.classList.add('dragging-player');
                startX = e.clientX; startY = e.clientY; initialLeft = el.offsetLeft; initialTop = el.offsetTop;
                el.style.transition = 'none'; handle.setPointerCapture(e.pointerId);
                handle.addEventListener('pointermove', dragMove); handle.addEventListener('pointerup', dragEnd); handle.addEventListener('pointercancel', dragEnd);
            }
            function dragMove(e) {
                if (!isDragging) return; const dx = e.clientX - startX; const dy = e.clientY - startY;
                let newLeft = initialLeft + dx; let newTop = initialTop + dy; const padding = 16;
                const maxLeft = window.innerWidth - el.offsetWidth - padding; const maxTop = window.innerHeight - el.offsetHeight - padding;
                newLeft = Math.max(padding, Math.min(newLeft, maxLeft)); newTop = Math.max(padding, Math.min(newTop, maxTop));
                el.style.left = newLeft + "px"; el.style.top = newTop + "px"; el.style.bottom = "auto"; el.style.right = "auto";
            }
            function dragEnd(e) {
                if (!isDragging) return; isDragging = false; handle.style.cursor = 'grab'; el.classList.remove('dragging-player');
                el.style.transition = 'opacity 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
                try { handle.releasePointerCapture(e.pointerId); } catch(err) {}
                handle.removeEventListener('pointermove', dragMove); handle.removeEventListener('pointerup', dragEnd); handle.removeEventListener('pointercancel', dragEnd);
            }
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    html = INDEX_HTML.replace("{BOT_USERNAME}", BOT_USERNAME)
    return HTMLResponse(content=html)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        raw_results = search_youtube(req.query, limit=25) or {}
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id")
            title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail")
                if not thumb_url and entry.get("thumbnails"):
                    thumb_url = entry.get("thumbnails")[0].get("url")
                if not thumb_url:
                    thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                valid_videos.append({
                    "id": video_id, "title": title,
                    "duration": entry.get("duration") or 0,
                    "uploader": entry.get("uploader") or entry.get("channel") or "غير معروف",
                    "thumbnail": thumb_url
                })
            if len(valid_videos) == 5: break
        return {"success": True, "entries": valid_videos}
    except Exception as e: return {"success": False, "error": str(e)}

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"success": True, "title": info.get("title", "بدون عنوان"), "thumb": info.get("thumbnail", "")}
    except Exception as e: return {"success": False, "error": str(e)}

@app.get("/api/generate_ad_session")
def generate_ad_session():
    click_id = uuid.uuid4().hex[:12]
    AD_VERIFICATIONS[click_id] = {"status": "pending", "created_at": time.time()}
    
    separator = "&" if "?" in AD_LINK else "?"
    tracked_link = f"{AD_LINK}{separator}clickid={click_id}"
    
    return {"click_id": click_id, "ad_link": tracked_link}

@app.get("/api/ad_callback")
def ad_callback(clickid: str):
    if clickid in AD_VERIFICATIONS:
        AD_VERIFICATIONS[clickid]["status"] = "verified"
        return {"status": "success", "message": "Ad verified successfully"}
    return {"status": "error", "message": "Invalid token"}

@app.get("/api/check_ad_status/{click_id}")
def check_ad_status(click_id: str):
    session = AD_VERIFICATIONS.get(click_id)
    if not session:
        return {"status": "not_found"}
        
    if session["status"] == "verified" or (time.time() - session["created_at"] > 10):
        return {"status": "verified"}
        
    return {"status": session["status"]}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            PROGRESS_CACHE[job_id] = {
                "status": "downloading", "percent": round((downloaded / total) * 100, 1),
                "total_mb": f"{total / 1048576:.1f} MB", "dl_mb": f"{downloaded / 1048576:.1f} MB",
                "spd_mb": f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s", "timestamp": time.time()
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
                "status": "completed", "url": f"/files/{filename}", "title": info.get('title', 'مقطع'), 
                "thumb": info.get('thumbnail', ''), "uploader": info.get('uploader', 'غير معروف'),
                "duration": info.get('duration', 0), "is_audio": mode == 'audio', "timestamp": time.time()
            }
    except Exception as e: PROGRESS_CACHE[job_id] = {"status": "error", "error": str(e), "timestamp": time.time()}

@app.post("/api/download")
async def start_download(req: URLRequest):
    session = AD_VERIFICATIONS.get(req.click_id)
    if not session:
        return {"success": False, "error": "جلسة إعلانية غير صالحة."}
        
    is_verified = session["status"] == "verified"
    is_expired_safe = (time.time() - session["created_at"] > 10)
    
    if not (is_verified or is_expired_safe):
        return {"success": False, "error": "خطأ: لم يتم تأكيد فك قفل التحميل بعد."}
        
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting", "timestamp": time.time()}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution)).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.post("/api/send_telegram")
def send_to_telegram(req: TelegramRequest):
    try:
        filename = req.file_url.split("/")[-1]
        file_path = WEB_DIR / filename
        if not file_path.exists(): return {"success": False, "error": "الملف غير موجود."}
        if not TELEGRAM_TOKEN: return {"success": False, "error": "البوت غير مفعل."}
        if file_path.stat().st_size / (1024 * 1024) > 49.5: return {"success": False, "error": "حجم الملف يتجاوز 50 ميجابايت."}

        api_method = "sendAudio" if req.is_audio else "sendVideo"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        
        dur = int(req.duration) if req.duration else 0
        caption = f"- @{BOT_USERNAME} , {formatTime(dur)}" if dur > 0 else f"- @{BOT_USERNAME}"
        reply_markup = {"inline_keyboard": [[{"text": "🌟 أعجبك البوت؟ شاركه", "url": "https://t.me/share/url?url=https://t.me/P1ay_Z0ne_Bot"}]]}
        
        data = {'chat_id': req.chat_id, 'caption': caption, 'reply_markup': json.dumps(reply_markup)}
        if req.is_audio: data.update({'title': req.title, 'performer': req.performer, 'duration': req.duration})
        else: data.update({'supports_streaming': True, 'duration': req.duration})

        with open(file_path, 'rb') as f:
            file_data = f.read()
            
        files = {'audio' if req.is_audio else 'video': (filename, file_data)}
        if req.thumb:
            try:
                t_res = requests.get(req.thumb, timeout=4)
                if t_res.status_code == 200: files['thumb'] = ('thumb.jpg', t_res.content, 'image/jpeg')
            except: pass
                
        response = requests.post(url, data=data, files=files, timeout=60)
        res_data = response.json()
        if response.status_code == 200 and res_data.get("ok"): return {"success": True}
        return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة أولاً مع البوت.")}
    except Exception as e: return {"success": False, "error": str(e)}
