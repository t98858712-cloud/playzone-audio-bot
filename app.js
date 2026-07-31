const BACKEND_URL = window.location.origin.includes('github.io') || window.location.origin.includes('localhost')
    ? 'https://your-playzone-bot-backend.up.railway.app'
    : window.location.origin;

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const isMiniApp = tg && tg.initData && tg.initData !== "";

const DB_NAME = 'PlayZoneOfflineStore_Persistent';
const DB_VERSION = 1;
const STORE_NAME = 'media_blobs';

let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
});

function setupDynamicManifest() {
    const logoUrl = "https://mgx-backend-cdn.metadl.com/generate/images/1300473/2026-07-24/tcy6smycajsa/playzone-logo-dark.png";
    const manifestData = {
        "name": "PlayZone Music",
        "short_name": "PlayZone",
        "start_url": window.location.href,
        "display": "standalone",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            { "src": logoUrl, "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
            { "src": logoUrl, "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
        ]
    };
    const stringManifest = JSON.stringify(manifestData);
    const blob = new Blob([stringManifest], {type: 'application/json'});
    const manifestElem = document.getElementById('manifestLink');
    if (manifestElem) manifestElem.setAttribute('href', URL.createObjectURL(blob));
}

function checkStandaloneAndHideBar() {
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone || isMiniApp;
    if (isStandalone) {
        const bar = document.getElementById('topShortcutBar');
        if (bar) bar.style.display = 'none';

        const statusText = document.getElementById('shortcutStatusText');
        const btnsGroup = document.getElementById('shortcutButtonsGroup');
        if (statusText) statusText.innerText = "التطبيق مضاف ومفعل حالياً على شاشتك الرئيسية 🎉";
        if (btnsGroup) btnsGroup.style.display = 'none';
    }
}

window.triggerDirectInstall = async function() {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    if (isIOS) {
        showIosInstructions();
    } else if (deferredPrompt) {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
            showToast("تمت إضافة الاختصار إلى الشاشة الرئيسية بنجاح! 🎉", "success");
        }
        deferredPrompt = null;
    } else {
        installAndroidApp();
    }
};

window.installAndroidApp = async function() {
    if (window.matchMedia('(display-mode: standalone)').matches || navigator.standalone) {
        showToast("الاختصار موجود بالفعل على شاشتك الرئيسية 🎉", "success");
        return;
    }
    if (isMiniApp && tg && tg.openLink) {
        showToast("جاري فتح الموقع في المتصفح لإضافة الاختصار... 📲", "success");
        try {
            tg.openLink(window.location.href, { try_instant_view: false });
        } catch(e) {
            window.open(window.location.href, '_blank');
        }
        return;
    }
    if (deferredPrompt) {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
            showToast("تمت إضافة الاختصار إلى الشاشة الرئيسية بنجاح! 🎉", "success");
        }
        deferredPrompt = null;
    } else {
        document.getElementById('androidModal').classList.replace('hidden', 'flex');
    }
};

window.closeAndroidModal = function() {
    document.getElementById('androidModal').classList.replace('flex', 'hidden');
};

window.showIosInstructions = function() {
    document.getElementById('iosModal').classList.replace('hidden', 'flex');
};

window.closeIosModal = function() {
    document.getElementById('iosModal').classList.replace('flex', 'hidden');
};

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME);
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    });
}

async function saveTrackBlob(id, blob) {
    try {
        if (navigator.storage && navigator.storage.persist) {
            await navigator.storage.persist();
        }
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).put(blob, id);
        return new Promise((resolve, reject) => {
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => reject(tx.error);
        });
    } catch(e) { console.error('Error saving blob to IndexedDB', e); }
}

async function getTrackBlob(id) {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const req = tx.objectStore(STORE_NAME).get(id);
        return new Promise((resolve) => {
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => resolve(null);
        });
    } catch(e) { return null; }
}

async function deleteTrackBlob(id) {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).delete(id);
    } catch(e) {}
}

async function clearAllTrackBlobs() {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).clear();
    } catch(e) {}
}

function triggerHaptic(style = 'light') {
    if (isMiniApp && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred(style);
    } else if (navigator.vibrate) {
        if (style === 'light') navigator.vibrate(12);
        else if (style === 'medium') navigator.vibrate(25);
        else if (style === 'heavy') navigator.vibrate(45);
    }
}

function triggerNotification(type = 'success') {
    if (isMiniApp && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred(type);
    } else if (navigator.vibrate) {
        if (type === 'success') navigator.vibrate([20, 50, 20]);
        else navigator.vibrate([50, 50, 50]);
    }
}

function initTypewriter() {
    const el = document.getElementById('typingTitle');
    if (!el) return;
    const phrases = [
        "✨ الصق الرابط أو ابدأ بالبحث…",
        "✨ ابحث عن موسيقاك المفضلة…",
        "✨ اكتب ما تريد تحميله…",
        "✨ ابحث واستمتع…",
        "✨ اعثر على ما تريد…",
        "✨ ابدأ البحث…",
        "✨ اكتشف أغنيتك القادمة…",
        "✨ ما الذي تريد الاستماع إليه؟",
        "✨ ما الأغنية التي تبحث عنها؟",
        "✨ ماذا ستشغّل اليوم؟",
        "✨ ماذا ستستمع اليوم؟",
        "✨ ما الذي ترغب بتحميله؟"
    ];

    let phraseIdx = Math.floor(Math.random() * phrases.length);
    let charIdx = 0;
    let isDeleting = false;

    function getRandomNextIdx(current) {
        let next;
        do {
            next = Math.floor(Math.random() * phrases.length);
        } while (next === current && phrases.length > 1);
        return next;
    }

    function typeStep() {
        const currentPhrase = phrases[phraseIdx];
        if (isDeleting) {
            el.innerText = currentPhrase.substring(0, charIdx - 1);
            charIdx--;
        } else {
            el.innerText = currentPhrase.substring(0, charIdx + 1);
            charIdx++;
        }

        const searchView = document.getElementById('searchView');
        if (charIdx > 0 && searchView && searchView.classList.contains('active')) {
            triggerHaptic('light');
        }

        let speed = isDeleting ? 30 : 70;

        if (!isDeleting && charIdx === currentPhrase.length) {
            speed = 2000;
            isDeleting = true;
        } else if (isDeleting && charIdx === 0) {
            isDeleting = false;
            phraseIdx = getRandomNextIdx(phraseIdx);
            speed = 350;
        }

        setTimeout(typeStep, speed);
    }
    typeStep();
}

let myLibrary = JSON.parse(localStorage.getItem('pz_enterprise_library')) || [];
let recentlyPlayed = JSON.parse(localStorage.getItem('pz_recently_played')) || [];
let currentUrl = "";
let currentClickId = "";
let currentAdUrl = "";
let lastDownloadedItem = null;
let adCheckInterval = null;
let adFallbackTimeout = null;
let currentPlayingIndex = -1;
let currentPlayingId = null;
let isShuffle = false;
let shuffleQueue = [];
let isRepeat = false;
let libraryPage = 1;
const itemsPerPage = 6;
let isMuted = false;
let lastVolume = 1;
let currentPlayingMode = 'audio';
let isScrubbing = false;
let lastLoggedPercent = 0;
let sleepTimerInterval = null;
let sleepTimerEndTime = null;
let pendingTgItem = null;

const mediaContainer = document.getElementById('floatingPlayer');
const videoElement = document.getElementById('globalVideoElement');

function saveCurrentSessionState() {
    const titleEl = document.getElementById('title');
    const thumbEl = document.getElementById('thumb');
    const adGateEl = document.getElementById('adGate');
    const dlOptionsEl = document.getElementById('dlOptions');

    if (!currentUrl || !titleEl) return;

    const state = {
        url: currentUrl,
        clickId: currentClickId,
        adUrl: currentAdUrl,
        title: titleEl.innerText || '',
        thumb: thumbEl ? thumbEl.src : '',
        adGateHidden: adGateEl ? adGateEl.classList.contains('hidden') : false,
        dlOptionsHidden: dlOptionsEl ? dlOptionsEl.classList.contains('hidden') : true,
        savedAt: Date.now()
    };
    localStorage.setItem('pz_active_download_state', JSON.stringify(state));
}

function restoreSessionState() {
    const saved = localStorage.getItem('pz_active_download_state');
    if (!saved) return;
    try {
        const state = JSON.parse(saved);
        if (!state || !state.url || (Date.now() - (state.savedAt || 0) > 1800000)) {
            localStorage.removeItem('pz_active_download_state');
            return;
        }

        currentUrl = state.url;
        currentClickId = state.clickId || '';
        currentAdUrl = state.adUrl || '';

        const titleEl = document.getElementById('title');
        const thumbEl = document.getElementById('thumb');
        const previewBox = document.getElementById('previewBox');
        const adGateEl = document.getElementById('adGate');
        const dlOptionsEl = document.getElementById('dlOptions');

        if (titleEl) titleEl.innerText = state.title || '';
        if (thumbEl) thumbEl.src = state.thumb || '';
        if (previewBox) previewBox.classList.remove('hidden');

        if (state.adGateHidden) {
            if (adGateEl) adGateEl.classList.add('hidden');
            if (dlOptionsEl) dlOptionsEl.classList.remove('hidden');
        } else {
            if (adGateEl) adGateEl.classList.remove('hidden');
            if (dlOptionsEl) dlOptionsEl.classList.add('hidden');
        }
    } catch(e) {
        localStorage.removeItem('pz_active_download_state');
    }
}

function clearActiveSessionState() {
    localStorage.removeItem('pz_active_download_state');
    localStorage.removeItem('pz_ad_opened_pending');
}

// 📌 تعديل هكـذا لفك قفل التحميل فقط دون البدء التلقائي
function checkAndAutoContinueDownload() {
    const isAdPending = localStorage.getItem('pz_ad_opened_pending');
    if (isAdPending === 'true' && currentUrl) {
        localStorage.removeItem('pz_ad_opened_pending');
        
        const adGateEl = document.getElementById('adGate');
        const dlOptionsEl = document.getElementById('dlOptions');
        
        if (adGateEl) adGateEl.classList.add('hidden');
        if (dlOptionsEl) dlOptionsEl.classList.remove('hidden');
        saveCurrentSessionState();

        showToast("تم فك قفل التحميل بنجاح! 🔓 اختر الصيغة والدقة ثم اضغط بدء التحميل", "success");
    }
}

window.addEventListener('DOMContentLoaded', () => {
    setupDynamicManifest();
    initTypewriter();
    checkStandaloneAndHideBar();
    
    restoreSessionState();
    checkAndAutoContinueDownload();

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(()=>{});
    }

    if (isMiniApp) {
        tg.ready();
        tg.expand();
        try { tg.setHeaderColor('#0a0a0f'); tg.setBackgroundColor('#0a0a0f'); } catch(e){}
        const tgUser = tg.initDataUnsafe && tg.initDataUnsafe.user;
        if (tgUser && tgUser.id) {
            localStorage.setItem('pz_tg_id', tgUser.id);
            document.getElementById('settingTgId').value = tgUser.id;
            document.getElementById('settingTgId').disabled = true;
        }
    }
    const savedId = localStorage.getItem('pz_tg_id') || "";
    if(savedId && !document.getElementById('settingTgId').disabled) {
        document.getElementById('settingTgId').value = savedId;
    }
    document.getElementById('tgIdInput').value = savedId;
    const autoFwd = localStorage.getItem('pz_auto_tg') !== 'false';
    document.getElementById('autoForwardToggle').checked = autoFwd;

    updateLibraryCount();
    switchView('searchView');
    setupAdvancedDraggable(mediaContainer, document.getElementById('playerHeader'));
    setupScrubbing();
    setupVideoEvents();

    const inputsList = document.querySelectorAll('input, select, textarea');
    inputsList.forEach(inputEl => {
        inputEl.addEventListener('keydown', (e) => { e.stopPropagation(); });
        inputEl.addEventListener('keyup', (e) => { e.stopPropagation(); });
    });

    document.getElementById('url').addEventListener('keydown', function(e) {
        e.stopPropagation();
        if(e.key === 'Enter') {
            e.preventDefault();
            this.blur();
            processInput();
        }
    });

    document.addEventListener('pointerup', (e) => {
        const activeBtn = document.activeElement;
        if (activeBtn && (activeBtn.tagName === 'BUTTON' || activeBtn.tagName === 'A')) {
            activeBtn.blur();
        }
    });

    const savedSleepEnd = localStorage.getItem('pz_sleep_end');
    if (savedSleepEnd && Number(savedSleepEnd) > Date.now()) {
        sleepTimerEndTime = Number(savedSleepEnd);
        startSleepCountdown();
    }
});

window.addEventListener('focus', checkAndAutoContinueDownload);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
        checkAndAutoContinueDownload();
    }
});

function updateSidebarPlayingState(isPlaying) {
    const logoContainer = document.getElementById('sidebarLogoIcon');
    const iconEl = document.getElementById('sidebarIconEl');
    if (!logoContainer || !iconEl) return;
    if (isPlaying) {
        logoContainer.classList.add('border-accent', 'shadow-lg', 'shadow-accent/40', 'scale-105', 'rounded-xl');
        logoContainer.classList.remove('rounded-2xl');
        iconEl.className = "fas fa-compact-disc z-10 text-sm text-white album-spin";
    } else {
        logoContainer.classList.remove('border-accent', 'shadow-lg', 'shadow-accent/40', 'scale-105', 'rounded-xl');
        logoContainer.classList.add('rounded-2xl');
        iconEl.className = "fas fa-headphones z-10 text-sm text-accentLight";
    }
}

function setupVideoEvents() {
    videoElement.onplay = () => {
        document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-pause"></i>';
        if (currentPlayingMode === 'audio') {
            document.getElementById('playerCoverImg').style.animationPlayState = 'running';
            animateVisualizerBars(true);
        }
        updateSidebarPlayingState(true);
        if ('mediaSession' in navigator) navigator.mediaSession.playbackState = "playing";
        updateActivePlayingHighlight();
    };
    videoElement.onpause = () => {
        document.getElementById('playPauseBtn').innerHTML = '<i class="fas fa-play ml-0.5"></i>';
        document.getElementById('playerCoverImg').style.animationPlayState = 'paused';
        animateVisualizerBars(false);
        updateSidebarPlayingState(false);
        if ('mediaSession' in navigator) navigator.mediaSession.playbackState = "paused";
    };
}

function formatTime(secs) {
    if(isNaN(secs) || secs === null) return "0:00";
    const m = Math.floor(secs / 60), s = Math.floor(secs % 60);
    return m + ":" + (s < 10 ? '0' + s : s);
}

function showToast(message, type = "success") {
    const toast = document.getElementById('toast');
    toast.innerText = message; toast.className = "show";
    if (type === "error") toast.style.background = "linear-gradient(135deg, #ef4444, #dc2626)";
    else toast.style.background = "linear-gradient(135deg, #8b5cf6, #7c3aed)";
    setTimeout(() => { toast.className = ""; }, 3500);
    setTimeout(() => { triggerNotification(type === "error" ? "error" : "success"); }, 50);
}

function timeAgo(timestamp) {
    const diff = Date.now() - timestamp;
    const secs = Math.floor(diff / 1000);
    if (secs < 20) return 'الآن';
    if (secs < 60) return `منذ ثوانٍ`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `منذ ${mins} دقيقة`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `منذ ${hours} ساعة`;
    const days = Math.floor(hours / 24);
    return `منذ ${days} يوم`;
}

window.switchView = function(viewId) {
    document.querySelectorAll('.view-section').forEach(el => {
        el.style.setProperty('display', 'none', 'important'); el.classList.remove('active');
    });
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.className = btn.className.replace(/nav-btn-active/g, '').trim();
        btn.classList.remove('text-accent');
        btn.classList.add('text-textMuted', 'hover:bg-panelLight', 'hover:text-white');
    });
    const targetSection = document.getElementById(viewId);
    if (targetSection) {
        if (targetSection.classList.contains('flex-layout')) targetSection.style.setProperty('display', 'flex', 'important');
        else targetSection.style.setProperty('display', 'block', 'important');
        targetSection.classList.add('active');
    }
    const activeBtn = document.getElementById('nav-' + viewId);
    if (activeBtn) {
        activeBtn.classList.remove('text-textMuted', 'hover:bg-panelLight', 'hover:text-white');
        activeBtn.classList.add('nav-btn-active');
    }
    if (viewId === 'libraryView') applyFilters();
    if (viewId === 'recentView') renderRecentlyPlayed();
};

window.processInput = async function() {
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
                    <div onclick="renderPreview('https://youtube.com/watch?v=${v.id}')" class="search-result-item w-full flex items-center p-3 bg-panel/50 border border-panelBorder/40 rounded-2xl cursor-pointer shadow-sm">
                        <div class="flex-shrink-0 w-20 h-12 rounded-xl overflow-hidden border border-panelBorder/30 relative ml-3 shadow-md">
                            <img src="${v.thumbnail}" class="w-full h-full object-cover" alt="${v.title}">
                            <div class="absolute bottom-0.5 right-0.5 bg-black/85 text-white text-[8px] px-1 py-0.5 rounded font-mono">${formatTime(v.duration || 0)}</div>
                        </div>
                        <div class="flex-1 min-w-0 flex flex-col justify-center text-right">
                            <h4 class="text-white font-bold text-xs sm:text-sm truncate w-full mb-0.5">${v.title}</h4>
                            <p class="text-textDim text-[10px] sm:text-xs truncate w-full flex items-center gap-1">
                                <i class="fas fa-user-circle text-accent/60"></i> ${v.uploader}
                            </p>
                        </div>
                        <div class="flex-shrink-0 w-8 h-8 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center text-accent mr-2">
                            <i class="fas fa-download text-xs"></i>
                        </div>
                    </div>`;
                });
                document.getElementById('searchResults').classList.remove('hidden');
            } else showToast("لم يتم العثور على نتائج", "error");
        } catch(e) { showToast("حدث خطأ في البحث", "error"); }
    }
    btn.innerHTML = '<i class="fas fa-search"></i> بحث'; btn.disabled = false;
};

window.renderPreview = async function(url) {
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
            currentAdUrl = sessionData.ad_link;
            
            saveCurrentSessionState();

            document.getElementById('adGate').classList.remove('hidden');
            let vBtn = document.getElementById('verifyBtn');
            vBtn.className = "btn bg-panel/80 text-textMuted flex-1 border border-panelBorder text-xs sm:text-sm cursor-wait";
            vBtn.innerHTML = '<i class="fas fa-sync fa-spin"></i> بانتظار فحص الإعلان...';
        } else showToast("الرابط غير صالح للتحميل", "error");
    } catch(e) { showToast("فشل تحميل المعاينة", "error"); }
};

window.openAdAndVerify = function(event) {
    if (event) event.preventDefault();
    
    localStorage.setItem('pz_ad_opened_pending', 'true');
    saveCurrentSessionState();
    
    if (currentAdUrl && currentAdUrl !== '#') {
        if (isMiniApp && tg && tg.openLink) {
            tg.openLink(currentAdUrl);
        } else {
            window.open(currentAdUrl, '_blank', 'noopener,noreferrer');
        }
    }
    startAdVerificationCheck();
};

window.toggleRes = function() { document.getElementById('resolution').style.display = document.getElementById('mode').value === 'audio' ? 'none' : 'block'; };

window.startAdVerificationCheck = function() {
    showToast("جاري فحص الاتصال وتأكيد الجلسة... ⏳", "success");
    if(adCheckInterval) clearInterval(adCheckInterval);
    if(adFallbackTimeout) clearTimeout(adFallbackTimeout);
    let vBtn = document.getElementById('verifyBtn');
    vBtn.className = "btn bg-accent/10 text-accent flex-1 border border-accent/30 text-xs sm:text-sm cursor-wait";
    vBtn.innerHTML = '<i class="fas fa-sync fa-spin"></i> جاري التحقق من الإعلان...';
    adFallbackTimeout = setTimeout(() => {
        document.getElementById('adGate').classList.add('hidden');
        document.getElementById('dlOptions').classList.remove('hidden');
        
        saveCurrentSessionState();
        showToast("تم فك القفل بنجاح! حدد خياراتك واضغط بدء التحميل 🔓", "success");
    }, 10000);
};

function animatePercentCounter(targetPercent) {
    let start = lastLoggedPercent; let end = parseFloat(targetPercent) || 0;
    if (start === end) return;
    let duration = 400; let startTime = null; const percentEl = document.getElementById('progPercent');
    function step(timestamp) {
        if (!startTime) startTime = timestamp;
        let progress = timestamp - startTime;
        let current = start + (end - start) * Math.min(progress / duration, 1);
        percentEl.innerText = Math.floor(current) + '%';
        percentEl.style.transform = 'scale(1.05)';
        if (progress < duration) window.requestAnimationFrame(step);
        else { percentEl.innerText = end + '%'; percentEl.style.transform = 'scale(1.0)'; lastLoggedPercent = end; }
    }
    window.requestAnimationFrame(step);
}

window.downloadTrack = async function(id) {
    const item = myLibrary.find(i => i.id === id);
    if (!item) return;
    showToast("جاري تحضير ملف التحميل... 💾", "success");
    try {
        const blob = await getTrackBlob(id);
        let downloadUrl = "";
        let isBlobUrl = false;
        if (blob) {
            downloadUrl = URL.createObjectURL(blob);
            isBlobUrl = true;
        } else {
            const filename = item.url.split('/').pop();
            downloadUrl = BACKEND_URL + '/api/download_file/' + filename;
        }
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = item.title + (item.is_audio ? '.mp3' : '.mp4');
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        if (isBlobUrl) {
            setTimeout(() => URL.revokeObjectURL(downloadUrl), 15000);
        }
        showToast("تم بدء التحميل بنجاح!", "success");
    } catch(e) {
        showToast("حدث خطأ أثناء تحميل الملف", "error");
    }
};

window.downloadCurrentActiveTrack = function() {
    if (lastDownloadedItem) {
        downloadTrack(lastDownloadedItem.id);
    }
};

window.startDownload = async function(event) {
    const btn = event ? event.currentTarget : document.getElementById('startDownloadBtnEl');
    const original = btn ? btn.innerHTML : '';
    if (btn) { btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري البدء...'; btn.disabled = true; }
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
                        document.getElementById('progStatus').innerHTML = '<span class="text-emerald-400"><i class="fas fa-check-circle"></i> اكتمل التحميل</span>';
                        document.getElementById('progPercent').innerText = '100%'; document.getElementById('progBar').style.width = '100%';
                        const dlArea = document.getElementById('directDownloadArea');
                        dlArea.classList.remove('hidden');
                        
                        const fullFileUrl = BACKEND_URL + prog.url;
                        const newItem = { id: Date.now().toString(), title: prog.title, url: fullFileUrl, thumb: prog.thumb, uploader: prog.uploader, duration: prog.duration, is_audio: prog.is_audio, timestamp: Date.now(), favorite: false };
                        lastDownloadedItem = newItem;
                        myLibrary.unshift(newItem);
                        localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
                        if(document.getElementById('libraryView').classList.contains('active')) applyFilters();
                        showToast("تم حفظ الملف بنجاح", "success");

                        clearActiveSessionState();

                        fetchAndCacheTrack(newItem.id, fullFileUrl);

                        if(localStorage.getItem('pz_auto_tg') !== 'false') {
                            const tgId = localStorage.getItem('pz_tg_id');
                            if (!tgId) {
                                pendingTgItem = newItem;
                                document.getElementById('tgIdInput').value = "";
                                document.getElementById('tgModal').classList.replace('hidden', 'flex');
                                showToast("يرجى ربط حسابك ليتم إرسال هذا الملف إلى البوت ⚠️", "error");
                            } else {
                                triggerSendToTelegram(newItem.id);
                            }
                        }
                    }
                    else if(prog.status === 'error') {
                        clearInterval(interval);
                        document.getElementById('progStatus').innerHTML = '<span class="text-red-400"><i class="fas fa-exclamation-circle"></i> فشل التحميل</span>';
                        showToast("حدث خطأ أثناء معالجة الملف", "error");
                    }
                } catch(err) {}
            }, 1000);
        } else {
            showToast(data.error, "error");
            document.getElementById('progStatus').innerHTML = `<span class="text-red-400">${data.error}</span>`;
        }
    } catch(e) { showToast("فشل الاتصال بالخادم", "error"); }
    if (btn) { btn.innerHTML = original; btn.disabled = false; }
};

async function fetchAndCacheTrack(id, url) {
    try {
        const res = await fetch(url);
        if (res.ok) {
            const blob = await res.blob();
            await saveTrackBlob(id, blob);
        }
    } catch(e) {}
}

window.applyFilters = function(withHaptic = false) {
    if(withHaptic) triggerHaptic('light');
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
        container.innerHTML = `<div class="col-span-full py-16 text-center text-textMuted">
            <div class="w-14 h-14 bg-panelLight/50 rounded-2xl flex items-center justify-center mx-auto mb-3">
                <i class="fas fa-folder-open text-xl text-textDim"></i>
            </div>
            <p class="text-xs sm:text-sm font-medium">لا توجد ملفات حالياً</p>
            <p class="text-[11px] text-textDim mt-1">ابدأ بتحميل أغانيك المفضلة</p>
        </div>`;
        document.getElementById('pagination').innerHTML = ""; return;
    }
    pageItems.forEach((item) => {
        const actualIndex = myLibrary.findIndex(i => i.id === item.id);
        const durationStr = formatTime(item.duration || 0);
        const favClass = item.favorite ? 'fas fa-heart text-red-400' : 'far fa-heart';
        const typeTag = item.is_audio ? '<span class="bg-accent/10 text-accentLight text-[9px] px-2 py-0.5 rounded-md font-bold">MP3</span>' : '<span class="bg-fuchsia-500/10 text-fuchsia-400 text-[9px] px-2 py-0.5 rounded-md font-bold">MP4</span>';
        container.innerHTML += `
            <div class="lib-card bg-panel/60 backdrop-blur-sm rounded-2xl p-3.5 border border-panelBorder/40 flex gap-3 items-center relative group w-full">
                <div class="relative w-20 h-12 rounded-xl overflow-hidden border border-panelBorder/40 flex-shrink-0 cursor-pointer shadow-lg" onclick="playMediaTrack(${actualIndex})">
                    <img src="${item.thumb || 'https://mgx-backend-cdn.metadl.com/generate/images/1300473/2026-07-24/tcy6tjycajra/default-album-art.png'}" class="w-full h-full object-cover" alt="${item.title}">
                    <div class="absolute inset-0 bg-black/50 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-300">
                        <div class="w-6 h-6 bg-accent/90 rounded-full flex items-center justify-center shadow-lg">
                            <i class="fas fa-play text-white text-[8px] ml-0.5"></i>
                        </div>
                    </div>
                    <div class="absolute bottom-0.5 right-0.5 bg-black/80 text-[8px] px-1 py-0.5 font-mono rounded text-white/80">${durationStr}</div>
                </div>
                <div class="flex-1 min-w-0 text-right">
                    <h4 class="text-white font-bold text-xs sm:text-sm truncate cursor-pointer hover:text-accentLight transition-colors" onclick="playMediaTrack(${actualIndex})">${item.title}</h4>
                    <div class="flex items-center gap-2 mt-1">
                        ${typeTag}
                        <p class="text-textDim text-[10px] truncate">${item.uploader || 'غير معروف'}</p>
                    </div>
                </div>
                <div class="flex items-center gap-1 flex-row-reverse flex-shrink-0">
                    <button onclick="deleteFromLibrary('${item.id}')" class="w-7 h-7 rounded-xl bg-bgDeep/60 border border-panelBorder/30 text-textMuted hover:text-red-400 hover:bg-red-500/10 hover:border-red-500/20 active:scale-90 transition-all flex items-center justify-center" title="حذف">
                        <i class="fas fa-trash-alt text-[10px]"></i>
                    </button>
                    <button onclick="downloadTrack('${item.id}')" class="w-7 h-7 rounded-xl bg-bgDeep/60 border border-panelBorder/30 text-textMuted hover:text-emerald-400 hover:bg-emerald-500/10 hover:border-emerald-500/20 active:scale-90 transition-all flex items-center justify-center" title="تحميل">
                        <i class="fas fa-download text-[10px]"></i>
                    </button>
                    <button onclick="triggerSendToTelegram('${item.id}')" class="w-7 h-7 rounded-xl bg-bgDeep/60 border border-panelBorder/30 text-textMuted hover:text-tgBlue hover:bg-tgBlue/10 hover:border-tgBlue/20 active:scale-90 transition-all flex items-center justify-center" title="إرسال لتيليجرام">
                        <i class="fab fa-telegram-plane text-[10px]"></i>
                    </button>
                    <button onclick="toggleFavorite('${item.id}')" class="w-7 h-7 rounded-xl bg-bgDeep/60 border border-panelBorder/30 text-textMuted hover:text-red-400 hover:bg-red-500/10 hover:border-red-500/20 active:scale-90 transition-all flex items-center justify-center" title="المفضلة">
                        <i class="${favClass} text-[10px]"></i>
                    </button>
                </div>
            </div>`;
    });
    renderPagination(totalPages);
};

function renderPagination(totalPages) {
    const pagBox = document.getElementById('pagination'); pagBox.innerHTML = ""; if (totalPages <= 1) return;
    let html = `<button onclick="changePage(${libraryPage - 1})" ${libraryPage === 1 ? 'disabled' : ''} class="btn px-3 py-1.5 bg-panel/60 border border-panelBorder/40 text-xs text-textMuted hover:text-white hover:border-accent/30 disabled:opacity-30 rounded-xl">السابق</button>`;
    for (let i = 1; i <= totalPages; i++) {
        const activeClass = (libraryPage === i) ? 'bg-gradient-to-r from-accent to-purple-600 text-white shadow-lg glow-accent' : 'bg-panel/60 border border-panelBorder/40 text-textMuted hover:text-white hover:border-accent/30';
        html += `<button onclick="changePage(${i})" class="btn px-3 py-1.5 ${activeClass} text-xs font-mono rounded-xl">${i}</button>`;
    }
    html += `<button onclick="changePage(${libraryPage + 1})" ${libraryPage === totalPages ? 'disabled' : ''} class="btn px-3 py-1.5 bg-panel/60 border border-panelBorder/40 text-xs text-textMuted hover:text-white hover:border-accent/30 disabled:opacity-30 rounded-xl">التالي</button>`;
    pagBox.innerHTML = html;
}

window.changePage = function(page) { libraryPage = page; applyFilters(); };

window.toggleFavorite = function(id) {
    const index = myLibrary.findIndex(i => i.id === id);
    if (index !== -1) {
        myLibrary[index].favorite = !myLibrary[index].favorite;
        localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary)); applyFilters();
        showToast(myLibrary[index].favorite ? "تمت الإضافة للمفضلة ❤️" : "تمت الإزالة من المفضلة", "success");
    }
};

window.deleteFromLibrary = function(id) {
    if (isMiniApp && tg.showConfirm) {
        tg.showConfirm("هل تريد بالتأكيد حذف هذا الملف؟", function(confirmed) { if (confirmed) executeDelete(id); });
    } else {
        if (confirm("هل تريد حذف هذا الملف؟")) executeDelete(id);
    }
};

function executeDelete(id) {
    myLibrary = myLibrary.filter(i => i.id !== id);
    localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
    deleteTrackBlob(id);
    applyFilters(); updateLibraryCount(); showToast("تم الحذف بنجاح", "success");
}

function updateLibraryCount() { document.getElementById('libCountStatus').innerText = "سجل الملفات (" + myLibrary.length + ")"; }

window.clearAllLibrary = function() {
    if (isMiniApp && tg.showConfirm) {
        tg.showConfirm("هل تود مسح الذاكرة وسجل الاستماع بالكامل؟", function(confirmed) { if (confirmed) executeClearAll(); });
    } else {
        if (confirm("هل تود مسح الذاكرة وسجل الاستماع بالكامل؟")) executeClearAll();
    }
};

function executeClearAll() {
    myLibrary = [];
    recentlyPlayed = [];
    localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
    localStorage.setItem('pz_recently_played', JSON.stringify(recentlyPlayed));
    clearAllTrackBlobs();
    applyFilters();
    renderRecentlyPlayed();
    updateLibraryCount();
    showToast("تم مسح الملفات وسجل الاستماع والذاكرة بنجاح", "success");
}

window.updateTgId = function() {
    const tgId = document.getElementById('settingTgId').value.trim();
    if (!tgId) { localStorage.removeItem('pz_tg_id'); showToast("تمت إزالة المعرف", "success"); }
    else { localStorage.setItem('pz_tg_id', tgId); showToast("تم الحفظ بنجاح ✓", "success"); }
};

window.toggleAutoForward = function() {
    const val = document.getElementById('autoForwardToggle').checked;
    localStorage.setItem('pz_auto_tg', val ? 'true' : 'false');
    showToast(val ? "تم تفعيل الإرسال التلقائي" : "تم تعطيل الإرسال التلقائي", "success");
};

window.triggerSendToTelegram = async function(id) {
    const item = myLibrary.find(i => i.id === id); if (!item) return;
    const tgId = localStorage.getItem('pz_tg_id');
    if (!tgId) {
        pendingTgItem = item; document.getElementById('tgIdInput').value = "";
        document.getElementById('tgModal').classList.replace('hidden', 'flex');
        return;
    }
    
    showToast("جاري تحضير الملف وإرساله إلى تيليجرام...", "success");
    
    const blob = await getTrackBlob(id);
    if (blob) {
        const formData = new FormData();
        formData.append('chat_id', tgId.toString());
        formData.append('is_audio', item.is_audio ? 'true' : 'false');
        formData.append('title', item.title || 'مقطع');
        formData.append('performer', item.uploader || 'PlayZone');
        formData.append('duration', Math.floor(Number(item.duration)) || 0);
        formData.append('thumb', item.thumb || '');
        formData.append('file_url', item.url || '');
        formData.append('file', blob, item.title + (item.is_audio ? '.mp3' : '.mp4'));

        try {
            const res = await fetch(`${BACKEND_URL}/api/send_telegram`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (data.success) showToast("تم إرسال الملف إلى حسابك بنجاح! 🎉", "success");
            else showToast("فشل الإرسال للبوت: " + data.error, "error");
        } catch(e) {
            sendToTelegram(item.url, item.is_audio, false, item.title, item.uploader, item.duration, item.thumb);
        }
    } else {
        sendToTelegram(item.url, item.is_audio, false, item.title, item.uploader, item.duration, item.thumb);
    }
};

async function sendToTelegram(fileUrl, isAudio, auto = false, title = "مقطع", performer = "PlayZone", duration = 0, thumb = "") {
    const chatId = localStorage.getItem('pz_tg_id'); if (!chatId) return;
    showToast(auto ? "جاري الإرسال لبوت تيليجرام تلقائياً..." : "جاري إرسال الملف إلى تيليجرام...", "success");
    try {
        const res = await fetch(`${BACKEND_URL}/api/send_telegram`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_url: fileUrl, chat_id: chatId.toString(), is_audio: isAudio, title: title, performer: performer, duration: Math.floor(Number(duration)) || 0, thumb: thumb || "" })
        });
        const data = await res.json();
        if (data.success) showToast("تم إرسال الملف إلى حسابك بنجاح! 🎉", "success");
        else showToast("فشل الإرسال للبوت: " + data.error, "error");
    } catch(e) { showToast("فشل الاتصال بالخادم لإرسال تيليجرام", "error"); }
}

window.closeTgModal = function() { document.getElementById('tgModal').classList.replace('flex', 'hidden'); pendingTgItem = null; };

window.saveTgIdFromModal = function() {
    const val = document.getElementById('tgIdInput').value.trim();
    if (!val) return showToast("أدخل معرف صالح", "error");
    localStorage.setItem('pz_tg_id', val); document.getElementById('settingTgId').value = val;
    closeTgModal(); showToast("تم الحفظ بنجاح وجاري الإرسال للبوت... 🎉", "success");
    if (pendingTgItem) {
        triggerSendToTelegram(pendingTgItem.id);
    }
};

function rebuildShuffleQueue() {
    shuffleQueue = Array.from({length: myLibrary.length}, (_, i) => i)
        .filter(i => i !== currentPlayingIndex);
    for (let i = shuffleQueue.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffleQueue[i], shuffleQueue[j]] = [shuffleQueue[j], shuffleQueue[i]];
    }
}

window.playMediaTrack = async function(index, reorderRecent = true) {
    currentPlayingIndex = index; const track = myLibrary[index]; if (!track) return;
    currentPlayingId = track.id;
    mediaContainer.classList.add('active-player');
    if (track.is_audio) {
        currentPlayingMode = 'audio'; document.getElementById('trackSource').innerText = '🎵 صوت';
        document.getElementById('videoContainer').classList.add('hidden'); document.getElementById('audioVisualizer').classList.remove('hidden');
        document.getElementById('pipBtn').classList.add('hidden');
        document.getElementById('playerCoverImg').src = track.thumb || 'https://mgx-backend-cdn.metadl.com/generate/images/1300473/2026-07-24/tcy6tjycajra/default-album-art.png';
    } else {
        currentPlayingMode = 'video'; document.getElementById('trackSource').innerText = '🎬 فيديو';
        document.getElementById('videoContainer').classList.remove('hidden'); document.getElementById('audioVisualizer').classList.add('hidden');
        document.getElementById('pipBtn').classList.remove('hidden');
    }

    let playSrc = track.url;
    const cachedBlob = await getTrackBlob(track.id);
    if (cachedBlob) {
        playSrc = URL.createObjectURL(cachedBlob);
    } else {
        fetchAndCacheTrack(track.id, track.url);
    }

    videoElement.src = playSrc; videoElement.load();
    videoElement.play().catch(() => { showToast("اضغط زر التشغيل للمتابعة", "success"); });
    document.getElementById('playerTitle').innerText = track.title;
    if (mediaContainer.classList.contains('compact-mode')) removeCompactLayout();

    addToRecentlyPlayed(track, reorderRecent);

    if ('mediaSession' in navigator) {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: track.title, artist: track.uploader || 'PlayZone', album: 'PlayZone Music',
            artwork: [{ src: track.thumb || 'https://mgx-backend-cdn.metadl.com/generate/images/1300473/2026-07-24/tcy6tjycajra/default-album-art.png', sizes: '512x512', type: 'image/png' }]
        });
        setupMediaSessionActions();
    }
};

window.togglePlay = function() {
    if (videoElement.paused) videoElement.play().catch(()=>{});
    else videoElement.pause();
};

window.seekMedia = function(seconds) {
    if (videoElement && !isNaN(videoElement.duration)) {
        videoElement.currentTime = Math.max(0, Math.min(videoElement.duration, videoElement.currentTime + seconds));
        triggerHaptic('light');
    }
};

function setupMediaSessionActions() {
    if ('mediaSession' in navigator) {
        navigator.mediaSession.setActionHandler('play', () => { togglePlay(); });
        navigator.mediaSession.setActionHandler('pause', () => { togglePlay(); });
        navigator.mediaSession.setActionHandler('previoustrack', () => { playPrev(); });
        navigator.mediaSession.setActionHandler('nexttrack', () => { playNext(); });
        navigator.mediaSession.setActionHandler('seekbackward', () => { seekMedia(-10); });
        navigator.mediaSession.setActionHandler('seekforward', () => { seekMedia(10); });
    }
}

window.playNext = function() {
    if (myLibrary.length === 0) return;
    if (isShuffle) {
        if (shuffleQueue.length === 0) rebuildShuffleQueue();
        if (shuffleQueue.length > 0) {
            const nextIdx = shuffleQueue.shift();
            playMediaTrack(nextIdx);
            return;
        }
    }
    let idx = currentPlayingIndex + 1;
    if (idx >= myLibrary.length) idx = 0;
    playMediaTrack(idx);
};

window.playPrev = function() {
    if (videoElement && videoElement.currentTime > 3) {
        videoElement.currentTime = 0;
        videoElement.play().catch(()=>{});
        return;
    }
    if (myLibrary.length === 0) return;
    let idx = currentPlayingIndex - 1; if (idx < 0) idx = myLibrary.length - 1; playMediaTrack(idx);
};

window.toggleRepeat = function(e) {
    if (e && e.currentTarget) e.currentTarget.blur();
    isRepeat = !isRepeat;
    const btn = document.getElementById('repeatBtn');
    if (isRepeat) {
        btn.classList.add('text-accent', 'bg-accent/10');
        btn.classList.remove('text-textMuted');
        showToast("تم تفعيل التكرار التلقائي 🔁", "success");
    } else {
        btn.classList.remove('text-accent', 'bg-accent/10');
        btn.classList.add('text-textMuted');
        showToast("تم تعطيل التكرار التلقائي", "success");
    }
    triggerHaptic('light');
};

window.toggleShuffle = function(e) {
    if (e && e.currentTarget) e.currentTarget.blur();
    isShuffle = !isShuffle;
    const btn = document.getElementById('shuffleBtn');
    if (isShuffle) {
        rebuildShuffleQueue();
        btn.classList.add('text-accent', 'bg-accent/10');
        btn.classList.remove('text-textMuted');
        showToast("تم تفعيل التشغيل العشوائي 🔀", "success");
    } else {
        shuffleQueue = [];
        btn.classList.remove('text-accent', 'bg-accent/10');
        btn.classList.add('text-textMuted');
        showToast("تم تعطيل التشغيل العشوائي", "success");
    }
    triggerHaptic('light');
};

window.updatePlayerProgress = function() {
    if (isScrubbing) return; const cur = videoElement.currentTime; const dur = videoElement.duration; if (isNaN(dur)) return;
    const pct = (cur / dur) * 100; document.getElementById('mediaProgressBar').style.width = pct + '%';
    document.getElementById('mediaProgressSlider').style.left = pct + '%'; document.getElementById('playerTime').innerText = formatTime(cur) + ' / ' + formatTime(dur);
};

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

window.changeVolume = function() {
    const val = document.getElementById('volumeSlider').value; videoElement.volume = val; lastVolume = val;
    const i = document.getElementById('muteBtn').querySelector('i');
    if (val == 0) { i.className = "fas fa-volume-mute text-xs"; isMuted = true; } else { i.className = "fas fa-volume-up text-xs"; isMuted = false; }
};

window.toggleMute = function() {
    const i = document.getElementById('muteBtn').querySelector('i');
    if (isMuted) { videoElement.volume = lastVolume || 1; document.getElementById('volumeSlider').value = lastVolume || 1; i.className = "fas fa-volume-up text-xs"; isMuted = false; }
    else { videoElement.volume = 0; document.getElementById('volumeSlider').value = 0; i.className = "fas fa-volume-mute text-xs"; isMuted = true; }
};

let currentSpeed = 1;
window.changeSpeed = function(e) {
    if (e && e.currentTarget) e.currentTarget.blur();
    const spds = [1, 1.25, 1.5, 1.75, 2];
    let idx = spds.indexOf(currentSpeed);
    idx = (idx + 1) % spds.length;
    currentSpeed = spds[idx];
    if (videoElement) videoElement.playbackRate = currentSpeed;
    const btn = document.getElementById('speedBtn');
    btn.innerText = currentSpeed + 'x';
    if (currentSpeed !== 1) {
        btn.classList.add('text-accent', 'border-accent/40', 'bg-accent/10');
        btn.classList.remove('text-textMuted');
    } else {
        btn.classList.remove('text-accent', 'border-accent/40', 'bg-accent/10');
        btn.classList.add('text-textMuted');
    }
    showToast(`سرعة التشغيل: ${currentSpeed}x ⚡`, "success");
    triggerHaptic('light');
};

window.handleMediaEnd = function() { if (isRepeat) { videoElement.currentTime = 0; videoElement.play().catch(()=>{}); } else { playNext(); } };
window.closePlayer = function() { videoElement.pause(); mediaContainer.classList.remove('active-player'); animateVisualizerBars(false); };
window.triggerPiP = function() { if (document.pictureInPictureEnabled && videoElement && currentPlayingMode === 'video') { if (document.pictureInPictureElement) document.exitPictureInPicture(); else videoElement.requestPictureInPicture().catch(()=>{}); } };
window.toggleCompactMode = function(e) { e.stopPropagation(); const icon = e.currentTarget.querySelector('i'); if (mediaContainer.classList.contains('compact-mode')) { removeCompactLayout(); icon.className = "fas fa-compress-alt text-[10px]"; } else { mediaContainer.classList.add('compact-mode'); document.getElementById('playerBody').style.display = 'none'; icon.className = "fas fa-expand-alt text-[10px]"; } };
function removeCompactLayout() { mediaContainer.classList.remove('compact-mode'); document.getElementById('playerBody').style.display = 'block'; }
window.resetPlayerPosition = function() { mediaContainer.style.top = 'auto'; mediaContainer.style.left = '16px'; mediaContainer.style.bottom = '16px'; mediaContainer.style.right = 'auto'; mediaContainer.style.transform = 'none'; };
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
        let newLeft = initialLeft + dx; let newTop = initialTop + dy; const padding = 8;
        const maxLeft = window.innerWidth - el.offsetWidth - padding; const maxTop = window.innerHeight - el.offsetHeight - padding;
        newLeft = Math.max(padding, Math.min(newLeft, maxLeft)); newTop = Math.max(padding, Math.min(newTop, maxTop));
        el.style.left = newLeft + "px"; el.style.top = newTop + "px"; el.style.bottom = "auto"; el.style.right = "auto";
    }
    function dragEnd(e) {
        if (!isDragging) return; isDragging = false; handle.style.cursor = 'grab'; el.classList.remove('dragging-player');
        el.style.transition = 'opacity 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
        try { handle.releasePointerCapture(e.pointerId); } catch(err) {}
        handle.removeEventListener('pointermove', dragMove); handle.removeEventListener('pointerup', dragEnd); handle.removeEventListener('pointercancel', dragEnd);
    }
}

function addToRecentlyPlayed(track, reorderDOM = true) {
    recentlyPlayed = recentlyPlayed.filter(r => r.id !== track.id);
    recentlyPlayed.unshift({ ...track, playedAt: Date.now() });
    if (recentlyPlayed.length > 20) recentlyPlayed = recentlyPlayed.slice(0, 20);
    localStorage.setItem('pz_recently_played', JSON.stringify(recentlyPlayed));
    
    renderRecentlyPlayed();
}

function updateActivePlayingHighlight() {
    document.querySelectorAll('.recent-item').forEach(el => {
        const id = el.getAttribute('data-id');
        if (id === currentPlayingId) {
            el.classList.add('active-playing-item');
        } else {
            el.classList.remove('active-playing-item');
        }
    });
}

window.addEventListener('storage', (e) => {
    if (e.key === 'pz_recently_played') {
        recentlyPlayed = JSON.parse(e.newValue) || [];
        renderRecentlyPlayed();
    }
});

function renderRecentlyPlayed() {
    const container = document.getElementById('recentContainer');
    if (!container) return;
    container.innerHTML = '';
    if (recentlyPlayed.length === 0) {
        container.innerHTML = `<div class="py-16 text-center text-textMuted">
            <div class="w-14 h-14 bg-panelLight/50 rounded-2xl flex items-center justify-center mx-auto mb-3">
                <i class="fas fa-history text-xl text-textDim"></i>
            </div>
            <p class="text-xs sm:text-sm font-medium">لم تستمع لأي مقطع بعد</p>
            <p class="text-[11px] text-textDim mt-1">ابدأ بتشغيل أغانيك المفضلة</p>
        </div>`;
        return;
    }
    recentlyPlayed.forEach((item) => {
        const durationStr = formatTime(item.duration || 0);
        const typeIcon = item.is_audio ? '<i class="fas fa-music text-accent text-[9px]"></i>' : '<i class="fas fa-video text-fuchsia-400 text-[9px]"></i>';
        const isActive = (item.id === currentPlayingId) ? 'active-playing-item' : '';
        container.innerHTML += `
            <div data-id="${item.id}" class="recent-item bg-panel/50 border border-panelBorder/40 rounded-2xl p-3 flex items-center gap-3 cursor-pointer w-full ${isActive}" onclick="playRecentTrackById('${item.id}')">
                <div class="relative w-16 h-10 rounded-xl overflow-hidden border border-panelBorder/30 flex-shrink-0 shadow-md">
                    <img src="${item.thumb || 'https://mgx-backend-cdn.metadl.com/generate/images/1300473/2026-07-24/tcy6tjycajra/default-album-art.png'}" class="w-full h-full object-cover" alt="${item.title}">
                    <div class="absolute bottom-0.5 right-0.5 bg-black/80 text-[8px] px-1 py-0.5 font-mono rounded text-white/80">${durationStr}</div>
                </div>
                <div class="flex-1 min-w-0 text-right">
                    <h4 class="text-white font-bold text-xs truncate">${item.title}</h4>
                    <div class="flex items-center gap-2 mt-0.5">
                        ${typeIcon}
                        <span class="text-textDim text-[10px] font-mono">${timeAgo(item.playedAt)}</span>
                    </div>
                </div>
                <div class="w-7 h-7 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center text-accent flex-shrink-0">
                    <i class="fas fa-play text-[8px] ml-0.5"></i>
                </div>
            </div>`;
    });
}

window.playRecentTrackById = function(trackId) {
    const track = recentlyPlayed.find(r => r.id === trackId);
    if (!track) return;
    let libIndex = myLibrary.findIndex(i => i.id === track.id);
    if (libIndex === -1) {
        myLibrary.unshift(track);
        localStorage.setItem('pz_enterprise_library', JSON.stringify(myLibrary));
        libIndex = 0;
        applyFilters();
    }
    playMediaTrack(libIndex, false);
};

window.clearRecentHistory = function() {
    if (confirm("هل تريد مسح سجل الاستماع؟")) {
        recentlyPlayed = [];
        localStorage.setItem('pz_recently_played', JSON.stringify(recentlyPlayed));
        renderRecentlyPlayed();
        showToast("تم مسح السجل", "success");
    }
};

window.setSleepTimer = function(minutes) {
    sleepTimerEndTime = Date.now() + (minutes * 60 * 1000);
    localStorage.setItem('pz_sleep_end', sleepTimerEndTime);
    startSleepCountdown();
    showToast(`مؤقت النوم: ${minutes} دقيقة ⏰`, "success");
};

window.setCustomSleepTimer = function() {
    const val = parseInt(document.getElementById('customSleepInput').value);
    if (!val || val <= 0) return showToast("يرجى إدخال عدد دقائق صحيح", "error");
    setSleepTimer(val);
    document.getElementById('customSleepInput').value = "";
};

function startSleepCountdown() {
    if (sleepTimerInterval) clearInterval(sleepTimerInterval);
    document.getElementById('sleepTimerStatus').classList.remove('hidden');
    sleepTimerInterval = setInterval(() => {
        const remaining = sleepTimerEndTime - Date.now();
        if (remaining <= 0) {
            executeSleepTimer();
            return;
        }
        const mins = Math.floor(remaining / 60000);
        const secs = Math.floor((remaining % 60000) / 1000);
        document.getElementById('sleepCountdown').innerText = `${mins}:${secs < 10 ? '0' + secs : secs}`;
    }, 1000);
}

function executeSleepTimer() {
    clearInterval(sleepTimerInterval);
    sleepTimerInterval = null;
    sleepTimerEndTime = null;
    localStorage.removeItem('pz_sleep_end');
    document.getElementById('sleepTimerStatus').classList.add('hidden');
    videoElement.pause();
    showToast("تم إيقاف التشغيل - مؤقت النوم 🌙", "success");
}

window.cancelSleepTimer = function() {
    clearInterval(sleepTimerInterval);
    sleepTimerInterval = null;
    sleepTimerEndTime = null;
    localStorage.removeItem('pz_sleep_end');
    document.getElementById('sleepTimerStatus').classList.add('hidden');
    showToast("تم إلغاء مؤقت النوم", "success");
};
