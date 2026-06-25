"""
وحدة تحميل قصص Instagram - نسخة محسنة
تستخدم طرق متعددة للوصول إلى القصص
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

logger = logging.getLogger("PlayZone.InstagramStories")

# ==========================================================
# إعدادات Instaloader - نسخة محسنة
# ==========================================================

class InstagramStoryDownloader:
    """مدير تحميل قصص Instagram - مع دعم متعدد"""
    
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
            # إضافة وكيل متصفح لتجنب الحظر
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._login_status = False
        self._login_attempted = False
        
    def _ensure_login(self) -> bool:
        """تأكيد تسجيل الدخول مع محاولات متعددة"""
        if self._login_status:
            return True
        if self._login_attempted:
            return False
        
        self._login_attempted = True
        
        # محاولة تحميل الجلسة المحفوظة أولاً
        try:
            if self.session_file.exists():
                try:
                    self.loader.load_session_from_file(str(self.session_file))
                    self._login_status = True
                    logger.info("✅ تم تحميل جلسة Instagram من الملف")
                    return True
                except Exception as e:
                    logger.warning(f"فشل تحميل الجلسة المحفوظة: {e}")
                    # حذف الملف التالف
                    try:
                        self.session_file.unlink()
                    except:
                        pass
        except Exception as e:
            logger.warning(f"خطأ في قراءة الجلسة: {e}")
        
        # محاولة تسجيل الدخول باستخدام بيانات من المتغيرات البيئية
        username = os.getenv("INSTAGRAM_USERNAME")
        password = os.getenv("INSTAGRAM_PASSWORD")
        
        if username and password:
            try:
                logger.info(f"🔄 محاولة تسجيل الدخول كـ {username}...")
                self.loader.login(username, password)
                
                # حفظ الجلسة للاستخدام المستقبلي
                try:
                    self.loader.save_session_to_file(str(self.session_file))
                except:
                    pass
                
                self._login_status = True
                logger.info(f"✅ تم تسجيل الدخول إلى Instagram كـ {username}")
                return True
            except instaloader.exceptions.BadCredentialsException:
                logger.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
            except instaloader.exceptions.TwoFactorAuthRequiredException:
                logger.error("❌ مطلوب رمز المصادقة الثنائية (2FA)")
                # محاولة استخدام رمز من المتغيرات
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
        
        # إذا فشل كل شيء، نحاول بدون تسجيل الدخول
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
                # استبعاد الكلمات المفتاحية
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
        """جلب قصص المستخدم الحالية - مع محاولات متعددة"""
        stories = []
        
        # محاولة تسجيل الدخول
        logged_in = self._ensure_login()
        
        if not logged_in:
            logger.info(f"محاولة جلب قصص {username} بدون تسجيل الدخول")
        
        try:
            # محاولة جلب الملف الشخصي
            try:
                profile = Profile.from_username(self.loader.context, username)
            except instaloader.exceptions.ProfileNotExistsException:
                logger.warning(f"⚠️ الحساب {username} غير موجود")
                return []
            except Exception as e:
                logger.warning(f"⚠️ خطأ في جلب الملف الشخصي: {e}")
                return []
            
            # محاولة جلب القصص
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
                
        except Exception as e:
            logger.error(f"خطأ عام في جلب قصص {username}: {e}")
            return []
    
    def _extract_story_data(self, item, username: str) -> Optional[Dict]:
        """استخراج بيانات القصة"""
        try:
            is_video = item.is_video
            
            # الحصول على رابط التحميل
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
            
            # تجنب التحميل المكرر
            if output_path.exists():
                return output_path
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
                "Origin": "https://www.instagram.com",
            }
            
            # إضافة الكوكيز إذا كانت متوفرة
            if self._login_status:
                try:
                    cookies = self.loader.context.get_cookies()
                    if cookies:
                        headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                except:
                    pass
            
            # تحميل الملف
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