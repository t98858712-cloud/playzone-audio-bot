from telegram import Update
from telegram.ext import ContextTypes
from database.users import register_user_sync
from utils.helpers import esc
from buttons.keyboards import user_main_keyboard, build_playzone_links_keyboard
from locales.language import _t
from core.config import BOT_USERNAME

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user_sync(update.effective_user); lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(_t("msg_start", lang, first_name=esc(update.effective_user.first_name or "")), reply_markup=user_main_keyboard(lang), parse_mode="HTML", disable_web_page_preview=True)

async def toggle_lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_lang = context.user_data.get("lang", "ar"); new_lang = "en" if current_lang == "ar" else "ar"; context.user_data["lang"] = new_lang
    await update.message.reply_text(_t("msg_lang_changed", new_lang), reply_markup=user_main_keyboard(new_lang))

async def show_playzone_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "ar")
    await update.message.reply_text(_t("msg_links", lang), reply_markup=build_playzone_links_keyboard(BOT_USERNAME), disable_web_page_preview=True)
