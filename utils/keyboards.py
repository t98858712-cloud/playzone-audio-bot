from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, TELEGRAM_BOT_PLAYZONE
from database.operations import get_setting
from utils.helpers import is_admin

def user_main_keyboard(lang: str = "ar", user_id: int = 0) -> ReplyKeyboardMarkup:
    keys = [
        [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],
        [KeyboardButton(_t("btn_add_group", lang))]
    ]
    
    # الزر السري يظهر للمدراء فقط
    if is_admin(user_id):
        keys.append([KeyboardButton("⚙️ لوحة التحكم")])
        
    return ReplyKeyboardMarkup(
        keys,
        resize_keyboard=True, is_persistent=True, input_field_placeholder=_t("txt_placeholder", lang)
    )

def build_preview_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}")],
        [InlineKeyboardButton(_t("btn_video", lang), callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton(_t("btn_cancel", lang), callback_data=f"cancel:{request_id}")],
    ])

def build_resolution_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("360p", callback_data=f"res:360:{request_id}"),
            InlineKeyboardButton("480p", callback_data=f"res:480:{request_id}")
        ],
        [
            InlineKeyboardButton("720p", callback_data=f"res:720:{request_id}"),
            InlineKeyboardButton("1080p", callback_data=f"res:1080:{request_id}")
        ],
        [InlineKeyboardButton(_t("btn_best_quality", lang), callback_data=f"res:best:{request_id}")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data=f"back:{request_id}")]
    ])

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],
    ])

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_bc_menu", lang), callback_data="adm_bc_menu"), InlineKeyboardButton(_t("btn_adm_users_menu", lang), callback_data="adm_users_menu")],
        [InlineKeyboardButton(_t("btn_adm_sec_menu", lang), callback_data="adm_sec_menu"), InlineKeyboardButton(_t("btn_adm_srv", lang), callback_data="adm_server")],
        [InlineKeyboardButton(_t("btn_adm_close", lang), callback_data="adm_close")]
    ])

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_bc_all", lang), callback_data="adm_bc_start:all")],
        [InlineKeyboardButton(_t("btn_adm_bc_active", lang), callback_data="adm_bc_start:active")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data="adm_main_back")]
    ])

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_stats", lang), callback_data="adm_stats"), InlineKeyboardButton(_t("btn_adm_users_list", lang), callback_data="adm_users")],
        [InlineKeyboardButton(_t("btn_adm_export", lang), callback_data="adm_export_db")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data="adm_main_back")]
    ])

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    maint = get_setting("maintenance", "0")
    maint_btn = InlineKeyboardButton(_t("btn_adm_maint_off", lang), callback_data="adm_toggle_maint") if maint == "1" else InlineKeyboardButton(_t("btn_adm_maint_on", lang), callback_data="adm_toggle_maint")
    
    # دمج أزرار الإدارة الجديدة لتجنب السلاش
    return InlineKeyboardMarkup([
        [maint_btn],
        [InlineKeyboardButton("🔄 تحديث المحرك (YT-DLP)", callback_data="adm_update_dlp")],
        [InlineKeyboardButton("💾 تحميل نسخة احتياطية (DB)", callback_data="adm_backup_db")],
        [InlineKeyboardButton("🍪 إرشادات الكوكيز", callback_data="adm_cookie_guide")],
        [InlineKeyboardButton(_t("btn_adm_vacuum", lang), callback_data="adm_vacuum_db"), InlineKeyboardButton(_t("btn_adm_clean", lang), callback_data="adm_clean")],
        [InlineKeyboardButton(_t("btn_back", lang), callback_data="adm_main_back")]
    ])

def admin_broadcast_cancel_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_cancel_bc", lang), callback_data="adm_cancel_bc")]
    ])
