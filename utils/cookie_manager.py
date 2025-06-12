"""
Утилиты для управления cookies YouTube
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

class CookieManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def get_youtube_cookies_config(self) -> Dict[str, Any]:
        """
        Получить конфигурацию cookies для YouTube
        """
        config = {}
        
        # Проверяем файл с cookies
        cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
        if cookies_file and os.path.exists(cookies_file):
            config["cookiefile"] = cookies_file
            self.logger.info(f"Используем файл cookies: {cookies_file}")
            return config
        
        # Проверяем браузерные cookies
        browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
        if browser_cookies:
            try:
                # Формат: "chrome,default" или "firefox,profile_name"
                browser, profile = browser_cookies.split(",", 1)
                config["cookiesfrombrowser"] = (browser.strip(), profile.strip())
                self.logger.info(f"Используем cookies браузера: {browser} ({profile})")
                return config
            except ValueError:
                self.logger.error(f"Неверный формат YOUTUBE_BROWSER_COOKIES: {browser_cookies}")
        
        # Проверяем строку cookies
        cookies_string = os.getenv("YOUTUBE_COOKIES_STRING")
        if cookies_string:
            # Создаем временный файл для cookies
            temp_cookies_file = self._create_temp_cookies_file(cookies_string)
            if temp_cookies_file:
                config["cookiefile"] = temp_cookies_file
                self.logger.info("Используем cookies из переменной окружения")
                return config
        
        self.logger.info("Cookies для YouTube не настроены")
        return config
    
    def _create_temp_cookies_file(self, cookies_string: str) -> Optional[str]:
        """
        Создать временный файл cookies из строки
        """
        try:
            # Создаем директорию для временных cookies
            temp_dir = Path("temp_cookies")
            temp_dir.mkdir(exist_ok=True)
            
            temp_file = temp_dir / "youtube_cookies.txt"
            
            # Записываем cookies в Netscape формате
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write(cookies_string)
            
            return str(temp_file)
        except Exception as e:
            self.logger.error(f"Ошибка создания временного файла cookies: {e}")
            return None
    
    def validate_cookies_file(self, cookies_file: str) -> bool:
        """
        Проверить валидность файла cookies
        """
        if not os.path.exists(cookies_file):
            self.logger.error(f"Файл cookies не найден: {cookies_file}")
            return False
        
        try:
            with open(cookies_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Базовая проверка формата Netscape cookies
            if not content.strip():
                self.logger.error("Файл cookies пустой")
                return False
                
            # Проверяем наличие YouTube cookies
            if "youtube.com" not in content.lower():
                self.logger.warning("В файле cookies не найдены cookies для YouTube")
                
            self.logger.info("Файл cookies прошел базовую валидацию")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка при валидации файла cookies: {e}")
            return False
    
    def cleanup_temp_cookies(self):
        """
        Очистить временные файлы cookies
        """
        try:
            temp_dir = Path("temp_cookies")
            if temp_dir.exists():
                for file in temp_dir.glob("*.txt"):
                    file.unlink()
                self.logger.info("Временные файлы cookies очищены")
        except Exception as e:
            self.logger.error(f"Ошибка при очистке временных cookies: {e}")
