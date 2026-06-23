import telebot
import instaloader
import os
import shutil
import glob

# ضع توكن البوت الخاص بك هنا
TOKEN = '8733816410:AAGg7di4Ddyj_kTN0FtWfWBMKwdoHrRgE7M'
bot = telebot.TeleBot(TOKEN)

# إعداد Instaloader
L = instaloader.Instaloader(
    download_pictures=True,
    download_videos=True,
    download_video_thumbnails=False,
    save_metadata=False,
    post_metadata_txt_pattern='',
    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
)

# --- قسم المعالجة الذاتية للجلسة ---
current_dir = os.path.dirname(os.path.abspath(__file__))
session_path = os.path.join(current_dir, "session-panther")

username_insta = "panther.6059084"
password_insta = "Hh112233hh"

# التحقق مما إذا كان ملف الجلسة موجوداً، وإذا لم يكن موجوداً، قم بإنشائه
if not os.path.exists(session_path):
    try:
        print("⏳ ملف الجلسة غير موجود. جاري إنشاء جلسة جديدة...")
        L.login(username_insta, password_insta)
        L.save_session_to_file(session_path)
        print("✅ تم إنشاء ملف الجلسة بنجاح!")
    except Exception as e:
        print(f"⚠️ فشل في تسجيل الدخول وإنشاء الجلسة: {e}")
else:
    try:
        print("⏳ جاري تحميل ملف الجلسة الموجود...")
        L.load_session_from_file(username_insta, session_path)
        print("✅ تم تحميل الجلسة بنجاح!")
    except Exception as e:
         print(f"⚠️ فشل في تحميل الجلسة: {e}")
# ------------------------------------

# ==============================================================
# --- من هنا يبدأ الكود الأصلي الخاص بك دون تغيير أي حرف ---
# ==============================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "مرحباً بك! 🕵️‍♂️\n"
        "أرسل لي (يوزر نيم) أي حساب إنستقرام عام (Public)،\n"
        "وسأقوم بجلب الستوريات الحالية لك بدون أن يعلم صاحبها."
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True)
def fetch_stories(message):
    username = message.text.strip().replace("@", "")
    
    # رسالة انتظار
    wait_msg = bot.reply_to(message, f"⏳ جاري البحث عن ستوريات الحساب: {username} ...")
    
    try:
        # جلب بيانات الحساب
        profile = instaloader.Profile.from_username(L.context, username)
        
        if profile.is_private:
            bot.edit_message_text("🔒 عذراً، هذا الحساب خاص (Private). لا يمكنني جلب الستوريات.", chat_id=message.chat.id, message_id=wait_msg.message_id)
            return

        if not profile.has_public_story:
            bot.edit_message_text("📭 هذا الحساب ليس لديه أي ستوريات حالياً أو مرت عليها 24 ساعة.", chat_id=message.chat.id, message_id=wait_msg.message_id)
            return

        bot.edit_message_text("📥 جاري التحميل وإرسال الستوريات...", chat_id=message.chat.id, message_id=wait_msg.message_id)
        
        # مجلد مؤقت لحفظ الستوريات
        target_dir = f"stories_{username}"
        
        # جلب الستوريات
        for story in L.get_stories([profile.userid]):
            for item in story.get_items():
                L.download_storyitem(item, target_dir)
        
        # إرسال الملفات المحملة إلى تليجرام
        if os.path.exists(target_dir):
            files = glob.glob(f"{target_dir}/*")
            media_found = False
            
            for file in files:
                if file.endswith(".jpg"):
                    with open(file, 'rb') as f:
                        bot.send_photo(message.chat.id, f)
                    media_found = True
                elif file.endswith(".mp4"):
                    with open(file, 'rb') as f:
                        bot.send_video(message.chat.id, f)
                    media_found = True
            
            if not media_found:
                bot.send_message(message.chat.id, "⚠️ تم العثور على ستوريات ولكن حدث خطأ في معالجتها.")
                
            # حذف المجلد بعد الانتهاء لتوفير المساحة
            shutil.rmtree(target_dir)
            bot.send_message(message.chat.id, "✅ انتهى العرض!")
        
    except instaloader.exceptions.ProfileNotExistsException:
        bot.edit_message_text("❌ الحساب غير موجود، تأكد من كتابة اليوزر نيم بشكل صحيح.", chat_id=message.chat.id, message_id=wait_msg.message_id)
    except Exception as e:
        # في حالة الحظر المؤقت من إنستقرام أو أخطاء أخرى
        bot.edit_message_text(f"⚠️ حدث خطأ أثناء جلب البيانات. قد يكون بسبب سياسات إنستقرام.", chat_id=message.chat.id, message_id=wait_msg.message_id)
        print(f"Error: {e}")

# تشغيل البوت بشكل دائم
print("Bot is running...")
bot.infinity_polling()
