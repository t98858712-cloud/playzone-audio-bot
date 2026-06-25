"""
معالجات Telegram لقصص Instagram
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, TimedOut, NetworkError

from instagram_stories import InstagramStoryDownloader

logger = logging.getLogger("PlayZone.InstagramHandlers")

# ==========================================================
# المتغيرات العامة
# ==========================================================

STORY_DOWNLOADER = None
ACTIVE_DOWNLOADS = set()

def init_story_downloader(download_dir: Path):
    """تهيئة محمل القصص"""
    global STORY_DOWNLOADER
    STORY_DOWNLOADER = InstagramStoryDownloader()
    return STORY_DOWNLOADER

def get_story_downloader() -> Optional[InstagramStoryDownloader]:
    return STORY_DOWNLOADER

# ==========================================================
# الأزرار
# ==========================================================

def stories_keyboard(username: str, request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 تحميل القصص", callback_data=f"ig_stories:{request_id}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_ig:{request_id}")],
    ])

# ==========================================================
# معالجة روابط القصص
# ==========================================================

async def handle_instagram_stories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رابط قصص Instagram"""
    if not STORY_DOWNLOADER:
        return await update.message.reply_text("❌ خدمة Instagram غير متاحة حالياً")
    
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    if user_id in ACTIVE_DOWNLOADS:
        return await update.message.reply_text("⏳ لديك تحميل قصص قيد التنفيذ")
    
    username = STORY_DOWNLOADER.extract_username_from_url(url)
    if not username:
        return await update.message.reply_text("❌ لم أتمكن من استخراج اسم المستخدم")
    
    import uuid
    request_id = uuid.uuid4().hex[:10]
    
    context.user_data[f"ig_request_{request_id}"] = {
        "url": url,
        "username": username,
        "created_at": int(time.time())
    }
    
    caption = (
        f"📸 <b>قصص Instagram</b>\n\n"
        f"👤 المستخدم: <b>{username}</b>\n"
        f"📋 سيتم جلب القصص الحالية\n\n"
        f"هل تريد تحميل قصص هذا الحساب؟"
    )
    
    await update.message.reply_text(
        caption,
        reply_markup=stories_keyboard(username, request_id),
        parse_mode="HTML"
    )

async def process_stories_download(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    """معالجة تحميل القصص"""
    user_id = update.effective_user.id
    query = update.callback_query
    
    request_data = context.user_data.get(f"ig_request_{request_id}")
    if not request_data:
        await query.answer("انتهت جلسة الطلب", show_alert=True)
        return
    
    username = request_data.get("username")
    ACTIVE_DOWNLOADS.add(user_id)
    
    try:
        await query.answer("جاري تحميل القصص...")
        await query.message.edit_text(f"📥 جاري جلب قصص {username}...")
        
        stories = STORY_DOWNLOADER.get_user_stories(username)
        
        if not stories:
            await query.message.edit_text(
                f"⚠️ لا توجد قصص متاحة لـ {username}\n\n"
                f"قد يكون الحساب خاصاً أو لا توجد قصص حالية"
            )
            return
        
        import tempfile
        temp_dir = Path(tempfile.mkdtemp())
        
        await query.message.edit_text(f"📤 جاري تحميل {len(stories)} قصة...")
        
        for idx, story in enumerate(stories, 1):
            try:
                file_path = STORY_DOWNLOADER.download_story(story, temp_dir)
                if not file_path:
                    continue
                
                caption = f"📸 قصة من {username}\n{idx}/{len(stories)}"
                
                if file_path.suffix in ['.mp4', '.mov']:
                    with open(file_path, 'rb') as f:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=f,
                            caption=caption,
                            supports_streaming=True,
                            timeout=120
                        )
                else:
                    with open(file_path, 'rb') as f:
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=f,
                            caption=caption,
                            timeout=120
                        )
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"فشل إرسال القصة {idx}: {e}")
        
        try:
            await query.message.delete()
        except Exception:
            pass
        
        share_text = "📥 حمّل قصص Instagram بسهولة!\n⚡ بوت سريع ومجاني\n👇 جرّبه الآن:"
        from urllib.parse import quote
        share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(share_text)}"
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ تم تحميل {len(stories)} قصة من {username}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌟 شارك البوت", url=share_link)]
            ])
        )
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    except Exception as e:
        logger.error(f"فشل تحميل القصص: {e}")
        try:
            await query.message.edit_text(f"❌ فشل تحميل القصص\n\nالسبب: {str(e)}")
        except Exception:
            pass
    finally:
        ACTIVE_DOWNLOADS.discard(user_id)

async def handle_instagram_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار Instagram"""
    query = update.callback_query
    data = query.data
    
    if data.startswith("ig_stories:"):
        request_id = data.split(":")[1]
        await process_stories_download(update, context, request_id)
    elif data.startswith("cancel_ig:"):
        request_id = data.split(":")[1]
        context.user_data.pop(f"ig_request_{request_id}", None)
        await query.answer("تم الإلغاء")
        try:
            await query.message.delete()
        except Exception:
            pass

# استيراد time للاستخدام في الكود
import time