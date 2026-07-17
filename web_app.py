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
BOT_USERNAME = "MusicPlayZoneBot"  # اليوزر الثابت اليدوي للبوت الخاص بك
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

# نظام التنظيف الذاتي لحماية مساحة السيرفر - آمن من الانهيارات
def cleanup_daemon():
    while True:
        try:
            now = time.time()
            for file_path in WEB_DIR.glob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > 86400:
                    file_path.unlink(missing_ok=True)
            
            # معالجة القاموس بشكل آمن لتجنب RuntimeError أثناء التكرار
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

# دالة البحث المعدلة لطلب 25 خيار بحث لجلب نتائج متعددة ونظيفة
def search_youtube(query: str, limit: int = 25):
    opts = get_hardened_ydl_options()
    opts['extract_flat'] = True
    
    if 'playlist_items' in opts:
        del opts['playlist_items']
    if 'noplaylist' in opts:
        del opts['noplaylist']
        
    with yt_dlp.YoutubeDL(opts) as ydl: 
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)


# ==========================================================
# قالب الـ HTML (استخدام نص عادي لمنع تداخل أقواس الـ CSS مع Python)
# ==========================================================
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>PlayZone Cloud | سينما وساحة ألعابك المتكاملة</title>
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
        body { background-color: #030303; color: #f4f4f5; transition: all 0.3s ease; overflow: hidden; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #1f1f2e; border-radius: 10px; }
        
        .modern-input { background: #07070a; border: 1px solid #1f1f2e; color: white; border-radius: 1rem; padding: 0.8rem 1.2rem; outline: none; transition: all 0.3s; width: 100%; }
        .modern-input:focus { border-color: #a855f7; box-shadow: 0 0 15px rgba(168, 85, 247, 0.2); }
        
        .btn { padding: 0.8rem 1.5rem; border-radius: 1rem; font-weight: bold; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; cursor: pointer; user-select: none; position: relative; overflow: hidden; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
        .btn:active:not(:disabled) { transform: scale(0.95); }
        
        @keyframes shimmer { 0% { transform: translateX(100%); } 100% { transform: translateX(-100%); } }
        .shimmer-bg { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent); animation: shimmer 1.5s infinite linear; }

        .view-section { display: none; animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1); }
        .view-section.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
        
        /* -------------------------------------------------- */
        /* تصميم مشغل الوسائط العائم والتفاعلي الجديد كلياً */
        /* -------------------------------------------------- */
        #floatingPlayer {
            position: fixed;
            bottom: 30px;
            left: 30px;
            width: 350px;
            max-width: calc(100vw - 40px);
            background: rgba(11, 11, 15, 0.85);
            backdrop-filter: blur(25px);
            -webkit-backdrop-filter: blur(25px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 24px;
            box-shadow: 0 30px 60px rgba(0,0,0,0.8), 0 0 20px rgba(168, 85, 247, 0.1);
            z-index: 100;
            overflow: hidden;
            transition: width 0.3s cubic-bezier(0.25, 0.8, 0.25, 1), height 0.3s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease;
            display: none;
        }
        
        .drag-handle {
            cursor: move;
            user-select: none;
            touch-action: none;
        }

        /* دوران صورة الألبوم عند تشغيل الموسيقى */
        @keyframes spinDisk {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        .album-spin {
            animation: spinDisk 15s linear infinite;
        }
        .album-paused {
            animation-play-state: paused;
        }

        /* مؤشر حركة الصوت التفاعلي الموهوم */
        .wave-bar {
            width: 3px;
            height: 15px;
            background-color: #a855f7;
            border-radius: 3px;
            animation: bounceWave 1.2s ease-in-out infinite alternate;
        }
        @keyframes bounceWave {
            0% { height: 5px; }
            100% { height: 35px; }
        }
        .wave-bar:nth-child(2) { animation-delay: 0.15s; }
        .wave-bar:nth-child(3) { animation-delay: 0.3s; }
        .wave-bar:nth-child(4) { animation-delay: 0.45s; }
        .wave-bar:nth-child(5) { animation-delay: 0.6s; }

        /* حالة المشغل المصغر جداً */
        #floatingPlayer.compact {
            width: 320px;
            height: 70px;
        }

        /* -------------------------------------------------- */

        #toast { position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-100%); opacity: 0; z-index: 1000; padding: 12px 24px; border-radius: 50px; font-weight: bold; color: white; transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55); box-shadow: 0 10px 25px rgba(0,0,0,0.5); pointer-events: none; }
        #toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
    </style>
</head>
<body class="antialiased flex h-[100dvh] w-full">
    <div id="toast"></div>

    <!-- الشريط الجانبي الأيمن: تم تصغيره بالكامل ليصبح شريطاً نحيفاً وأنيقاً لا يستهلك المساحة -->
    <aside class="w-16 md:w-20 bg-panel border-l border-panelBorder flex flex-col justify-between h-[100dvh] z-40 flex-shrink-0">
        <div>
            <!-- شعار بسيط وأنيق ومضيء -->
            <div class="h-20 flex items-center justify-center border-b border-panelBorder relative">
                <div class="w-10 h-10 bg-accent/10 rounded-2xl flex items-center justify-center text-accent text-xl relative group cursor-pointer">
                    <div class="absolute inset-0 bg-accent/20 rounded-2xl blur-md opacity-0 group-hover:opacity-100 transition-opacity"></div>
                    <i class="fas fa-play z-10 text-base"></i>
                </div>
            </div>

            <!-- أزرار التنقل الذكية المدمجة عمودياً -->
            <nav class="mt-8 px-2 space-y-4">
                <button onclick="switchView('searchView')" id="nav-searchView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl bg-panelBorder text-accent transition-all duration-300 relative group" title="البحث والتحميل">
                    <i class="fas fa-search text-lg"></i>
                    <span class="text-[9px] mt-1 font-bold hidden md:block">البحث</span>
                </button>
                <button onclick="switchView('libraryView')" id="nav-libraryView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl text-textMuted hover:bg-panelBorder/50 hover:text-white transition-all duration-300 relative group" title="مكتبتي المحفوظة">
                    <i class="fas fa-folder text-lg"></i>
                    <span class="text-[9px] mt-1 font-medium hidden md:block">ملفاتي</span>
                </button>
                <button onclick="switchView('settingsView')" id="nav-settingsView" class="nav-btn w-12 h-12 md:w-14 md:h-14 mx-auto flex flex-col items-center justify-center rounded-2xl text-textMuted hover:bg-panelBorder/50 hover:text-white transition-all duration-300 relative group" title="الإعدادات">
                    <i class="fas fa-cog text-lg"></i>
                    <span class="text-[9px] mt-1 font-medium hidden md:block">الإعدادات</span>
                </button>
            </nav>
        </div>
        
        <!-- رابط البوت السفلي المدمج -->
        <div class="p-2 border-t border-panelBorder flex justify-center">
            <a href="https://t.me/{BOT_USERNAME}" target="_blank" class="w-12 h-12 rounded-2xl bg-tgBlue/10 text-tgBlue hover:bg-tgBlue/25 flex items-center justify-center transition-colors" title="افتح البوت">
                <i class="fab fa-telegram-plane text-xl"></i>
            </a>
        </div>
    </aside>

    <!-- مساحة عرض المحتوى الرئيسية -->
    <main class="flex-1 h-[100dvh] overflow-y-auto pb-24 relative scroll-smooth bg-gradient-to-tr from-[#020203] via-[#050508] to-[#0a0a10]">
        
        <!-- قسم البحث السريع -->
        <section id="searchView" class="view-section active p-4 md:p-8 max-w-4xl mx-auto">
            <div class="bg-panel/60 backdrop-blur-md rounded-3xl p-6 md:p-8 border border-panelBorder/60 mb-6 relative overflow-hidden shadow-2xl">
                <div class="absolute -top-10 -left-10 w-44 h-44 bg-accent/5 rounded-full blur-3xl"></div>
                
                <h2 class="text-2xl md:text-3xl font-black mb-2 text-white flex items-center gap-2">
                    <span class="bg-gradient-to-r from-accent to-fuchsia-500 bg-clip-text text-transparent">PlayZone Cloud</span> ⚡
                </h2>
                <p class="text-textMuted mb-6 text-sm">ابحث عن مقاطع الفيديو والأغاني، شاهد الإعلان السريع، وحمّلها فوراً وبأعلى جودة إلى سيرفرك الشخصي وهاتفك.</p>
                
                <div class="flex flex-col md:flex-row gap-3">
                    <input type="text" id="url" placeholder="أدخل رابط المقطع أو ابحث باسمه مباشرة..." class="modern-input flex-1">
                    <button onclick="processInput()" id="mainBtn" class="btn bg-accent hover:bg-accentHover text-white md:w-36 shadow-lg shadow-accent/25">
                        <i class="fas fa-search"></i> ابحث الآن
                    </button>
                </div>
                
                <!-- نتائج البحث المتجاوبة كلياً -->
                <div id="searchResults" class="hidden mt-8 bg-black/40 border border-panelBorder rounded-2xl p-4">
                    <div class="mb-4 pb-3 border-b border-panelBorder/60 flex justify-between items-center">
                        <h3 class="text-white font-bold text-sm md:text-base flex items-center gap-2">🎬 نتائج البحث المقترحة:</h3>
                        <button onclick="document.getElementById('searchResults').classList.add('hidden')" class="text-textMuted hover:text-red-400 p-2"><i class="fas fa-times"></i></button>
                    </div>
                    <div id="searchResultsList" class="flex flex-col gap-3 w-full"></div>
                </div>
            </div>

            <!-- بطاقة المعاينة والتنزيل الذكية -->
            <div id="previewBox" class="hidden bg-panel/60 backdrop-blur-md rounded-3xl p-6 border border-panelBorder/60 shadow-2xl">
                <div class="flex flex-col md:flex-row gap-6 items-center">
                    <div class="w-full md:w-1/3">
                        <img id="thumb" class="w-full rounded-2xl object-cover aspect-video shadow-lg border border-panelBorder">
                    </div>
                    <div class="w-full md:w-2/3 space-y-4">
                        <h3 id="title" class="font-bold text-lg text-white line-clamp-2"></h3>
                        
                        <div id="adGate" class="bg-black/50 border border-panelBorder p-4 rounded-2xl text-center">
                            <p class="text-sm mb-3 text-textMuted font-medium"><i class="fas fa-info-circle text-accent ml-1"></i> يرجى مشاهدة إعلان التمويل لثوانٍ معدودة لبدء التحميل والتحويل.</p>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <a href="{AD_LINK}" target="_blank" onclick="startAdTimer()" class="btn bg-blue-600 text-white flex-1 hover:bg-blue-500"><i class="fas fa-eye"></i> 1. مشاهدة الإعلان الممول</a>
                                <button id="verifyBtn" disabled class="btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder"><i class="fas fa-lock"></i> 2. تحقق وفك القفل</button>
                            </div>
                        </div>

                        <div id="dlOptions" class="hidden space-y-4">
                            <div class="grid grid-cols-2 gap-3">
                                <select id="mode" onchange="toggleRes()" class="modern-input bg-black/60 py-2.5 text-sm"><option value="video">🎬 فيديو (MP4)</option><option value="audio">🎵 صوت (MP3)</option></select>
                                <select id="resolution" class="modern-input bg-black/60 py-2.5 text-sm"><option value="480">جودة 480p</option><option value="720" selected>جودة 720p</option></select>
                            </div>
                            <button onclick="startDownload()" class="btn bg-gradient-to-r from-accent to-fuchsia-600 text-white w-full hover:from-accentHover hover:to-fuchsia-700 shadow-xl shadow-accent/20">
                                <i class="fas fa-download"></i> بدء تنزيل وتحويل الملف
                            </button>
                        </div>

                        <div id="progressBox" class="hidden bg-black/50 p-5 rounded-2xl border border-panelBorder">
                            <div class="flex justify-between items-center mb-3">
                                <span id="progStatus" class="text-accent font-bold text-xs flex items-center gap-2"><i class="fas fa-circle-notch fa-spin"></i> جاري إرسال الطلب...</span>
                                <span id="progPercent" class="font-mono font-bold text-white text-base">0%</span>
                            </div>
                            <div class="w-full bg-zinc-900 rounded-full h-2.5 mb-2 overflow-hidden relative">
                                <div id="progBar" class="bg-gradient-to-r from-accent to-fuchsia-500 h-full relative" style="width: 0%">
                                    <div class="shimmer-bg"></div>
                                </div>
                            </div>
                            <div class="flex justify-between text-[11px] text-textMuted font-mono">
                                <span id="progSize">-- MB / -- MB</span>
                                <span id="progSpeed">-- MB/s</span>
                            </div>
                            
                            <div id="directDownloadArea" class="hidden mt-4 pt-4 border-t border-panelBorder/60">
                                <a id="directDownloadBtn" href="#" download class="btn bg-emerald-600 text-white w-full hover:bg-emerald-500 shadow-lg shadow-emerald-600/20"><i class="fas fa-arrow-alt-circle-down"></i> تحميل وحفظ الملف مباشرة بجهازك 💾</a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- قسم مكتبة الملفات المحفوظة -->
        <section id="libraryView" class="view-section p-4 md:p-8 max-w-6xl mx-auto h-full flex flex-col">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8">
                <div>
                    <h2 class="text-2xl font-black text-white">ملفاتي المحفوظة 📂</h2>
                    <p class="text-textMuted text-xs mt-1">المقاطع التي تم تحميلها مسبقاً وتخزينها سحابياً بجهازك.</p>
                </div>
                <div class="flex flex-wrap gap-2 w-full md:w-auto">
                    <div class="relative flex-1 md:w-64">
                        <i class="fas fa-search absolute right-3.5 top-1/2 transform -translate-y-1/2 text-textMuted text-sm"></i>
                        <input type="text" id="libSearch" oninput="applyFilters()" placeholder="ابحث في ملفاتك..." class="modern-input pl-3 pr-10 py-2 bg-panel/60 text-sm">
                    </div>
                    <select id="libFilter" onchange="applyFilters()" class="modern-input py-2 px-4 w-auto bg-panel text-accent font-bold text-sm">
                        <option value="all">الكل</option>
                        <option value="favorites">❤️ المفضلة</option>
                        <option value="audio">🎵 الصوتيات</option>
                        <option value="video">🎬 الفيديو</option>
                    </select>
                </div>
            </div>
            <div id="libraryContainer" class="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 content-start"></div>
            <div id="pagination" class="mt-8 flex justify-center items-center gap-3 pb-8"></div>
        </section>

        <!-- قسم الإعدادات -->
        <section id="settingsView" class="view-section p-4 md:p-8 max-w-3xl mx-auto">
            <h2 class="text-2xl font-black mb-6 text-white">إعدادات التحكم ⚙️</h2>
            <div class="space-y-6">
                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-2xl">
                    <h3 class="text-base font-bold text-white mb-3 flex items-center gap-2"><i class="fab fa-telegram text-tgBlue text-lg"></i> ربط حساب تيليجرام (مهم للمزامنة)</h3>
                    <p class="text-textMuted text-xs mb-4">أدخل معرف حسابك الشخصي لتمكين السحابة من تمرير وإرسال أي ملف تقوم بتحميله إلى البوت فوراً.</p>
                    <div class="flex gap-3 mb-5">
                        <input type="text" id="settingTgId" placeholder="معرف المستخدم (User ID)" class="modern-input font-mono bg-black/45">
                        <button onclick="updateTgId()" class="btn bg-tgBlue hover:bg-opacity-90 text-white px-6">حفظ الحساب</button>
                    </div>
                    <div class="flex items-center justify-between p-4 bg-black/40 rounded-2xl border border-panelBorder">
                        <div>
                            <p class="font-bold text-white text-sm">الإرسال الفوري المؤتمت</p>
                            <p class="text-xs text-textMuted mt-0.5">تمرير الفيديوهات والأغاني لدردشتك تلقائياً فور نجاح التحميل.</p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="autoForwardToggle" onchange="toggleAutoForward()" class="sr-only peer" checked>
                            <div class="w-11 h-6 bg-zinc-800 rounded-full peer peer-checked:after:-translate-x-full peer-checked:bg-accent after:content-[''] after:absolute after:top-[2px] after:right-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all"></div>
                        </label>
                    </div>
                </div>

                <div class="bg-panel rounded-3xl p-6 border border-panelBorder shadow-2xl">
                    <h3 class="text-base font-bold text-white mb-3 flex items-center gap-2"><i class="fas fa-database text-red-400"></i> صيانة البيانات وقاعدة السيرفر</h3>
                    <div class="flex justify-between items-center p-4 bg-black/40 rounded-2xl border border-panelBorder">
                        <div>
                            <p class="font-bold text-white text-sm" id="libCountStatus">مجموع السجلات (0)</p>
                            <p class="text-xs text-textMuted mt-0.5">قاعدة البيانات المحلية المتواجدة على متصفحك.</p>
                        </div>
                        <button onclick="clearAllLibrary()" class="btn bg-red-500/10 text-red-500 hover:bg-red-500/20 text-xs px-4">حذف السجل بالكامل</button>
                    </div>
                </div>
            </div>
        </section>
    </main>

    <!-- ========================================================== -->
    <!-- مشغل الوسائط العائم والمطور كلياً (يدعم الصوت والفيديو والتحريك) -->
    <!-- ========================================================== -->
    <div id="floatingPlayer" class="active">
        <!-- ترويسة المشغل القابلة للسحب والتحريك -->
        <div id="playerHeader" class="drag-handle p-3.5 bg-black/60 border-b border-panelBorder/50 flex items-center justify-between">
            <div class="flex items-center gap-2 overflow-hidden flex-1">
                <i class="fas fa-grip-vertical text-textMuted cursor-grab px-1 text-xs"></i>
                <div class="overflow-hidden w-full">
                    <p id="playerTitle" class="font-bold text-xs text-white truncate w-full">لا يوجد تشغيل حالياً</p>
                </div>
            </div>
            <!-- أزرار نافذة التحكم -->
            <div class="flex items-center gap-2 flex-shrink-0 mr-2">
                <button onclick="toggleCompactMode()" class="text-textMuted hover:text-accent p-1 transition-colors" title="تصغير/تكبير"><i class="fas fa-compress-alt text-xs"></i></button>
                <button onclick="resetPlayerPosition()" class="text-textMuted hover:text-white p-1 transition-colors" title="إعادة تعيين الموضع"><i class="fas fa-redo-alt text-[10px]"></i></button>
                <button onclick="closePlayer()" class="text-textMuted hover:text-red-400 p-1 transition-colors"><i class="fas fa-times text-xs"></i></button>
            </div>
        </div>

        <!-- جسم المشغل التفاعلي -->
        <div id="playerBody" class="p-4 transition-all duration-300">
            <!-- 1. شاشة عرض الفيديو (تظهر فقط عند تشغيل فيديو) -->
            <div id="videoContainer" class="hidden w-full aspect-video rounded-xl overflow-hidden bg-black mb-3 border border-panelBorder">
                <video id="globalVideoElement" class="w-full h-full object-contain" ontimeupdate="updatePlayerProgress()" onended="handleMediaEnd()"></video>
            </div>

            <!-- 2. واجهة تشغيل الأغاني والصوتيات (تظهر فقط عند تشغيل صوت) -->
            <div id="audioVisualizer" class="flex flex-col items-center justify-center py-4 mb-2">
                <!-- قرص الفينيل الدوار -->
                <div id="diskContainer" class="relative w-28 h-28 rounded-full border-4 border-zinc-800 shadow-xl overflow-hidden mb-3">
                    <img id="playerCoverImg" src="https://via.placeholder.com/150" class="w-full h-full object-cover album-spin album-paused">
                    <div class="absolute inset-0 bg-gradient-to-tr from-black/40 to-transparent"></div>
                    <div class="absolute w-6 h-6 bg-zinc-950 rounded-full top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 border-2 border-zinc-700 flex items-center justify-center">
                        <div class="w-1.5 h-1.5 bg-white rounded-full"></div>
                    </div>
                </div>
                <!-- أعمدة التردد المتفاعلة محاكاة للصوت -->
                <div id="visualizerBars" class="flex gap-1.5 items-end h-8 justify-center mt-1 hidden">
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                    <div class="wave-bar"></div>
                </div>
            </div>

            <!-- خط تتبع الوقت والتقدم -->
            <div class="relative w-full h-1 bg-zinc-800 rounded-full mb-3 cursor-pointer group" id="progressContainer" onclick="seekMedia(event)">
                <div id="mediaProgressBar" class="absolute h-full bg-accent rounded-full w-0 transition-all duration-100 relative">
                    <div class="absolute -right-2 -top-1 w-3 h-3 bg-white border-2 border-accent rounded-full scale-0 group-hover:scale-100 transition-transform"></div>
                </div>
            </div>

            <!-- معلومات وتفاصيل الوقت -->
            <div class="flex justify-between items-center text-[10px] text-textMuted font-mono mb-4">
                <span id="playerTime">0:00 / 0:00</span>
                <span id="trackSource" class="bg-accent/10 text-accent px-1.5 py-0.5 rounded text-[9px] font-bold">MODE</span>
            </div>

            <!-- أزرار التحكم في التشغيل -->
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-3">
                    <button onclick="toggleShuffle()" id="shuffleBtn" class="text-textMuted hover:text-white transition-colors text-sm" title="عشوائي"><i class="fas fa-random"></i></button>
                    <button onclick="playPrev()" class="text-white hover:text-accent transition-colors text-base" title="السابق"><i class="fas fa-step-backward"></i></button>
                </div>

                <button onclick="togglePlay()" id="playPauseBtn" class="w-12 h-12 rounded-full bg-accent hover:bg-accentHover text-white flex items-center justify-center text-base shadow-lg shadow-accent/30 active:scale-95 transition-all" title="تشغيل / إيقاف">
                    <i class="fas fa-play ml-0.5"></i>
                </button>

                <div class="flex items-center gap-3">
                    <button onclick="playNext()" class="text-white hover:text-accent transition-colors text-base" title="التالي"><i class="fas fa-step-forward"></i></button>
                    <button onclick="toggleRepeat()" id="repeatBtn" class="text-textMuted hover:text-white transition-colors text-sm" title="تكرار"><i class="fas fa-redo"></i></button>
                </div>
            </div>

            <!-- خيارات إضافية متقدمة (سرعة التشغيل، التحكم بالصوت) -->
            <div id="playerAdvancedRow" class="flex items-center justify-between mt-4 pt-3 border-t border-panelBorder/40">
                <button onclick="changeSpeed()" id="speedBtn" class="text-[10px] font-bold font-mono px-2 py-1 border border-panelBorder rounded-lg text-textMuted hover:text-white transition-colors">1.0x</button>
                
                <div class="flex items-center gap-2">
                    <button onclick="toggleMute()" id="muteBtn" class="text-textMuted hover:text-white"><i class="fas fa-volume-up text-xs"></i></button>
                    <input type="range" id="volumeSlider" min="0" max="1" step="0.05" value="1" oninput="changeVolume()" class="w-16 h-1 bg-zinc-800 accent-accent rounded-lg cursor-pointer">
                </div>

                <button onclick="triggerPiP()" id="pipBtn" class="text-textMuted hover:text-white hidden" title="صورة داخل صورة"><i class="fas fa-clone text-xs"></i></button>
            </div>
        </div>
    </div>

    <!-- نافذة ربط حساب تيليجرام المنبثقة -->
    <div id="tgModal" class="fixed inset-0 bg-black/85 backdrop-blur-md z-[200] hidden flex-col items-center justify-center p-4">
        <div class="bg-panel border border-panelBorder p-6 rounded-3xl max-w-sm w-full text-center shadow-2xl" id="tgModalContent">
            <div class="w-14 h-14 bg-tgBlue/20 text-tgBlue rounded-2xl flex items-center justify-center mx-auto mb-4 text-2xl"><i class="fab fa-telegram-plane"></i></div>
            <h3 class="text-lg font-bold mb-1 text-white">ربط السحاب بـ Telegram</h3>
            <p class="text-textMuted text-xs mb-5">مطلوب معرف حسابك (User ID) ليتمكن البرنامج من إرسال وتمرير الملفات تلقائياً عبر البوت الخاص بك.</p>
            <button onclick="window.open('https://t.me/{BOT_USERNAME}', '_blank')" class="btn bg-tgBlue text-white w-full mb-3 text-sm"><i class="fas fa-robot"></i> 1. الدخول للبوت ونسخ المعرف</button>
            <input type="text" id="tgIdInput" placeholder="2. ألصق المعرف الرقمي هنا" class="modern-input bg-black/60 text-center text-sm mb-4 font-mono py-2">
            <div class="flex gap-2">
                <button onclick="saveTgIdFromModal()" class="btn bg-accent text-white flex-1 text-xs py-2">حفظ ومتابعة</button>
                <button onclick="closeTgModal()" class="btn bg-panelBorder text-textMuted flex-1 hover:text-white text-xs py-2">تجاهل الآن</button>
            </div>
        </div>
    </div>

    <!-- كود JavaScript آمن جداً ومبني للعمل مع السيرفر دون مشاكل -->
    <script>
        // المتغيرات السحابية العامة
        let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
        let currentUrl = "";
        let adWatched = false;
        let currentPlayingIndex = -1;
        let isShuffle = false;
        let isRepeat = false;
        let libraryPage = 1;
        const itemsPerPage = 6;
        let isMuted = false;
        let lastVolume = 1;
        let currentPlayingMode = 'audio'; // 'audio' or 'video'

        // تحديد عناصر الوسائط والمشغل
        const mediaContainer = document.getElementById('floatingPlayer');
        const videoElement = document.getElementById('globalVideoElement');
        let currentActiveMedia = videoElement; // تم توحيد تشغيل الصوت والفيديو باستخدام عنصر الـ <video> الذكي لتجنب تعارضات التشغيل

        window.addEventListener('DOMContentLoaded', () => {
            // التحقق والربط الذاتي ببيئة تيليجرام
            if (window.Telegram && window.Telegram.WebApp) {
                window.Telegram.WebApp.ready();
                window.Telegram.WebApp.expand();
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
            setupDraggable(mediaContainer, document.getElementById('playerHeader'));
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
                toast.style.background = "linear-gradient(135deg, #f43f5e, #e11d48)";
            } else {
                toast.style.background = "linear-gradient(135deg, #a855f7, #7c3aed)";
            }
            setTimeout(() => { toast.className = ""; }, 3000);
        }

        function switchView(viewId) {
            document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
            document.getElementById(viewId).classList.add('active');
            
            document.querySelectorAll('.nav-btn').forEach(btn => {
                btn.classList.remove('bg-panelBorder', 'text-accent');
                btn.classList.add('text-textMuted', 'hover:bg-panelBorder/50', 'hover:text-white');
            });
            
            const activeBtn = document.getElementById('nav-' + viewId);
            if (activeBtn) {
                activeBtn.classList.remove('text-textMuted', 'hover:bg-panelBorder/50', 'hover:text-white');
                activeBtn.classList.add('bg-panelBorder', 'text-accent');
            }
            
            if (viewId === 'libraryView') {
                applyFilters();
            }
        }

        // فلترة وعرض ملفات المكتبة الذكية
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
                    <div class="col-span-full py-16 text-center text-textMuted">
                        <i class="fas fa-folder-open text-5xl mb-4 text-zinc-800 block"></i>
                        لا توجد نتائج مطابقة لبحثك في ملفاتك.
                    </div>
                `;
                document.getElementById('pagination').innerHTML = "";
                return;
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
                            <img src="${item.thumb || 'https://via.placeholder.com/150'}" class="w-full h-full object-cover" onerror="this.src='https://via.placeholder.com/150'">
                            <div class="absolute inset-0 bg-black/60 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                <i class="fas fa-play text-white text-base"></i>
                            </div>
                            <div class="absolute bottom-1 right-1 bg-black/80 text-[9px] px-1.5 font-mono rounded text-white">${durationStr}</div>
                        </div>
                        <div class="flex-1 min-w-0 text-right">
                            <h4 class="text-white font-bold text-xs md:text-sm truncate cursor-pointer hover:text-accent" onclick="playMediaTrack(${actualIndex})">${item.title}</h4>
                            <p class="text-textMuted text-[10px] md:text-xs mt-1 truncate">${icon} ${item.uploader || 'غير معروف'}</p>
                        </div>
                        <div class="flex items-center gap-1.5 flex-row-reverse">
                            <button onclick="deleteFromLibrary('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-red-400 active:scale-90 transition-transform" title="حذف">
                                <i class="fas fa-trash-alt text-xs"></i>
                            </button>
                            
                            <a href="${item.url}" download="${item.title}.${fileExt}" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-emerald-400 active:scale-90 transition-transform flex items-center justify-center" title="تحميل للجهاز">
                                <i class="fas fa-download text-xs"></i>
                            </a>
                            
                            <button onclick="triggerSendToTelegram('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-tgBlue active:scale-90 transition-transform" title="تمرير لتيليجرام">
                                <i class="fab fa-telegram-plane text-xs"></i>
                            </button>
                            <button onclick="toggleFavorite('${item.id}')" class="p-2 bg-black/40 rounded-xl border border-panelBorder/40 text-textMuted hover:text-red-500 active:scale-90 transition-transform" title="مفضلة">
                                <i class="${favClass} text-xs"></i>
                            </button>
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

            let html = '<button onclick="changePage(' + (libraryPage - 1) + ')" ' + (libraryPage === 1 ? 'disabled' : '') + ' class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">السابق</button>';
            for (let i = 1; i <= totalPages; i++) {
                const activeClass = (libraryPage === i) ? 'bg-accent text-white' : 'bg-panel border border-panelBorder text-textMuted';
                html += '<button onclick="changePage(' + i + ')" class="btn px-3 py-1.5 ' + activeClass + ' text-xs font-mono">' + i + '</button>';
            }
            html += '<button onclick="changePage(' + (libraryPage + 1) + ')" ' + (libraryPage === totalPages ? 'disabled' : '') + ' class="btn px-3 py-1.5 bg-panel border border-panelBorder text-xs text-textMuted hover:text-white disabled:opacity-40">التالي</button>';
            pagBox.innerHTML = html;
        }

        function changePage(page) {
            libraryPage = page;
            applyFilters();
        }

        function toggleFavorite(id) {
            const index = myLibrary.findIndex(i => i.id === id);
            if (index !== -1) {
                myLibrary[index].favorite = !myLibrary[index].favorite;
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
                showToast(myLibrary[index].favorite ? "أضيف للمفضلة ❤️" : "تمت الإزالة من المفضلة", "success");
            }
        }

        function deleteFromLibrary(id) {
            if (confirm("هل تريد حذف هذا الملف نهائياً من قائمتك؟")) {
                myLibrary = myLibrary.filter(i => i.id !== id);
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
                updateLibraryCount();
                showToast("تم الحذف بنجاح", "success");
            }
        }

        function updateLibraryCount() {
            const count = myLibrary.length;
            document.getElementById('libCountStatus').innerText = "مجموع السجلات (" + count + ")";
        }

        function clearAllLibrary() {
            if (confirm("تحذير: هل أنت متأكد من رغبتك في مسح السجل بالكامل؟")) {
                myLibrary = [];
                localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                applyFilters();
                updateLibraryCount();
                showToast("تم تصفير السجل", "success");
            }
        }

        function updateTgId() {
            const tgId = document.getElementById('settingTgId').value.trim();
            if (!tgId) {
                localStorage.removeItem('pz_tg_id');
                showToast("تم إزالة معرف تيليجرام", "success");
            } else {
                localStorage.setItem('pz_tg_id', tgId);
                showToast("تم ربط حساب تيليجرام الخاص بك", "success");
            }
        }

        function toggleAutoForward() {
            const val = document.getElementById('autoForwardToggle').checked;
            localStorage.setItem('pz_auto_tg', val ? 'true' : 'false');
            showToast(val ? "تم تنشيط الإرسال التلقائي" : "تم إيقاف الإرسال التلقائي", "success");
        }

        // معالجة النافذة المنبثقة لتيليجرام
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
            if (!val) return showToast("يرجى إدخال معرف رقمي صالح", "error");
            
            localStorage.setItem('pz_tg_id', val);
            document.getElementById('settingTgId').value = val;
            closeTgModal();
            showToast("تم الربط بنجاح!", "success");
            
            if (pendingTgItem) {
                sendToTelegram(pendingTgItem.url, pendingTgItem.is_audio, false, pendingTgItem.title, pendingTgItem.uploader, pendingTgItem.duration, pendingTgItem.thumb);
            }
        }

        async function sendToTelegram(fileUrl, isAudio, auto = false, title = "مقطع", performer = "PlayZone", duration = 0, thumb = "") {
            const chatId = localStorage.getItem('pz_tg_id');
            if (!chatId) {
                if (auto) showToast("فشل الإرسال التلقائي (لم يربط الحساب)", "error");
                return;
            }

            if (auto) showToast("جاري إرسال الملف تلقائياً لبوتك... 🚀", "success");
            else showToast("جاري الإرسال لبوت تيليجرام الخاص بك... 🚀", "success");

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
                    showToast("وصل الملف لبوت تيليجرام بنجاح! 🎉", "success");
                } else {
                    showToast("خطأ أثناء الإرسال: " + data.error, "error");
                }
            } catch(e) {
                showToast("خطأ في الربط ببوت الإرسال", "error");
            }
        }

        // معالجة البحث العام
        async function processInput() {
            const input = document.getElementById('url').value.trim(); 
            if(!input) return;
            const btn = document.getElementById('mainBtn'); btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري...'; btn.disabled = true;
            
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
                            box.innerHTML += `
                            <div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="w-full flex items-center p-3 bg-panel border border-panelBorder rounded-2xl cursor-pointer hover:border-accent/50 transition-all active:scale-[0.98] shadow-md mb-1">
                                <div class="flex-shrink-0 w-24 h-14 rounded-xl overflow-hidden border border-panelBorder relative ml-3">
                                    <img src="${v.thumbnail}" class="w-full h-full object-cover">
                                    <div class="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono">${duration}</div>
                                </div>
                                <div class="flex-1 min-w-0 flex flex-col justify-center text-right">
                                    <h4 class="text-white font-bold text-xs md:text-sm truncate w-full mb-1" dir="auto">${v.title}</h4>
                                    <p class="text-textMuted text-[10px] md:text-xs truncate w-full" dir="auto">
                                        <i class="fas fa-user-circle text-accent/70 ml-1"></i> ${v.uploader}
                                    </p>
                                </div>
                                <div class="flex-shrink-0 w-8 h-8 rounded-full bg-black/40 border border-panelBorder flex items-center justify-center text-accent mr-2">
                                    <i class="fas fa-download text-xs"></i>
                                </div>
                            </div>`;
                        });
                        document.getElementById('searchResults').classList.remove('hidden');
                    } else showToast("لم نعثر على نتائج مطابقة", "error");
                } catch(e) { showToast("خطأ في الاتصال بسيرفر البحث", "error"); }
            }
            btn.innerHTML = '<i class="fas fa-search"></i> ابحث الآن'; btn.disabled = false;
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
                    vBtn.className = "btn bg-panel text-textMuted flex-1 cursor-not-allowed border border-panelBorder";
                    vBtn.innerHTML = '<i class="fas fa-lock"></i> 2. تحقق وفك القفل'; adWatched = false;
                } else showToast("هذا الرابط غير متاح للتنزيل حالياً", "error");
            } catch(e) { showToast("حدث خطأ في تحميل المعاينة", "error"); }
        }

        function toggleRes() { 
            document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; 
        }

        function startAdTimer() {
            if(adWatched) return;
            let btn = document.getElementById('verifyBtn'); let timeLeft = 5;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري التحقق (' + timeLeft + ')...';
            let timer = setInterval(() => {
                timeLeft--;
                if(timeLeft > 0) btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري التحقق (' + timeLeft + ')...'; 
                else {
                    clearInterval(timer); btn.disabled = false; btn.onclick = unlockDownload; 
                    btn.className = "btn bg-green-600 text-white hover:bg-green-500 shadow-lg shadow-green-500/30 flex-1";
                    btn.innerHTML = "<i class='fas fa-unlock-alt'></i> 2. تحقق وفك القفل"; adWatched = true;
                }
            }, 1000);
        }

        function unlockDownload() {
            if(!adWatched) return;
            document.getElementById('adGate').classList.add('hidden'); document.getElementById('dlOptions').classList.remove('hidden');
        }

        async function startDownload() {
            const btn = event.currentTarget; const original = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التجهيز...'; btn.disabled = true;
            document.getElementById('dlOptions').classList.add('hidden'); document.getElementById('progressBox').classList.remove('hidden');
            
            document.getElementById('directDownloadArea').classList.add('hidden');
            
            document.getElementById('progPercent').innerText = '0%';
            document.getElementById('progBar').style.width = '0%';
            document.getElementById('progSize').innerText = '-- / --';
            document.getElementById('progSpeed').innerText = '--';
            document.getElementById('progStatus').innerHTML = '<i class="fas fa-cloud-download-alt"></i> جاري الاتصال...';

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
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري تحميل الملف...';
                                document.getElementById('progPercent').innerText = prog.percent + '%';
                                document.getElementById('progBar').style.width = prog.percent + '%';
                                document.getElementById('progSize').innerText = prog.dl_mb + ' / ' + prog.total_mb;
                                document.getElementById('progSpeed').innerText = prog.spd_mb;
                            } 
                            else if(prog.status === 'converting') { 
                                document.getElementById('progStatus').innerHTML = '<i class="fas fa-cog fa-spin"></i> جاري معالجة وتجهيز الملف النهائي...'; 
                                document.getElementById('progBar').style.width = '100%'; 
                            } 
                            else if(prog.status === 'completed') {
                                clearInterval(interval); 
                                document.getElementById('progStatus').innerHTML = '<span class="text-green-400"><i class="fas fa-check-circle"></i> اكتمل التحميل بنجاح</span>';
                                
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
                                showToast("أضيف إلى ملفاتك المحفوظة 🎉", "success");

                                if(localStorage.getItem('pz_auto_tg') !== 'false') {
                                    sendToTelegram(prog.url, prog.is_audio, true, prog.title, prog.uploader, prog.duration, prog.thumb);
                                }
                            } 
                            else if(prog.status === 'error') { clearInterval(interval); document.getElementById('progStatus').innerHTML = '<span class="text-red-500">فشل التحميل</span>'; }
                        } catch(err) {}
                    }, 800);
                }
            } catch(e) { showToast("فشل بدء عملية التحميل", "error"); }
            btn.innerHTML = original; btn.disabled = false;
        }

        // =======================================================
        // نظام مشغل الوسائط العائم الذكي (فيديو + صوت وموجات متفاعلة)
        // =======================================================
        
        function playMediaTrack(index) {
            currentPlayingIndex = index;
            const track = myLibrary[index];
            if (!track) return;
            
            // إظهار لوحة المشغل العائم في حال كانت مخفية
            mediaContainer.style.display = 'block';
            
            // تحديد نوع الوسائط
            if (track.is_audio) {
                currentPlayingMode = 'audio';
                document.getElementById('trackSource').innerText = '🎵 صوتي';
                document.getElementById('videoContainer').classList.add('hidden');
                document.getElementById('audioVisualizer').classList.remove('hidden');
                document.getElementById('pipBtn').classList.add('hidden');
                document.getElementById('playerCoverImg').src = track.thumb || 'https://via.placeholder.com/150';
                
                // تنشيط قرص الصوت والترددات التفاعلية
                document.getElementById('playerCoverImg').classList.remove('album-paused');
                document.getElementById('visualizerBars').classList.remove('hidden');
            } else {
                currentPlayingMode = 'video';
                document.getElementById('trackSource').innerText = '🎬 سينمائي';
                document.getElementById('videoContainer').classList.remove('hidden');
                document.getElementById('audioVisualizer').classList.add('hidden');
                document.getElementById('pipBtn').classList.remove('hidden');
            }

            // تحميل المقطع الصوتي أو الفيديو في الـ Player الموحد
            videoElement.src = track.url;
            videoElement.load();
            videoElement.play()
                .then(() => {
                    document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
                })
                .catch(e => {
                    showToast("لم نتمكن من تشغيل هذا الملف", "error");
                    document.getElementById('playerCoverImg').classList.add('album-paused');
                    document.getElementById('visualizerBars').classList.add('hidden');
                });
            
            document.getElementById('playerTitle').innerText = track.title;
            
            // إلغاء الوضع المصغر عند تشغيل مقطع جديد لتسهيل رؤيته
            if (mediaContainer.classList.contains('compact')) {
                toggleCompactMode();
            }
        }

        function togglePlay() {
            if (videoElement.paused) {
                videoElement.play().then(() => {
                    document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
                    document.getElementById('playerCoverImg').classList.remove('album-paused');
                    document.getElementById('visualizerBars').classList.remove('hidden');
                }).catch(e => {});
            } else {
                videoElement.pause();
                document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-play ml-0.5"></i>';
                document.getElementById('playerCoverImg').classList.add('album-paused');
                document.getElementById('visualizerBars').classList.add('hidden');
            }
        }

        function playNext() {
            if (myLibrary.length === 0) return;

            if (isShuffle) {
                const rand = Math.floor(Math.random() * myLibrary.length);
                playMediaTrack(rand);
            } else {
                let nextIdx = currentPlayingIndex + 1;
                if (nextIdx >= myLibrary.length) nextIdx = 0;
                playMediaTrack(nextIdx);
            }
        }

        function playPrev() {
            if (myLibrary.length === 0) return;

            let prevIdx = currentPlayingIndex - 1;
            if (prevIdx < 0) prevIdx = myLibrary.length - 1;
            playMediaTrack(prevIdx);
        }

        function toggleShuffle() {
            isShuffle = !isShuffle;
            const btn = document.getElementById('shuffleBtn');
            if (isShuffle) {
                btn.className = "text-accent hover:text-white transition-colors text-sm";
                showToast("تفعيل وضع التشغيل العشوائي 🔀", "success");
            } else {
                btn.className = "text-textMuted hover:text-white transition-colors text-sm";
            }
        }

        function toggleRepeat() {
            isRepeat = !isRepeat;
            const btn = document.getElementById('repeatBtn');
            if (isRepeat) {
                btn.className = "text-accent hover:text-white transition-colors text-sm";
                showToast("تفعيل وضع تكرار المقطع 🔁", "success");
            } else {
                btn.className = "text-textMuted hover:text-white transition-colors text-sm";
            }
        }

        function updatePlayerProgress() {
            const cur = videoElement.currentTime;
            const dur = videoElement.duration;
            if (isNaN(dur)) return;
            
            const pct = (cur / dur) * 100;
            document.getElementById('mediaProgressBar').style.width = pct + '%';
            document.getElementById('playerTime').innerText = formatTime(cur) + ' / ' + formatTime(dur);
        }

        function seekMedia(e) {
            const container = document.getElementById('progressContainer');
            const rect = container.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const pct = clickX / rect.width;
            if (!isNaN(videoElement.duration)) {
                videoElement.currentTime = pct * videoElement.duration;
            }
        }

        function changeVolume() {
            const val = document.getElementById('volumeSlider').value;
            videoElement.volume = val;
            lastVolume = val;
            
            const muteIcon = document.getElementById('muteBtn').querySelector('i');
            if (val == 0) {
                muteIcon.className = "fas fa-volume-mute text-xs";
                isMuted = true;
            } else {
                muteIcon.className = "fas fa-volume-up text-xs";
                isMuted = false;
            }
        }

        function toggleMute() {
            const muteIcon = document.getElementById('muteBtn').querySelector('i');
            if (isMuted) {
                videoElement.volume = lastVolume || 1;
                document.getElementById('volumeSlider').value = lastVolume || 1;
                muteIcon.className = "fas fa-volume-up text-xs";
                isMuted = false;
            } else {
                videoElement.volume = 0;
                document.getElementById('volumeSlider').value = 0;
                muteIcon.className = "fas fa-volume-mute text-xs";
                isMuted = true;
            }
        }

        let currentSpeed = 1;
        function changeSpeed() {
            const speeds = [1, 1.25, 1.5, 1.75, 2];
            let idx = speeds.indexOf(currentSpeed);
            idx = (idx + 1) % speeds.length;
            currentSpeed = speeds[idx];
            videoElement.playbackRate = currentSpeed;
            document.getElementById('speedBtn').innerText = currentSpeed + 'x';
        }

        function handleMediaEnd() {
            if (isRepeat) {
                videoElement.currentTime = 0;
                videoElement.play().catch(e => {});
            } else {
                playNext();
            }
        }

        function closePlayer() {
            videoElement.pause();
            mediaContainer.style.display = 'none';
        }

        // تفعيل وضع "صورة داخل صورة" (Picture in Picture)
        function triggerPiP() {
            if (document.pictureInPictureEnabled && videoElement) {
                if (document.pictureInPictureElement) {
                    document.exitPictureInPicture();
                } else {
                    videoElement.requestPictureInPicture();
                }
            }
        }

        // تبديل حجم المشغل بين المظهر الكامل والتصغير الذكي
        function toggleCompactMode() {
            const body = document.getElementById('playerBody');
            const icon = event.currentTarget.querySelector('i');
            
            if (mediaContainer.classList.contains('compact')) {
                mediaContainer.classList.remove('compact');
                body.classList.remove('hidden');
                icon.className = "fas fa-compress-alt text-xs";
            } else {
                mediaContainer.classList.add('compact');
                body.classList.add('hidden');
                icon.className = "fas fa-expand-alt text-xs";
            }
        }

        function resetPlayerPosition() {
            mediaContainer.style.top = 'auto';
            mediaContainer.style.left = '30px';
            mediaContainer.style.bottom = '30px';
            mediaContainer.style.right = 'auto';
            mediaContainer.style.transform = 'none';
            showToast("تم إعادة تعيين مكان المشغل", "success");
        }

        // =======================================================
        // كود تحريك وسحب مشغل الوسائط على الشاشة (Mouse & Touch)
        // =======================================================
        function setupDraggable(element, handle) {
            let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
            
            handle.onmousedown = dragMouseDown;
            handle.ontouchstart = dragMouseDown;

            function dragMouseDown(e) {
                e = e || window.event;
                if (e.type === 'mousedown' && e.button !== 0) return; // سحب بالزر الأيسر فقط
                
                if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON' && !e.target.closest('button')) {
                    // منع التحريك التلقائي المتعارض مع المتصفح
                } else {
                    return; // السماح للأزرار والمدخلات بالعمل فوراً
                }

                const clientX = e.type === 'touchstart' ? e.touches[0].clientX : e.clientX;
                const clientY = e.type === 'touchstart' ? e.touches[0].clientY : e.clientY;

                pos3 = clientX;
                pos4 = clientY;
                
                document.onmouseup = closeDragElement;
                document.onmousemove = elementDrag;
                document.ontouchend = closeDragElement;
                document.ontouchmove = elementDrag;
                
                element.style.transition = 'none'; // تعطيل الترانزيشن أثناء السحب المباشر
            }

            function elementDrag(e) {
                e = e || window.event;
                
                const clientX = e.type === 'touchmove' ? e.touches[0].clientX : e.clientX;
                const clientY = e.type === 'touchmove' ? e.touches[0].clientY : e.clientY;

                pos1 = pos3 - clientX;
                pos2 = pos4 - clientY;
                pos3 = clientX;
                pos4 = clientY;
                
                let newTop = element.offsetTop - pos2;
                let newLeft = element.offsetLeft - pos1;
                
                // منع خروج المشغل تماماً من الشاشة وحمايته
                const maxLeft = window.innerWidth - element.offsetWidth - 10;
                const maxTop = window.innerHeight - element.offsetHeight - 10;
                
                newLeft = Math.max(10, Math.min(newLeft, maxLeft));
                newTop = Math.max(10, Math.min(newTop, maxTop));
                
                element.style.top = newTop + "px";
                element.style.left = newLeft + "px";
                element.style.bottom = "auto";
                element.style.right = "auto";
            }

            function closeDragElement() {
                document.onmouseup = null;
                document.onmousemove = null;
                document.ontouchend = null;
                document.ontouchmove = null;
                element.style.transition = 'width 0.3s ease, height 0.3s ease, opacity 0.3s ease';
            }
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    # استبدال آمن للمتغيرات في القالب دون الإضرار بـ JavaScript المدمج
    html = INDEX_HTML.replace("{BOT_USERNAME}", BOT_USERNAME).replace("{AD_LINK}", AD_LINK)
    return HTMLResponse(content=html)


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
                [{"text": "🌟 أعجبك البوت؟ شاركه", "url": "https://t.me/share/url?url=https://t.me/P1ay_Z0ne_Bot"}]
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
