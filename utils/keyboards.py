from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, BOT_USERNAME
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}")],
        [InlineKeyboardButton("🎛️ هندسة صوتية" if lang == "ar" else "🎛️ Sound Engineering", callback_data=f"aud_pro:{request_id}")],
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

def build_playzone_links_keyboard(bot_username: str = BOT_USERNAME) -> InlineKeyboardMarkup:
    tg_url = f"https://t.me/{bot_username.replace('@', '')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=tg_url)],
    ])

# =======================================================
#    📊 لوحة تحكم الإدارة الاحترافية (UI/UX Redesign)
# =======================================================

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة القيادة الرئيسية - شبكة مدمجة scannable ومريحة للعين"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"),
            InlineKeyboardButton("💽 السيرفر", callback_data="adm_server")
        ],
        [
            InlineKeyboardButton("📢 الإذاعة", callback_data="adm_bc_menu"),
            InlineKeyboardButton("👥 المشتركين", callback_data="adm_users_menu")
        ],
        [InlineKeyboardButton("⚙️ إعدادات النظام والحماية", callback_data="adm_sec_menu")],
        [InlineKeyboardButton("❌ إغلاق لوحة القيادة", callback_data="adm_close")]
    ])

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """قائمة خيارات الإذاعة والتواصل المدمجة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌍 إرسال للكل", callback_data="adm_bc_start:all"),
            InlineKeyboardButton("⚡ للنشطين فقط", callback_data="adm_bc_start:active")
        ],
        [InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="adm_main_back")]
    ])

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """قائمة خيارات المشتركين والتقارير الاستخراجية"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 أحدث الأعضاء", callback_data="adm_users"),
            InlineKeyboardButton("🔎 استعلام ID", callback_data="adm_user_info_prompt")
        ],
        [InlineKeyboardButton("📥 استخراج الداتا (CSV)", callback_data="adm_export_db")],
        [InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="adm_main_back")]
    ])

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    """لوحة الحماية والنظام المحدثة - نقاط ديناميكية حية وبدون ميزات مكررة"""
    # 🟢/🔴 تبديل مؤشر وضع الصيانة
    maint_on = get_setting("maintenance", "0") == "1"
    maint_text = "🟢 وضع الصيانة" if maint_on else "🔴 وضع الصيانة"
    
    # 🟢/🔴 تبديل مؤشر إعلانات Hilltop
    hilltop_on = get_setting("hilltop_status", "1") == "1"
    hilltop_text = "🟢 إعلانات Hilltop" if hilltop_on else "🔴 إعلانات Hilltop"
    
    # 🟢/🔴 تبديل مؤشر إعلانات Adsterra
    adsterra_on = get_setting("adsterra_status", "1") == "1"
    adsterra_text = "🟢 إعلانات AdSterra" if adsterra_on else "🔴 إعلانات AdSterra"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(maint_text, callback_data="adm_toggle_maint")],
        [
            InlineKeyboardButton(hilltop_text, callback_data="adm_toggle_hilltop"),
            InlineKeyboardButton(adsterra_text, callback_data="adm_toggle_adsterra")
        ],
        [
            InlineKeyboardButton("🔄 تحديث المحرك", callback_data="adm_update_dlp"),
            InlineKeyboardButton("🍪 تجديد الكوكيز", callback_data="adm_cookie_guide")
        ],
        [
            InlineKeyboardButton("🧹 تنظيف الكاش", callback_data="adm_clean"),
            InlineKeyboardButton("💾 نسخة سحابية", callback_data="adm_backup_db")
        ],
        [InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="adm_main_back")]
    ])

def admin_cancel_action_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء الإجراء الحالي", callback_data="adm_cancel_action")]
    ])
