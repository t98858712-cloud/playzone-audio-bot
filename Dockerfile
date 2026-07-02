# استخدام صورة سيرفر تيليجرام كأساس
FROM aiogram/telegram-bot-api:latest

USER root

# تثبيت بايثون وأدوات الميديا (FFmpeg)
RUN apk update && apk add --no-cache python3 py3-pip python3-dev ffmpeg aria2 bash

WORKDIR /app

# إنشاء بيئة بايثون افتراضية وتفعيلها
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# نسخ المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات البوت
COPY . .

# إنشاء سكربت لتشغيل السيرفر والبوت معاً في نفس الوقت
RUN echo '#!/bin/bash' > start.sh && \
    echo 'telegram-bot-api --local --api-id=$TELEGRAM_API_ID --api-hash=$TELEGRAM_API_HASH -d /app/data &' >> start.sh && \
    echo 'sleep 3' >> start.sh && \
    echo 'python3 main.py' >> start.sh && \
    chmod +x start.sh

# التشغيل المزدوج
CMD ["./start.sh"]
