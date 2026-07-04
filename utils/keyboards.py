from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, TELEGRAM_BOT_PLAYZONE
from database.operations import get_setting

def user_main_keyboard(lang: str = "ar") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],
            [KeyboardButton(_t("btn_add_group", lang))]
        ],
        resize_keyboard=True, is_persistent=True, input_field_placeholder=_t("txt_placeholder", lang)
    )

def build_preview_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    # ... (كما هو بدون تغيير)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}")],
        [InlineKeyboardButton(_t("btn_video", lang), callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton(_t("btn_cancel", lang), callback_data=f"cancel:{request_id}")],
    ])

def build_resolution_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    # ... (كما هو بدون تغيير)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("360p", callback_data=f"res:360:{request_id}"), InlineKeyboardButton("480p", callback_data=f"res:480:{request_id}")],
        [InlineKeyboardButton("720p", callback_data=f"res:720:{request_id}"), InlineKeyboardButton("1080p", callback_data=f"res:1080:{request_id}")],
        [InlineKeyboardButton(_t("btn_best_quality", lang), callback_data=f"res:best:{request_id}")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data=f"back:{request_id}")]
    ])

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    # ... (كما هو بدون تغيير)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📱 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
    ])

# ================= لوحات الإدارة الجديدة =================

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 الإذاعة الشاملة", callback_data="adm_bc_menu"), InlineKeyboardButton("👥 المستخدمين", callback_data="adm_users_menu")],
        [InlineKeyboardButton("🛡️ الحماية والصيانة", callback_data="adm_sec_menu"), InlineKeyboardButton("⚙️ إعدادات النظام", callback_data="adm_sys_menu")],
        [InlineKeyboardButton("✖️ إغلاق", callback_data="adm_close")]
    ])

def admin_system_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث المكتبة (yt-dlp)", callback_data="adm_update_dlp")],
        [InlineKeyboardButton("🍪 تحديث الكوكيز", callback_data="adm_req_cookie"), InlineKeyboardButton("📁 حالة السيرفر", callback_data="adm_server")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main_back")]
    ])

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"), InlineKeyboardButton("📋 آخر المستخدمين", callback_data="adm_users")],
        [InlineKeyboardButton("🔍 الاستعلام عن مستخدم", callback_data="adm_req_user")],
        [InlineKeyboardButton("📥 تصدير البيانات (CSV)", callback_data="adm_export_db")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main_back")]
    ])

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    maint = get_setting("maintenance", "0")
    maint_btn = InlineKeyboardButton("إيقاف الصيانة 🟢", callback_data="adm_toggle_maint") if maint == "1" else InlineKeyboardButton("تشغيل الصيانة 🔴", callback_data="adm_toggle_maint")
    return InlineKeyboardMarkup([
        [maint_btn],
        [InlineKeyboardButton("🗜️ تحسين الـ Database", callback_data="adm_vacuum_db"), InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main_back")]
    ])

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("رسالة للجميع 🌍", callback_data="adm_bc_start:all")],
        [InlineKeyboardButton("للنشطين (48h) ⚡", callback_data="adm_bc_start:active")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main_back")]
    ])

def admin_cancel_action_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء العملية والرجوع", callback_data="adm_main_back")]
    ])
