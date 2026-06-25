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

from instagram_stories import InstagramStoryHandler

logger = logging.getLogger("PlayZoneEnterpriseBot.InstagramHandlers")

# ==========================================================
# المتغيرات العامة
# ==========================================================

INSTAGRAM_HANDLER = None
ACTIVE_INSTAGRAM_DOWNLOADS = set()

def init_instagram_handler(download_dir: Path):
    """تهيئة معالج Instagram"""
    global INSTAGRAM_HANDLER
    INSTAGRAM_HANDLER = InstagramStoryHandler(download_dir)
    return INSTAGRAM_HANDLER

def get_instagram_handler() -> Optional[InstagramStoryHandler]:
    """الحصول على معالج Instagram"""
    return INSTAGRAM_HANDLER

# ==========================================================
# أزرار القصص
# ==========================================================

def stories_keyboard(username: str, request_id: str) -> InlineKeyboardMarkup:
    """لوحة مفاتيح للقصص"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 تحميل القصص", callback_data=f"ig_stories:{request_id}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_ig:{request_id}")],
    ])

def stories_select_keyboard(stories_count: int, request_id: str) -> InlineKeyboardMarkup:
    """لوحة مفاتيح لاختيار القصص"""
    buttons = []
    
    # أزرار للقصص الفردية (حد أقصى 5)
    for i in range(min(stories_count, 5)):
        buttons.append([InlineKeyboardButton(f"📸 قصة {i+1}", callback_data=f"ig_story:{request_id}:{i}")])
    
    buttons.append([InlineKeyboardButton("📥 تحميل الكل", callback_data=f"ig_all:{request_id}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_ig:{request_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ==========================================================
# معالجة روابط القصص
# ==========================================================

async def handle_instagram_stories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالجة رابط قصص Instagram وإظهار معاينة
    """
    if not INSTAGRAM_HANDLER:
        return await update.message.reply_text("❌ خدمة Instagram غير متاحة حالياً")
    
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    # التحقق من أن المستخدم ليس مشغولاً
    if user_id in ACTIVE_INSTAGRAM_DOWNLOADS:
        return await update.message.reply_text("⏳ لديك تحميل قصص قيد التنفيذ")
    
    # استخراج اسم المستخدم
    username = INSTAGRAM_HANDLER.extract_username(url)
    if not username:
        return await update.message.reply_text("❌ لم أتمكن من استخراج اسم المستخدم من الرابط")
    
    # إنشاء معرف للطلب
    import uuid
    request_id = uuid.uuid4().hex[:10]
    
    # تخزين بيانات الطلب
    context.user_data[f"ig_request_{request_id}"] = {
        "url": url,
        "username": username,
        "created_at": int(asyncio.get_event_loop().time())
    }
    
    # إظهار معاينة
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
    """
    معالجة تحميل القصص
    """
    user_id = update.effective_user.id
    query = update.callback_query
    
    # الحصول على بيانات الطلب
    request_data = context.user_data.get(f"ig_request_{request_id}")
    if not request_data:
        await query.answer("انتهت جلسة الطلب", show_alert=True)
        return
    
    username = request_data.get("username")
    
    # إضافة المستخدم إلى القائمة النشطة
    ACTIVE_INSTAGRAM_DOWNLOADS.add(user_id)
    
    try:
        # تحديث الرسالة
        await query.answer("جاري تحميل القصص...")
        await query.message.edit_text(f"📥 جاري جلب قصص {username}...")
        
        # دالة تحديث التقدم
        async def progress_callback(text: str):
            try:
                await query.message.edit_text(text)
            except Exception:
                pass
        
        # معالجة القصص
        downloaded_files, story_data = await INSTAGRAM_HANDLER.process_stories(
            request_data["url"],
            progress_callback
        )
        
        if not downloaded_files:
            await query.message.edit_text(
                f"⚠️ لا توجد قصص متاحة لـ {username}\n\n"
                f"قد يكون الحساب خاصاً أو لا توجد قصص حالية"
            )
            return
        
        # إرسال القصص
        await query.message.edit_text(f"📤 جاري إرسال {len(downloaded_files)} قصة...")
        
        for idx, file_path in enumerate(downloaded_files, 1):
            try:
                caption = f"📸 قصة من {username}\n{idx}/{len(downloaded_files)}"
                
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
                
                await asyncio.sleep(1)  # تجنب تجاوز الحدود
                
            except Exception as e:
                logger.error(f"فشل إرسال القصة {idx}: {e}")
                try:
                    await query.message.reply_text(f"⚠️ فشل إرسال القصة {idx}")
                except Exception:
                    pass
        
        # تنظيف الرسالة الأصلية
        try:
            await query.message.delete()
        except Exception:
            pass
        
        # زر المشاركة
        share_text = (
            "📥 حمّل قصص Instagram بسهولة!\n"
            "⚡ بوت سريع ومجاني\n"
            "👇 جرّبه الآن:"
        )
        from urllib.parse import quote
        share_link = f"https://t.me/share/url?url={quote('https://t.me/MusicPlayZoneBot')}&text={quote(share_text)}"
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ تم تحميل {len(downloaded_files)} قصة من {username}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌟 شارك البوت", url=share_link)]
            ])
        )
        
    except Exception as e:
        logger.error(f"فشل تحميل القصص: {e}")
        try:
            await query.message.edit_text(
                f"❌ فشل تحميل القصص\n\n"
                f"السبب: {str(e)}"
            )
        except Exception:
            pass
    finally:
        ACTIVE_INSTAGRAM_DOWNLOADS.discard(user_id)
        # تنظيف مجلد التحميلات
        try:
            job_dir = INSTAGRAM_HANDLER.download_dir / f"ig_{username}_{int(asyncio.get_event_loop().time())}"
            if job_dir.exists():
                shutil.rmtree(job_dir)
        except Exception:
            pass

# ==========================================================
# معالجة الأزرار
# ==========================================================

async def handle_instagram_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالجة أزرار Instagram
    """
    query = update.callback_query
    data = query.data
    
    if data.startswith("ig_stories:"):
        # تحميل القصص
        request_id = data.split(":")[1]
        await process_stories_download(update, context, request_id)
        
    elif data.startswith("cancel_ig:"):
        # إلغاء
        request_id = data.split(":")[1]
        context.user_data.pop(f"ig_request_{request_id}", None)
        await query.answer("تم الإلغاء")
        try:
            await query.message.delete()
        except Exception:
            pass
    
    elif data.startswith("ig_story:"):
        # تحميل قصة فردية (سيتم تنفيذها لاحقاً)
        await query.answer("سيتم إضافة هذه الميزة قريباً")
        
    elif data.startswith("ig_all:"):
        # تحميل الكل
        request_id = data.split(":")[1]
        await process_stories_download(update, context, request_id)