import subprocess
from site import app  # استيراد تطبيق الويب من ملف site.py الخاص بك

@app.on_event("startup")
async def launch_bot_background():
    # تشغيل البوت في عملية منفصلة بالخلفية فوراً عند انطلاق السيرفر
    subprocess.Popen(["python", "bot.py"])
