"""
وحدة تحميل قصص Instagram للبوت
تدعم الحسابات العامة والخاصة (باستخدام الكوكيز)
"""

import os
import re
import json
import time
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

import instaloader
from instaloader import Profile, Post, Story, Instaloader

logger = logging.getLogger("PlayZoneEnterpriseBot.InstagramStories")

# ==========================================================
# إعدادات Instaloader
# ==========================================================

class InstagramStoryDownloader:
    """مدير تحميل قصص Instagram"""
    
    def __init__(self, session_file: Optional[Path] = None):
        """
        تهيئة محمل Instagram
        
        Args:
            session_file: مسار ملف الجلسة للحفاظ على تسجيل الدخول
        """
        self.session_file = session_file or Path("./data/instagram_session")
        self.loader = Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            compress_json=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            max_connection_attempts=3,
        )
        self._login_status = False
        self._login_attempted = False
        
    def _ensure_login(self) -> bool:
        """تأكيد تسجيل الدخول إلى Instagram"""
        if self._login_status:
            return True
            
        if self._login_attempted:
            return False
            
        self._login_attempted = True
        
        try:
            # محاولة تحميل الجلسة المحفوظة
            if self.session_file.exists():
                try:
                    self.loader.load_session_from_file(str(self.session_file))
                    logger.info("تم تحميل جلسة Instagram من الملف")
                    self._login_status = True
                    return True
                except Exception as e:
                    logger.warning(f"فشل تحميل الجلسة المحفوظة: {e}")
            
            # محاولة تسجيل الدخول باستخدام بيانات من المتغيرات البيئية
            username = os.getenv("INSTAGRAM_USERNAME")
            password = os.getenv("INSTAGRAM_PASSWORD")
            
            if username and password:
                self.loader.login(username, password)
                self.loader.save_session_to_file(str(self.session_file))
                self._login_status = True
                logger.info(f"تم تسجيل الدخول إلى Instagram كـ {username}")
                return True
            
            logger.warning("لا توجد بيانات تسجيل دخول Instagram متاحة")
            return False
            
        except Exception as e:
            logger.error(f"فشل تسجيل الدخول إلى Instagram: {e}")
            return False
    
    def extract_username_from_url(self, url: str) -> Optional[str]:
        """استخراج اسم المستخدم من رابط Instagram"""
        patterns = [
            r'instagram\.com/([^/?]+)',
            r'instagr\.am/([^/?]+)',
            r'www\.instagram\.com/([^/?]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                username = match.group(1)
                # استبعاد الكلمات المفتاحية
                if username not in ['p', 'reel', 'stories', 'explore', 'direct']:
                    return username
        return None
    
    def is_instagram_stories_url(self, url: str) -> bool:
        """التحقق مما إذا كان الرابط خاص بقصص Instagram"""
        patterns = [
            r'instagram\.com/stories/[^/]+',
            r'instagram\.com/[^/]+/stories',
            r'instagram\.com/[^/]+/story',
            r'instagram\.com/stories/highlights/[^/]+',
        ]
        return any(re.search(pattern, url) for pattern in patterns)
    
    def get_user_stories(self, username: str) -> List[Dict]:
        """
        جلب قصص المستخدم الحالية
        
        Args:
            username: اسم المستخدم
            
        Returns:
            قائمة بالقصص مع بياناتها
        """
        stories = []
        
        if not self._ensure_login():
            # محاولة بدون تسجيل الدخول (للحسابات العامة فقط)
            logger.info(f"محاولة جلب قصص {username} بدون تسجيل الدخول")
        
        try:
            profile = Profile.from_username(self.loader.context, username)
            
            # جلب القصص
            story_iterator = self.loader.get_stories(userids=[profile.userid])
            
            for story in story_iterator:
                for item in story.get_items():
                    story_data = self._extract_story_data(item, username)
                    if story_data:
                        stories.append(story_data)
            
            logger.info(f"تم جلب {len(stories)} قصة من {username}")
            return stories
            
        except instaloader.exceptions.LoginRequiredException:
            logger.warning(f"الحساب {username} خاص، يتطلب تسجيل الدخول")
            return []
        except instaloader.exceptions.ProfileNotExistsException:
            logger.warning(f"الحساب {username} غير موجود")
            return []
        except Exception as e:
            logger.error(f"خطأ في جلب قصص {username}: {e}")
            return []
    
    def _extract_story_data(self, item, username: str) -> Optional[Dict]:
        """استخراج بيانات القصة"""
        try:
            is_video = item.is_video
            url = f"https://instagram.com/p/{item.mediaid}" if not is_video else item.video_url
            
            return {
                "id": item.mediaid,
                "username": username,
                "is_video": is_video,
                "url": url,
                "timestamp": item.date_utc.timestamp() if hasattr(item, 'date_utc') else time.time(),
                "caption": getattr(item, 'caption', ''),
                "thumbnail": item.thumbnail_url if hasattr(item, 'thumbnail_url') else None,
                # روابط مباشرة للتحميل
                "download_url": item.video_url if is_video else item.url,
            }
        except Exception as e:
            logger.error(f"خطأ في استخراج بيانات القصة: {e}")
            return None
    
    def download_story(self, story_data: Dict, output_dir: Path) -> Optional[Path]:
        """
        تحميل قصة معينة
        
        Args:
            story_data: بيانات القصة
            output_dir: مجلد التحميل
            
        Returns:
            مسار الملف المحمل أو None
        """
        try:
            download_url = story_data.get("download_url")
            if not download_url:
                return None
            
            # تحديد امتداد الملف
            ext = ".mp4" if story_data.get("is_video") else ".jpg"
            filename = f"story_{story_data.get('id')}{ext}"
            output_path = output_dir / filename
            
            # تحميل الملف
            import requests
            
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
            }
            
            # إضافة الكوكيز إذا كانت متوفرة
            if self._login_status:
                cookies = self.loader.context.get_cookies()
                headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])
            
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # حفظ الملف
            with open(output_path, "wb") as f:
                f.write(response.content)
            
            logger.info(f"تم تحميل القصة: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"فشل تحميل القصة: {e}")
            return None
    
    def get_highlight_stories(self, username: str, highlight_id: str = None) -> List[Dict]:
        """
        جلب قصص الهايلايت
        
        Args:
            username: اسم المستخدم
            highlight_id: معرف الهايلايت (اختياري)
            
        Returns:
            قائمة بالقصص
        """
        stories = []
        
        if not self._ensure_login():
            logger.warning("تسجيل الدخول مطلوب للوصول إلى الهايلايت")
            return stories
        
        try:
            profile = Profile.from_username(self.loader.context, username)
            
            for highlight in profile.get_highlights():
                if highlight_id and str(highlight.id) != highlight_id:
                    continue
                    
                for item in highlight.get_items():
                    story_data = self._extract_story_data(item, username)
                    if story_data:
                        story_data["highlight_title"] = highlight.title
                        stories.append(story_data)
            
            logger.info(f"تم جلب {len(stories)} قصة هايلايت من {username}")
            return stories
            
        except Exception as e:
            logger.error(f"خطأ في جلب هايلايت {username}: {e}")
            return []


# ==========================================================
# دمج مع البوت الرئيسي
# ==========================================================

class InstagramStoryHandler:
    """معالج قصص Instagram للبوت"""
    
    def __init__(self, download_dir: Path):
        self.download_dir = download_dir
        self.story_downloader = InstagramStoryDownloader()
        self.active_downloads = set()
    
    def can_handle(self, url: str) -> bool:
        """التحقق مما إذا كان يمكن معالجة الرابط"""
        return "instagram.com" in url.lower() and ("/stories/" in url or "/story/" in url)
    
    def extract_username(self, url: str) -> Optional[str]:
        """استخراج اسم المستخدم من رابط القصص"""
        patterns = [
            r'instagram\.com/stories/([^/]+)',
            r'instagram\.com/([^/]+)/stories',
            r'instagram\.com/([^/]+)/story',
            r'instagram\.com/stories/highlights/[^/]+/([^/]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # محاولة بديلة
        return self.story_downloader.extract_username_from_url(url)
    
    async def process_stories(self, url: str, progress_callback=None) -> Tuple[List[Path], List[Dict]]:
        """
        معالجة رابط القصص وتحميلها
        
        Args:
            url: رابط Instagram
            progress_callback: دالة لتحديث التقدم
            
        Returns:
            (قائمة بمسارات الملفات المحملة, قائمة ببيانات القصص)
        """
        username = self.extract_username(url)
        if not username:
            return [], []
        
        # إنشاء مجلد مؤقت للتحميلات
        job_id = f"ig_{username}_{int(time.time())}"
        job_dir = self.download_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        downloaded_files = []
        story_data_list = []
        
        try:
            # جلب القصص
            if progress_callback:
                await progress_callback(f"🔍 جاري جلب قصص {username}...")
            
            stories = self.story_downloader.get_user_stories(username)
            
            if not stories:
                if progress_callback:
                    await progress_callback(f"⚠️ لا توجد قصص متاحة لـ {username}")
                return [], []
            
            # تحميل القصص
            total = len(stories)
            for idx, story in enumerate(stories, 1):
                if progress_callback:
                    await progress_callback(f"📥 تحميل القصة {idx}/{total}...")
                
                file_path = self.story_downloader.download_story(story, job_dir)
                if file_path:
                    downloaded_files.append(file_path)
                    story_data_list.append(story)
            
            if progress_callback:
                await progress_callback(f"✅ تم تحميل {len(downloaded_files)} قصة من {username}")
            
            return downloaded_files, story_data_list
            
        except Exception as e:
            logger.error(f"فشل معالجة قصص {username}: {e}")
            raise
    
    def cleanup(self, job_dir: Path):
        """تنظيف الملفات المؤقتة"""
        try:
            import shutil
            shutil.rmtree(job_dir)
        except Exception:
            pass