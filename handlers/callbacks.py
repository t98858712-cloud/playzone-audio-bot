        # الكود الخاص بالتحقق من الإعلانات في handle_callbacks:
        from database.operations import check_ad_verified_status, get_setting
        adsterra_status = get_setting("adsterra_status", "1")
        
        if is_admin(uid) or adsterra_status == "0" or check_ad_verified_status(uid):
            if data.startswith("v_ad:"):
                await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)
            else:
                if mode == "audio": await query.answer(_t("msg_prep_audio", lang))
                else: await query.answer(_t("msg_prep_video", lang))

            request = ensure_pending_requests(context).pop(request_id, None)
            trim_old_pending_requests(context)
            
            if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))
            if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
            
            await start_download_from_callback(query, context, request, mode, resolution, lang)
        else:
            if data.startswith("v_ad:"):
                ad_start = context.user_data.get(f"ad_start_{request_id}", 0)
                if time.time() - ad_start >= 8:
                    from database.operations import verify_user_ad_completion
                    verify_user_ad_completion(uid)
                    
                    await query.answer("✅ تم التحقق بنجاح! جاري بدء التحميل...", show_alert=True)
                    request = ensure_pending_requests(context).pop(request_id, None)
                    trim_old_pending_requests(context)
                    if not request: return await edit_message_smart(query.message, _t("msg_session_expired", lang))
                    if uid in ACTIVE_USERS: return await query.answer(_t("msg_wait_current", lang), show_alert=True)
                    await start_download_from_callback(query, context, request, mode, resolution, lang)
                else:
                    return await query.answer("❌ يرجى فتح رابط Adsterra والانتظار ثوانٍ قبل التحقق.", show_alert=True)
            else:
                await query.answer()
                context.user_data[f"ad_start_{request_id}"] = time.time()
                
                ad_direct_url = ADSTERRA_LINK
                
                btn_watch = "📺 مشاهدة إعلان" if lang == "ar" else "📺 Watch Ad"
                btn_verify = "🔄 التحقق من اكتمال المشاهدة" if lang == "ar" else "🔄 Verify Ad Completion"
                
                ad_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(btn_watch, url=ad_direct_url)],
                    [InlineKeyboardButton(btn_verify, callback_data=f"v_ad:{mode}:{resolution}:{request_id}")]
                ])
                
                msg_text = (
                    "📥 <b>فك قفل التحميل:</b>\n\n"
                    "1️⃣ اضغط زر الإعلان أعلاه.\n"
                    "2️⃣ افتح الرابط وعد للبوت.\n"
                    "3️⃣ اضغط زر التحقق للبدء الفوري. ❤️"
                )
                await edit_message_smart(query.message, msg_text, reply_markup=ad_keyboard)
