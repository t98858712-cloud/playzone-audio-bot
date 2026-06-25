"""
دمج ميزة Instagram Stories مع البوت الرئيسي
"""

import logging
import re
from pathlib import Path

from telegram.ext import Application, CallbackQueryHandler

from instagram_handlers import (
    init_story_downloader,
    get_story_downloader,
    handle_instagram_stories,
    handle_instagram_callbacks,
    ACTIVE_DOWNLOADS
)

logger = logging.getLogger("PlayZone.InstagramIntegration")

INSTAGRAM_PATTERNS = [
    r'instagram\.com/stories/',
    r'instagram\.com/[^/]+/stories',
    r'instagram\.com/[^/]+/story',
]

def is_instagram_stories_url(text: str) -> bool:
    """التحقق من رابط قصص Instagram"""
    text = text.lower()
    for pattern in INSTAGRAM_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

def setup_instagram_integration(app: Application, download_dir: Path):
    """إعداد دمج Instagram مع البوت"""
    init_story_downloader(download_dir)
    logger.info("✅ تم تهيئة خدمة Instagram Stories")
    
    # إضافة معالج للكولباك
    app.add_handler(CallbackQueryHandler(handle_instagram_callbacks, pattern="^(ig_|cancel_ig)"), group=2)
    
    logger.info("✅ تم إعداد معالج Instagram Stories")
    return True