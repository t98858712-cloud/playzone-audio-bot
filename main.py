import subprocess
from server import app  # استدعاء آمن ونظيف تماماً من ملفك بعد تغيير اسمه

@app.on_event("startup")
async def launch_bot_background():
    # تشغيل ملف البوت في عملية منفصلة بالخلفية فور انطلاق خادم الويب
    subprocess.Popen(["python", "bot.py"])
