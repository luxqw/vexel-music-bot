import os
import logging
import discord
import asyncio
import sys
import re
import sqlite3
import json
import time
import threading
from discord.ext import commands
from discord import app_commands
import yt_dlp
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = int(os.getenv("MAX_PLAYLIST_SIZE", "15"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
queues = {}

# Словарь для хранения сообщений плеера по гильдиям
player_messages = {}
# Хранение текущих треков
current_tracks = {}
# Словарь для хранения каналов плеера
player_channels = {}

# ✅ Улучшенная система кэширования с SQLite
class CacheManager:
    def __init__(self, db_path="/app/cache/bot_cache.db"):
        self.db_path = db_path
        self.memory_cache = {}
        self.cache_lock = threading.RLock()
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных для постоянного кэша"""
        try:
            # Создаем директорию если не существует
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS track_cache (
                        key TEXT PRIMARY KEY,
                        data TEXT,
                        created_at INTEGER,
                        expires_at INTEGER
                    )
                ''')
                # Очистка устаревших записей при запуске
                current_time = int(time.time())
                conn.execute('DELETE FROM track_cache WHERE expires_at < ?', (current_time,))
                conn.commit()
                
            logger.info(f"✅ SQLite кэш инициализирован: {self.db_path}")
            
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать SQLite кэш: {e}")
            logger.info("🔄 Используем только память для кэширования")
            self.db_path = None  # Отключаем SQLite, используем только память
    
    def get(self, key):
        """Получить данные из кэша (сначала память, потом SQLite)"""
        with self.cache_lock:
            # Проверяем память кэш
            if key in self.memory_cache:
                data, expires_at = self.memory_cache[key]
                if expires_at > time.time():
                    return data
                else:
                    del self.memory_cache[key]
            
            # Проверяем SQLite кэш если доступен
            if self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.execute(
                            'SELECT data, expires_at FROM track_cache WHERE key = ? AND expires_at > ?',
                            (key, int(time.time()))
                        )
                        row = cursor.fetchone()
                        if row:
                            data = json.loads(row[0])
                            expires_at = row[1]
                            # Загружаем в память для быстрого доступа
                            self.memory_cache[key] = (data, expires_at)
                            return data
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка чтения SQLite кэша: {e}")
            
            return None
    
    def set(self, key, data, ttl=3600):
        """Сохранить данные в кэш"""
        expires_at = int(time.time()) + ttl
        
        with self.cache_lock:
            # Сохраняем в память (всегда)
            self.memory_cache[key] = (data, expires_at)
            
            # Сохраняем в SQLite если доступен
            if self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute(
                            'INSERT OR REPLACE INTO track_cache (key, data, created_at, expires_at) VALUES (?, ?, ?, ?)',
                            (key, json.dumps(data), int(time.time()), expires_at)
                        )
                        conn.commit()
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка записи в SQLite кэш: {e}")
    
    def cleanup(self):
        """Очистка устаревших записей"""
        current_time = time.time()
        
        with self.cache_lock:
            # Очищаем память кэш
            expired_keys = [k for k, (_, exp) in self.memory_cache.items() if exp <= current_time]
            for key in expired_keys:
                del self.memory_cache[key]
            
            # Очищаем SQLite кэш если доступен
            if self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute('DELETE FROM track_cache WHERE expires_at < ?', (int(current_time),))
                        conn.commit()
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка очистки SQLite кэша: {e}")

# ✅ Правильное логирование с выводом в консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("VexelBot")

# Попытка добавить файловое логирование если возможно
try:
    os.makedirs("/app/logs", exist_ok=True)
    file_handler = logging.FileHandler("/app/logs/bot.log")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    logger.info("✅ Файловое логирование активировано")
except Exception as e:
    logger.info(f"ℹ️ Файловое логирование недоступно: {e}")

# Глобальный менеджер кэша
cache_manager = CacheManager()

# ✅ Оптимизированный пул потоков для yt-dlp
class YTDLPPool:
    def __init__(self, max_workers=4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="YTDLP")
        self.active_tasks = {}
        self.task_lock = threading.Lock()
    
    def submit_task(self, task_id, func, *args, **kwargs):
        """Отправить задачу в пул с отслеживанием"""
        with self.task_lock:
            if task_id in self.active_tasks:
                # Задача уже выполняется
                return self.active_tasks[task_id]
            
            future = self.executor.submit(func, *args, **kwargs)
            self.active_tasks[task_id] = future
            
            def cleanup_task(fut):
                with self.task_lock:
                    self.active_tasks.pop(task_id, None)
            
            future.add_done_callback(cleanup_task)
            return future
    
    def shutdown(self):
        """Корректное завершение пула"""
        self.executor.shutdown(wait=True)

# Глобальный пул для yt-dlp операций
ytdl_pool = YTDLPPool(max_workers=6)

# Добавляем поддержку cookies
def get_ytdl_opts(extract_flat=False):
    """Получить ytdl_opts с улучшенными настройками"""
    ytdl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best[height<=720]/best",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "extract_flat": extract_flat,
        "writethumbnail": False,
        "writeinfojson": False,
        "logtostderr": False,
        "extractaudio": True,
        "audioformat": "best",
        "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
        "restrictfilenames": True,
        # ✅ Оптимизации для скорости
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    
    # Проверяем файл cookies
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ytdl_opts["cookiefile"] = cookies_file
    
    # Проверяем браузерные cookies
    browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
    if browser_cookies and not cookies_file:
        try:
            browser, profile = browser_cookies.split(",", 1)
            ytdl_opts["cookiesfrombrowser"] = (browser.strip(), profile.strip())
        except ValueError:
            logger.error(f"❌ Неверный формат YOUTUBE_BROWSER_COOKIES: {browser_cookies}")
    
    return ytdl_opts

def log_command(user, command):
    logging.info(f"{user} использовал {command}")

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

def create_source(url):
    return discord.FFmpegPCMAudio(
        url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options='-vn'
    )

def is_age_restricted_error(error):
    """Проверить, является ли ошибка связанной с age-restriction"""
    error_str = str(error).lower()
    age_restricted_keywords = [
        "age-restricted",
        "sign in to confirm your age",
        "video is age restricted", 
        "sign in to confirm",
        "login required"
    ]
    return any(keyword in error_str for keyword in age_restricted_keywords)

def clean_search_query(query):
    """Очистить поисковый запрос от проблемных символов"""
    cleaned = re.sub(r'[^\w\s\-.,!?]', '', query)
    return cleaned.strip()

async def safe_voice_connect(channel, max_retries=3):
    """Безопасное подключение к голосовому каналу с retry механизмом"""
    for attempt in range(max_retries):
        try:
            logger.info(f"🔌 Попытка подключения к голосовому каналу {channel.name} (попытка {attempt + 1}/{max_retries})")
            
            # Проверяем, есть ли уже подключение к этой гильдии
            existing_vc = discord.utils.get(bot.voice_clients, guild=channel.guild)
            if existing_vc:
                if existing_vc.channel == channel:
                    logger.info("✅ Уже подключен к этому каналу")
                    return existing_vc
                else:
                    logger.info("🔄 Перемещаемся в новый канал")
                    await existing_vc.move_to(channel)
                    return existing_vc
            
            # Новое подключение
            vc = await channel.connect(timeout=10.0, reconnect=True)
            logger.info(f"✅ Успешно подключен к {channel.name}")
            return vc
            
        except discord.errors.ClientException as e:
            logger.warning(f"⚠️ ClientException при подключении (попытка {attempt + 1}): {e}")
            if "already connected" in str(e).lower():
                existing_vc = discord.utils.get(bot.voice_clients, guild=channel.guild)
                if existing_vc:
                    return existing_vc
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут подключения (попытка {attempt + 1})")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка подключения (попытка {attempt + 1}): {e}")
        
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # Экспоненциальная задержка
            logger.info(f"⏳ Ждем {wait_time} секунд перед повторной попыткой...")
            await asyncio.sleep(wait_time)
    
    raise Exception(f"Не удалось подключиться к голосовому каналу после {max_retries} попыток")

async def safe_voice_disconnect(vc, guild_id):
    """Безопасное отключение от голосового канала"""
    if not vc:
        return
    
    try:
        logger.info("🔌 Отключаемся от голосового канала...")
        
        # Останавливаем воспроизведение
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        
        # Отключаемся
        await vc.disconnect(force=True)
        logger.info("✅ Успешно отключились от голосового канала")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при отключении от голосового канала: {e}")
    finally:
        # Очищаем данные в любом случае
        await cleanup_guild_data(guild_id)

async def cleanup_guild_data(guild_id):
    """Полная очистка данных гильдии"""
    try:
        # Удаляем плеер
        await delete_old_player(guild_id)
        
        # Очищаем все данные
        player_channels.pop(guild_id, None)
        current_tracks.pop(guild_id, None)
        queues.pop(guild_id, None)
        
        logger.info(f"🧹 Очищены данные для гильдии {guild_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке данных гильдии: {e}")

# ✅ Оптимизированная функция получения аудио URL с кэшем
async def get_audio_url(track_url, title="Unknown", use_cache=True):
    """Получить аудио URL с многоуровневым кэшированием"""
    cache_key = f"audio_url:{track_url}"
    
    # Проверяем кэш
    if use_cache:
        cached_url = cache_manager.get(cache_key)
        if cached_url:
            logger.info(f"📦 Используем кэшированный аудио URL для {title}")
            return cached_url
    
    formats_to_try = [
        "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio",
        "bestaudio/best[height<=720]",
        "best[height<=480]",
        "worst"
    ]
    
    for format_selector in formats_to_try:
        try:
            opts = get_ytdl_opts()
            opts["format"] = format_selector
            
            # Используем пул потоков для неблокирующего выполнения
            task_id = f"audio_url:{track_url}:{format_selector}"
            future = ytdl_pool.submit_task(task_id, _extract_audio_url, opts, track_url)
            
            audio_url = await asyncio.wrap_future(future)
            
            if audio_url:
                # Кэшируем результат на 30 минут
                if use_cache:
                    cache_manager.set(cache_key, audio_url, ttl=1800)
                
                logger.info(f"✅ Получен аудио URL для {title} с форматом: {format_selector}")
                return audio_url
                
        except Exception as e:
            logger.warning(f"⚠️ Формат {format_selector} не работает для {title}: {str(e)}")
            continue
    
    raise Exception(f"Не удалось получить аудио URL для {title} со всеми форматами")

def _extract_audio_url(opts, track_url):
    """Вспомогательная функция для извлечения аудио URL в отдельном потоке"""
    ytdl_temp = yt_dlp.YoutubeDL(opts)
    info = ytdl_temp.extract_info(track_url, download=False)
    return info.get("url") if info else None

# ✅ Улучшенная система предзагрузки с параллельной обработкой
class PreloadManager:
    def __init__(self):
        self.active_preloads = {}
        self.preload_lock = asyncio.Lock()
    
    async def preload_tracks(self, guild_id, count=3):
        """Предзагрузить следующие треки с оптимизацией"""
        try:
            async with self.preload_lock:
                queue = get_queue(guild_id)
                if not queue:
                    return
                
                # Находим треки для предзагрузки
                tracks_to_preload = []
                for i, track in enumerate(queue[:count]):
                    if (track.get("lazy_load") and not track.get("loaded") 
                        and not track.get("preloading")):
                        tracks_to_preload.append((i, track))
                
                if not tracks_to_preload:
                    return
                
                logger.info(f"🚀 Запуск предзагрузки {len(tracks_to_preload)} треков для гильдии {guild_id}")
                
                # Параллельная предзагрузка
                tasks = []
                for i, track in tracks_to_preload:
                    track["preloading"] = True
                    task = asyncio.create_task(self._preload_single_track(track, i))
                    tasks.append(task)
                
                # Ждем завершения всех задач
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Логируем результаты
                success_count = sum(1 for r in results if r is True)
                logger.info(f"✅ Успешно предзагружено {success_count}/{len(tracks_to_preload)} треков")
        except Exception as e:
            logger.error(f"❌ Ошибка в preload_tracks: {e}")
    
    async def _preload_single_track(self, track, index):
        """Предзагрузить один трек"""
        try:
            logger.info(f"🚀 Предзагрузка #{index + 1}: {track['title']}")
            
            cache_key = f"track_full:{track['playlist_url']}:{track['playlist_index']}"
            
            # Проверяем кэш
            cached_data = cache_manager.get(cache_key)
            if cached_data:
                logger.info(f"📦 Трек уже в кэше: {track['title']}")
                track.update(cached_data)
                track["loaded"] = True
                track["preloading"] = False
                return True
            
            # Загружаем данные трека
            full_info = await self._load_track_metadata(
                track["playlist_url"], 
                track["playlist_index"]
            )
            
            if full_info:
                # Кэшируем на 1 час
                cache_manager.set(cache_key, full_info, ttl=3600)
                
                track.update(full_info)
                track["loaded"] = True
                logger.info(f"✅ Предзагружен: {track['title']}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка предзагрузки {track['title']}: {e}")
            return False
        finally:
            track["preloading"] = False
    
    async def _load_track_metadata(self, playlist_url, index):
        """Загрузить метаданные трека оптимизированным способом"""
        try:
            # Используем пул потоков для загрузки
            task_id = f"metadata:{playlist_url}:{index}"
            future = ytdl_pool.submit_task(
                task_id, 
                self._extract_track_metadata, 
                playlist_url, 
                index
            )
            
            return await asyncio.wrap_future(future)
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки метаданных трека {index}: {e}")
            return None
    
    def _extract_track_metadata(self, playlist_url, index):
        """Извлечь метаданные трека в отдельном потоке"""
        try:
            opts = get_ytdl_opts(extract_flat=False)
            opts["skip_download"] = True
            opts["quiet"] = True
            opts["playliststart"] = index + 1
            opts["playlistend"] = index + 1
            
            ytdl_temp = yt_dlp.YoutubeDL(opts)
            info = ytdl_temp.extract_info(playlist_url, download=False)
            
            if info and "entries" in info and len(info["entries"]) > 0:
                entry = info["entries"][0]
                return {
                    "url": entry.get("url", ""),
                    "webpage_url": entry.get("webpage_url", ""),
                    "thumbnail": entry.get("thumbnail", ""),
                    "title": entry.get("title", "Unknown Track")
                }
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения метаданных: {e}")
        
        return None

# Глобальный менеджер предзагрузки
preload_manager = PreloadManager()

def _extract_info_with_cache(search_query):
    """Извлечь информацию с проверкой кэша и оптимизацией для плейлистов"""
    cache_key = f"search:{search_query}"
    
    # Проверяем кэш
    cached_info = cache_manager.get(cache_key)
    if cached_info:
        logger.info(f"📦 Используем кэшированный результат поиска")
        return cached_info
    
    # Определяем, плейлист это или одиночный трек
    is_playlist = "list=" in search_query or "playlist" in search_query.lower()
    
    if is_playlist:
        # Для плейлистов получаем только базовую информацию
        opts = get_ytdl_opts(extract_flat=True)
        opts["playlistend"] = MAX_PLAYLIST_SIZE
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = ytdl_temp.extract_info(search_query, download=False)
        
        # Для первых 3 треков сразу получаем полную информацию
        if info and "entries" in info and info["entries"]:
            logger.info(f"🚀 Быстрая предзагрузка первых 3 треков")
            opts_full = get_ytdl_opts(extract_flat=False)
            
            for i in range(min(3, len(info["entries"]))):
                entry = info["entries"][i]
                if entry and entry.get("url"):
                    try:
                        opts_full["playliststart"] = i + 1
                        opts_full["playlistend"] = i + 1
                        ytdl_full = yt_dlp.YoutubeDL(opts_full)
                        full_info = ytdl_full.extract_info(search_query, download=False)
                        
                        if full_info and "entries" in full_info and full_info["entries"]:
                            full_entry = full_info["entries"][0]
                            info["entries"][i].update({
                                "url": full_entry.get("url", ""),
                                "webpage_url": full_entry.get("webpage_url", ""),
                                "thumbnail": full_entry.get("thumbnail", ""),
                            })
                            logger.info(f"✅ Предзагружен: {info['entries'][i].get('title', 'Unknown')}")
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось предзагрузить трек {i}: {e}")
    else:
        # Для одиночных треков используем обычную загрузку
        opts = get_ytdl_opts(extract_flat=False)
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = ytdl_temp.extract_info(search_query, download=False)
    
    # Кэшируем результат на 10 минут
    if info:
        cache_manager.set(cache_key, info, ttl=600)
    
    return info

class MusicPlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        
        vc = interaction.guild.voice_client
        if not vc:
            try:
                await interaction.followup.send("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            except:
                pass
            return
        
        try:
            if vc.is_playing():
                vc.pause()
                await interaction.followup.send("⏸️ Пауза", ephemeral=True)
            elif vc.is_paused():
                vc.resume()
                await interaction.followup.send("▶️ Продолжаем", ephemeral=True)
            else:
                await interaction.followup.send("❌ Сейчас ничего не играет.", ephemeral=True)
                return
            
            await update_player_buttons(interaction.guild.id)
        except Exception as e:
            logger.error(f"❌ Ошибка pause/resume: {e}")
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        
        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            try:
                await interaction.followup.send("❌ Сейчас ничего не играет.", ephemeral=True)
            except:
                pass
            return
        
        try:
            vc.stop()
            await interaction.followup.send("⏭️ Скип", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка skip: {e}")
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        
        vc = interaction.guild.voice_client
        if not vc:
            try:
                await interaction.followup.send("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            except:
                pass
            return
        
        try:
            await interaction.followup.send("⏹️ Останавливаем...", ephemeral=True)
            await safe_voice_disconnect(vc, interaction.guild.id)
        except Exception as e:
            logger.error(f"❌ Ошибка stop: {e}")
    
    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.secondary, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(interaction.guild.id)
        
        if not queue:
            try:
                await interaction.response.send_message(f"📭 **Очередь пуста** (0/{MAX_QUEUE_SIZE})", ephemeral=True)
            except:
                pass
            return
        
        embed = discord.Embed(
            title=f"📃 Очередь треков ({len(queue)}/{MAX_QUEUE_SIZE})",
            color=0x2f3136
        )
        
        queue_text = ""
        for i, track in enumerate(queue[:10]):
            if track.get("preloading"):
                status_icon = "🚀"
            elif track.get("lazy_load") and not track.get("loaded"):
                status_icon = "⏳"
            else:
                status_icon = "✅"
            
            title_display = track['title'][:45] + ('...' if len(track['title']) > 45 else '')
            queue_text += f"`{i+1}.` {status_icon} **{title_display}**\n*Заказал: {track['requester']}*\n\n"
        
        if len(queue) > 10:
            queue_text += f"*... и еще {len(queue) - 10} треков*"
        
        embed.description = queue_text if queue_text else "Очередь пуста"
        embed.set_footer(text=f"Всего треков в очереди: {len(queue)}/{MAX_QUEUE_SIZE}")
        
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass

def create_player_embed(guild_id):
    """Создать embed для плеера"""
    current_track = current_tracks.get(guild_id)
    queue = get_queue(guild_id)
    
    embed = discord.Embed(color=0x2f3136)
    
    if current_track:
        embed.title = "🎵 Сейчас играет"
        embed.description = f"**{current_track['title']}**"
        
        embed.add_field(
            name="👤 Заказал", 
            value=current_track['requester'], 
            inline=True
        )
        embed.add_field(
            name="📃 В очереди", 
            value=f"{len(queue)}/{MAX_QUEUE_SIZE} треков", 
            inline=True
        )
        
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        if vc:
            if vc.is_playing():
                embed.add_field(name="🔊 Статус", value="▶️ Играет", inline=True)
            elif vc.is_paused():
                embed.add_field(name="🔊 Статус", value="⏸️ Пауза", inline=True)
            else:
                embed.add_field(name="🔊 Статус", value="⏹️ Остановлено", inline=True)
        else:
            embed.add_field(name="🔊 Статус", value="🔌 Не подключен", inline=True)
            
        if 'thumbnail' in current_track and current_track['thumbnail']:
            embed.set_thumbnail(url=current_track['thumbnail'])
    else:
        embed.title = "🎵 Музыкальный плеер"
        embed.description = "*Готов к воспроизведению*"
        
        if queue:
            embed.add_field(
                name="📃 В очереди ожидает", 
                value=f"{len(queue)}/{MAX_QUEUE_SIZE} треков", 
                inline=True
            )
        
        embed.add_field(
            name="🔊 Статус", 
            value="⏹️ Остановлен", 
            inline=True
        )
    
    return embed

async def delete_old_player(guild_id):
    """Безопасное удаление старого плеера"""
    if guild_id in player_messages:
        try:
            await player_messages[guild_id].delete()
        except:
            pass
        player_messages.pop(guild_id, None)

async def update_player_buttons(guild_id):
    """Быстро обновить только кнопки плеера без пересоздания"""
    if guild_id not in player_messages:
        return
    
    try:
        message = player_messages[guild_id]
        view = MusicPlayerView()
        
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        for item in view.children:
            if item.custom_id == "pause_resume":
                if vc and vc.is_playing():
                    item.emoji = "⏸️"
                else:
                    item.emoji = "▶️"
                break
        
        await message.edit(view=view)
    except:
        pass

async def create_new_player(guild_id, channel):
    """Создать новый плеер"""
    if not channel:
        return
    
    await delete_old_player(guild_id)
    
    embed = create_player_embed(guild_id)
    view = MusicPlayerView()
    
    try:
        player_msg = await channel.send(embed=embed, view=view)
        player_messages[guild_id] = player_msg
        player_channels[guild_id] = channel
        return True
    except Exception:
        return False

# ✅ Периодическая очистка кэша
async def cleanup_cache_periodic():
    """Периодическая очистка кэша"""
    while True:
        try:
            await asyncio.sleep(1800)  # Каждые 30 минут
            cache_manager.cleanup()
            logger.info("🧹 Выполнена очистка кэша")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки кэша: {e}")

@bot.event
async def on_ready():
    logger.info(f"✅ Вошли как {bot.user}")
    
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        logger.info("🔐 YouTube cookies настроены")
    else:
        logger.info("ℹ️ YouTube cookies не настроены")
    
    logger.info(f"📊 Лимит плейлиста: {MAX_PLAYLIST_SIZE} треков")
    logger.info(f"📊 Лимит очереди: {MAX_QUEUE_SIZE} треков")
    logger.info("🚀 Система кэширования активна")
    
    bot.add_view(MusicPlayerView())
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/help"
    ))
    
    # Запуск очистки кэша в фоне
    asyncio.create_task(cleanup_cache_periodic())
    
    try:
        synced = await tree.sync()
        logger.info(f"📡 Синхронизированы {len(synced)} команд(ы)")
    except Exception as e:
        logger.error(f"Ошибка sync: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    vc = discord.utils.get(bot.voice_clients, guild=member.guild)

    if vc and vc.channel and len(vc.channel.members) == 1: 
        if vc.is_playing():
            vc.pause() 
            logger.info("⏸️ Музыка приостановлена, так как бот остался один в канале.")

        await asyncio.sleep(60)  
        if vc.channel and len(vc.channel.members) == 1:  
            logger.info(f"⏹️ Отключение из канала {vc.channel.name} на сервере {member.guild.name}")
            await safe_voice_disconnect(vc, member.guild.id)

@tree.command(name="play", description="Воспроизвести музыку или плейлист с YouTube")
@app_commands.describe(query="Ссылка или запрос")
async def play(interaction: discord.Interaction, query: str):
    log_command(interaction.user.name, "/play")

    vc = interaction.guild.voice_client
    if not vc:
        if interaction.user.voice and interaction.user.voice.channel:
            try:
                vc = await safe_voice_connect(interaction.user.voice.channel)
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ **Ошибка подключения к голосовому каналу**\n"
                    f"💡 {str(e)}", 
                    ephemeral=True
                )
                return
        else:
            await interaction.response.send_message("⚠️ Сначала зайдите в голосовой канал.", ephemeral=True)
            return

    queue = get_queue(interaction.guild.id)
    
    if len(queue) >= MAX_QUEUE_SIZE:
        await interaction.response.send_message(
            f"❌ **Очередь переполнена!** ({len(queue)}/{MAX_QUEUE_SIZE})",
            ephemeral=True
        )
        return

    try:
        await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)
    except Exception:
        return

    search_query = f"ytsearch1:{clean_search_query(query)}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        logger.info(f"🔍 Обрабатываем запрос: {query}")
        
        task_id = f"search:{search_query}"
        future = ytdl_pool.submit_task(task_id, _extract_info_with_cache, search_query)
        info = await asyncio.wrap_future(future)
        
        logger.info(f"✅ Получена информация от yt-dlp")
    except Exception as e:
        logger.error(f"❌ Ошибка yt-dlp: {str(e)}")
        try:
            await interaction.edit_original_response(content=f"❌ Ошибка при обработке запроса: {str(e)}")
        except:
            pass
        return

    if not info:
        try:
            await interaction.edit_original_response(content="❌ **Не найдено**\nНе удалось найти трек.")
        except:
            pass
        return

    if "entries" in info and info["entries"]:
        # Обработка плейлистов
        total_entries = len(info["entries"])
        remaining_slots = MAX_QUEUE_SIZE - len(queue)
        max_to_add = min(MAX_PLAYLIST_SIZE, remaining_slots, total_entries)
        entries_to_process = info["entries"][:max_to_add]
        
        added_count = 0
        for i, entry in enumerate(entries_to_process):
            if entry and entry.get("title"):
                has_full_info = entry.get("url") and entry.get("webpage_url")
                
                track_data = {
                    "title": entry.get("title", f"Track {i+1}"),
                    "playlist_url": search_query,
                    "playlist_index": i,
                    "lazy_load": not has_full_info,
                    "loaded": has_full_info,
                    "preloading": False,
                    "requester": interaction.user.name,
                }
                
                if has_full_info:
                    track_data.update({
                        "url": entry.get("url", ""),
                        "webpage_url": entry.get("webpage_url", ""),
                        "thumbnail": entry.get("thumbnail", ""),
                    })
                
                queue.append(track_data)
                added_count += 1
        
        # Запускаем предзагрузку
        lazy_tracks = [track for track in queue if track.get("lazy_load")]
        if lazy_tracks:
            asyncio.create_task(preload_manager.preload_tracks(interaction.guild.id, 3))
        
        try:
            ready_count = sum(1 for track in queue[-added_count:] if track.get("loaded"))
            message = f"📃 **Добавлено {added_count} из {total_entries} треков**\n"
            
            if ready_count > 0:
                message += f"✅ {ready_count} треков готовы\n"
            if added_count - ready_count > 0:
                message += f"⏳ {added_count - ready_count} загружаются\n"
            
            message += f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE}"
            
            await interaction.edit_original_response(content=message)
        except:
            pass
            
    elif info.get("title"):
        # Одиночный трек
        track = {
            "title": info["title"],
            "url": info.get("url", ""),
            "webpage_url": info.get("webpage_url", ""),
            "thumbnail": info.get("thumbnail", ""),
            "requester": interaction.user.name,
            "lazy_load": False,
            "loaded": True,
        }
        queue.append(track)
        
        try:
            await interaction.edit_original_response(
                content=f"🎶 **Добавлен:** {track['title']}\n📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE}"
            )
        except:
            pass

    player_channels[interaction.guild.id] = interaction.channel
    await create_new_player(interaction.guild.id, interaction.channel)

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

async def play_next(vc, guild_id):
    """Воспроизвести следующий трек"""
    queue = get_queue(guild_id)
    if not queue:
        current_tracks[guild_id] = None
        logger.info("📭 Очередь пуста")
        channel = player_channels.get(guild_id)
        if channel:
            await create_new_player(guild_id, channel)
        return

    if not vc or not vc.is_connected():
        logger.warning("⚠️ Voice client отключен")
        await cleanup_guild_data(guild_id)
        return

    next_track = queue.pop(0)
    current_tracks[guild_id] = next_track
    logger.info(f"⏭️ Следующий трек: {next_track['title']}")
    
    # Предзагрузка в фоне
    if queue:
        asyncio.create_task(preload_manager.preload_tracks(guild_id, 3))
    
    # Загружаем трек если нужно
    if next_track.get("lazy_load") and not next_track.get("loaded"):
        try:
            cache_key = f"track_full:{next_track['playlist_url']}:{next_track['playlist_index']}"
            
            cached_data = cache_manager.get(cache_key)
            if cached_data:
                next_track.update(cached_data)
                next_track["loaded"] = True
            else:
                full_info = await preload_manager._load_track_metadata(
                    next_track["playlist_url"], 
                    next_track["playlist_index"]
                )
                
                if full_info:
                    cache_manager.set(cache_key, full_info, ttl=3600)
                    next_track.update(full_info)
                    next_track["loaded"] = True
                else:
                    raise Exception("Не удалось загрузить информацию о треке")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки трека: {e}")
            await play_next(vc, guild_id)
            return
    
    try:
        if next_track.get("url"):
            audio_url = await get_audio_url(next_track["url"], next_track["title"])
        else:
            logger.error(f"❌ Отсутствует URL для трека: {next_track['title']}")
            await play_next(vc, guild_id)
            return
        
        source = create_source(audio_url)
        
        def after_play(error):
            if error:
                logger.error(f"❌ Ошибка воспроизведения: {error}")
            
            if vc and vc.is_connected():
                bot.loop.create_task(play_next(vc, guild_id))
            else:
                bot.loop.create_task(cleanup_guild_data(guild_id))
        
        vc.play(source, after=after_play)
        logger.info(f"🎵 Играет: {next_track['title']}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка воспроизведения: {e}")
        await play_next(vc, guild_id)
        return
    
    # Обновляем плеер
    channel = player_channels.get(guild_id)
    if channel:
        await create_new_player(guild_id, channel)

@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Пауза", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)

@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Продолжаем", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Музыка не на паузе", ephemeral=True)

@tree.command(name="stop", description="Остановить и отключиться")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await interaction.response.send_message("⏹️ Останавливаем...", ephemeral=True)
        await safe_voice_disconnect(vc, interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Не подключен", ephemeral=True)

@tree.command(name="skip", description="Пропустить трек")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Скип", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)

@tree.command(name="queue", description="Показать очередь")
async def queue_cmd(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    
    if not queue:
        await interaction.response.send_message(f"📭 Очередь пуста (0/{MAX_QUEUE_SIZE})", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"📃 Очередь ({len(queue)}/{MAX_QUEUE_SIZE})", color=0x2f3136)
    
    queue_text = ""
    for i, track in enumerate(queue[:10]):
        status_icon = "🚀" if track.get("preloading") else ("⏳" if track.get("lazy_load") and not track.get("loaded") else "✅")
        title = track['title'][:40] + ('...' if len(track['title']) > 40 else '')
        queue_text += f"`{i+1}.` {status_icon} **{title}**\n"
    
    if len(queue) > 10:
        queue_text += f"*... и еще {len(queue) - 10} треков*"
    
    embed.description = queue_text
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="help", description="Справка по командам")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Команды", color=0x2f3136)
    embed.add_field(
        name="🎵 Команды",
        value="`/play` - Воспроизвести\n`/pause` - Пауза\n`/resume` - Продолжить\n`/skip` - Скип\n`/stop` - Стоп\n`/queue` - Очередь",
        inline=False
    )
    embed.add_field(
        name="⚡ Особенности",
        value=f"• Лимит очереди: {MAX_QUEUE_SIZE}\n• Лимит плейлиста: {MAX_PLAYLIST_SIZE}\n• Кэширование треков\n• Предзагрузка",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Запуск бота
if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ DISCORD_TOKEN не найден в переменных окружения")
        sys.exit(1)
    
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
