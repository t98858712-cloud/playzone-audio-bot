import uuid
import logging
import datetime

logger = logging.getLogger("PlayZoneEnterpriseBot")

class PlayZoneException(Exception):
    """الاستثناء الرئيسي لكافة أخطاء المنظومة المؤسسية"""
    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.correlation_id = f"ERR-{uuid.uuid4().hex[:8].upper()}"
        self.timestamp = datetime.datetime.utcnow().isoformat()
        self.context = context or {}
        logger.error(f"[{self.correlation_id}] Exception Occurred: {message} | Context: {self.context}")

class DatabaseConnectionException(PlayZoneException): """فشل الاتصال بقاعدة البيانات السحابية"""
class MediaDownloadException(PlayZoneException): """انهيار محرك التنزيل الخارgi"""
class TelegramDeliveryException(PlayZoneException): """فشل تسليم الملف المعتمد لتليجرام"""
class ContentRestrictedException(PlayZoneException): """المحتوى محمي بحقوق ملكية أو خاص"""
