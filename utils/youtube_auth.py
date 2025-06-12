"""
Утилиты для аутентификации YouTube
"""
import yt_dlp
import logging
from typing import Dict, Any, Optional
from .cookie_manager import CookieManager

class YouTubeAuthenticator:
    def __init__(self):
        self.cookie_manager = CookieManager()
        self.logger = logging.getLogger(__name__)
    
    def get_authenticated_ytdl_opts(self, base_opts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Получить ytdl_opts с поддержкой аутентификации
        """
        opts = base_opts.copy()
        
        # Добавляем конфигурацию cookies
        cookie_config = self.cookie_manager.get_youtube_cookies_config()
        opts.update(cookie_config)
        
        return opts
    
    def test_authentication(self, test_url: str = None) -> Dict[str, Any]:
        """
        Протестировать аутентификацию с age-restricted контентом
        """
        if not test_url:
            # Используем известное age-restricted видео для теста
            test_url = "https://www.youtube.com/watch?v=hHW1oY26kxQ"  # Пример
        
        result = {
            "success": False,
            "message": "",
            "cookies_used": False,
            "video_info": None
        }
        
        # Тестируем без cookies
        basic_opts = {
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True
        }
        
        try:
            ytdl_basic = yt_dlp.YoutubeDL(basic_opts)
            info_basic = ytdl_basic.extract_info(test_url, download=False)
            result["message"] = "Контент доступен без аутентификации"
            result["success"] = True
            result["video_info"] = {
                "title": info_basic.get("title", "Неизвестно"),
                "duration": info_basic.get("duration", 0)
            }
            return result
        except Exception as e:
            if "age-restricted" in str(e).lower() or "sign in" in str(e).lower():
                self.logger.info("Контент требует аутентификации, пробуем с cookies")
            else:
                result["message"] = f"Ошибка доступа к контенту: {str(e)}"
                return result
        
        # Тестируем с cookies
        auth_opts = self.get_authenticated_ytdl_opts(basic_opts)
        
        if "cookiefile" in auth_opts or "cookiesfrombrowser" in auth_opts:
            result["cookies_used"] = True
            try:
                ytdl_auth = yt_dlp.YoutubeDL(auth_opts)
                info_auth = ytdl_auth.extract_info(test_url, download=False)
                result["success"] = True
                result["message"] = "Аутентификация успешна! Age-restricted контент доступен"
                result["video_info"] = {
                    "title": info_auth.get("title", "Неизвестно"),
                    "duration": info_auth.get("duration", 0)
                }
            except Exception as e:
                result["message"] = f"Ошибка аутентификации: {str(e)}"
        else:
            result["message"] = "Cookies не настроены, age-restricted контент недоступен"
        
        return result
    
    def is_age_restricted_error(self, error: Exception) -> bool:
        """
        Проверить, является ли ошибка связанной с age-restriction
        """
        error_str = str(error).lower()
        age_restricted_keywords = [
            "age-restricted",
            "sign in to confirm your age",
            "video is age restricted",
            "sign in to confirm",
            "login required"
        ]
        
        return any(keyword in error_str for keyword in age_restricted_keywords)
