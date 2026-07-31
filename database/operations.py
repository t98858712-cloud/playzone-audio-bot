import time
import logging
import json
import csv
import io
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from database.connection import db
from firebase_admin import firestore

logger = logging.getLogger("PlayZoneEnterpriseBot")

# ==================== إدارة الكاش والحظر اللحظي ====================

def load_banned_users():
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
        logger.info(f"🔥 [Firebase] تم شحن كاش الحظر بـ {len(BANNED_USERS_CACHE)} مستخدم.")
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
    except Exception as e:
        logger.error(f"Error banning user: {e}")

def unban_user_db(uid):
    if db is None: return
    try:
        db.collection('banned_users').document(str(uid)).delete()
        from core.security import BANNED_USERS_CACHE
        BANNED_USERS_CACHE.discard(int(uid))
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")

# ==================== الإعدادات الديناميكية بالسيرفر ====================

def set_setting(key, value):
    if db is None: return
    try:
        db.collection('settings').document('config').set({key: str(value)}, merge=True)
    except Exception as e:
        logger.error(f"Error setting config {key}: {e}")

def get_setting(key, default="0"):
    if db is None: return default
    try:
        doc = db.collection('settings').document('config').get()
        if doc.exists:
            return doc.to_dict().get(key, default)
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
    return default

# ==================== سجلات وسلوك المستخدمين ====================

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

def all_user_ids() -> list:
    if db is None: return []
    try:
        docs = db.collection('users').select(['id']).stream()
        return [int(doc.id) for doc in docs]
    except Exception as e:
        logger.error(f"Error getting all user ids: {e}")
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

def get_all_users_data() -> list:
    if db is None: return []
    try:
        docs = db.collection('users').stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"Error getting all users data: {e}")
        return []

# ==================== الإحصائيات الحية المباشرة ====================

def stat_inc_sync(key: str, value: int = 1):
    if db is None: return
    try:
        db.collection('settings').document('stats').set({key: Increment(value)}, merge=True)
    except Exception as e:
        logger.error(f"Error incrementing stat {key}: {e}")

def load_full_analytics_sync() -> dict:
    if db is None: return {}
    try:
        doc = db.collection('settings').document('stats').get()
        stats = doc.to_dict() if doc.exists else {}
        
        all_users = len(all_user_ids())
        active_48h = len(get_active_users_48h())
        
        bot_req = stats.get('requests', 0)
        bot_succ = stats.get('success', 0)
        bot_fail = stats.get('failed', 0)
        
        success_rate = (bot_succ / bot_req * 100) if bot_req > 0 else 100.0
        
        return {
            "total_users": all_users,
            "active_48h": active_48h,
            "bot_requests": bot_req,
            "bot_success": bot_succ,
            "bot_failed": bot_fail,
            "bot_success_rate": round(success_rate, 1),
            "bot_bytes": stats.get('bytes', 0),
            "web_requests": stats.get('web_requests', 0),
            "web_downloads": stats.get('web_downloads', 0),
            "adsterra_clicks": stats.get('adsterra_clicks', 0),
            "adsterra_verified": stats.get('adsterra_verified', 0),
            "broadcasts": stats.get('broadcasts', 0)
        }
    except Exception as e:
        logger.error(f"Error loading analytics: {e}")
        return {}

def verify_user_ad_completion(user_id: int):
    if db is None: return
    try:
        db.collection('users').document(str(user_id)).update({'last_ad_completion': int(time.time())})
        stat_inc_sync("adsterra_verified", 1)
    except Exception as e:
        logger.error(f"Error updating ad completion for {user_id}: {e}")

def check_ad_verified_status(user_id: int) -> bool:
    if db is None: return False
    try:
        doc = db.collection('users').document(str(user_id)).get()
        if doc.exists:
            last_ad = doc.to_dict().get('last_ad_completion', 0)
            if int(time.time()) - last_ad < 15:
                return True
    except Exception as e:
        logger.error(f"Error checking ad status for {user_id}: {e}")
    return False

def optimize_db():
    pass

# ==================== تصدير البيانات (3 صيغ) ====================

def export_users_csv() -> str:
    users = get_all_users_data()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Username", "First Name", "Last Name", "First Seen", "Last Seen"])
    for u in users:
        writer.writerow([
            u.get('id',''), 
            u.get('username',''), 
            u.get('first_name',''), 
            u.get('last_name',''), 
            u.get('first_seen',''), 
            u.get('last_seen','')
        ])
    return output.getvalue()

def export_firebase_backup_json() -> str:
    if db is None: return "{}"
    try:
        backup = {
            "metadata": {
                "exported_at": int(time.time()),
                "system": "PlayZone Enterprise DB"
            },
            "users": [], 
            "banned_users": [], 
            "settings": []
        }
        for doc in db.collection('users').stream():
            backup["users"].append(doc.to_dict())
        for doc in db.collection('banned_users').stream():
            backup["banned_users"].append({"id": doc.id, **(doc.to_dict() or {})})
        for doc in db.collection('settings').stream():
            backup["settings"].append({"document_id": doc.id, **(doc.to_dict() or {})})
        return json.dumps(backup, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to export JSON backup: {e}")
        return "{}"

def generate_analytics_txt_report() -> str:
    from utils.helpers import format_size
    analytics = load_full_analytics_sync()
    bot_bytes = format_size(analytics.get('bot_bytes', 0))
    now_str = time.strftime('%Y-%m-%d %H:%M:%S GMT')
    
    return f"""================================================================
            PLAYZONE ENTERPRISE - EXECUTIVE ANALYTICS REPORT
================================================================
Generated Date : {now_str}
Database Engine: Firebase Firestore
Monetization   : Adsterra Exclusive Network
================================================================

1. USER METRICS & RETENTION:
----------------------------------------------------------------
• Total Registered Base  : {analytics.get('total_users', 0)} users
• Active Users (48 Hours): {analytics.get('active_48h', 0)} users

2. TELEGRAM BOT ENGINE PERFORMANCE:
----------------------------------------------------------------
• Total Media Requests   : {analytics.get('bot_requests', 0)}
• Successful Downloads   : {analytics.get('bot_success', 0)}
• Failed / Error Requests: {analytics.get('bot_failed', 0)}
• Request Success Rate   : {analytics.get('bot_success_rate', 100.0)}%
• Bandwidth Delivered    : {bot_bytes}
• Broadcast Campaigns    : {analytics.get('broadcasts', 0)}

3. WEB APP (MINI APP & WEB) PERFORMANCE:
----------------------------------------------------------------
• Web Searches & Visits  : {analytics.get('web_requests', 0)}
• Direct Web Downloads   : {analytics.get('web_downloads', 0)}

4. ADSTERRA MONETIZATION & CONVERSION:
----------------------------------------------------------------
• Total Ad Link Sessions : {analytics.get('adsterra_clicks', 0)}
• Verified Ad Unlocks    : {analytics.get('adsterra_verified', 0)}

================================================================
                   END OF DIAGNOSTIC REPORT
================================================================
"""
