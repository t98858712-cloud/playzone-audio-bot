import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import RetryAfter
from database.users import all_user_ids, get_active_users_48h
from database.stats import stat_inc_sync
from locales.language import _t

logger = logging.getLogger("PlayZoneEnterpriseBot")

async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_active"] = False
    lang = context.user_data.get("lang", "ar")
    target = context.user_data.get("bc_target", "all")
    users = all_user_ids() if target == "all" else get_active_users_48h()
    if not users:
        return await update.message.reply_text("📋 لا يوجد مستخدمين.")
    status = await update.message.reply_text(_t("msg_adm_bc_start", lang))
    sent, fail, total = 0, 0, len(users)
    for i, user_id in enumerate(users):
        try:
            await update.message.copy(chat_id=user_id)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
            try:
                await update.message.copy(chat_id=user_id)
                sent += 1
            except:
                fail += 1
        except:
            fail += 1
        if i % 20 == 0 and i > 0:
            try:
                await status.edit_text(f"⏳ جاري النشر: {i} / {total}\n✅ نجاح: {sent} | ❌ فشل: {fail}", parse_mode="HTML")
            except:
                pass
    stat_inc_sync("broadcasts")
    await status.edit_text(_t("msg_adm_bc_done", lang, sent=sent, fail=fail), parse_mode="HTML")
