import os
import asyncio
import shutil
import time
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# التوكن من Railway Variables
TOKEN = os.getenv("TELEGRAM_TOKEN")

BASE_DOWNLOAD_DIR = Path("./downloads")
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

# حد تيليجرام للبوتات العادية 50MB
MAX_TELEGRAM_SIZE = 50 * 1024 * 1024
COOKIES_FILE = "cookies.txt"  # اسم ملف الكوكيز


def make_job_dir(user_id: int) -> Path:
    job_dir = BASE_DOWNLOAD_DIR / f"{user_id}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def clean_job_dir(job_dir: Path):
    try:
        if job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception as e:
        print(f"خطأ أثناء تنظيف الملفات: {e}")


def find_downloaded_file(job_dir: Path):
    files = [p for p in job_dir.iterdir() if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def short_error(e: Exception) -> str:
    msg = str(e)
    if len(msg) > 900:
        msg = msg[:900] + "..."
    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً بك في بوت التحميل.\n\n"
        "أرسل رابط يوتيوب، ثم اختر الصيغة المطلوبة.\n\n"
        "⚠️ استخدم البوت فقط مع المحتوى الذي تملك حق تحميله أو استخدامه."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ هذا لا يبدو كرابط يوتيوب صحيح.")
        return

    context.user_data["current_url"] = url

    keyboard = [
        [
            InlineKeyboardButton("🎵 صوت MP3", callback_data="mp3"),
            InlineKeyboardButton("🎬 فيديو MP4", callback_data="mp4"),
        ]
    ]

    await update.message.reply_text(
        "اختر الصيغة التي تريد تحميلها:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data
    url = context.user_data.get("current_url")

    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الطلب. أرسل الرابط مرة أخرى.")
        return

    user_id = query.from_user.id
    job_dir = make_job_dir(user_id)

    status_message = await query.edit_message_text("⏳ جاري تجهيز الطلب...")

    out_tmpl = str(job_dir / "%(title).80s [%(id)s].%(ext)s")

    # إعدادات مشتركة للتمويه وتخطي حظر يوتيوب
    base_ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],  # التمويه كإصدار أندرويد لتفادي الحظر
                "skip": ["webpage"]
            }
        }
    }

    # التحقق من وجود ملف الكوكيز وتطبيقه تلقائياً
    if os.path.exists(COOKIES_FILE):
        base_ydl_opts["cookiefile"] = COOKIES_FILE
        print("ℹ️ تم العثور على ملف الكوكيز وتطبيقه بنجاح.")

    if choice == "mp3":
        ydl_opts = {
            **base_ydl_opts,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }
            ],
        }
    else:
        ydl_opts = {
            **base_ydl_opts,
            "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
            "merge_output_format": "mp4",
        }

    loop = asyncio.get_running_loop()

    try:
        await status_message.edit_text("📥 جاري التحميل والمعالجة...")

        def download_file():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await loop.run_in_executor(None, download_file)

        title = info.get("title", "ملف")
        file_path = find_downloaded_file(job_dir)

        if not file_path or not file_path.exists():
            await status_message.edit_text("❌ لم يتم العثور على الملف بعد التحميل.")
            return

        file_size = file_path.stat().st_size

        if file_size > MAX_TELEGRAM_SIZE:
            size_mb = round(file_size / 1024 / 1024, 2)
            await status_message.edit_text(
                f"❌ حجم الملف {size_mb}MB وهذا أكبر من حد تيليجرام للبوتات العادية 50MB.\n\n"
                "جرّب فيديو أقصر أو جودة أقل."
            )
            return

        await status_message.edit_text("📤 جاري رفع الملف إلى تيليجرام...")

        with open(file_path, "rb") as f:
            if choice == "mp3":
                await query.message.reply_audio(
                    audio=f,
                    title=title,
                    caption="✅ تم تحميل الصوت بنجاح",
                )
            else:
                await query.message.reply_video(
                    video=f,
                    caption=f"✅ {title}",
                )

        await status_message.delete()

    except Exception as e:
        await status_message.edit_text(
            f"❌ حدث خطأ أثناء المعالجة:\n{short_error(e)}"
        )
        print(f"Error: {e}")

    finally:
        clean_job_dir(job_dir)
        context.user_data.pop("current_url", None)


def main():
    if not TOKEN:
        print("❌ خطأ: لم يتم العثور على TELEGRAM_TOKEN في Railway Variables")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))

    print("🚀 البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
