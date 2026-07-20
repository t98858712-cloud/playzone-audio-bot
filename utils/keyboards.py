from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
# تم استبدال TELEGRAM_BOT_PLAYZONE بالمتغير العالمي BOT_USERNAME ليكون صمام أمان
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, BOT_USERNAME
from database.operations import get_setting

def user_main_keyboard(lang: str = "ar") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],
            #[KeyboardButton(_t("btn_add_group", lang))]
        ],
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

# تم وضع = BOT_USERNAME كقيمة افتراضية لمنع أي خطأ برمي في السيرفر عند استدعاء الدالة
def build_playzone_links_keyboard(bot_username: str = BOT_USERNAME) -> InlineKeyboardMarkup:
    # إنشاء رابط حي ومضمون للبوت الحالي لمنع أي توقف
    tg_url = f"https://t.me/{bot_username.replace('@', '')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=tg_url)],
    ])

# =======================================================
#               لوحات تحكم الإدارة (ترتيب مؤسسي)
# =======================================================

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 الإذاعة والتواصل", callback_data="adm_bc_menu"), 
         InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_users_menu")],
        [InlineKeyboardButton("🛡️ الحماية والصيانة", callback_data="adm_sec_menu"), 
         InlineKeyboardButton("📊 حالة السيرفر", callback_data="adm_server")],
        [InlineKeyboardButton("✖️ إغلاق لوحة التحكم", callback_data="adm_close")]
    ])

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 إرسال للجميع (كل المستخدمين)", callback_data="adm_bc_start:all")],
        [InlineKeyboardButton("⚡ إرسال للنشطين (آخر 48 ساعة)", callback_data="adm_bc_start:active")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")]
    ])

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats")],
        [InlineKeyboardButton("📋 أحدث المنضمين", callback_data="adm_users"), 
         InlineKeyboardButton("🔎 استعلام عن (ID)", callback_data="adm_user_info_prompt")],
        [InlineKeyboardButton("📥 تصدير تقرير المستخدمين (CSV)", callback_data="adm_export_db")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")]
    ])

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    maint = get_setting("maintenance", "0")
    maint_btn = InlineKeyboardButton("🔴 إيقاف وضع الصيانة" if maint == "1" else "🟢 تفعيل وضع الصيانة", callback_data="adm_toggle_maint")
    
    # فحص حالة مفتاح إعلانات HilltopAds من قاعدة البيانات
    hilltop_status = get_setting("hilltop_status", "1")
    hilltop_btn = InlineKeyboardButton("🔴 إيقاف HilltopAds" if hilltop_status == "1" else "🟢 تفعيل HilltopAds", callback_data="adm_toggle_hilltop")
    
    # فحص حالة مفتاح إعلانات Adsterra من قاعدة البيانات
    adsterra_status = get_setting("adsterra_status", "1")
    adsterra_btn = InlineKeyboardButton("🔴 إيقاف Adsterra" if adsterra_status == "1" else "🟢 تفعيل Adsterra", callback_data="adm_toggle_adsterra")
    
    return InlineKeyboardMarkup([
        [maint_btn], 
        [hilltop_btn],
        [adsterra_btn], 
        [InlineKeyboardButton("🔄 تحديث مكتبة المحرك", callback_data="adm_update_dlp"), 
         InlineKeyboardButton("🍪 إرشادات الكوكيز", callback_data="adm_cookie_guide")],
        [InlineKeyboardButton("🗜️ تحسين القاعدة", callback_data="adm_vacuum_db"), 
         InlineKeyboardButton("🧹 تنظيف التخزين المؤقت", callback_data="adm_clean")],
        [InlineKeyboardButton("💾 تحميل نسخة احتياطية (DB)", callback_data="adm_backup_db")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")]
    ])

def admin_cancel_action_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء الإجراء الحالي", callback_data="adm_cancel_action")]
    ])
