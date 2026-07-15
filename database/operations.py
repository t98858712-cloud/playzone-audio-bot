import logging
import time
import json
from database.connection import db
from core.security import BANNED_USERS_CACHE

logger = logging.getLogger("PlayZoneEnterpriseBot")

def register_user_sync(user):
    """تسجيل أو تحديث بيانات المستخدم في Firebase Firestore"""
    if db is None: return
    try:
        uid = str(user.id)
        user_ref = db.collection('users').document(uid)
        user_ref.set({
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "last_seen": int(time.time())
        }, merge=True)
    except Exception as e:
        logger.error(f"Error in register_user_sync: {e}")

def load_banned_users():
    """شحن قائمة المستخدمين المحظورين إلى الكاش فوراً عند الإقلاع"""
    if db is None: return
    try:
        BANNED_USERS_CACHE.clear()
        docs = db.collection('users').where('banned', '==', True).stream()
        for doc in docs:
            BANNED_USERS_CACHE.add(int(doc.id))
        logger.info(f"🔥 [Firebase] تم شحن كاش الحماية بنجاح بـ {len(BANNED_USERS_CACHE)} مستخدم محظور.")
    except Exception as e:
        logger.error(f"Error in load_banned_users: {e}")

def ban_user_db(uid: int):
    """حظر مستخدم في قاعدة البيانات السحابية"""
    if db is None: return
    try:
        db.collection('users').document(str(uid)).set({"banned": True}, merge=True)
    except Exception as e:
        logger.error(f"Error banning user {uid}: {e}")

def get_setting(key: str, default: str = "") -> str:
    """جلب قيمة إعداد معين من الـ Settings سحابياً"""
    if db is None: return default
    try:
        doc = db.collection('settings').document(key).get()
        if doc.exists:
            return str(doc.to_dict().get("value", default))
    except Exception:
        pass
    return default

def set_setting(key: str, value: str):
    """تحديث أو تفعيل قيمة إعداد في الـ Settings سحابياً"""
    if db is None: return
    try:
        db.collection('settings').document(key).set({"value": value}, merge=True)
    except Exception as e:
        logger.error(f"Error setting {key} to {value}: {e}")

def load_stats_sync() -> dict:
    """جلب إحصائيات البوت التراكمية"""
    if db is None: return {}
    try:
        doc = db.collection('stats').document('global').get()
        if doc.exists:
            return doc.to_dict()
    except Exception: pass
    return {}

def stat_inc_sync(key: str, value: int = 1):
    """زيادة العدادات الإحصائية سحابياً بالتزامن مع الحركات"""
    if db is None: return
    try:
        from google.cloud import firestore
        db.collection('stats').document('global').update({
            key: firestore.Increment(value)
        })
    except Exception:
        try:
            db.collection('stats').document('global').set({key: value}, merge=True)
        except Exception: pass

def all_user_ids() -> list:
    """جلب قائمة بكافة معرّفات المستخدمين المشتركين للراديو والإذاعة"""
    if db is None: return []
    try:
        users = db.collection('users').stream()
        return [int(u.id) for u in users]
    except Exception as e:
        logger.error(f"Error getting all user IDs: {e}")
        return []

def get_active_users_48h() -> list:
    """جلب المستخدمين المتفاعلين خلال آخر 48 ساعة فقط"""
    if db is None: return []
    try:
        cutoff = int(time.time()) - (48 * 3600)
        users = db.collection('users').where('last_seen', '>=', cutoff).stream()
        return [int(u.id) for u in users]
    except Exception:
        return all_user_ids()

def get_latest_users(limit: int = 10) -> list:
    """جلب قائمة بأحدث الأعضاء المنضمين للبوت لتفقد جودة الإقلاع"""
    if db is None: return []
    try:
        users = db.collection('users').order_by('last_seen', direction='DESCENDING').limit(limit).stream()
        return [u.to_dict() for u in users]
    except Exception:
        return []

def get_all_users_data() -> list:
    """جلب كافة بيانات الأعضاء لغرض التصدير لملفات الـ CSV"""
    if db is None: return []
    try:
        users = db.collection('users').stream()
        return [u.to_dict() for u in users]
    except Exception: return []

def check_ad_verified_status(uid: int) -> bool:
    """التحقق من حالة تخطي المستخدم للإعلانات حالياً"""
    if db is None: return True
    try:
        doc = db.collection('users').document(str(uid)).get()
        if doc.exists:
            data = doc.to_dict()
            expire = data.get("ad_expire", 0)
            if time.time() < expire:
                return True
    except Exception: pass
    return False

def verify_user_ad_completion(uid: int):
    """تفعيل تخطي الإعلانات للمستخدم بنجاح لمدة 24 ساعة"""
    if db is None: return
    try:
        expire_time = int(time.time()) + (24 * 3600)
        db.collection('users').document(str(uid)).set({"ad_expire": expire_time}, merge=True)
    except Exception as e:
        logger.error(f"Error verifying ad for user {uid}: {e}")

def export_firebase_backup_json() -> str:
    """سحب نسخة احتياطية سحابية كاملة بهيئة JSON"""
    if db is None: return ""
    try:
        backup = {"users": [], "settings": [], "stats": {}}
        for u in db.collection('users').stream():
            backup["users"].append(u.to_dict())
        for s in db.collection('settings').stream():
            backup["settings"].append({s.id: s.to_dict()})
        stats_doc = db.collection('stats').document('global').get()
        if stats_doc.exists:
            backup["stats"] = stats_doc.to_dict()
        return json.dumps(backup, ensure_ascii=False, indent=2)
    except Exception: return ""
