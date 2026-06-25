"""
وحدة تحميل قصص Instagram للبوت
"""

import os
import re
import time
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

import instaloader
from instaloader import Profile, Post, Story, Instaloader

logger = logging.getLogger("PlayZone.InstagramStories")

# ==========================================================
# إعدادات Instaloader
# ==========================================================

class InstagramStoryDownloader:
    """مدير تحميل قصص Instagram"""
    
    def __init__(self, session_file: Optional[Path] = None):
        self.session_file = session_file or Path("./data/instagram_session")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
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
            if self.session_file.exists():
                try:
                    self.loader.load_session_from_file(str(self.session_file))
                    self._login_status = True
                    logger.info("✅ تم تحميل جلسة Instagram من الملف")
                    return True
                except Exception as e:
                    logger.warning(f"فشل تحميل الجلسة المحفوظة: {e}")
            
            username = os.getenv("INSTAGRAM_USERNAME")
            password = os.getenv("INSTAGRAM_PASSWORD")
            
            if username and password:
                self.loader.login(username, password)
                self.loader.save_session_to_file(str(self.session_file))
                self._login_status = True
                logger.info(f"✅ تم تسجيل الدخول إلى Instagram كـ {username}")
                return True
            
            logger.warning("⚠️ لا توجد بيانات تسجيل دخول Instagram")
            return False
        except Exception as e:
            logger.error(f"فشل تسجيل الدخول: {e}")
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
                if username not in ['p', 'reel', 'stories', 'explore', 'direct', 'highlights']:
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
        """جلب قصص المستخدم الحالية"""
        stories = []
        
        if not self._ensure_login():
            logger.info(f"محاولة جلب قصص {username} بدون تسجيل الدخول")
        
        try:
            profile = Profile.from_username(self.loader.context, username)
            
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
        except instaloader.exceptions.ProfileNotExistsException:
            logger.warning(f"⚠️ الحساب {username} غير موجود")
            return []
        except Exception as e:
            logger.error(f"خطأ في جلب قصص {username}: {e}")
            return []
    
    def _extract_story_data(self, item, username: str) -> Optional[Dict]:
        """استخراج بيانات القصة"""
        try:
            is_video = item.is_video
            return {
                "id": item.mediaid,
                "username": username,
                "is_video": is_video,
                "timestamp": item.date_utc.timestamp() if hasattr(item, 'date_utc') else time.time(),
                "caption": getattr(item, 'caption', ''),
                "thumbnail": item.thumbnail_url if hasattr(item, 'thumbnail_url') else None,
                "download_url": item.video_url if is_video else item.url,
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
            
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
            }
            
            if self._login_status:
                cookies = self.loader.context.get_cookies()
                headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])
            
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            with open(output_path, "wb") as f:
                f.write(response.content)
            
            logger.info(f"✅ تم تحميل القصة: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"فشل تحميل القصة: {e}")
            return None