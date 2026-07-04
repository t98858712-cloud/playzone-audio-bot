import time
import logging
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from database.connection import db
from firebase_admin import firestore

logger = logging.getLogger("PlayZoneEnterpriseBot")

def load_banned_users():
    if db is None: return set()
    try:
        docs = db.collection('banned_users').stream()
        return {int(doc.id) for doc in docs}
    except Exception as e:
        logger.error(f"Error loading banned users: {e}")
        return set()

def ban_user_db(uid):
    if db is None: return
    try:
        db.collection('banned_users').document(str(uid)).set({'banned_at': int(time.time())})
    except Exception as e:
        logger.error(f"Error banning user: {e}")

def set_setting(key, value):
    if db is None: return
    try:
        db.collection('settings').document('config').set({key: str(value)}, merge=True)
    except Exception as e:
        logger.error(f"Error setting config: {e}")

def get_setting(key, default="0"):
    if db is None: return default
    try:
        doc = db.collection('settings').document('config').get()
        if doc.exists:
            return doc.to_dict().get(key, default)
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
    return default

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
