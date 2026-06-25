import instaloader
import requests
from instaloader import Profile

# هذه الرؤوس تحاكي متصفح حقيقي، وهو مطلوب لتجنب الحظر [citation:2]
HEADERS = {
    "X-IG-App-ID": "936619743392459",
    "X-ASBD-ID": "198387",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
}

def fetch_profile_safely(L: instaloader.Instaloader, username: str) -> instaloader.Profile:
    """
    محاولة جلب الملف الشخصي باستخدام الطريقة الأساسية أولاً.
    إذا فشلت، تستخدم واجهة web_profile_info كحل بديل (fallback) للحصول على البيانات. [citation:2]
    """
    try:
        # المحاولة الأولى: الطريقة الرسمية (التي قد تفشل حالياً)
        return Profile.from_username(L.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        # إذا فشلت، ننتقل إلى الحل البديل
        print(f"⚠️ فشلت الطريقة الأساسية، جاري استخدام web_profile_info لـ {username}...")
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        headers = {**HEADERS, "Referer": f"https://www.instagram.com/{username}/"}
        
        try:
            resp = L.context._session.get(url, headers=headers, timeout=L.context.request_timeout)
            if resp.status_code == 404:
                raise instaloader.exceptions.ProfileNotExistsException(f"الحساب {username} غير موجود.")
            resp.raise_for_status()
            
            user_data = (resp.json().get("data") or {}).get("user")
            if not user_data:
                raise instaloader.exceptions.ProfileNotExistsException(f"الحساب {username} غير موجود.")
            
            # إنشاء كائن Profile من البيانات المسترجعة
            return instaloader.Profile(L.context, user_data)
        except Exception as e:
            raise instaloader.exceptions.ProfileNotExistsException(f"فشل جلب الحساب {username}: {e}")