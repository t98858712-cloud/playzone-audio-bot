import time
import logging
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from database.connection import db
from firebase_admin import firestore

logger = logging.getLogger("PlayZoneEnterpriseBot")

# 🌟 دالة فارغة مضافة لحل مشكلة الاستيراد في admin.py وأي ملفات أخرى دون التأثير على النظام
def load_settings_to_cache():
    pass

def load_banned_users():
    """شحن كاش الـ RAM تلقائياً من فايربيس عند تشغيل البوت لمنع ضياع البيانات""[span_4](start_span)"[span_4](end_span)
    if db is None: return set()[span_5](start_span)[span_5](end_span)
    try:
        docs = db.collection('banned_users').stream()[span_6](start_span)[span_6](end_span)
        from core.security import BANNED_USERS_CACHE[span_7](start_span)[span_7](end_span)
        BANNED_USERS_CACHE.clear()[span_8](start_span)[span_8](end_span)
        
        for doc in docs:[span_9](start_span)[span_9](end_span)
            try:
                BANNED_USERS_CACHE.add(int(doc.id))[span_10](start_span)[span_10](end_span)
            except ValueError:
                BANNED_USERS_CACHE.add(doc.id)[span_11](start_span)[span_11](end_span)
                
        logger.info(f"🔥 [Firebase] تم شحن كاش الحماية بنجاح بـ {len(BANNED_USERS_CACHE)} مستخدم محظور.")[span_12](start_span)[span_12](end_span)
        return BANNED_USERS_CACHE[span_13](start_span)[span_13](end_span)
    except Exception as e:
        logger.error(f"Error loading banned users: {e}")[span_14](start_span)[span_14](end_span)
        return set()[span_15](start_span)[span_15](end_span)

def ban_user_db(uid):
    """حظر المستخدم في قاعدة البيانات وإضافته فوراً للكاش اللحظي""[span_16](start_span)"[span_16](end_span)
    if db is None: return[span_17](start_span)[span_17](end_span)
    try:
        # 1. الحفظ في فايربيس
        db.collection('banned_users').document(str(uid)).set({'banned_at': int(time.time())})[span_18](start_span)[span_18](end_span)
        
        # 2. المزامنة الفورية مع كاش الـ RAM لمنع السبام فوراً
        from core.security import BANNED_USERS_CACHE[span_19](start_span)[span_19](end_span)
        BANNED_USERS_CACHE.add(int(uid))[span_20](start_span)[span_20](end_span)
        logger.info(f"🚫 تم حظر المستخدم {uid} ومزامنته مع فايربيس والكاش.")[span_21](start_span)[span_21](end_span)
    except Exception as e:
        logger.error(f"Error banning user: {e}")[span_22](start_span)[span_22](end_span)

def unban_user_db(uid):
    """إلغاء حظر المستخدم من قاعدة البيانات وإزالته من كاش الحماية ليعود للعمل""[span_23](start_span)"[span_23](end_span)
    if db is None: return[span_24](start_span)[span_24](end_span)
    try:
        # 1. الحذف من فايربيس
        db.collection('banned_users').document(str(uid)).delete()[span_25](start_span)[span_25](end_span)
        
        # 2. الحذف من كاش الـ RAM
        from core.security import BANNED_USERS_CACHE[span_26](start_span)[span_26](end_span)
        BANNED_USERS_CACHE.discard(int(uid))[span_27](start_span)[span_27](end_span)
        logger.info(f"🟢 تم فك حظر المستخدم {uid} ومزامنته مع فايربيس والكاش.")[span_28](start_span)[span_28](end_span)
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")[span_29](start_span)[span_29](end_span)

def set_setting(key, value):
    if db is None: return[span_30](start_span)[span_30](end_span)
    try:
        db.collection('settings').document('config').set({key: str(value)}, merge=True)[span_31](start_span)[span_31](end_span)
    except Exception as e:
        logger.error(f"Error setting config: {e}")[span_32](start_span)[span_32](end_span)

def get_setting(key, default="0"):
    if db is None: return default[span_33](start_span)[span_33](end_span)
    try:
        doc = db.collection('settings').document('config').get()[span_34](start_span)[span_34](end_span)
        if doc.exists:[span_35](start_span)[span_35](end_span)
            return doc.to_dict().get(key, default)[span_36](start_span)[span_36](end_span)
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")[span_37](start_span)[span_37](end_span)
    return default[span_38](start_span)[span_38](end_span)

def register_user_sync(user):
    if not user or db is None: return[span_39](start_span)[span_39](end_span)
    now = int(time.time())[span_40](start_span)[span_40](end_span)
    try:
        doc_ref = db.collection('users').document(str(user.id))[span_41](start_span)[span_41](end_span)
        doc = doc_ref.get()[span_42](start_span)[span_42](end_span)
        if not doc.exists:[span_43](start_span)[span_43](end_span)
            doc_ref.set({
                'id': user.id,
                'username': user.username or "",
                'first_name': user.first_name or "",
                'last_name': user.last_name or "",
                'first_seen': now,
                'last_seen': now
            })[span_44](start_span)[span_44](end_span)
        else:
            doc_ref.update({
                'username': user.username or "",
                'first_name': user.first_name or "",
                'last_name': user.last_name or "",
                'last_seen': now
            })[span_45](start_span)[span_45](end_span)
    except Exception as e:
        logger.error(f"Error registering user {user.id}: {e}")[span_46](start_span)[span_46](end_span)

def stat_inc_sync(key: str, value: int = 1):
    if db is None: return[span_47](start_span)[span_47](end_span)
    try:
        db.collection('settings').document('stats').update({key: Increment(value)})[span_48](start_span)[span_48](end_span)
    except Exception as e:
        logger.error(f"Error incrementing stat {key}: {e}")[span_49](start_span)[span_49](end_span)

def load_stats_sync() -> dict:
    if db is None: return {}[span_50](start_span)[span_50](end_span)
    try:
        doc = db.collection('settings').document('stats').get()[span_51](start_span)[span_51](end_span)
        return doc.to_dict() if doc.exists else {}[span_52](start_span)[span_52](end_span)
    except Exception as e:
        logger.error(f"Error loading stats: {e}")[span_53](start_span)[span_53](end_span)
        return {}[span_54](start_span)[span_54](end_span)

def all_user_ids() -> list:
    if db is None: return [][span_55](start_span)[span_55](end_span)
    try:
        docs = db.collection('users').select(['id']).stream()[span_56](start_span)[span_56](end_span)
        return [int(doc.id) for doc in docs][span_57](start_span)[span_57](end_span)
    except Exception as e:
        logger.error(f"Error getting all user ids: {e}")[span_58](start_span)[span_58](end_span)
        return [][span_59](start_span)[span_59](end_span)

def get_all_users_data() -> list:
    if db is None: return [][span_60](start_span)[span_60](end_span)
    try:
        docs = db.collection('users').stream()[span_61](start_span)[span_61](end_span)
        return [doc.to_dict() for doc in docs][span_62](start_span)[span_62](end_span)
    except Exception as e:
        logger.error(f"Error getting all users data: {e}")[span_63](start_span)[span_63](end_span)
        return [][span_64](start_span)[span_64](end_span)

def get_active_users_48h() -> list:
    if db is None: return [][span_65](start_span)[span_65](end_span)
    threshold = int(time.time()) - (48 * 3600)[span_66](start_span)[span_66](end_span)
    try:
        docs = db.collection('users').where(filter=FieldFilter('last_seen', '>=', threshold)).select(['id']).stream()[span_67](start_span)[span_67](end_span)
        return [int(doc.id) for doc in docs][span_68](start_span)[span_68](end_span)
    except Exception as e:
        logger.error(f"Error getting active users: {e}")[span_69](start_span)[span_69](end_span)
        return [][span_70](start_span)[span_70](end_span)

def get_latest_users(limit: int = 10) -> list:
    if db is None: return [][span_71](start_span)[span_71](end_span)
    try:
        docs = db.collection('users').order_by('last_seen', direction=firestore.Query.DESCENDING).limit(limit).stream()[span_72](start_span)[span_72](end_span)
        return [doc.to_dict() for doc in docs][span_73](start_span)[span_73](end_span)
    except Exception as e:
        logger.error(f"Error getting latest users: {e}")[span_74](start_span)[span_74](end_span)
        return [][span_75](start_span)[span_75](end_span)

def optimize_db():
    """
    قاعدة بيانات Firebase السحابية لا تتطلب عملية ضغط (Vacuum) كالسابق،
    لذا تبقى هذه الدالة لتلبية طلب زر (تحسين الـ Database) في لوحة التحكم دون التسبب بأخطاء.
    ""[span_76](start_span)"[span_76](end_span)
    pass[span_77](start_span)[span_77](end_span)

def verify_user_ad_completion(user_id: int):
    """تسجيل وقت اكتمال مشاهدة الإعلان للمخدم بداخل الفايرستور للتأكيد""[span_78](start_span)"[span_78](end_span)
    if db is None: return[span_79](start_span)[span_79](end_span)
    try:
        db.collection('users').document(str(user_id)).update({
            'last_ad_completion': int(time.time())
        })[span_80](start_span)[span_80](end_span)
        logger.info(f"💰 Recorded ad completion for user {user_id}")[span_81](start_span)[span_81](end_span)
    except Exception as e:
        logger.error(f"Error updating ad completion for {user_id}: {e}")[span_82](start_span)[span_82](end_span)

def check_ad_verified_status(user_id: int) -> bool:
    """التحقق هل أكمل المستخدم الإعلان خلال آخر 10 دقائق لتخطي حجب التنزيل""[span_83](start_span)"[span_83](end_span)
    if db is None: return False[span_84](start_span)[span_84](end_span)
    try:
        doc = db.collection('users').document(str(user_id)).get()[span_85](start_span)[span_85](end_span)
        if doc.exists:[span_86](start_span)[span_86](end_span)
            data = doc.to_dict()[span_87](start_span)[span_87](end_span)
            last_ad = data.get('last_ad_completion', 0)[span_88](start_span)[span_88](end_span)
            if int(time.time()) - last_ad < 600:  # تم ضبطها هندسياً لـ 10 دقائق (600 ثانية) لتصبح عملية ومنطقية للمستخدم[span_89](start_span)[span_89](end_span)
                return True[span_90](start_span)[span_90](end_span)
    except Exception as e:
        logger.error(f"Error checking ad status for {user_id}: {e}")[span_91](start_span)[span_91](end_span)
    return False[span_92](start_span)[span_92](end_span)
