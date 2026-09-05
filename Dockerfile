# استخدام بيئة بايثون رسمية وخفيفة
FROM python:3.13-slim

# منع بايثون من كتابة ملفات مؤقتة وتفعيل إخراج السجلات مباشرة
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# تحديث النظام وتثبيت FFmpeg والأدوات المساعدة بشكل إجباري
RUN apt-get update && \
    apt-get install -y ffmpeg aria2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# تحديد مسار العمل داخل السيرفر
WORKDIR /app

# نسخ ملف المتطلبات وتثبيت مكتبات البايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات البوت (الكود، الكوكيز، إلخ)
COPY . .

# أمر تشغيل البوت والموقع معاً
CMD sh -c "PORT=9999 python main.py & uvicorn web_app:app --host 0.0.0.0 --port ${PORT:-8080}"
