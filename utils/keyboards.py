from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from locales.language import _t
from core.config import WEBSITE_PLAYZONE, FACEBOOK_PLAYZONE, INSTAGRAM_PLAYZONE, THREADS_PLAYZONE, TELEGRAM_BOT_PLAYZONE
from database.operations import get_setting

def user_main_keyboard(lang: str = "ar") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([span_105](start_span)[span_105](end_span)
        [[span_106](start_span)[span_106](end_span)
            [KeyboardButton(_t("btn_guide", lang)), KeyboardButton(_t("btn_links", lang))],[span_107](start_span)[span_107](end_span)
            [KeyboardButton(_t("btn_add_group", lang))][span_108](start_span)[span_108](end_span)
        ],[span_109](start_span)[span_109](end_span)
        resize_keyboard=True, is_persistent=True, input_field_placeholder=_t("txt_placeholder", lang)[span_110](start_span)[span_110](end_span)
    )[span_111](start_span)[span_111](end_span)

def build_preview_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    # 🌟 تم وضع زر الهندسة الصوتية بشكل منفصل واحترافي هنا وتطهير أزرار العزل
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_t("btn_audio", lang), callback_data=f"aud:{request_id}"),
            InlineKeyboardButton(_t("btn_video", lang), callback_data=f"vid:{request_id}")
        ],
        [
            InlineKeyboardButton("🎚 هندسة صوتية احترافية (HQ)" if lang == "ar" else "🎚 Audio Normalization (HQ)", callback_data=f"norm:{request_id}")
        ],
        [InlineKeyboardButton(_t("btn_cancel", lang), callback_data=f"cancel:{request_id}")],
    ])

def build_resolution_keyboard(request_id: str, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_112](start_span)[span_112](end_span)
        [[span_113](start_span)[span_113](end_span)
            InlineKeyboardButton("360p", callback_data=f"res:360:{request_id}"),[span_114](start_span)[span_114](end_span)
            InlineKeyboardButton("480p", callback_data=f"res:480:{request_id}")[span_115](start_span)[span_115](end_span)
        ],[span_116](start_span)[span_116](end_span)
        [[span_117](start_span)[span_117](end_span)
            InlineKeyboardButton("720p", callback_data=f"res:720:{request_id}"),[span_118](start_span)[span_118](end_span)
            InlineKeyboardButton("1080p", callback_data=f"res:1080:{request_id}")[span_119](start_span)[span_119](end_span)
        ],[span_120](start_span)[span_120](end_span)
        [InlineKeyboardButton(_t("btn_best_quality", lang), callback_data=f"res:best:{request_id}")],[span_121](start_span)[span_121](end_span)
        [InlineKeyboardButton(_t("btn_back", lang), callback_data=f"back:{request_id}")][span_122](start_span)[span_122](end_span)
    ])[span_123](start_span)[span_123](end_span)

def build_playzone_links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_124](start_span)[span_124](end_span)
        [InlineKeyboardButton("🌐 Website PlayZone", url=WEBSITE_PLAYZONE)],[span_125](start_span)[span_125](end_span)
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_PLAYZONE), InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_PLAYZONE)],[span_126](start_span)[span_126](end_span)
        [InlineKeyboardButton("🧵 Threads", url=THREADS_PLAYZONE), InlineKeyboardButton("🤖 Telegram Bot", url=TELEGRAM_BOT_PLAYZONE)],[span_127](start_span)[span_127](end_span)
    ])[span_128](start_span)[span_128](end_span)

# =======================================================
#               لوحات تحكم الإدارة (ترتيب مؤسسي)
# =======================================================

def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_129](start_span)[span_129](end_span)
        [InlineKeyboardButton("📢 الإذاعة والتواصل", callback_data="adm_bc_menu"),[span_130](start_span)[span_130](end_span)
         InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_users_menu")],[span_131](start_span)[span_131](end_span)
        [InlineKeyboardButton("🛡️ الحماية والصيانة", callback_data="adm_sec_menu"),[span_132](start_span)[span_132](end_span)
         InlineKeyboardButton("📊 حالة السيرفر", callback_data="adm_server")],[span_133](start_span)[span_133](end_span)
        [InlineKeyboardButton("✖️ إغلاق لوحة التحكم", callback_data="adm_close")][span_134](start_span)[span_134](end_span)
    ])[span_135](start_span)[span_135](end_span)

def admin_broadcast_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_136](start_span)[span_136](end_span)
        [InlineKeyboardButton("📨 إرسال للجميع (كل المستخدمين)", callback_data="adm_bc_start:all")],[span_137](start_span)[span_137](end_span)
        [InlineKeyboardButton("⚡ إرسال للنشطين (آخر 48 ساعة)", callback_data="adm_bc_start:active")],[span_138](start_span)[span_138](end_span)
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")][span_139](start_span)[span_139](end_span)
    ])[span_140](start_span)[span_140](end_span)

def admin_users_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_141](start_span)[span_141](end_span)
        [InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats")],[span_142](start_span)[span_142](end_span)
        [InlineKeyboardButton("📋 أحدث المنضمين", callback_data="adm_users"),[span_143](start_span)[span_143](end_span)
         InlineKeyboardButton("🔎 استعلام عن (ID)", callback_data="adm_user_info_prompt")],[span_144](start_span)[span_144](end_span)
        [InlineKeyboardButton("📥 تصدير تقرير المستخدمين (CSV)", callback_data="adm_export_db")],[span_145](start_span)[span_145](end_span)
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")][span_146](start_span)[span_146](end_span)
    ])[span_147](start_span)[span_147](end_span)

def admin_security_menu(lang: str = "ar") -> InlineKeyboardMarkup:
    maint = get_setting("maintenance", "0")[span_148](start_span)[span_148](end_span)
    maint_btn = InlineKeyboardButton("🔴 إيقاف وضع الصيانة" if maint == "1" else "🟢 تفعيل وضع الصيانة", callback_data="adm_toggle_maint")[span_149](start_span)[span_149](end_span)
    
    ads_status = get_setting("ads_status", "1")[span_150](start_span)[span_150](end_span)
    ads_btn = InlineKeyboardButton("🔴 إيقاف الإعلانات مؤقتاً" if ads_status == "1" else "🟢 تفعيل الإعلانات", callback_data="adm_toggle_ads")[span_151](start_span)[span_151](end_span)
    
    return InlineKeyboardMarkup([[span_152](start_span)[span_152](end_span)
        [maint_btn],[span_153](start_span)[span_153](end_span)
        [ads_btn],[span_154](start_span)[span_154](end_span)
        [InlineKeyboardButton("🔄 تحديث مكتبة المحرك", callback_data="adm_update_dlp"),[span_155](start_span)[span_155](end_span)
         InlineKeyboardButton("🍪 إرشادات الكوكيز", callback_data="adm_cookie_guide")],[span_156](start_span)[span_156](end_span)
        [InlineKeyboardButton("🗜️ تحسين القاعدة", callback_data="adm_vacuum_db"),[span_157](start_span)[span_157](end_span)
         InlineKeyboardButton("🧹 تنظيف التخزين المؤقت", callback_data="adm_clean")],[span_158](start_span)[span_158](end_span)
        [InlineKeyboardButton("💾 تحميل نسخة احتياطية (DB)", callback_data="adm_backup_db")],[span_159](start_span)[span_159](end_span)
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="adm_main_back")][span_160](start_span)[span_160](end_span)
    ])[span_161](start_span)[span_161](end_span)

def admin_cancel_action_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[span_162](start_span)[span_162](end_span)
        [InlineKeyboardButton("❌ إلغاء الإجراء الحالي", callback_data="adm_cancel_action")][span_163](start_span)[span_163](end_span)
    ])[span_164](start_span)[span_164](end_span)
