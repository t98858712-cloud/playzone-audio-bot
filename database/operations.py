import time
import logging
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from database.connection import db
from firebase_admin import firestore

logger = logging.getLogger("PlayZoneEnterpriseBot")

# كاش الإعدادات اللحظي لضمان سرعة استجابة فائقة (0ms)[span_5](start_span)[span_5](end_span)
SETTINGS_CACHE = {}

def load_settings_to_cache():
    """شحن إعدادات البوت بالكامل في كاش الـ RAM عند الإقلاع لتجنب قراءة Firebase المتكررة""[span_6](start_span)"[span_6](end_span)
    if db is None: return
    try:
        doc = db.collection('settings').document('config').get()
        if doc.exists:
            data = doc.to_dict()
            for k, v in data.items():
                SETTINGS_CACHE[k] = str(v)
            logger.info(f"⚡ [Firebase] تم شحن {len(SETTINGS_CACHE)} إعداد بنجاح في كاش الـ RAM اللحظي.")
    except Exception as e:
        logger.error(f"Error loading settings to cache: {e}")

def load_banned_users():
    """شحن كاش الـ RAM تلقائياً من فايربيس عند تشغيل البوت لمنع ضياع البيانات""[span_7](start_span)"[span_7](end_span)
    if db is None: return set()
    try:
        docs = db.collection('banned_users').stream()
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.clear()
        
        for doc in docs:
            try:
                BANNED_USERS_CACHE.add(int(doc.id))
            except ValueError:
                BANNED_USERS_CACHE.add(doc.id)
                
        logger.info(f"🔥 [Firebase] تم شحن كاش الحماية بنجاح بـ {len(BANNED_USERS_CACHE)} مستخدم محظور.")
        return BANNED_USERS_CACHE
    except Exception as e:
        logger.error(f"Error loading banned users: {e}")
        return set()

def ban_user_db(uid):
    """حظر المستخدم في قاعدة البيانات وإضافته فوراً للكاش اللحظي""[span_8](start_span)"[span_8](end_span)
    if db is None: return
    try:
        db.collection('banned_users').document(str(uid)).set({'banned_at': int(time.time())})
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.add(int(uid))
        logger.info(f"🚫 تم حظر المستخدم {uid} ومزامنته مع فايربيس والكاش.")
    except Exception as e:
        logger.error(f"Error banning user: {e}")

def unban_user_db(uid):
    """إلغاء حظر المستخدم من قاعدة البيانات وإزالته من كاش الحماية ليعود للعمل""[span_9](start_span)"[span_9](end_span)
    if db is None: return
    try:
        db.collection('banned_users').document(str(uid)).delete()
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.discard(int(uid))
        logger.info(f"🟢 تم فك حظر المستخدم {uid} ومزامنته مع فايربيس والكاش.")
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")

def set_setting(key, value):
    if db is None: return
    try:
        db.collection('settings').document('config').set({key: str(value)}, merge=True)
        # تحديث الكاش اللحظي فوراً لضمان التطابق الفوري للتغييرات[span_10](start_span)[span_10](end_span)
        SETTINGS_CACHE[key] = str(value)
    except Exception as e:
        logger.error(f"Error setting config: {e}")

def get_setting(key, default="0"):
    # قراءة فائقة السرعة من كاش الـ RAM مباشرة دون الاتصال بـ Firebase في كل رسالة[span_11](start_span)[span_11](end_span)
    return SETTINGS_CACHE.get(key, default)

def register_user_sync(user):
    if not user or db is None: return
    now = int(time.time())
    try:
        doc_ref = db.collection('users').document(str(user.id))
        doc = doc_ref.get()
        if not doc.exists:
            doc_ref.set({
                'id': user.id,
                'username': user.username or "",
                'first_name': user.first_name or "",
                'last_name': user.last_name or "",
                'first_seen': now,
                'last_seen': now
            })
        else:
            doc_ref.update({
                'username': user.username or "",
                'first_name': user.first_name or "",
                'last_name': user.last_name or "",
                'last_seen': now
            })
    except Exception as e:
        logger.error(f"Error registering user {user.id}: {e}")

def stat_inc_sync(key: str, value: int = 1):
    if db is None: return
    try:
        db.collection('settings').document('stats').update({key: Increment(value)})
    except Exception as e:
        logger.error(f"Error incrementing stat {key}: {e}")

def load_stats_sync() -> dict:
    if db is None: return {}
    try:
        doc = db.collection('settings').document('stats').get()
        return doc.to_dict() if doc.exists else {}
    except Exception as e:
        logger.error(f"Error loading stats: {e}")
        return {}

def all_user_ids() -> list:
    if db is None: return []
    try:
        docs = db.collection('users').select(['id']).stream()
        return [int(doc.id) for doc in docs]
    except Exception as e:
        logger.error(f"Error getting all user ids: {e}")
        return []

def get_all_users_data() -> list:
    if db is None: return []
    try:
        docs = db.collection('users').stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"Error getting all users data: {e}")
        return []

def get_active_users_48h() -> list:
    if db is None: return []
    threshold = int(time.time()) - (48 * 3600)
    try:
        docs = db.collection('users').where(filter=FieldFilter('last_seen', '>=', threshold)).select(['id']).stream()
        return [int(doc.id) for doc in docs]
    except Exception as e:
        logger.error(f"Error getting active users: {e}")
        return []

def get_latest_users(limit: int = 10) -> list:
    if db is None: return []
    try:
        docs = db.collection('users').order_by('last_seen', direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"Error getting latest users: {e}")
        return []

def optimize_db():
    pass

def verify_user_ad_completion(user_id: int):
    """تسجيل وقت اكتمال مشاهدة الإعلان للمخدم بداخل الفايرستور للتأكيد""[span_12](start_span)"[span_12](end_span)
    if db is None: return
    try:
        db.collection('users').document(str(user_id)).update({
            'last_ad_completion': int(time.time())
        })
        logger.info(f"💰 Recorded ad completion for user {user_id}")
    except Exception as e:
        logger.error(f"Error updating ad completion for {user_id}: {e}")

def check_ad_verified_status(user_id: int) -> bool:
    """التحقق هل أكمل المستخدم الإعلان خلال آخر 10 دقائق لتخطي حجب التنزيل""[span_13](start_span)"[span_13](end_span)
    if db is None: return False
    try:
        doc = db.collection('users').document(str(user_id)).get()
        if doc.exists:
            data = doc.to_dict()
            last_ad = data.get('last_ad_completion', 0)
            if int(time.time()) - last_ad < 600:
                return True
    except Exception as e:
        logger.error(f"Error checking ad status for {user_id}: {e}")
    return False
