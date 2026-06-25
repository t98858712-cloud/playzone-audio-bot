"""
دمج ميزة Instagram Stories مع البوت الرئيسي
"""

import logging
from pathlib import Path

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from instagram_handlers import (
    init_instagram_handler,
    get_instagram_handler,
    handle_instagram_stories,
    handle_instagram_callbacks,
    ACTIVE_INSTAGRAM_DOWNLOADS
)

logger = logging.getLogger("PlayZoneEnterpriseBot.InstagramIntegration")

# ==========================================================
# أدوات للكشف عن روابط Instagram
# ==========================================================

INSTAGRAM_PATTERNS = [
    r'instagram\.com/stories/',
    r'instagram\.com/[^/]+/stories',
    r'instagram\.com/[^/]+/story',
    r'instagr\.am/stories/',
]

def is_instagram_stories_url(text: str) -> bool:
    """التحقق مما إذا كان النص يحتوي على رابط قصص Instagram"""
    import re
    text = text.lower()
    for pattern in INSTAGRAM_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

def setup_instagram_integration(app: Application, download_dir: Path):
    """
    إعداد دمج Instagram مع البوت
    
    Args:
        app: تطبيق البوت
        download_dir: مجلد التحميلات
    """
    # تهيئة معالج Instagram
    init_instagram_handler(download_dir)
    logger.info("✅ تم تهيئة خدمة Instagram Stories")
    
    # إضافة معالج للروابط
    def instagram_filter(update):
        """فلتر للكشف عن روابط Instagram"""
        if not update.message or not update.message.text:
            return False
        return is_instagram_stories_url(update.message.text)
    
    # إضافة معالج مخصص لروابط Instagram (يتم التحقق قبل المعالج العام)
    # سنقوم بدمج هذا في main.py من خلال تعديل دالة handle_incoming_text
    
    logger.info("✅ تم إعداد معالج Instagram Stories")
    
    # إضافة معالج للكولباك
    app.add_handler(CallbackQueryHandler(handle_instagram_callbacks, pattern="^(ig_|cancel_ig)"), group=2)
    
    return True

# ==========================================================
# دالة لدمج Instagram في معالج النصوص الرئيسي
# ==========================================================

async def handle_incoming_text_with_instagram(update, context, original_handler):
    """
    نسخة معدلة من handle_incoming_text تدعم Instagram
    هذه الدالة تستخدم لاستبدال الدالة الأصلية
    """
    from telegram import Update
    from telegram.ext import ContextTypes
    
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # التحقق من رابط Instagram Stories
    if is_instagram_stories_url(text):
        return await handle_instagram_stories(update, context)
    
    # استخدام المعالج الأصلي للروابط الأخرى
    return await original_handler(update, context)