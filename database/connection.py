import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger("PlayZoneEnterpriseBot")

db = None
firebase_init_error = None

def init_db():
    global db, firebase_init_error
    try:
        firebase_json_str = os.getenv("FIREBASE_KEY_JSON")
        if not firebase_json_str:
            firebase_init_error = "متغير FIREBASE_KEY_JSON غير موجود في السيرفر."
            logger.error(f"❌ {firebase_init_error}")
            return
            
        firebase_json_str = firebase_json_str.strip()
        if firebase_json_str.startswith("'") and firebase_json_str.endswith("'"):
            firebase_json_str = firebase_json_str[1:-1].strip()
        elif firebase_json_str.startswith('"') and firebase_json_str.endswith('"'):
            firebase_json_str = firebase_json_str[1:-1].strip()
            
        cred_dict = json.loads(firebase_json_str, strict=False)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
            
        cred = credentials.Certificate(cred_dict)
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
            
        db = firestore.client()
        firebase_init_error = None
        logger.info("✅ تم الاتصال بقاعدة بيانات Firebase Firestore بنجاح.")
        
        stats_ref = db.collection('settings').document('stats')
        if not stats_ref.get().exists:
            stats_ref.set({k: 0 for k in ["requests", "success", "failed", "bytes", "broadcasts"]})
            
        config_ref = db.collection('settings').document('config')
        if not config_ref.get().exists:
            config_ref.set({'maintenance': '0'})
    except Exception as e:
        firebase_init_error = str(e)
        logger.error(f"❌ فشل تهيئة Firebase: {e}")

init_db()
