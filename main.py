import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp

# سحب التوكن بأمان من متغيرات بيئة سيرفر Railway
TOKEN = os.getenv("TELEGRAM_TOKEN")
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البدء /start"""
    await update.message.reply_text(
        "👋 أهلاً بك في بوت تحميل يوتيوب على سيرفر Railway!\n"
        "قم بإرسال أي رابط فيديو من يوتيوب وسأقوم بالواجب."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 1: استلاف الرابط من المستخدم"""
    url = update.message.text
    
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ عذراً، هذا الرابط لا يبدو كرابط يوتيوب صحيح.")
        return

    # حفظ الرابط مؤقتاً في ذاكرة الجلسة
    context.user_data['current_url'] = url

    # إنشاء الأزرار التفاعلية للصيغ
    keyboard = [
        [
            InlineKeyboardButton("🎵 صوت (MP3)", callback_data='mp3'),
            InlineKeyboardButton("🎬 فيديو (MP4)", callback_data='mp4')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر الصيغة التي ترغب بتحميلها:", reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوات 2، 3، 4، 5، 6: المعالجة، التحميل، التحويل، الرفع، ثم الحذف"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    url = context.user_data.get('current_url')

    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الجلسة، يرجى إعادة إرسال الرابط.")
        return

    status_message = await query.edit_message_text("⏳ جاري استخراج روابط يوتيوب الداخلية وبدء المعالجة...")
    out_tmpl = os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s')

    # إعدادات yt-dlp واستدعاء ffmpeg للتحويل تلقائياً
    if choice == 'mp3':
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_tmpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
    else:
        ydl_opts = {
            # اختيار جودة متوسطة لعدم تخطي حد الـ 50 ميجا الخاص بتيليجرام
            'format': 'best[ext=mp4][height<=720]/best',
            'outtmpl': out_tmpl,
            'quiet': True,
        }

    loop = asyncio.get_running_loop()
    file_path = None

    try:
        await status_message.edit_text("📥 السيرفر يقوم بتحميل الملف وتحويله الآن عبر ffmpeg...")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # الخطوة 2 و 3: استخراج المعلومات وتحميل الملف مؤقتاً على السيرفر
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
            # الخطوة 4: تحديد المسار النهائي للملف بعد معالجة ffmpeg
            if choice == 'mp3':
                file_path = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp3"
            else:
                file_path = ydl.prepare_filename(info)
                if not os.path.exists(file_path):
                    file_path = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp4"

            title = info.get('title', 'Audio/Video')

        # الخطوة 5: إرسال الملف النهائي عبر Telegram Bot API للمستخدم
        await status_message.edit_text("📤 جاري رفع الملف إلى تيليجرام...")
        
        with open(file_path, 'rb') as file_to_send:
            if choice == 'mp3':
                await query.message.reply_audio(audio=file_to_send, title=title)
            else:
                await query.message.reply_video(video=file_to_send, caption=title)
        
        await status_message.delete()

    except Exception as e:
        await status_message.edit_text(f"❌ حدث خطأ أثناء المعالجة: {str(e)}")
        print(f"Error: {e}")

    finally:
        # الخطوة 6: حذف الملف من السيرفر فوراً لحفظ مساحة التخزين في Railway
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"🧹 تم تنظيف السيرفر وحذف: {file_path}")
            except Exception as delete_error:
                print(f"خطأ أثناء الحذف: {delete_error}")
        
        context.user_data.pop('current_url', None)

def main():
    if not TOKEN:
        print("❌ خطأ: لم يتم العثور على متغير البيئة TELEGRAM_TOKEN في Railway!")
        return

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("🚀 البوت يعمل الآن على Railway...")
    application.run_polling()

if __name__ == '__main__':
    main()
