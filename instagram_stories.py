"""
وحدة تحميل قصص Instagram - نسخة محسنة مع دعم web_profile_info
"""

import os
import re
import time
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

import instaloader
from instaloader import Profile, Post, Story, Instaloader
import requests

logger = logging.getLogger("PlayZone.InstagramStories")

# ==========================================================
# رؤوس HTTP لتجنب الحظر
# ==========================================================

HEADERS = {
    "X-IG-App-ID": "936619743392459",
    "X-ASBD-ID": "198387",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
}

def fetch_profile_safely(loader: instaloader.Instaloader, username: str) -> instaloader.Profile:
    """
    جلب الملف الشخصي باستخدام الطريقة الأساسية أولاً.
    إذا فشلت، تستخدم web_profile_info كحل بديل.
    """
    try:
        # المحاولة الأولى: الطريقة الرسمية
        return Profile.from_username(loader.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        logger.warning(f"⚠️ فشلت الطريقة الأساسية، جاري استخدام web_profile_info لـ {username}...")
        
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        headers = {**HEADERS, "Referer": f"https://www.instagram.com/{username}/"}
        
        try:
            resp = loader.context._session.get(url, headers=headers, timeout=30)
            
            if resp.status_code == 404:
                raise instaloader.exceptions.ProfileNotExistsException(f"الحساب {username} غير موجود.")
            resp.raise_for_status()
            
            data = resp.json()
            user_data = data.get("data", {}).get("user")
            
            if not user_data:
                raise instaloader.exceptions.ProfileNotExistsException(f"الحساب {username} غير موجود.")
            
            # إنشاء كائن Profile من البيانات المسترجعة
            profile = instaloader.Profile(loader.context, user_data)
            logger.info(f"✅ تم جلب حساب {username} عبر web_profile_info")
            return profile
            
        except requests.exceptions.RequestException as e:
            logger.error(f"فشل طلب web_profile_info: {e}")
            raise instaloader.exceptions.ProfileNotExistsException(f"فشل جلب الحساب {username}: {e}")
        except Exception as e:
            logger.error(f"خطأ غير متوقع في web_profile_info: {e}")
            raise instaloader.exceptions.ProfileNotExistsException(f"فشل جلب الحساب {username}: {e}")

# ==========================================================
# مدير تحميل قصص Instagram
# ==========================================================

class InstagramStoryDownloader:
    """مدير تحميل قصص Instagram - نسخة محسنة"""
    
    def __init__(self, session_file: Optional[Path] = None):
        self.session_file = session_file or Path("./data/instagram_session")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        
        # تهيئة Instaloader بإعدادات محسنة
        self.loader = Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            compress_json=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            max_connection_attempts=5,
            request_timeout=30,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._login_status = False
        self._login_attempted = False
        
    def _ensure_login(self) -> bool:
        """تأكيد تسجيل الدخول مع دعم Session Cookie"""
        if self._login_status:
            return True
        if self._login_attempted:
            return False
        
        self._login_attempted = True
        
        # ==========================================================
        # الطريقة الأولى: تحميل Session Cookie من البيئة
        # ==========================================================
        session_cookie_str = os.getenv("INSTAGRAM_SESSION_COOKIE")
        if session_cookie_str:
            try:
                for cookie in session_cookie_str.split(';'):
                    cookie = cookie.strip()
                    if cookie:
                        name, value = cookie.split('=', 1)
                        self.loader.context._session.cookies.set(name, value)
                self._login_status = True
                logger.info("✅ تم تحميل Session Cookie من البيئة")
                return True
            except Exception as e:
                logger.warning(f"فشل تحميل Session Cookie: {e}")
        
        # ==========================================================
        # الطريقة الثانية: تحميل الجلسة المحفوظة
        # ==========================================================
        try:
            if self.session_file.exists():
                try:
                    self.loader.load_session_from_file(str(self.session_file))
                    self._login_status = True
                    logger.info("✅ تم تحميل جلسة Instagram من الملف")
                    return True
                except Exception as e:
                    logger.warning(f"فشل تحميل الجلسة: {e}")
                    try:
                        self.session_file.unlink()
                    except:
                        pass
        except Exception as e:
            logger.warning(f"خطأ في قراءة الجلسة: {e}")
        
        # ==========================================================
        # الطريقة الثالثة: تسجيل الدخول العادي
        # ==========================================================
        username = os.getenv("INSTAGRAM_USERNAME")
        password = os.getenv("INSTAGRAM_PASSWORD")
        
        if username and password:
            try:
                logger.info(f"🔄 محاولة تسجيل الدخول كـ {username}...")
                self.loader.login(username, password)
                
                try:
                    self.loader.save_session_to_file(str(self.session_file))
                except:
                    pass
                
                self._login_status = True
                logger.info(f"✅ تم تسجيل الدخول كـ {username}")
                return True
                
            except instaloader.exceptions.BadCredentialsException:
                logger.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
            except instaloader.exceptions.TwoFactorAuthRequiredException:
                logger.error("❌ مطلوب رمز المصادقة الثنائية (2FA)")
                two_factor_code = os.getenv("INSTAGRAM_2FA_CODE")
                if two_factor_code:
                    try:
                        self.loader.two_factor_login(two_factor_code)
                        self.loader.save_session_to_file(str(self.session_file))
                        self._login_status = True
                        logger.info("✅ تم تسجيل الدخول باستخدام 2FA")
                        return True
                    except Exception as e:
                        logger.error(f"فشل 2FA: {e}")
            except Exception as e:
                logger.error(f"فشل تسجيل الدخول: {e}")
        
        # ==========================================================
        # الطريقة الرابعة: محاولة بدون تسجيل الدخول
        # ==========================================================
        logger.warning("⚠️ سيتم المحاولة بدون تسجيل الدخول (قد لا تعمل الحسابات الخاصة)")
        return False
    
    def extract_username_from_url(self, url: str) -> Optional[str]:
        """استخراج اسم المستخدم من رابط Instagram"""
        patterns = [
            r'instagram\.com/stories/([^/?]+)',
            r'instagram\.com/([^/?]+)/stories',
            r'instagram\.com/([^/?]+)/story',
            r'instagr\.am/([^/?]+)',
            r'www\.instagram\.com/([^/?]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                username = match.group(1)
                if username not in ['p', 'reel', 'stories', 'explore', 'direct', 'highlights', 'tv']:
                    return username
        return None
    
    def is_stories_url(self, url: str) -> bool:
        """التحقق من رابط قصص Instagram"""
        patterns = [
            r'instagram\.com/stories/[^/]+',
            r'instagram\.com/[^/]+/stories',
            r'instagram\.com/[^/]+/story',
        ]
        return any(re.search(p, url) for p in patterns)
    
    def get_user_stories(self, username: str) -> List[Dict]:
        """جلب قصص المستخدم الحالية باستخدام طريقة آمنة"""
        stories = []
        
        # محاولة تسجيل الدخول
        logged_in = self._ensure_login()
        
        if not logged_in:
            logger.info(f"محاولة جلب قصص {username} بدون تسجيل الدخول")
        
        try:
            # استخدام الدالة الآمنة لجلب الملف الشخصي
            profile = fetch_profile_safely(self.loader, username)
            
        except instaloader.exceptions.ProfileNotExistsException as e:
            logger.warning(f"⚠️ {e}")
            return []
        except Exception as e:
            logger.error(f"خطأ غير متوقع في جلب الملف الشخصي: {e}")
            return []
        
        # جلب القصص
        try:
            for story in self.loader.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    story_data = self._extract_story_data(item, username)
                    if story_data:
                        stories.append(story_data)
            
            logger.info(f"✅ تم جلب {len(stories)} قصة من {username}")
            return stories
            
        except instaloader.exceptions.LoginRequiredException:
            logger.warning(f"⚠️ الحساب {username} خاص، يتطلب تسجيل الدخول")
            return []
        except Exception as e:
            logger.error(f"خطأ في جلب قصص {username}: {e}")
            return []
    
    def _extract_story_data(self, item, username: str) -> Optional[Dict]:
        """استخراج بيانات القصة"""
        try:
            is_video = item.is_video
            
            download_url = None
            if is_video:
                try:
                    download_url = item.video_url
                except:
                    pass
            else:
                try:
                    download_url = item.url
                except:
                    pass
            
            if not download_url:
                return None
            
            return {
                "id": item.mediaid,
                "username": username,
                "is_video": is_video,
                "timestamp": item.date_utc.timestamp() if hasattr(item, 'date_utc') else time.time(),
                "caption": getattr(item, 'caption', ''),
                "thumbnail": item.thumbnail_url if hasattr(item, 'thumbnail_url') else None,
                "download_url": download_url,
            }
        except Exception as e:
            logger.error(f"خطأ في استخراج بيانات القصة: {e}")
            return None
    
    def download_story(self, story_data: Dict, output_dir: Path) -> Optional[Path]:
        """تحميل قصة معينة"""
        try:
            download_url = story_data.get("download_url")
            if not download_url:
                return None
            
            import requests
            
            ext = ".mp4" if story_data.get("is_video") else ".jpg"
            filename = f"story_{story_data.get('id')}{ext}"
            output_path = output_dir / filename
            
            if output_path.exists():
                return output_path
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
                "Origin": "https://www.instagram.com",
            }
            
            if self._login_status:
                try:
                    cookies = self.loader.context.get_cookies()
                    if cookies:
                        headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                except:
                    pass
            
            response = requests.get(download_url, headers=headers, timeout=30, stream=True)
            response.raise_for_status()
            
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logger.info(f"✅ تم تحميل القصة: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"فشل تحميل القصة: {e}")
            return None