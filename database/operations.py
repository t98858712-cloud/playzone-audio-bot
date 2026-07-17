import time
import logging
import json
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_admin import firestore
from database.connection import db

logger = logging.getLogger("PlayZoneEnterpriseBot")

def load_banned_users():
    if db is None: return set()
    try:
        docs = db.collection('banned_users').stream()
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.clear()
        for doc in docs:
            try: BANNED_USERS_CACHE.add(int(doc.id))
            except ValueError: BANNED_USERS_CACHE.add(doc.id)
        return BANNED_USERS_CACHE
    except Exception as e:
        logger.error(f"Error loading banned users: {e}")
        return set()

def ban_user_db(uid):
    if db is None: return
    try:
        db.collection('banned_users').document(str(uid)).set({'banned_at': int(time.time())})
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.add(int(uid))
    except Exception: pass

def set_setting(key, value):
    if db is None: return
    try: db.collection('settings').document('config').set({key: str(value)}, merge=True)
    except Exception: pass

def get_setting(key, default="0"):
    if db is None: return default
    try:
        doc = db.collection('settings').document('config').get()
        if doc.exists: return doc.to_dict().get(key, default)
    except Exception: pass
    return default

def register_user_sync(user):
    if not user or db is None: return
    now = int(time.time())
    try:
        doc_ref = db.collection('users').document(str(user.id))
        if not doc_ref.get().exists:
            doc_ref.set({'id': user.id, 'username': user.username or "", 'first_name': user.first_name or "", 'last_name': user.last_name or "", 'first_seen': now, 'last_seen': now})
        else:
            doc_ref.update({'username': user.username or "", 'first_name': user.first_name or "", 'last_name': user.last_name or "", 'last_seen': now})
    except Exception: pass

def stat_inc_sync(key: str, value: int = 1):
    if db is None: return
    try: db.collection('settings').document('stats').update({key: Increment(value)})
    except Exception: pass

def load_stats_sync() -> dict:
    if db is None: return {}
    try: doc = db.collection('settings').document('stats').get(); return doc.to_dict() if doc.exists else {}
    except Exception: return {}

def all_user_ids() -> list:
    if db is None: return []
    try: return [int(doc.id) for doc in db.collection('users').select(['id']).stream()]
    except Exception: return []

def get_all_users_data() -> list:
    if db is None: return []
    try: return [doc.to_dict() for doc in db.collection('users').stream()]
    except Exception: return []

def get_active_users_48h() -> list:
    if db is None: return []
    threshold = int(time.time()) - (48 * 3600)
    try: return [int(doc.id) for doc in db.collection('users').where(filter=FieldFilter('last_seen', '>=', threshold)).select(['id']).stream()]
    except Exception: return []

def get_latest_users(limit: int = 10) -> list:
    if db is None: return []
    try: return [doc.to_dict() for doc in db.collection('users').order_by('last_seen', direction=firestore.Query.DESCENDING).limit(limit).stream()]
    except Exception: return []

def optimize_db(): pass

def verify_user_ad_completion(user_id: int):
    if db is None: return
    try: db.collection('users').document(str(user_id)).update({'last_ad_completion': int(time.time())})
    except Exception: pass

def check_ad_verified_status(user_id: int) -> bool:
    if db is None: return False
    try:
        doc = db.collection('users').document(str(user_id)).get()
        if doc.exists:
            if int(time.time()) - doc.to_dict().get('last_ad_completion', 0) < 5: return True
    except Exception: pass
    return False

def export_firebase_backup_json() -> str:
    if db is None: return ""
    try:
        backup = {"users": [], "banned_users": [], "settings": []}
        for doc in db.collection('users').stream(): backup["users"].append(doc.to_dict())
        for doc in db.collection('banned_users').stream(): backup["banned_users"].append({"id": doc.id, **(doc.to_dict() or {})})
        for doc in db.collection('settings').stream(): backup["settings"].append({"document_id": doc.id, **(doc.to_dict() or {})})
        return json.dumps(backup, indent=4, ensure_ascii=False)
    except Exception: return ""
