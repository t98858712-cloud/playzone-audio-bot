"""
📸 بوت تحميل ستوريات الإنستغرام
الإصدار النهائي - آمن وسهل الاستخدام
"""

import os
import re
import time
import shutil
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ==========================================================
# 🔒 تحميل المتغيرات البيئية
# ==========================================================

try:
    from dotenv import load_dotenv
    load_dotenv()  # تحميل من ملف .env إن وجد
except ImportError:
    pass  # تجاهل إذا لم تكن المكتبة مثبتة

import instaloader
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================================
# 📋 الإعدادات الأساسية (من المتغيرات البيئية)
# ==========================================================

# توكن البوت - مطلوب
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN غير موجود! أضفه في المتغيرات البيئية")

# بيانات الإنستغرام - اختيارية (لتحميل الحسابات الخاصة)
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
INSTAGRAM_SESSION_FILE = Path(os.getenv("INSTAGRAM_SESSION_FILE", "./data/instagram_session.session"))

# إعدادات التحميل
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DOWNLOAD_DIR.mkdir(exist_ok=True)

# إعدادات الأداء
MAX_STORIES = int(os.getenv("MAX_STORIES", "25"))  # الحد الأقصى للستوريات
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))   # عدد العمليات المتوازية
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50"))  # الحد الأقصى للحجم بالميجابايت

# معرفات المديرين (اختياري)
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# ==========================================================
# 📝 إعدادات السجلات
# ==========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("InstagramBot")

# ==========================================================
# 🛠️ أدوات مساعدة
# ==========================================================

def format_size(size_bytes: int) -> str:
    """تحويل الحجم إلى صيغة مقروءة"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

def clean_filename(text: str) -> str:
    """تنظيف اسم الملف من الأحرف غير المسموحة"""
    return re.sub(r'[<>:"/\\|?*]', '_', text)

# ==========================================================
# 🎯 محمل الإنستغرام
# ==========================================================

class InstagramDownloader:
    """الكلاس المسؤول عن تحميل ستوريات الإنستغرام"""
    
    def __init__(self):
        self.loader = None
        self.authenticated = False
        self._init_loader()

    def _init_loader(self):
        """تهيئة المحمل مع محاولة المصادقة"""
        self.loader = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            compress_json=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            max_connection_attempts=3,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # محاولة تحميل جلسة مخزنة
        if INSTAGRAM_SESSION_FILE.exists():
            try:
                self.loader.load_session_from_file(INSTAGRAM_USERNAME, str(INSTAGRAM_SESSION_FILE))
                self.authenticated = True
                logger.info("✅ تم تحميل جلسة الإنستغرام من الملف")
                return
            except Exception as e:
                logger.warning(f"⚠️ فشل تحميل الجلسة المخزنة: {e}")

        # محاولة تسجيل دخول جديد
        if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
            try:
                logger.info("🔄 محاولة تسجيل الدخول إلى الإنستغرام...")
                self.loader.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                self.loader.save_session_to_file(str(INSTAGRAM_SESSION_FILE))
                self.authenticated = True
                logger.info(f"✅ تم تسجيل الدخول بنجاح كـ {INSTAGRAM_USERNAME}")
                return
            except instaloader.exceptions.TwoFactorAuthRequiredException:
                logger.error("❌ مطلوب رمز المصادقة الثنائية (2FA)!")
            except Exception as e:
                logger.error(f"❌ فشل تسجيل الدخول: {e}")

        logger.warning("⚠️ وضع عدم المصادقة - فقط الحسابات العامة")

    def refresh_session(self) -> bool:
        """تحديث الجلسة"""
        self._init_loader()
        return self.authenticated

    def extract_username(self, text: str) -> Optional[str]:
        """استخراج اسم المستخدم من النص"""
        patterns = [
            r'(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/([^/?\s]+)',
            r'@([a-zA-Z0-9_.]{1,30})',
            r'^([a-zA-Z0-9_.]{1,30})$'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text.strip())
            if match:
                username = match.group(1)
                # استبعاد الكلمات المفتاحية
                if username not in ['p', 'reel', 'tv', 'explore', 'accounts', 'stories', 'share']:
                    return username
        return None

    def download_stories(self, username: str, output_dir: Path) -> List[Dict]:
        """
        تحميل جميع الستوريات الحالية لمستخدم معين
        إرجاع قائمة بالملفات المحملة
        """
        if not self.authenticated:
            logger.warning(f"⚠️ تحميل ستوريات @{username} بدون مصادقة (قد يفشل للحسابات الخاصة)")

        stories = []
        
        try:
            # جلب الملف الشخصي
            profile = instaloader.Profile.from_username(self.loader.context, username)
            logger.info(f"📊 جاري تحميل ستوريات @{username}...")
            
            count = 0
            for story in profile.get_stories():
                for item in story.get_items():
                    if count >= MAX_STORIES:
                        break
                        
                    try:
                        # تحديد نوع الملف
                        is_video = item.is_video
                        ext = "mp4" if is_video else "jpg"
                        
                        # إنشاء اسم فريد للملف
                        timestamp = int(item.creation_time.timestamp()) if item.creation_time else int(time.time())
                        filename = f"{username}_story_{count}_{timestamp}.{ext}"
                        filepath = output_dir / filename
                        
                        # تحميل الستوري
                        self.loader.download_storyitem(
                            item,
                            target_folder=str(output_dir),
                            filename=filename
                        )
                        
                        # التحقق من وجود الملف
                        if filepath.exists():
                            file_size = filepath.stat().st_size
                            if file_size > 0:
                                stories.append({
                                    "path": str(filepath),
                                    "is_video": is_video,
                                    "size": file_size,
                                    "created": item.creation_time,
                                    "filename": filename
                                })
                                count += 1
                                logger.info(f"✅ تم تحميل ستوري #{count} من @{username}")
                            else:
                                logger.warning(f"⚠️ ملف فارغ: {filepath}")
                        else:
                            # محاولة البحث عن الملف بأي امتداد
                            possible_files = list(output_dir.glob(f"{username}_story_{count}.*"))
                            if possible_files:
                                filepath = possible_files[0]
                                stories.append({
                                    "path": str(filepath),
                                    "is_video": filepath.suffix.lower() in ['.mp4', '.mov', '.avi'],
                                    "size": filepath.stat().st_size,
                                    "created": item.creation_time,
                                    "filename": filepath.name
                                })
                                count += 1
                                
                    except Exception as e:
                        logger.error(f"❌ فشل تحميل ستوري: {e}")
                        
            logger.info(f"📊 تم تحميل {len(stories)} ستوري من @{username}")
            return stories
            
        except instaloader.exceptions.ProfileNotExistsException:
            logger.error(f"❌ الحساب @{username} غير موجود")
        except instaloader.exceptions.PrivateProfileNotFollowedException:
            logger.error(f"🔒 الحساب @{username} خاص ولا توجد مصادقة")
        except Exception as e:
            logger.error(f"❌ فشل الوصول للحساب @{username}: {e}")
            
        return []

# ==========================================================
# 💬 واجهة البوت
# ==========================================================

# تهيئة المحمل العالمي
downloader = InstagramDownloader()

def main_keyboard() -> ReplyKeyboardMarkup:
    """لوحة المفاتيح الرئيسية"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📸 تحميل ستوريات")],
            [KeyboardButton("📘 التعليمات"), KeyboardButton("ℹ️ معلومات البوت")],
            [KeyboardButton("🔄 تحديث الجلسة")] if downloader.authenticated else [],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل رابط الحساب أو اسم المستخدم..."
    )

def cancel_keyboard() -> ReplyKeyboardMarkup:
    """لوحة إلغاء"""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 إلغاء")]],
        resize_keyboard=True
    )

def admin_keyboard() -> InlineKeyboardMarkup:
    """لوحة التحكم للمدير"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")],
        [InlineKeyboardButton("🔄 تحديث الجلسة", callback_data="refresh")],
        [InlineKeyboardButton("🧹 تنظيف الملفات", callback_data="clean")],
        [InlineKeyboardButton("✖️ إغلاق", callback_data="close")],
    ])

# ==========================================================
# 🎯 دوال البوت الأساسية
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
    user = update.effective_user
    welcome_text = (
        f"👋 أهلاً بك {user.first_name}!\n\n"
        "📸 **بوت تحميل ستوريات الإنستغرام**\n\n"
        "✨ **ماذا يمكنني أن أفعل؟**\n"
        "• تحميل جميع الستوريات الحالية لأي حساب\n"
        "• دعم الحسابات العامة والخاصة (مع المصادقة)\n"
        "• إرسال الستوريات كصور وفيديوهات مباشرة\n\n"
        "📌 **كيف تبدأ؟**\n"
        "1️⃣ اضغط على زر '📸 تحميل ستوريات'\n"
        "2️⃣ أرسل رابط الحساب أو اسم المستخدم\n"
        "3️⃣ استمتع بالستوريات!\n\n"
        f"🔐 حالة المصادقة: {'✅ مفعلة' if downloader.authenticated else '❌ غير مفعلة (عام فقط)'}"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /help"""
    help_text = (
        "📘 **دليل الاستخدام**\n\n"
        "🔹 **طريقة التحميل:**\n"
        "• اضغط على زر '📸 تحميل ستوريات'\n"
        "• أرسل رابط الحساب أو اسم المستخدم\n\n"
        "🔹 **أمثلة:**\n"
        "• `instagram.com/username`\n"
        "• `@username`\n"
        "• `username`\n\n"
        "🔹 **معلومات:**\n"
        f"• الحد الأقصى: {MAX_STORIES} ستوري\n"
        f"• حجم الملف الأقصى: {MAX_FILE_SIZE} ميجابايت\n"
        f"• المصادقة: {'✅ مفعلة' if downloader.authenticated else '❌ غير مفعلة'}\n\n"
        "⚠️ **ملاحظة:** الحسابات الخاصة تحتاج إلى مصادقة"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /info"""
    status = "🟢 متصلة" if downloader.authenticated else "🟡 غير موثوقة"
    
    info_text = (
        "ℹ️ **معلومات البوت**\n\n"
        f"• الحالة: {status}\n"
        f"• المستخدم: {INSTAGRAM_USERNAME if INSTAGRAM_USERNAME else 'غير محدد'}\n"
        f"• الحد الأقصى: {MAX_STORIES} ستوري\n"
        f"• حجم الملف: {MAX_FILE_SIZE} ميجابايت\n"
        f"• العمليات المتوازية: {MAX_WORKERS}\n\n"
        f"📂 مجلد التحميل: `{DOWNLOAD_DIR}`"
    )
    
    if update.effective_user.id in ADMIN_IDS:
        await update.message.reply_text(
            info_text + "\n\n🛠 **لوحة المدير:** اضغط /admin",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    else:
        await update.message.reply_text(
            info_text,
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة المدير - /admin"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ غير مصرح لك!")
        return
    
    await update.message.reply_text(
        "🛠 **لوحة التحكم**\n\nاختر إحدى العمليات:",
        parse_mode="Markdown",
        reply_markup=admin_keyboard()
    )

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار لوحة المدير"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "close":
        await query.message.delete()
        return
    
    elif query.data == "stats":
        # إحصائيات بسيطة
        total_files = sum(1 for _ in DOWNLOAD_DIR.rglob("*") if _.is_file())
        total_size = sum(_.stat().st_size for _ in DOWNLOAD_DIR.rglob("*") if _.is_file())
        
        await query.message.edit_text(
            f"📊 **الإحصائيات**\n\n"
            f"• الملفات المؤقتة: {total_files}\n"
            f"• الحجم الكلي: {format_size(total_size)}\n"
            f"• المصادقة: {'✅ مفعلة' if downloader.authenticated else '❌ غير مفعلة'}\n"
            f"• المستخدم: {INSTAGRAM_USERNAME if INSTAGRAM_USERNAME else 'غير محدد'}",
            parse_mode="Markdown",
            reply_markup=admin_keyboard()
        )
    
    elif query.data == "refresh":
        # تحديث الجلسة
        await query.message.edit_text(
            "🔄 جاري تحديث الجلسة...",
            reply_markup=admin_keyboard()
        )
        success = downloader.refresh_session()
        await query.message.edit_text(
            f"{'✅' if success else '❌'} تحديث الجلسة: {'نجح' if success else 'فشل'}",
            reply_markup=admin_keyboard()
        )
    
    elif query.data == "clean":
        # تنظيف الملفات
        await query.message.edit_text("🧹 جاري تنظيف الملفات المؤقتة...")
        count = 0
        for item in DOWNLOAD_DIR.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                count += 1
            except Exception:
                pass
        await query.message.edit_text(
            f"✅ تم تنظيف {count} ملف/مجلد",
            reply_markup=admin_keyboard()
        )

# ==========================================================
# 📸 معالجة طلبات التحميل
# ==========================================================

async def handle_instagram_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالدة النصوص الواردة"""
    user = update.effective_user
    text = update.message.text.strip()
    
    # معالجة الأزرار
    if text == "📸 تحميل ستوريات":
        context.user_data["waiting_for_username"] = True
        await update.message.reply_text(
            "📸 **أرسل رابط الحساب أو اسم المستخدم**\n\n"
            "مثال:\n"
            "• `instagram.com/username`\n"
            "• `@username`\n"
            "• `username`\n\n"
            "يمكنك إلغاء العملية بالضغط على '🔙 إلغاء'",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return
    
    if text == "🔙 إلغاء":
        context.user_data.pop("waiting_for_username", None)
        await update.message.reply_text(
            "✅ تم الإلغاء",
            reply_markup=main_keyboard()
        )
        return
    
    if text in ["📘 التعليمات", "/help"]:
        await help_command(update, context)
        return
    
    if text in ["ℹ️ معلومات البوت", "/info"]:
        await info_command(update, context)
        return
    
    if text == "🔄 تحديث الجلسة":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("⛔ غير مصرح لك!")
            return
        success = downloader.refresh_session()
        await update.message.reply_text(
            f"{'✅' if success else '❌'} تحديث الجلسة: {'نجح' if success else 'فشل'}",
            reply_markup=main_keyboard()
        )
        return
    
    # إذا كان في وضع انتظار اسم المستخدم
    if context.user_data.get("waiting_for_username"):
        await process_username(update, context)

async def process_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اسم المستخدم وتحميل الستوريات"""
    text = update.message.text.strip()
    
    # استخراج اسم المستخدم
    username = downloader.extract_username(text)
    
    if not username:
        await update.message.reply_text(
            "❌ **لم أتمكن من استخراج اسم المستخدم**\n\n"
            "تأكد من الرابط أو الاسم ثم أعد المحاولة.\n\n"
            "أمثلة صحيحة:\n"
            "• `instagram.com/username`\n"
            "• `@username`\n"
            "• `username`",
            parse_mode="Markdown"
        )
        return
    
    # رسالة الحالة
    status_msg = await update.message.reply_text(
        f"🔍 **جاري البحث عن ستوريات @{username}...**\n\n"
        f"{'🔐 باستخدام المصادقة' if downloader.authenticated else '🌐 وضع عام فقط'}",
        parse_mode="Markdown"
    )
    
    # إنشاء مجلد مؤقت
    job_dir = DOWNLOAD_DIR / f"{username}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # تحميل الستوريات في خيط منفصل
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            stories = await loop.run_in_executor(
                executor,
                lambda: downloader.download_stories(username, job_dir)
            )
        
        # التحقق من النتائج
        if not stories:
            await status_msg.edit_text(
                f"❌ **لا توجد ستوريات حالية لـ @{username}**\n\n"
                "الأسباب المحتملة:\n"
                "• الحساب خاص ولا توجد مصادقة\n"
                "• لا توجد ستوريات منشورة حالياً\n"
                "• الحساب غير موجود",
                parse_mode="Markdown",
                reply_markup=main_keyboard()
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            context.user_data.pop("waiting_for_username", None)
            return
        
        # تحديث رسالة الحالة
        await status_msg.edit_text(
            f"📤 **جاري إرسال {len(stories)} ستوري من @{username}...**",
            parse_mode="Markdown"
        )
        
        # إرسال الستوريات
        sent = 0
        failed = 0
        
        for idx, story in enumerate(stories, 1):
            file_path = Path(story["path"])
            if not file_path.exists():
                failed += 1
                continue
            
            try:
                file_size = story.get("size", 0)
                if file_size > MAX_FILE_SIZE * 1024 * 1024:
                    logger.warning(f"⚠️ ملف كبير جداً: {file_size} bytes")
                    failed += 1
                    continue
                
                with open(file_path, "rb") as f:
                    caption = f"📸 ستوري {idx}/{len(stories)} من @{username}"
                    
                    if story["is_video"]:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=f,
                            caption=caption,
                            supports_streaming=True,
                            read_timeout=60,
                            write_timeout=60
                        )
                    else:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=f,
                            caption=caption
                        )
                    sent += 1
                    
                    # تأخير بسيط لتجنب حد الـ Rate Limit
                    await asyncio.sleep(0.3)
                    
            except Exception as e:
                logger.error(f"❌ فشل إرسال ستوري: {e}")
                failed += 1
        
        # رسالة النتيجة النهائية
        result_text = (
            f"✅ **تم التحميل بنجاح!**\n\n"
            f"👤 المستخدم: @{username}\n"
            f"📊 تم الإرسال: {sent}\n"
            f"❌ فشل: {failed}\n"
            f"📦 المجموع: {len(stories)}"
        )
        
        await status_msg.edit_text(
            result_text,
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        
        logger.info(f"✅ اكتمل تحميل ستوريات @{username}: {sent}/{len(stories)}")
        
    except Exception as e:
        logger.error(f"❌ خطأ في المعالجة: {e}")
        await status_msg.edit_text(
            "❌ **حدث خطأ أثناء المعالجة**\n\n"
            "يرجى المحاولة مرة أخرى لاحقاً.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    
    finally:
        # تنظيف الملفات بعد 5 دقائق
        async def cleanup():
            await asyncio.sleep(300)
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"🧹 تم تنظيف: {job_dir}")
            except Exception as e:
                logger.warning(f"⚠️ فشل تنظيف الملفات: {e}")
        
        asyncio.create_task(cleanup())
        context.user_data.pop("waiting_for_username", None)

# ==========================================================
# ⚠️ معالجة الأخطاء
# ==========================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء العام"""
    logger.error(f"❌ خطأ: {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."
            )
    except Exception:
        pass

# ==========================================================
# 🚀 تشغيل البوت
# ==========================================================

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    logger.info("=" * 50)
    logger.info("🚀 بدء تشغيل بوت تحميل ستوريات الإنستغرام...")
    logger.info(f"📂 مجلد التحميل: {DOWNLOAD_DIR}")
    logger.info(f"🔐 المصادقة: {'✅ مفعلة' if downloader.authenticated else '❌ غير مفعلة'}")
    logger.info(f"📊 الحد الأقصى للستوريات: {MAX_STORIES}")
    logger.info("=" * 50)
    
    # إنشاء التطبيق
    app = Application.builder().token(TOKEN).build()
    
    # إضافة معالجات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # معالجات الأزرار والرسائل
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(stats|refresh|clean|close)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_instagram_request))
    
    # معالج الأخطاء
    app.add_error_handler(error_handler)
    
    # تشغيل البوت
    logger.info("✅ البوت جاهز للاستخدام!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()