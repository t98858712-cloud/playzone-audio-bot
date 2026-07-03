# استخدام صورة سيرفر تيليجرام كأساس
FROM aiogram/telegram-bot-api:latest

USER root

# تثبيت بايثون وأدوات الميديا والتنزيل (مع أدوات البناء الأساسية)
RUN apk update && apk add --no-cache python3 py3-pip python3-dev ffmpeg aria2 bash gcc musl-dev

WORKDIR /app

# إنشاء المجلدات المطلوبة وتجهيز الصلاحيات
RUN mkdir -p /app/data /app/downloads && chmod -R 777 /app

# إنشاء بيئة بايثون افتراضية وتفعيلها
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# نسخ المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات البوت
COPY . .

# إنشاء سكربت تشغيل السيرفر والبوت معاً
RUN echo '#!/bin/bash' > start.sh && \
    echo 'telegram-bot-api --local --api-id=$TELEGRAM_API_ID --api-hash=$TELEGRAM_API_HASH --dir=/app/data --temp-dir=/app/data/temp &' >> start.sh && \
    echo 'sleep 5' >> start.sh && \
    echo 'python3 main.py' >> start.sh && \
    chmod +x start.sh

# إلغاء القفل الافتراضي للصورة وتشغيل السكربت الخاص بنا
ENTRYPOINT ["/bin/bash", "./start.sh"]
