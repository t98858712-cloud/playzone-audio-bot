import os, threading, uuid, time, requests, json, logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

# --- إعدادات البوت والبيئة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MusicPlayZoneBot")
AD_LINK = os.getenv("AD_LINK", "https://example.com/ad")

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PlayZone Cloud Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# إعداد المجلدات
BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "downloads" / "web_library"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(WEB_DIR)), name="files")

PROGRESS_CACHE = {}

# نظام التنظيف الذاتي لحماية مساحة السيرفر
def cleanup_daemon():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
                    logger.info(f"Cleaned up file: {file_path}")
            
            expired_jobs = [jid for jid, data in list(PROGRESS_CACHE.items()) if now - data.get("timestamp", now) > 86400]
            for jid in expired_jobs:
                PROGRESS_CACHE.pop(jid, None)
        except Exception as e:
            logger.error(f"Cleanup daemon error: {e}")
        time.sleep(3600)

threading.Thread(target=cleanup_daemon, daemon=True).start()

# --- نماذج البيانات ---
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

# --- أدوات yt-dlp ---
def get_hardened_ydl_options(outtmpl_path=None, progress_hook=None):
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "playlist_items": "1",
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30, "cachedir": False,
        "no_check_certificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv"], "player_skip": ["web", "mweb"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "ar-SA,ar;q=0.9"}
    }
    if outtmpl_path: opts["outtmpl"] = str(outtmpl_path)
    if progress_hook: opts["progress_hooks"] = [progress_hook]
    return opts

def search_youtube(query: str, limit: int = 5):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    opts.pop('playlist_items', None)
    opts.pop('noplaylist', None)
    with yt_dlp.YoutubeDL(opts) as ydl: 
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

# ==========================================
# قالب HTML يتم استبدال المتغيرات به لتفادي تعارض الأقواس
# ==========================================
HTML_TEMPLATE = """
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
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: { sans: ['Tajawal', 'sans-serif'] }, 
                    colors: { 
                        accent: '#8b5cf6', accentHover: '#7c3aed', 
                        bgDark: '#09090b', panel: '#18181b', panelBorder: '#27272a',
                        textMuted: '#a1a1aa', tgBlue: '#3b82f6'
                    }
                }
            }
        }
    </script>
    <style>
        body { background-color: #09090b; color: #f4f4f5; transition: all 0.3s ease; overflow: hidden; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #3f3f46; border-radius: 4px; }
        .modern-input { background: #09090b; border: 1px solid #27272a; color: white; border-radius: 0.75rem; padding: 0.8rem 1rem; outline: none; transition: border-color 0.3s, box-shadow 0.3s; width: 100%; }
        .modern-input:focus { border-color: #8b5cf6; box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.15); }
        .btn { padding: 0.8rem 1.5rem; border-radius: 0.75rem; font-weight: bold; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; cursor: pointer; user-select: none; position: relative; overflow: hidden; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none !important; }
        .btn:active:not(:disabled) { transform: scale(0.96); }
        @keyframes shimmer { 0% { transform: translateX(100%); } 100% { transform: translateX(-100%); } }
        .shimmer-bg { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent); animation: shimmer 1.5s infinite linear; }
        .view-section { display: none; animation: fadeIn 0.3s ease-out; }
        .view-section.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        #musicPlayer { position: fixed; bottom: 0; left: 0; right: 5rem; z-index: 50; transform: translateY(100%); transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1); border-top: 1px solid #27272a; background: #18181b; }
        @media (min-width: 768px) { #musicPlayer { right: 16rem; } }
        #musicPlayer.active { transform: translateY(0); }
        .progress-container { width: 100%; height: 4px; background: #27272a; cursor: pointer; position: absolute; top: -2px; left: 0; transition: height 0.2s; }
        .progress-container:hover { height: 8px; top: -4px; }
        .progress-bar { height: 100%; background: #8b5cf6; width: 0%; position: relative; transition: width 0.3s ease-out; }
        #toast { position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-100%); opacity: 0; z-index: 1000; padding: 12px 24px; border-radius: 50px; font-weight: bold; color: white; transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55); box-shadow: 0 10px 25px rgba(0,0,0,0.5); pointer-events: none; }
        #toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
    </style>
</head>
<body class="antialiased flex h-[100dvh] w-full">
    <div id="toast"></div>
    <aside class="w-20 md:w-64 bg-panel border-l border-panelBorder flex flex-col justify-between h-[100dvh] z-40 flex-shrink-0">
        <div>
            <div class="h-20 flex items-center justify-center md:justify-start md:px-6 border-b border-panelBorder">
                <div class="w-10 h-10 bg-accent/20 rounded-xl flex items-center justify-center text-accent text-xl flex-shrink-0"><i class="fas fa-play"></i></div>
                <h1 class="text-xl font-black text-white mr-3 hidden md:block">Play<span class="text-accent">Zone</span></h1>
            </div>
            <nav class="mt-6 px-2 md:px-3 space-y-2">
                <button onclick="switchView('searchView')" id="nav-searchView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 p-3 md:px-4 md:py-3 rounded-xl bg-panelBorder text-accent font-bold"><i class="fas fa-search text-xl flex-shrink-0"></i><span class="hidden md:block">البحث والتحميل</span></button>
                <button onclick="switchView('libraryView')" id="nav-libraryView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 p-3 md:px-4 md:py-3 rounded-xl text-textMuted hover:bg-panelBorder hover:text-white bg-transparent"><i class="fas fa-folder text-xl flex-shrink-0"></i><span class="hidden md:block">ملفاتي المحفوظة</span></button>
                <button onclick="switchView('settingsView')" id="nav-settingsView" class="nav-btn btn w-full flex items-center justify-center md:justify-start gap-4 p-3 md:px-4 md:py-3 rounded-xl text-textMuted hover:bg-panelBorder hover:text-white bg-transparent"><i class="fas fa-cog text-xl flex-shrink-0"></i><span class="hidden md:block">الإعدادات</span></button>
            </nav>
        </div>
        <div class="p-2 md:p-4 border-t border-panelBorder">
            <a href="https://t.me/__BOT_USERNAME__" target="_blank" class="btn w-full flex items-center justify-center md:justify-start gap-3 p-3 md:px-4 md:py-3 rounded-xl bg-tgBlue/10 text-tgBlue hover:bg-tgBlue/20"><i class="fab fa-telegram-plane text-xl flex-shrink-0"></i><span class="hidden md:block font-bold text-sm truncate" dir="ltr">@__BOT_USERNAME__</span></a>
        </div>
    </aside>

    <main class="flex-1 h-[100dvh] overflow-y-auto pb-36 md:pb-28 relative scroll-smooth">
        <section id="searchView" class="view-section active p-4 md:p-8 max-w-4xl mx-auto">
            <div class="bg-panel rounded-3xl p-6 md:p-8 border border-panelBorder mb-6 relative overflow-hidden">
                <div class="absolute top-0 left-0 w-32 h-32 bg-accent/5 rounded-full blur-3xl"></div>
                <h2 class="text-2xl font-bold mb-2 text-white relative z-10">البحث السريع ⚡</h2>
                <p class="text-textMuted mb-6 text-sm relative z-10">الصق رابط المقطع هنا أو ابحث باسمه مباشرة.</p>
                <div class="flex flex-col md:flex-row gap-4 relative z-10">
                    <input type="text" id="url" placeholder="الرابط أو الكلمة البحثية..." class="modern-input flex-1">
                    <button onclick="processInput()" id="mainBtn" class="btn bg-accent hover:bg-accentHover text-white md:w-32 shadow-lg shadow-accent/20"><i class="fas fa-search"></i> بحث</button>
                </div>
                <div id="searchResults" class="hidden mt-8 bg-[#18181b] border border-panelBorder rounded-3xl p-4 md:p-5 shadow-xl">
                    <div class="mb-4 pb-3 border-b border-panelBorder flex justify-between items-center">
                        <h3 class="text-white font-bold text-base md:text-lg">🎬 اختر المقطع المطلوب:</h3>
                        <button onclick="document.getElementById('searchResults').classList.add('hidden')" class="text-textMuted hover:text-red-400 p-2 bg-bgDark rounded-full"><i class="fas fa-times"></i></button>
                    </div>
                    <div id="searchResultsList" class="flex flex-col gap-3 w-full"></div>
                </div>
            </div>
            <div id="previewBox" class="hidden bg-panel rounded-3xl p-6 border border-panelBorder">
                <div class="flex flex-col md:flex-row gap-6 items-center">
                    <div class="w-full md:w-1/3"><img id="thumb" class="w-full rounded-xl object-cover aspect-video shadow-md border border-panelBorder"></div>
                    <div class="w-full md:w-2/3 space-y-4">
                        <h3 id="title" class="font-bold text-lg text-white line-clamp-2"></h3>
                        <div id="adGate" class="bg-bgDark border border-panelBorder p-4 rounded-xl text-center">
                            <p class="text-sm mb-3 text-textMuted font-medium"><i class="fas fa-info-circle text-accent ml-1"></i> للتحميل، يجب مشاهدة الإعلان لعدة ثوانٍ ثم الرجوع هنا للتحقق.</p>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <a href="__AD_LINK__" target="_blank" onclick="startAdTimer()" class="btn bg-tgBlue text-white flex-1 hover:bg-blue-500"><i class="fas fa-eye"></i> 1. مشاهدة الإعلان</a>
                                <button id="verifyBtn" disabled class="btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder"><i class="fas fa-lock"></i> 2. التحقق للتحميل</button>
                            </div>
                        </div>
                        <div id="dlOptions" class="hidden space-y-4">
                            <div class="grid grid-cols-2 gap-3">
                                <select id="mode" onchange="toggleRes()" class="modern-input bg-bgDark py-2 text-sm"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                                <select id="resolution" class="modern-input bg-bgDark py-2 text-sm"><option value="480">جودة 480p</option><option value="720" selected>جودة 720p</option></select>
                            </div>
                            <button onclick="startDownload(this)" class="btn bg-accent text-white w-full hover:bg-accentHover shadow-lg shadow-accent/30"><i class="fas fa-download"></i> بدء التحميل الآن</button>
                        </div>
                        <div id="progressBox" class="hidden bg-bgDark p-5 rounded-xl border border-panelBorder">
                            <div class="flex justify-between items-center mb-3">
                                <span id="progStatus" class="text-accent font-bold text-sm"><i class="fas fa-circle-notch fa-spin"></i> جاري التجهيز...</span>
                                <span id="progPercent" class="font-mono font-bold text-white text-lg">0%</span>
                            </div>
                            <div class="w-full bg-panel rounded-full h-3 mb-2 overflow-hidden relative"><div id="progBar" class="bg-gradient-to-r from-accentHover to-accent h-full relative" style="width: 0%"><div class="shimmer-bg"></div></div></div>
                            <div class="flex justify-between text-xs text-textMuted font-mono mt-1"><span id="progSize">-- MB / -- MB</span><span id="progSpeed">-- MB/s</span></div>
                            <div id="directDownloadArea" class="hidden mt-4 pt-4 border-t border-panelBorder"><a id="directDownloadBtn" href="#" download class="btn bg-green-600 text-white w-full hover:bg-green-500 shadow-lg shadow-green-500/20"><i class="fas fa-arrow-alt-circle-down"></i> تحميل الملف إلى جهازك مباشرة 💾</a></div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <section id="libraryView" class="view-section p-4 md:p-8 max-w-6xl mx-auto h-full flex flex-col">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
                <h2 class="text-2xl font-bold text-white">ملفاتي المحفوظة</h2>
                <div class="flex flex-wrap gap-2 w-full md:w-auto">
                    <div class="relative flex-1 md:w-56"><i class="fas fa-search absolute right-3 top-1/2 transform -translate-y-1/2 text-textMuted"></i><input type="text" id="libSearch" oninput="applyFilters()" placeholder="بحث سريع..." class="modern-input pl-3 pr-10 py-2 bg-panel text-sm"></div>
                    <select id="libFilter" onchange="applyFilters()" class="modern-input py-2 px-3 w-auto bg-panel text-accent font-bold text-sm"><option value="all">الكل</option><option value="favorites">❤️ المفضلة</option><option value="audio">🎵 صوتيات</option><option value="video">🎬 فيديوهات</option></select>
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
                    <div class="flex gap-3 mb-5"><input type="text" id="settingTgId" placeholder="الآي دي (ID)" class="modern-input font-mono bg-bgDark"><button onclick="updateTgId()" class="btn bg-tgBlue hover:bg-blue-500 text-white px-6">حفظ</button></div>
                    <div class="flex items-center justify-between p-4 bg-bgDark rounded-xl border border-panelBorder">
                        <div><p class="font-bold text-white text-sm">إرسال تلقائي (أتمتة)</p><p class="text-xs text-textMuted mt-1">إرسال الملف للبوت فور انتهاء تحميله.</p></div>
                        <label class="relative inline-flex items-center cursor-pointer"><input type="checkbox" id="autoForwardToggle" onchange="toggleAutoForward()" class="sr-only peer" checked><div class="w-11 h-6 bg-panelBorder rounded-full peer peer-checked:after:-translate-x-full peer-checked:bg-accent after:content-[''] after:absolute after:top-[2px] after:right-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all"></div></label>
                    </div>
                </div>
                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-sm">
                    <h3 class="text-lg font-bold text-white mb-3 flex items-center gap-2"><i class="fas fa-database text-red-400"></i> إدارة البيانات</h3>
                    <div class="flex justify-between items-center p-4 bg-bgDark rounded-xl border border-panelBorder"><div><p class="font-bold text-white text-sm" id="libCountStatus">السجل (0)</p></div><button onclick="clearAllLibrary()" class="btn bg-red-500/10 text-red-500 hover:bg-red-500/20 text-sm px-4">مسح السجل بالكامل</button></div>
                </div>
            </div>
        </section>
    </main>

    <div id="musicPlayer" class="pb-safe">
        <div class="progress-container" id="progressContainer" onclick="seekAudio(event)"><div class="progress-bar" id="audioProgressBar"></div></div>
        <div class="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4 p-3 md:px-6">
            <div class="flex items-center gap-3 w-full md:w-1/3 overflow-hidden">
                <div class="w-12 h-12 rounded-full bg-accent/20 flex items-center justify-center text-accent text-xl flex-shrink-0 border border-accent/30" id="playerThumbPlaceholder"><i class="fas fa-music"></i></div>
                <div class="overflow-hidden"><p id="playerTitle" class="font-bold text-sm text-white truncate">جاهز للتشغيل</p><p id="playerTime" class="text-xs text-textMuted font-mono mt-0.5">0:00 / 0:00</p></div>
            </div>
            <div class="flex items-center justify-center gap-6 w-full md:w-1/3">
                <button onclick="toggleShuffle()" id="shuffleBtn" class="text-textMuted hover:text-white transition-colors text-lg active:scale-90"><i class="fas fa-random"></i></button>
                <button onclick="playPrev()" class="text-white hover:text-accent transition-colors text-xl active:scale-90"><i class="fas fa-step-backward"></i></button>
                <button onclick="togglePlay()" id="playPauseBtn" class="w-12 h-12 rounded-full bg-accent text-white flex items-center justify-center text-lg active:scale-90 transition-transform"><i class="fas fa-play ml-1"></i></button>
                <button onclick="playNext()" class="text-white hover:text-accent transition-colors text-xl active:scale-90"><i class="fas fa-step-forward"></i></button>
                <button onclick="toggleRepeat()" id="repeatBtn" class="text-textMuted hover:text-white transition-colors text-lg active:scale-90"><i class="fas fa-redo"></i></button>
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
        <div class="bg-panel border border-panelBorder p-6 rounded-3xl max-w-sm w-full text-center shadow-2xl">
            <div class="w-14 h-14 bg-tgBlue/20 text-tgBlue rounded-full flex items-center justify-center mx-auto mb-4 text-2xl"><i class="fab fa-telegram-plane"></i></div>
            <h3 class="text-lg font-bold mb-2 text-white">يرجى ربط حسابك</h3>
            <p class="text-textMuted text-sm mb-5">أدخل الـ ID الخاص بك لمرة واحدة للتمكن من الإرسال.</p>
            <button onclick="window.open('https://t.me/__BOT_USERNAME__', '_blank')" class="btn bg-tgBlue text-white w-full mb-3 text-sm"><i class="fas fa-robot"></i> 1. نسخ الـ ID من البوت</button>
            <input type="text" id="tgIdInput" placeholder="2. الصق الـ ID هنا" class="modern-input bg-bgDark text-center text-base mb-4 font-mono py-2">
            <div class="flex gap-2"><button onclick="saveTgIdFromModal()" class="btn bg-accent text-white flex-1 text-sm">حفظ ومتابعة</button><button onclick="closeTgModal()" class="btn bg-panelBorder text-textMuted flex-1 hover:text-white text-sm">إلغاء</button></div>
        </div>
    </div>

    <script>
        let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
        let currentUrl = "", adWatched = false, currentPlayingIndex = -1, isShuffle = false, isRepeat = false, libraryPage = 1;
        const itemsPerPage = 6;
        const audioEl = document.getElementById('globalAudioElement');

        window.addEventListener('DOMContentLoaded', () => {
            if (window.Telegram && window.Telegram.WebApp) {
                window.Telegram.WebApp.ready();
                window.Telegram.WebApp.expand();
                const tgUser = window.Telegram.WebApp.initDataUnsafe.user;
                if (tgUser && tgUser.id) { localStorage.setItem('pz_tg_id', tgUser.id); showToast("تم ربط حساب تيليجرام تلقائياً 🛡️", "success"); }
            }
            document.getElementById('settingTgId').value = localStorage.getItem('pz_tg_id') || "";
            document.getElementById('tgIdInput').value = localStorage.getItem('pz_tg_id') || "";
            document.getElementById('autoForwardToggle').checked = localStorage.getItem('pz_auto_tg') !== 'false';
            updateLibraryCount();
            switchView('searchView');
        });

        function formatTime(secs) { if(isNaN(secs) || secs === null) return "0:00"; const m = Math.floor(secs / 60), s = Math.floor(secs % 60); return m + ":" + (s < 10 ? '0' + s : s); }
        function showToast(message, type = "success") { const toast = document.getElementById('toast'); toast.innerText = message; toast.className = "show"; toast.style.background = type === "error" ? "linear-gradient(135deg, #f43f5e, #e11d48)" : "linear-gradient(135deg, #10b981, #059669)"; setTimeout(() => { toast.className = ""; }, 3000); }
        function switchView(viewId) {
            document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
            document.getElementById(viewId).classList.add('active');
            document.querySelectorAll('.nav-btn').forEach(btn => { btn.classList.remove('bg-panelBorder', 'text-accent', 'font-bold'); btn.classList.add('text-textMuted', 'hover:bg-panelBorder', 'hover:text-white', 'bg-transparent'); });
            const activeBtn = document.getElementById('nav-' + viewId);
            if (activeBtn) { activeBtn.classList.remove('text-textMuted', 'hover:bg-panelBorder', 'hover:text-white', 'bg-transparent'); activeBtn.classList.add('bg-panelBorder', 'text-accent', 'font-bold'); }
            if (viewId === 'libraryView') applyFilters();
        }

        function applyFilters() {
            const query = document.getElementById('libSearch').value.toLowerCase(), filter = document.getElementById('libFilter').value;
            let filtered = myLibrary.filter(item => {
                const matchesSearch = item.title.toLowerCase().includes(query) || (item.uploader && item.uploader.toLowerCase().includes(query));
                let matchesType = true;
                if (filter === 'favorites') matchesType = item.favorite; else if (filter === 'audio') matchesType = item.is_audio; else if (filter === 'video') matchesType = !item.is_audio;
                return matchesSearch && matchesType;
            });
            const totalPages = Math.ceil(filtered.length / itemsPerPage);
            if (libraryPage > totalPages) libraryPage = Math.max(1, totalPages);
            const pageItems = filtered.slice((libraryPage - 1) * itemsPerPage, libraryPage * itemsPerPage);
            const container = document.getElementById('libraryContainer');
            container.innerHTML = "";
            if (pageItems.length === 0) { container.innerHTML = `<div class="col-span-full py-12 text-center text-textMuted"><i class="fas fa-folder-open text-5xl mb-3 block"></i>لا توجد ملفات محفوظة.</div>`; document.getElementById('pagination').innerHTML = ""; return; }
            pageItems.forEach((item) => {
                const actualIndex = myLibrary.findIndex(i => i.id === item.id);
                const favClass = item.favorite ? 'fas fa-heart text-red-500' : 'far fa-heart';
                const icon = item.is_audio ? '<i class="fas fa-music text-accent"></i>' : '<i class="fas fa-video text-tgBlue"></i>';
                const fileExt = item.is_audio ? 'mp3' : 'mp4';
                const playAction = item.is_audio ? `playAudioTrack(${actualIndex})` : `watchVideo('${item.url}')`;
                container.innerHTML += `
                <div class="bg-panel rounded-2xl p-4 border border-panelBorder flex gap-4 items-center relative group">
                    <div class="relative w-24 h-16 rounded-xl overflow-hidden border border-panelBorder flex-shrink-0 cursor-pointer" onclick="${playAction}">
                        <img src="${item.thumb || 'https://via.placeholder.com/150'}" class="w-full h-full object-cover" onerror="this.src='https://via.placeholder.com/150'">
                        <div class="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"><i class="fas ${item.is_audio ? 'fa-play' : 'fa-external-link-alt'} text-white text-lg"></i></div>
                        <div class="absolute bottom-1 right-1 bg-black/80 text-[10px] px-1 font-mono rounded text-white">${formatTime(item.duration || 0)}</div>
                    </div>
                    <div class="flex-1 min-w-0 text-right"><h4 class="text-white font-bold text-sm truncate cursor-pointer" onclick="${playAction}">${item.title}</h4><p class="text-textMuted text-xs mt-1 truncate">${icon} ${item.uploader || 'غير معروف'}</p></div>
                    <div class="flex items-center gap-2 flex-row-reverse">
                        <button onclick="deleteFromLibrary('${item.id}')" class="p-2 bg-bgDark rounded-full border border-panelBorder text-textMuted hover:text-red-400 active:scale-95 transition-transform"><i class="fas fa-trash-alt"></i></button>
                        <a href="${item.url}" download="${item.title}.${fileExt}" class="p-2 bg-bgDark rounded-full border border-panelBorder text-textMuted hover:text-green-400 active:scale-95 transition-transform flex items-center justify-center"><i class="fas fa-download"></i></a>
                        <button onclick="triggerSendToTelegram('${item.id}')" class="p-2 bg-bgDark rounded-full border border-panelBorder text-textMuted hover:text-tgBlue active:scale-95 transition-transform"><i class="fab fa-telegram-plane"></i></button>
                        <button onclick="toggleFavorite('${item.id}')" class="p-2 bg-bgDark rounded-full border border-panelBorder text-textMuted hover:text-red-500 active:scale-95 transition-transform"><i class="${favClass}"></i></button>
                    </div>
                </div>`;
            });
            renderPagination(totalPages);
        }

        function renderPagination(totalPages) {
            const pagBox = document.getElementById('pagination'); pagBox.innerHTML = "";
            if (totalPages <= 1) return;
            let html = `<button onclick="changePage(${libraryPage - 1})" ${libraryPage === 1 ? 'disabled' : ''} class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">السابق</button>`;
            for (let i = 1; i <= totalPages; i++) { const activeClass = (libraryPage === i) ? 'bg-accent text-white' : 'bg-panel border border-panelBorder text-textMuted'; html += `<button onclick="changePage(${i})" class="btn px-3 py-1.5 ${activeClass} text-xs font-mono">${i}</button>`; }
            html += `<button onclick="changePage(${libraryPage + 1})" ${libraryPage === totalPages ? 'disabled' : ''} class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">التالي</button>`;
            pagBox.innerHTML = html;
        }

        function changePage(page) { libraryPage = page; applyFilters(); }
        function toggleFavorite(id) { const index = myLibrary.findIndex(i => i.id === id); if (index !== -1) { myLibrary[index].favorite = !myLibrary[index].favorite; localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters(); } }
        function deleteFromLibrary(id) { if (confirm("هل أنت متأكد من حذف هذا الملف؟")) { myLibrary = myLibrary.filter(i => i.id !== id); localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters(); updateLibraryCount(); showToast("تم الحذف", "success"); } }
        function updateLibraryCount() { document.getElementById('libCountStatus').innerText = "السجل (" + myLibrary.length + ")"; }
        function clearAllLibrary() { if (confirm("هل أنت متأكد من مسح السجل بالكامل؟")) { myLibrary = []; localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters(); updateLibraryCount(); showToast("تم مسح السجل", "success"); } }
        function updateTgId() { const tgId = document.getElementById('settingTgId').value.trim(); if (!tgId) { localStorage.removeItem('pz_tg_id'); } else { localStorage.setItem('pz_tg_id', tgId); } showToast("تم الحفظ", "success"); }
        function toggleAutoForward() { localStorage.setItem('pz_auto_tg', document.getElementById('autoForwardToggle').checked ? 'true' : 'false'); }

        let pendingTgItem = null;
        function triggerSendToTelegram(id) {
            const item = myLibrary.find(i => i.id === id); if (!item) return;
            const tgId = localStorage.getItem('pz_tg_id');
            if (!tgId) { pendingTgItem = item; document.getElementById('tgModal').classList.remove('hidden'); document.getElementById('tgModal').classList.add('flex'); } 
            else { sendToTelegram(item.url, item.is_audio, false, item.title, item.uploader, item.duration, item.thumb); }
        }
        function closeTgModal() { document.getElementById('tgModal').classList.add('hidden'); document.getElementById('tgModal').classList.remove('flex'); pendingTgItem = null; }
        function saveTgIdFromModal() {
            const val = document.getElementById('tgIdInput').value.trim();
            if (!val) return showToast("الرجاء إدخال ID", "error");
            localStorage.setItem('pz_tg_id', val); document.getElementById('settingTgId').value = val; closeTgModal(); showToast("تم الحفظ", "success");
            if (pendingTgItem) sendToTelegram(pendingTgItem.url, pendingTgItem.is_audio, false, pendingTgItem.title, pendingTgItem.uploader, pendingTgItem.duration, pendingTgItem.thumb);
        }

        async function sendToTelegram(fileUrl, isAudio, auto = false, title = "مقطع", performer = "PlayZone", duration = 0, thumb = "") {
            const chatId = localStorage.getItem('pz_tg_id'); if (!chatId) return;
            showToast("جاري الإرسال للبوت... 🚀", "success");
            try {
                const res = await fetch('/api/send_telegram', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_url: fileUrl, chat_id: chatId.toString(), is_audio: isAudio, title, performer, duration, thumb }) });
                const data = await res.json();
                if (data.success) showToast("تم الإرسال لحسابك! 🎉", "success"); else showToast("خطأ: " + data.error, "error");
            } catch(e) { showToast("خطأ في الاتصال", "error"); }
        }

        function watchVideo(url) { window.open(url, '_blank'); }

        async function processInput() {
            const input = document.getElementById('url').value.trim(); if(!input) return;
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> بحث'; btn.disabled = true;
            document.getElementById('previewBox').classList.add('hidden');
            if (input.startsWith('http')) { await renderPreview(input); } 
            else {
                try {
                    const res = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:input})});
                    const data = await res.json();
                    if(data.success && data.entries.length) {
                        let box = document.getElementById('searchResultsList'); box.innerHTML = '';
                        data.entries.forEach((v) => {
                            box.innerHTML += `
                            <div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="w-full flex items-center p-3 bg-panel rounded-2xl border border-panelBorder cursor-pointer hover:border-accent/50 transition-all active:scale-[0.98] mb-1">
                                <div class="flex-shrink-0 w-24 h-14 rounded-xl overflow-hidden border border-panelBorder shadow-sm relative ml-3"><img src="${v.thumbnail}" class="w-full h-full object-cover"><div class="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono">${formatTime(v.duration || 0)}</div></div>
                                <div class="flex-1 min-w-0 flex flex-col justify-center text-right"><h4 class="text-white font-bold text-sm truncate w-full mb-1" dir="auto">${v.title}</h4><p class="text-textMuted text-xs truncate w-full" dir="auto"><i class="fas fa-user-circle text-accent/70"></i> ${v.uploader}</p></div>
                                <div class="flex-shrink-0 w-8 h-8 rounded-full bg-bgDark border border-panelBorder flex items-center justify-center text-accent mr-2"><i class="fas fa-download text-xs"></i></div>
                            </div>`;
                        });
                        document.getElementById('searchResults').classList.remove('hidden');
                    } else showToast("لم يتم العثور على نتائج", "error");
                } catch(e) { showToast("خطأ في الاتصال", "error"); }
            }
            btn.innerHTML = '<i class="fas fa-search"></i> بحث'; btn.disabled = false;
        }

        async function renderPreview(url) {
            currentUrl = url; document.getElementById('searchResults').classList.add('hidden'); document.getElementById('previewBox').classList.add('hidden'); document.getElementById('progressBox').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden');
            try {
                const res = await fetch('/api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:url})});
                const data = await res.json();
                if(data.success) {
                    document.getElementById('previewBox').classList.remove('hidden');
                    document.getElementById('thumb').src = data.thumb;
                    document.getElementById('title').innerText = data.title;
                    document.getElementById('adGate').classList.remove('hidden');
                    document.getElementById('dlOptions').classList.add('hidden');
                    let vBtn = document.getElementById('verifyBtn'); vBtn.disabled = true; vBtn.onclick = null;
                    vBtn.className = "btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder";
                    vBtn.innerHTML = '<i class="fas fa-lock"></i> 2. التحقق للتحميل'; adWatched = false;
                } else showToast("الرابط غير متاح", "error");
            } catch(e) { showToast("حدث خطأ", "error"); }
        }

        function toggleRes() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; }

        function startAdTimer() {
            if(adWatched) return;
            let btn = document.getElementById('verifyBtn'); let timeLeft = 5;
            btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`;
            let timer = setInterval(() => {
                timeLeft--;
                if(timeLeft > 0) btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> جاري التحقق (${timeLeft})...`; 
                else {
                    clearInterval(timer); btn.disabled = false; btn.onclick = unlockDownload; 
                    btn.className = "btn bg-green-600 text-white hover:bg-green-500 shadow-lg shadow-green-500/30 flex-1";
                    btn.innerHTML = `<i class="fas fa-unlock-alt"></i> 2. تحقق وفك القفل`; adWatched = true;
                }
            }, 1000);
        }
        function unlockDownload() { if(!adWatched) return; document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden'); }

        async function startDownload(btn) {
            const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري البدء...'; btn.disabled = true;
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden'); document.getElementById('directDownloadArea').classList.add('hidden');
            document.getElementById('progPercent').innerText = '0%'; document.getElementById('progBar').style.width = '0%'; document.getElementById('progStatus').innerHTML = '<i class="fas fa-cloud-download-alt"></i> جاري الاتصال...';
            const mode = document.getElementById('mode').value, resVal = document.getElementById('resolution').value;
            try {
                const res = await fetch('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:currentUrl, mode:mode, resolution:resVal})});
                const data = await res.json();
                if(data.success) {
                    const interval = setInterval(async ()=>{
                        try {
                            const progRes = await fetch(`/api/progress/${data.job_id}`); const prog = await progRes.json();
                            if(prog.status === 'downloading') {
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري تحميل الملف...';
                                document.getElementById('progPercent').innerText = prog.percent + '%'; document.getElementById('progBar').style.width = prog.percent + '%';
                                document.getElementById('progSize').innerText = prog.dl_mb + ' / ' + prog.total_mb; document.getElementById('progSpeed').innerText = prog.spd_mb;
                            } else if(prog.status === 'converting') { document.getElementById('progStatus').innerHTML = '<i class="fas fa-cog fa-spin"></i> جاري دمج وتجهيز الملف...'; document.getElementById('progBar').style.width = '100%'; } 
                            else if(prog.status === 'completed') {
                                clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-green-400"><i class="fas fa-check-circle"></i> اكتمل التحميل</span>';
                                const dlBtn = document.getElementById('directDownloadBtn'); dlBtn.href = prog.url; dlBtn.setAttribute('download', prog.title + (prog.is_audio ? '.mp3' : '.mp4')); document.getElementById('directDownloadArea').classList.remove('hidden');
                                myLibrary.unshift({ id: Date.now().toString(), title: prog.title, url: prog.url, thumb: prog.thumb, uploader: prog.uploader, duration: prog.duration, is_audio: prog.is_audio, timestamp: Date.now(), favorite: false });
                                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                                if(document.getElementById('libraryView').classList.contains('active')) applyFilters();
                                showToast("أضيف إلى ملفاتي", "success");
                                if(localStorage.getItem('pz_auto_tg') !== 'false') sendToTelegram(prog.url, prog.is_audio, true, prog.title, prog.uploader, prog.duration, prog.thumb);
                            } else if(prog.status === 'error') { clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">فشل التحميل</span>'; }
                        } catch(err) {}
                    }, 800);
                }
            } catch(e) { showToast("فشل الاتصال", "error"); }
            btn.innerHTML = original; btn.disabled = false;
        }

        function playAudioTrack(index) {
            currentPlayingIndex = index; const track = myLibrary[index]; if (!track || !track.is_audio) return;
            audioEl.src = track.url; audioEl.play().catch(e => showToast("لا يمكن تشغيل الملف", "error"));
            document.getElementById('playerTitle').innerText = track.title;
            document.getElementById('playerThumbPlaceholder').innerHTML = `<img src="${track.thumb || ''}" class="w-full h-full object-cover rounded-full">`;
            document.getElementById('musicPlayer').classList.add('active');
            document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
        }
        function togglePlay() { if (audioEl.paused) { audioEl.play().then(() => document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>').catch(e=>{}); } else { audioEl.pause(); document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-play ml-1"></i>'; } }
        function playNext() { const audioTracks = myLibrary.map((t, idx) => ({t, idx})).filter(x => x.t.is_audio); if (audioTracks.length === 0) return; if (isShuffle) { playAudioTrack(audioTracks[Math.floor(Math.random() * audioTracks.length)].idx); } else { const pos = audioTracks.findIndex(x => x.idx === currentPlayingIndex); if (pos !== -1 && pos < audioTracks.length - 1) playAudioTrack(audioTracks[pos + 1].idx); else if (audioTracks.length > 0) playAudioTrack(audioTracks[0].idx); } }
        function playPrev() { const audioTracks = myLibrary.map((t, idx) => ({t, idx})).filter(x => x.t.is_audio); if (audioTracks.length === 0) return; const pos = audioTracks.findIndex(x => x.idx === currentPlayingIndex); if (pos > 0) playAudioTrack(audioTracks[pos - 1].idx); else if (audioTracks.length > 0) playAudioTrack(audioTracks[audioTracks.length - 1].idx); }
        function toggleShuffle() { isShuffle = !isShuffle; document.getElementById('shuffleBtn').classList.toggle('text-accent', isShuffle); document.getElementById('shuffleBtn').classList.toggle('text-textMuted', !isShuffle); }
        function toggleRepeat() { isRepeat = !isRepeat; document.getElementById('repeatBtn').classList.toggle('text-accent', isRepeat); document.getElementById('repeatBtn').classList.toggle('text-textMuted', !isRepeat); }
        function updatePlayerProgress() { const cur = audioEl.currentTime, dur = audioEl.duration; if (isNaN(dur)) return; document.getElementById('audioProgressBar').style.width = (cur / dur) * 100 + '%'; document.getElementById('playerTime').innerText = formatTime(cur) + ' / ' + formatTime(dur); }
        function seekAudio(e) { const rect = document.getElementById('progressContainer').getBoundingClientRect(); if (!isNaN(audioEl.duration)) audioEl.currentTime = ((e.clientX - rect.left) / rect.width) * audioEl.duration; }
        function changeVolume() { audioEl.volume = document.getElementById('volumeSlider').value; }
        let currentSpeed = 1;
        function changeSpeed() { const speeds = [1, 1.25, 1.5, 1.75, 2]; currentSpeed = speeds[(speeds.indexOf(currentSpeed) + 1) % speeds.length]; audioEl.playbackRate = currentSpeed; document.getElementById('speedBtn').innerText = currentSpeed + 'x'; }
        function handleAudioEnd() { if (isRepeat) { audioEl.currentTime = 0; audioEl.play(); } else playNext(); }
        function closePlayer() { audioEl.pause(); document.getElementById('musicPlayer').classList.remove('active'); }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    # استبدال المتغيرات في القالب لتجنب استخدام f-strings التي تكسر أكواد JS
    html_content = HTML_TEMPLATE.replace("__BOT_USERNAME__", BOT_USERNAME).replace("__AD_LINK__", AD_LINK)
    return HTMLResponse(content=html_content)

@app.post("/api/search")
async def api_search(req: SearchRequest):
    try:
        raw_results = search_youtube(req.query, limit=5) or {}
        entries = raw_results.get("entries") or []
        valid_videos = []
        for entry in entries:
            if not entry: continue
            video_id = entry.get("id"); title = entry.get("title")
            if video_id and title:
                thumb_url = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                valid_videos.append({ "id": video_id, "title": title, "duration": entry.get("duration") or 0, "uploader": entry.get("uploader") or "غير معروف", "thumbnail": thumb_url })
        return {"success": True, "entries": valid_videos}
    except Exception as e: 
        logger.error(f"Search error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/api/preview")
async def get_preview(req: URLRequest):
    try:
        opts = get_hardened_ydl_options()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"success": True, "title": info.get("title", "بدون عنوان"), "thumb": info.get("thumbnail", "")}
    except Exception as e: 
        return {"success": False, "error": str(e)}

def bg_download(job_id: str, url: str, mode: str, res: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0); speed = d.get('speed', 0)
            percent = round((downloaded / total) * 100, 1)
            PROGRESS_CACHE[job_id] = { "status": "downloading", "percent": percent, "total_mb": f"{total / 1048576:.1f} MB", "dl_mb": f"{downloaded / 1048576:.1f} MB", "spd_mb": f"{speed / 1048576:.1f} MB/s" if speed else "0 MB/s", "timestamp": time.time() }
        elif d['status'] == 'finished': 
            PROGRESS_CACHE[job_id] = {"status": "converting", "timestamp": time.time()}

    opts = get_hardened_ydl_options(outtmpl_path=WEB_DIR / f'{job_id}.%(ext)s', progress_hook=hook)
    if mode == 'audio': 
        opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    else: 
        opts.update({'format': f'bestvideo[height<={res}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'merge_output_format': 'mp4'})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url, download=True)
            filename = f"{job_id}.mp3" if mode == 'audio' else f"{job_id}.mp4"
            PROGRESS_CACHE[job_id] = { "status": "completed", "url": f"/files/{filename}", "title": info.get('title', 'مقطع'), "thumb": info.get('thumbnail', ''), "uploader": info.get('uploader', 'غير معروف'), "duration": info.get('duration', 0), "is_audio": mode == 'audio', "timestamp": time.time() }
    except Exception as e: 
        PROGRESS_CACHE[job_id] = {"status": "error", "error": str(e), "timestamp": time.time()}

@app.post("/api/download")
async def start_download(req: URLRequest):
    job_id = uuid.uuid4().hex[:8]
    PROGRESS_CACHE[job_id] = {"status": "starting", "timestamp": time.time()}
    threading.Thread(target=bg_download, args=(job_id, req.url, req.mode, req.resolution), daemon=True).start()
    return {"success": True, "job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str): 
    return PROGRESS_CACHE.get(job_id, {"status": "waiting"})

@app.post("/api/send_telegram")
async def send_to_telegram(req: TelegramRequest):
    try:
        filename = req.file_url.split("/")[-1]
        file_path = WEB_DIR / filename
        if not file_path.exists(): return {"success": False, "error": "الملف مسح من السيرفر. يرجى إعادة التحميل."}
        if not TELEGRAM_TOKEN: return {"success": False, "error": "البوت غير مفعل حالياً."}

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 49.5: return {"success": False, "error": "حجم الملف يتجاوز الحد المسموح للإرسال في تيليجرام (50MB)."}

        api_method = "sendAudio" if req.is_audio else "sendVideo"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{api_method}"
        dur = int(req.duration) if req.duration else 0
        caption = f"- @{BOT_USERNAME}"
        if dur > 0:
            m, s = divmod(dur, 60); h, m = divmod(m, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            caption = f"- @{BOT_USERNAME} , {time_str}"
            
        reply_markup = {"inline_keyboard": [[{"text": "🌟 أعجبك البوت؟ شاركه", "url": f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"}]]}
        data = {'chat_id': req.chat_id, 'caption': caption, 'reply_markup': json.dumps(reply_markup)}
        if req.is_audio: data.update({'title': req.title, 'performer': req.performer, 'duration': req.duration})
        else: data.update({'supports_streaming': 'true', 'duration': req.duration})

        # إرسال الملف مباشرة من القرص الصلب (Streaming) لتوفير الذاكرة ومنع الانهيار
        with open(file_path, 'rb') as f:
            files = {'audio' if req.is_audio else 'video': (filename, f)}
            thumb_data = None
            if req.thumb:
                try:
                    thumb_res = requests.get(req.thumb, timeout=5)
                    if thumb_res.status_code == 200: thumb_data = thumb_res.content
                except: pass
            if thumb_data: files['thumb'] = ('thumb.jpg', thumb_data, 'image/jpeg')
            response = requests.post(url, data=data, files=files, timeout=300)
            
        res_data = response.json()
        if response.status_code == 200 and res_data.get("ok"): return {"success": True}
        else: return {"success": False, "error": res_data.get("description", "تأكد من بدء المحادثة مع البوت.")}
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)