from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, BOT_USERNAME
from database.operations import get_setting

def user_main_keyboard(lang: str = "ar") -> ReplyKeyboardMarkup:
    """لوحة مفاتيح المستخدم الرئيسية بعد تصفية وإزالة زر اللغة"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],
            [KeyboardButton(_t("btn_add_group", lang))]
        ],
        resize_keyboard=True, is_persistent=True, input_field_placeholder=_t("txt_placeholder", lang)
    )

def build_preview_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة خيارات التحميل للمقطع"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}")],
        [InlineKeyboardButton(_t("btn_audio_pro", lang), callback_data=f"aud_pro:{request_id}")],
        [InlineKeyboardButton(_t("btn_video", lang), callback_data=f"vid:{request_id}")],
        [InlineKeyboardButton(_t("btn_cancel", lang), callback_data=f"cancel:{request_id}")],
    ])

def build_resolution_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة اختيار جودة الفيديو"""
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

def build_playzone_links_keyboard(bot_username: str = BOT_USERNAME) -> InlineKeyboardMarkup:
    """لوحة روابط السوشيال ميديا لـ PlayZone"""
    tg_url = f"https://t.me/{bot_username.replace('@', '')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=tg_url)],
    ])

# =====================================================================
# 📊 لوحة تحكم الإدارة المتقدمة المصفّاة والموزعة شبكياً (Grid Layout)
# =====================================================================

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    """اللوحة الرئيسية للادمن - أزرار متقابلة ومتناسقة تمنع الإطالة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_t("btn_adm_stats", lang), callback_data="adm_stats"),
            InlineKeyboardButton(_t("btn_adm_server", lang), callback_data="adm_server")
        ],
        [
            InlineKeyboardButton(_t("btn_adm_bc", lang), callback_data="adm_bc_menu"),
            InlineKeyboardButton(_t("btn_adm_users", lang), callback_data="adm_users_menu")
        ],
        [InlineKeyboardButton(_t("btn_adm_sec", lang), callback_data="adm_sec_menu")],
        [InlineKeyboardButton(_t("btn_adm_close", lang), callback_data="adm_close")]
    ])

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة قسم الإذاعة والنشر"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_t("btn_adm_bc_all", lang), callback_data="adm_bc_start:all"),
            InlineKeyboardButton(_t("btn_adm_bc_active", lang), callback_data="adm_bc_start:active")
        ],
        [InlineKeyboardButton(_t("btn_adm_back", lang), callback_data="adm_main_back")]
    ])

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة التحكم بالمشتركين واستخراج الداتا"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_t("btn_adm_usr_latest", lang), callback_data="adm_users"),
            InlineKeyboardButton(_t("btn_adm_usr_id", lang), callback_data="adm_user_info_prompt")
        ],
        [InlineKeyboardButton(_t("btn_adm_usr_csv", lang), callback_data="adm_export_db")],
        [InlineKeyboardButton(_t("btn_adm_back", lang), callback_data="adm_main_back")]
    ])

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة إعدادات النظام، الصيانة، مفاتيح الإعلانات وأدوات المطور المعززة"""
    maint_on = get_setting("maintenance", "0") == "1"
    maint_text = _t("txt_maint_on", lang) if maint_on else _t("txt_maint_off", lang)
    
    hilltop_on = get_setting("hilltop_status", "1") == "1"
    hilltop_text = _t("txt_ad_on", lang, name="Hilltop") if hilltop_on else _t("txt_ad_off", lang, name="Hilltop")
    
    adsterra_on = get_setting("adsterra_status", "1") == "1"
    adsterra_text = _t("txt_ad_on", lang, name="AdSterra") if adsterra_on else _t("txt_ad_off", lang, name="AdSterra")
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(maint_text, callback_data="adm_toggle_maint")],
        [
            InlineKeyboardButton(hilltop_text, callback_data="adm_toggle_hilltop"),
            InlineKeyboardButton(adsterra_text, callback_data="adm_toggle_adsterra")
        ],
        [
            InlineKeyboardButton(_t("btn_adm_sec_update", lang), callback_data="adm_update_dlp"),
            InlineKeyboardButton(_t("btn_adm_sec_cookie", lang), callback_data="adm_cookie_guide")
        ],
        [
            InlineKeyboardButton(_t("btn_adm_sec_clean", lang), callback_data="adm_clean"),
            InlineKeyboardButton(_t("btn_adm_sec_backup", lang), callback_data="adm_backup_db")
        ],
        [InlineKeyboardButton(_t("btn_adm_back", lang), callback_data="adm_main_back")]
    ])

def admin_cancel_action_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    """زر إلغاء أي إجراء إداري معلق والعودة الفورية للرئيسية"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_adm_cancel_action", lang), callback_data="adm_cancel_action")]
    ])
