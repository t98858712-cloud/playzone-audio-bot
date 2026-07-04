def admin_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_t("btn_adm_stats", lang), callback_data="adm_view_stats"),
            InlineKeyboardButton(_t("btn_adm_srv", lang), callback_data="adm_view_server")
        ],
        [
            InlineKeyboardButton("👤 فحص مستخدم", callback_data="adm_req_user"),
            InlineKeyboardButton("🍪 تحديث الكوكيز", callback_data="adm_req_cookie")
        ],
        [
            InlineKeyboardButton("🔄 تحديث yt-dlp", callback_data="adm_update_dlp"),
            InlineKeyboardButton("💾 نسخة احتياطية", callback_data="adm_backup")
        ],
        [
            InlineKeyboardButton(_t("btn_adm_bc_menu", lang), callback_data="adm_bc_menu"), 
            InlineKeyboardButton(_t("btn_adm_sec_menu", lang), callback_data="adm_sec_menu")
        ],
        [
            InlineKeyboardButton(_t("btn_adm_close", lang), callback_data="adm_close")
        ]
    ])
