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
import urllib.parse
from discord.ext import commands
from discord import app_commands
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = int(os.getenv("MAX_PLAYLIST_SIZE", "15"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))
YTDLP_WORKERS = int(os.getenv("YTDLP_WORKERS", "6"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", "/app/cache/bot_cache.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

COLOR_PLAYING = 0x57F287  # green
COLOR_PAUSED  = 0xFEE75C  # yellow
COLOR_IDLE    = 0x2f3136  # dark gray

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("VexelBot")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# Stored in on_ready — used for thread-safe coroutine scheduling from audio callbacks
event_loop: asyncio.AbstractEventLoop = None

queues = {}
player_messages = {}
current_tracks = {}
player_channels = {}
play_next_locks = {}
loop_modes: dict = {}       # guild_id -> "off" | "track" | "queue"
auto_paused_guilds: set = set()  # guilds where bot auto-paused due to empty channel
alone_tasks: dict = {}      # guild_id -> asyncio.Task (pending auto-disconnect)

class CacheManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CacheManager, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return

        self.db_path = CACHE_DB_PATH
        self.memory_cache = {}
        self.cache_lock = threading.RLock()
        self.init_db()
        self.initialized = True

    def init_db(self):
        try:
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
                current_time = int(time.time())
                conn.execute('DELETE FROM track_cache WHERE expires_at < ?', (current_time,))
                conn.commit()

            logger.info(f"✅ Кэш инициализирован: {self.db_path}")

        except Exception as e:
            logger.warning(f"⚠️ Ошибка инициализации кэша: {e}")
            self.db_path = None

    def get(self, key):
        with self.cache_lock:
            if key in self.memory_cache:
                data, expires_at = self.memory_cache[key]
                if expires_at > time.time():
                    return data
                else:
                    del self.memory_cache[key]

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
                            self.memory_cache[key] = (data, expires_at)
                            return data
                except Exception:
                    pass

            return None

    def set(self, key, data, ttl=None):
        if ttl is None:
            ttl = CACHE_TTL
        expires_at = int(time.time()) + ttl

        with self.cache_lock:
            self.memory_cache[key] = (data, expires_at)

            if self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute(
                            'INSERT OR REPLACE INTO track_cache (key, data, created_at, expires_at) VALUES (?, ?, ?, ?)',
                            (key, json.dumps(data), int(time.time()), expires_at)
                        )
                        conn.commit()
                except Exception:
                    pass

    def cleanup(self):
        current_time = time.time()

        with self.cache_lock:
            expired_keys = [k for k, (_, exp) in self.memory_cache.items() if exp <= current_time]
            for key in expired_keys:
                del self.memory_cache[key]

            if self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute('DELETE FROM track_cache WHERE expires_at < ?', (int(current_time),))
                        conn.commit()
                except Exception:
                    pass

cache_manager = CacheManager()

class YTDLPPool:
    def __init__(self, max_workers=6):
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="YTDLP")
        self.active_tasks = {}
        self.task_lock = threading.Lock()

    def submit_task(self, task_id, func, *args, **kwargs):
        with self.task_lock:
            if task_id in self.active_tasks:
                return self.active_tasks[task_id]
            future = self.executor.submit(func, *args, **kwargs)
            self.active_tasks[task_id] = future

        # add_done_callback must be registered OUTSIDE the lock.
        # If the future is already done, concurrent.futures calls the callback
        # immediately in the current thread — which would deadlock if we're
        # still holding task_lock (threading.Lock is not reentrant).
        def cleanup_task(fut):
            try:
                with self.task_lock:
                    self.active_tasks.pop(task_id, None)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка очистки задачи {task_id}: {e}")

        future.add_done_callback(cleanup_task)
        return future

ytdl_pool = YTDLPPool(max_workers=YTDLP_WORKERS)

class PreloadManager:
    def __init__(self):
        self.preload_locks = {}

    def get_preload_lock(self, guild_id):
        if guild_id not in self.preload_locks:
            self.preload_locks[guild_id] = asyncio.Lock()
        return self.preload_locks[guild_id]

    async def preload_tracks(self, guild_id, count=3):
        lock = self.get_preload_lock(guild_id)
        async with lock:
            try:
                queue = get_queue(guild_id)
                if not queue:
                    return

                tracks_to_preload = []
                for i, track in enumerate(queue[:count]):
                    if (track.get("lazy_load") and not track.get("loaded")
                        and not track.get("preloading")):
                        tracks_to_preload.append((i, track))

                if not tracks_to_preload:
                    return

                logger.info(f"🚀 Предзагрузка {len(tracks_to_preload)} треков")

                tasks = []
                for i, track in tracks_to_preload:
                    track["preloading"] = True
                    task = asyncio.create_task(self._preload_single_track(track, i))
                    tasks.append(task)

                results = await asyncio.gather(*tasks, return_exceptions=True)

                success_count = sum(1 for r in results if r is True)
                logger.info(f"✅ Предзагружено {success_count}/{len(tracks_to_preload)} треков")

            except Exception as e:
                logger.error(f"❌ Ошибка предзагрузки: {e}")

    async def _preload_single_track(self, track, index):
        try:
            logger.info(f"🚀 Предзагрузка #{index + 1}: {track['title']}")

            cache_key = f"track_full:{track['playlist_url']}:{track['playlist_index']}"

            cached_data = cache_manager.get(cache_key)
            if cached_data:
                logger.info(f"📦 Трек уже в кэше: {track['title']}")
                track.update(cached_data)
                track["loaded"] = True
                track["preloading"] = False
                return True

            full_info = await self._load_track_metadata(
                track["playlist_url"],
                track["playlist_index"]
            )

            if full_info:
                cache_manager.set(cache_key, full_info)
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
        try:
            task_id = f"metadata:{playlist_url}:{index}"
            future = ytdl_pool.submit_task(
                task_id,
                self._extract_track_metadata,
                playlist_url,
                index
            )

            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=15.0)

        except asyncio.TimeoutError:
            logger.error(f"❌ Timeout загрузки метаданных трека {index}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки метаданных трека {index}: {e}")
            return None

    def _extract_track_metadata(self, playlist_url, index):
        # Use flat extraction to avoid downloading full video info for every playlist item.
        # We only need the stable watch URL (webpage_url) — the CDN URL is obtained at play time.
        # Always normalize to playlist?list=... — watch?v=...&list=... bypasses extract_flat.
        try:
            clean_url = normalize_playlist_url(playlist_url)
            opts = get_ytdl_opts(extract_flat=True)
            opts["quiet"] = True
            opts["socket_timeout"] = 15

            ytdl_temp = yt_dlp.YoutubeDL(opts)
            info = ytdl_temp.extract_info(clean_url, download=False)

            if info and "entries" in info and len(info["entries"]) > index:
                entry = info["entries"][index]
                if entry:
                    watch_url = entry.get("webpage_url") or entry.get("url", "")
                    return {
                        "url": watch_url,
                        "webpage_url": watch_url,
                        "thumbnail": entry.get("thumbnail", ""),
                        "title": entry.get("title", "Unknown Track"),
                        "duration": entry.get("duration", 0),
                    }
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if "DRM" in error_msg or "protected" in error_msg.lower():
                logger.warning(f"🔒 DRM трек пропущен: {error_msg}")
            else:
                logger.error(f"❌ Ошибка извлечения метаданных: {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения метаданных: {e}")

        return None

preload_manager = PreloadManager()

def get_ytdl_opts(extract_flat=False):
    ytdl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
        "noplaylist": False,
        "quiet": False,
        "no_warnings": False,
        "ignoreerrors": False,
        "extract_flat": extract_flat,
        "writethumbnail": False,
        "writeinfojson": False,
        "logtostderr": False,
        "extractaudio": True,
        "audioformat": "best",
        "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
        "restrictfilenames": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 5,
        # Force IPv4 — avoids IPv6-based blocks from YouTube
        "source_address": "0.0.0.0",
        # Use Node.js for YouTube n-challenge solving (web clients require it)
        "js_runtimes": {"node": {}},
        # Allow downloading the EJS challenge solver script from GitHub
        "allow_unplayable_formats": False,
        "remote_components": ["ejs:github"],
        # Container has read_only: true — /home/botuser/.cache is not writable.
        # Point yt-dlp cache to /tmp (tmpfs) so JS challenge solver and sigfuncs can be cached.
        "cachedir": "/tmp/yt-dlp-cache",
    }

    # Build YouTube extractor args
    yt_extractor_args = {
        # tv_embedded — YouTube TV embedded player; no PO token required, no JS challenge,
        # provides audio-only CDN URLs without sefc=1. web — fallback with cookies.
        "player_client": ["tv_embedded", "web"],
    }

    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ytdl_opts["cookiefile"] = cookies_file

    po_token = os.getenv("YTDLP_PO_TOKEN")
    if po_token:
        yt_extractor_args["po_token"] = [po_token]

    ytdl_opts["extractor_args"] = {"youtube": yt_extractor_args}

    return ytdl_opts

def normalize_playlist_url(url: str) -> str:
    """Convert watch?v=ID&list=LIST to playlist?list=LIST.
    Required because watch+list URLs bypass extract_flat in yt-dlp,
    causing full per-video extraction on every metadata load.
    """
    if not url.startswith("http") or "list=" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "list" in params:
            return f"https://www.youtube.com/playlist?list={params['list'][0]}"
    except Exception:
        pass
    return url

def format_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def log_command(user, command):
    logger.info(f"{user} использовал {command}")

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

def get_play_lock(guild_id):
    if guild_id not in play_next_locks:
        play_next_locks[guild_id] = asyncio.Lock()
    return play_next_locks[guild_id]

def _parse_netscape_cookies(cookie_file):
    """Return Cookie header string parsed from a Netscape-format cookie file."""
    cookies = []
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    name, value = parts[5], parts[6]
                    # Skip values with chars that would break FFmpeg header parsing
                    if '"' not in value and '\r' not in value and '\n' not in value:
                        cookies.append(f"{name}={value}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось прочитать куки: {e}")
    return "; ".join(cookies)

def create_source(url):
    before_opts = (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-reconnect_at_eof 1 -multiple_requests 1 -rw_timeout 10000000 "
        '-user_agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"'
    )
    cookie_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookie_file and os.path.exists(cookie_file):
        cookie_str = _parse_netscape_cookies(cookie_file)
        if cookie_str:
            before_opts += f' -headers "Cookie: {cookie_str}"'
    return discord.FFmpegPCMAudio(
        url,
        before_options=before_opts,
        options='-vn -bufsize 512k'
    )

def clean_search_query(query):
    # Remove only ASCII control characters; preserve Unicode (Cyrillic, CJK, etc.)
    return re.sub(r'[\x00-\x1f\x7f]', '', query).strip()

def get_embed_color(guild_id) -> int:
    vc = next((v for v in bot.voice_clients if v.guild.id == guild_id), None)
    if vc and vc.is_playing():
        return COLOR_PLAYING
    if vc and vc.is_paused():
        return COLOR_PAUSED
    return COLOR_IDLE

async def safe_voice_connect(channel, max_retries=3):
    for attempt in range(max_retries):
        try:
            logger.info(f"🔌 Подключение к {channel.name}")

            existing_vc = discord.utils.get(bot.voice_clients, guild=channel.guild)
            if existing_vc:
                if existing_vc.channel == channel and existing_vc.is_connected():
                    return existing_vc
                try:
                    await existing_vc.disconnect(force=True)
                except Exception:
                    pass
                # Wait for Discord to acknowledge the disconnect before opening a new
                # session — without this delay, the server sees two concurrent sessions
                # and kills the new one with 4006.
                await asyncio.sleep(1.0)

            vc = await channel.connect(timeout=5.0, reconnect=True)
            logger.info(f"✅ Подключен к {channel.name}")
            return vc

        except discord.errors.ConnectionClosed as e:
            if e.code == 4006:
                logger.warning(f"⚠️ Голосовая сессия устарела (4006), сброс и повтор {attempt + 1}/{max_retries}")
            else:
                logger.warning(f"⚠️ Ошибка подключения (код {e.code}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка подключения: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    raise Exception("Не удалось подключиться к голосовому каналу")

async def safe_voice_disconnect(vc, guild_id):
    if not vc:
        return

    try:
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await vc.disconnect(force=True)
        logger.info("✅ Отключились от голосового канала")
    except Exception as e:
        logger.error(f"❌ Ошибка отключения: {e}")
    finally:
        await cleanup_guild_data(guild_id)

async def cleanup_guild_data(guild_id):
    try:
        await delete_old_player(guild_id)
        player_channels.pop(guild_id, None)
        current_tracks.pop(guild_id, None)
        queues.pop(guild_id, None)
        play_next_locks.pop(guild_id, None)
        loop_modes.pop(guild_id, None)
        preload_manager.preload_locks.pop(guild_id, None)
        auto_paused_guilds.discard(guild_id)
        task = alone_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()
        logger.info("🧹 Данные очищены")
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")

async def get_audio_url(track_url, title="Unknown", use_cache=True):
    cache_key = f"audio_url:{track_url}"

    if use_cache:
        cached_url = cache_manager.get(cache_key)
        if cached_url:
            return cached_url

    formats_to_try = [
        "bestaudio/best",
        "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio",
        "best",
    ]

    last_error = None
    for format_selector in formats_to_try:
        try:
            opts = get_ytdl_opts()
            opts["format"] = format_selector

            task_id = f"audio_url:{track_url}:{format_selector}"
            future = ytdl_pool.submit_task(task_id, _extract_audio_url, opts, track_url)

            audio_url = await asyncio.wait_for(asyncio.wrap_future(future), timeout=30.0)

            if audio_url:
                if use_cache:
                    cache_manager.set(cache_key, audio_url, ttl=1800)
                return audio_url

        except asyncio.TimeoutError:
            last_error = f"Timeout при формате {format_selector}"
            logger.warning(f"⚠️ {last_error}")
            continue
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ Формат {format_selector} не работает: {e}")
            continue

    raise Exception(f"⛔ {last_error or f'Не удалось получить аудио URL для {title}'}")

def _extract_audio_url(opts, track_url):
    try:
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = ytdl_temp.extract_info(track_url, download=False)
        return info.get("url") if info else None
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "DRM" in error_msg or "protected" in error_msg.lower():
            logger.error(f"🔒 DRM защита: {error_msg}")
            raise Exception("⛔ Видео защищено DRM (Widewine). Невозможно воспроизвести.")
        elif "unavailable" in error_msg.lower():
            logger.error(f"❌ Видео недоступно: {error_msg}")
            raise Exception("❌ Видео недоступно или удалено.")
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка извлечения аудио: {e}")
        raise

def _normalize_entries(info):
    """Ensure every playlist entry has webpage_url set from its url field.
    Must be applied to both fresh and cached data, since old cache may lack webpage_url."""
    if info and "entries" in info:
        for entry in info.get("entries") or []:
            if entry:
                watch_url = entry.get("webpage_url") or entry.get("url", "")
                if watch_url:
                    entry["webpage_url"] = watch_url
                    entry["url"] = watch_url

def _extract_info_with_cache(search_query):
    cache_key = f"search:{search_query}"

    cached_info = cache_manager.get(cache_key)
    if cached_info:
        _normalize_entries(cached_info)
        return cached_info

    is_playlist = "list=" in search_query or "playlist" in search_query.lower()

    try:
        if is_playlist:
            # Normalize watch?v=ID&list=LIST → playlist?list=LIST so that extract_flat=True
            # works correctly. Without this, yt-dlp downloads each video individually.
            extraction_url = normalize_playlist_url(search_query)
            opts = get_ytdl_opts(extract_flat=True)
            opts["playlistend"] = MAX_PLAYLIST_SIZE
            opts["socket_timeout"] = 30
            ytdl_temp = yt_dlp.YoutubeDL(opts)
            info = ytdl_temp.extract_info(extraction_url, download=False)

            if info and "entries" in info and info["entries"]:
                valid_entries = [e for e in info["entries"] if e is not None and e.get("title")]

                if not valid_entries:
                    logger.error("❌ Все видео в плейлисте недоступны (возможно защищены DRM)")
                    return None

                info["entries"] = valid_entries

                # Normalize flat entries: ensure webpage_url is set from the watch URL.
                # CDN URLs are intentionally NOT stored here — get_audio_url fetches a
                # fresh one at play time. This avoids expiring CDN URLs in the cache.
                for entry in info["entries"]:
                    if entry:
                        watch_url = entry.get("webpage_url") or entry.get("url", "")
                        entry["webpage_url"] = watch_url
                        entry["url"] = watch_url
        else:
            opts = get_ytdl_opts(extract_flat=False)
            opts["socket_timeout"] = 30
            ytdl_temp = yt_dlp.YoutubeDL(opts)
            info = ytdl_temp.extract_info(search_query, download=False)

        if info:
            cache_manager.set(cache_key, info, ttl=600)

        return info

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "DRM" in error_msg or "protected" in error_msg.lower():
            logger.error(f"🔒 DRM ошибка: {error_msg}")
            raise Exception("⛔ Видео защищено DRM (Widewine). Невозможно воспроизвести.")
        elif "unavailable" in error_msg.lower() or "not found" in error_msg.lower():
            logger.error(f"❌ Видео недоступно: {error_msg}")
            raise Exception("❌ Видео недоступно или удалено.")
        else:
            logger.error(f"❌ Ошибка загрузки: {error_msg}")
            raise
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {str(e)}")
        raise

class MusicPlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc or not vc.is_connected():
                await interaction.followup.send("⚠️ Бот не подключён. Используйте `/play`.", ephemeral=True)
                return
            if vc.is_playing():
                vc.pause()
                await interaction.followup.send("⏸️ Пауза", ephemeral=True)
            elif vc.is_paused():
                vc.resume()
                await interaction.followup.send("▶️ Возобновлено", ephemeral=True)
            else:
                await interaction.followup.send("❌ Ничего не играет.", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка pause/resume: {e}")

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc or not (vc.is_playing() or vc.is_paused()):
                await interaction.followup.send("❌ Ничего не играет.", ephemeral=True)
                return
            vc.stop()
            await interaction.followup.send("⏭️ Скип", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка skip: {e}")

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="toggle_loop")
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            gid = interaction.guild.id
            current = loop_modes.get(gid, "off")
            next_mode = {"off": "track", "track": "queue", "queue": "off"}[current]
            loop_modes[gid] = next_mode
            labels = {"off": "Выключен ❌", "track": "Трек 🔂", "queue": "Очередь 🔁"}
            await interaction.response.send_message(
                f"Повтор: **{labels[next_mode]}**", ephemeral=True
            )
            channel = player_channels.get(gid)
            if channel:
                await create_new_player(gid, channel)
        except Exception as e:
            logger.error(f"❌ Ошибка toggle_loop: {e}")

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc or not vc.is_connected():
                await interaction.followup.send("⚠️ Бот не подключён. Используйте `/play`.", ephemeral=True)
                return
            await interaction.followup.send("⏹️ Останавливаем...", ephemeral=True)
            await safe_voice_disconnect(vc, interaction.guild.id)
        except Exception as e:
            logger.error(f"❌ Ошибка stop: {e}")

    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.secondary, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            queue = get_queue(interaction.guild.id)
            if not queue:
                await interaction.response.send_message(
                    f"📭 **Очередь пуста** (0/{MAX_QUEUE_SIZE})", ephemeral=True
                )
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
                title_display = track['title'][:40] + ('...' if len(track['title']) > 40 else '')
                dur = format_duration(track.get("duration", 0))
                dur_str = f" `{dur}`" if dur else ""
                queue_text += f"`{i+1}.` {status_icon} **{title_display}**{dur_str}\n*{track['requester']}*\n\n"
            if len(queue) > 10:
                queue_text += f"*... и еще {len(queue) - 10} треков*"
            embed.description = queue_text
            embed.set_footer(text="✅ Готов | 🚀 Загружается | ⏳ Ожидает")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка show_queue: {e}")

def create_player_embed(guild_id):
    current_track = current_tracks.get(guild_id)
    queue = get_queue(guild_id)
    vc = next((v for v in bot.voice_clients if v.guild.id == guild_id), None)

    if current_track:
        is_paused = vc and vc.is_paused()
        color = COLOR_PAUSED if is_paused else COLOR_PLAYING
        embed = discord.Embed(color=color)

        embed.set_author(name="⏸  На паузе" if is_paused else "♪  Сейчас играет")

        title = current_track["title"]
        embed.title = (title[:57] + "…") if len(title) > 60 else title

        dur = format_duration(current_track.get("duration", 0))
        meta_parts = [f"👤 {current_track['requester']}"]
        if dur:
            meta_parts.append(f"⏱ `{dur}`")
        meta_parts.append(f"📋 {len(queue)}/{MAX_QUEUE_SIZE}")
        embed.description = "  •  ".join(meta_parts)

        if queue:
            next_title = queue[0]["title"]
            embed.add_field(
                name="⏭️  Следующий",
                value=(next_title[:45] + "…") if len(next_title) > 45 else next_title,
                inline=False,
            )

        loop_mode = loop_modes.get(guild_id, "off")
        if loop_mode == "track":
            embed.set_footer(text="🔂 Повтор трека")
        elif loop_mode == "queue":
            embed.set_footer(text="🔁 Повтор очереди")

        if current_track.get("thumbnail"):
            embed.set_thumbnail(url=current_track["thumbnail"])
    else:
        embed = discord.Embed(
            color=COLOR_IDLE,
            title="🎵 Vexel Music",
            description="*Готов к воспроизведению*\n`/play` — добавить трек",
        )
        if queue:
            embed.add_field(name="📋 В очереди", value=f"{len(queue)} треков", inline=True)

    return embed

async def delete_old_player(guild_id):
    if guild_id in player_messages:
        try:
            await player_messages[guild_id].delete()
        except Exception:
            pass
        player_messages.pop(guild_id, None)

async def create_new_player(guild_id, channel):
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

async def play_next_safe(vc, guild_id):
    try:
        await play_next(vc, guild_id)
    except Exception as e:
        logger.error(f"❌ Ошибка в play_next_safe: {e}")

async def cleanup_cache_periodic():
    while True:
        try:
            await asyncio.sleep(1800)
            cache_manager.cleanup()
            logger.info("🧹 Очистка кэша")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки кэша: {e}")

@bot.event
async def on_ready():
    global event_loop
    event_loop = asyncio.get_event_loop()

    logger.info(f"✅ Запущен: {bot.user}")
    logger.info(f"📊 Лимиты: плейлист {MAX_PLAYLIST_SIZE}, очередь {MAX_QUEUE_SIZE}")
    logger.info(f"⚙️ Конфиг: workers={YTDLP_WORKERS}, cache_ttl={CACHE_TTL}s, log={LOG_LEVEL}")

    bot.add_view(MusicPlayerView())

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/play"
    ))

    asyncio.create_task(cleanup_cache_periodic())

    try:
        synced = await tree.sync()
        logger.info(f"📡 Синхронизировано {len(synced)} команд")
    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации: {e}")

async def _alone_disconnect_task(guild: discord.Guild):
    """Waits 60 s then disconnects the bot if it's still alone in the voice channel."""
    await asyncio.sleep(60)
    alone_tasks.pop(guild.id, None)
    vc = discord.utils.get(bot.voice_clients, guild=guild)
    if not vc or not vc.channel:
        return
    if not any(m for m in vc.channel.members if not m.bot):
        logger.info(f"⏹️ Авто-отключение от {guild.name} (никого нет)")
        await safe_voice_disconnect(vc, guild.id)

@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    vc = discord.utils.get(bot.voice_clients, guild=guild)

    # Handle the bot itself being moved or disconnected
    if member == bot.user:
        if before.channel and not after.channel:
            # Bot was kicked from the channel
            logger.info("⚠️ Бот был выкинут из голосового канала")
            auto_paused_guilds.discard(guild.id)
            alone_tasks.pop(guild.id, None)
            await cleanup_guild_data(guild.id)
        return

    if not vc or not vc.channel:
        return

    human_members = [m for m in vc.channel.members if not m.bot]

    if len(human_members) == 0:
        # Cancel any existing alone-task, start a fresh one
        existing = alone_tasks.get(guild.id)
        if existing and not existing.done():
            return  # already counting down

        if vc.is_playing():
            vc.pause()
            auto_paused_guilds.add(guild.id)
            logger.info("⏸️ Пауза — бот один в канале")

        task = asyncio.create_task(_alone_disconnect_task(guild))
        alone_tasks[guild.id] = task

    else:
        # Someone is in the channel — cancel pending auto-disconnect
        task = alone_tasks.pop(guild.id, None)
        if task and not task.done():
            task.cancel()

        # Resume only if we were the ones who auto-paused
        if guild.id in auto_paused_guilds and vc.is_paused():
            auto_paused_guilds.discard(guild.id)
            vc.resume()
            logger.info("▶️ Возобновление — пользователь вернулся в канал")

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandInvokeError):
        if isinstance(error.original, discord.NotFound):
            logger.warning(f"⚠️ Истекло взаимодействие: {interaction.command.name if interaction.command else 'unknown'}")
            return

    logger.error(f"❌ Ошибка команды: {error}")

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Ошибка выполнения команды.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Ошибка выполнения команды.", ephemeral=True)
    except Exception:
        pass

@tree.command(name="play", description="Воспроизвести музыку")
@app_commands.describe(query="Ссылка или запрос")
async def play(interaction: discord.Interaction, query: str):
    log_command(interaction.user.name, "/play")

    guild_id = interaction.guild.id
    voice_channel = interaction.user.voice.channel if interaction.user.voice else None
    vc = interaction.guild.voice_client

    # Validate: must be in a voice channel or bot already connected
    if not voice_channel and (not vc or not vc.is_connected()):
        await interaction.response.send_message("⚠️ Зайдите в голосовой канал.", ephemeral=True)
        return

    queue = get_queue(guild_id)
    if len(queue) >= MAX_QUEUE_SIZE:
        await interaction.response.send_message(f"❌ Очередь полная ({len(queue)}/{MAX_QUEUE_SIZE})", ephemeral=True)
        return

    # Acknowledge immediately — user sees feedback while voice connects + search runs
    age = (discord.utils.utcnow() - interaction.created_at).total_seconds()
    logger.info(f"⏱️ Interaction age before defer: {age:.3f}s")
    try:
        await interaction.response.defer(ephemeral=True)
    except (discord.errors.InteractionResponded, discord.errors.HTTPException) as e:
        logger.warning(f"⚠️ /play: не удалось подтвердить взаимодействие: {e}")
        return

    search_query = (
        f"ytsearch1:{clean_search_query(query)}"
        if not (query.startswith("http://") or query.startswith("https://"))
        else query
    )
    logger.info(f"🔍 Запрос: {query}")

    # Launch voice connection and yt-dlp search concurrently
    voice_task = None
    if not vc or not vc.is_connected():
        voice_task = asyncio.create_task(safe_voice_connect(voice_channel))

    task_id = f"search:{search_query}"
    search_future = ytdl_pool.submit_task(task_id, _extract_info_with_cache, search_query)
    search_awaitable = asyncio.wrap_future(search_future)

    # Wait for voice connection first
    if voice_task:
        try:
            vc = await voice_task
        except Exception as e:
            try:
                await interaction.edit_original_response(content=f"❌ Ошибка подключения: {e}")
            except Exception:
                pass
            return

    # Wait for search result
    try:
        info = await asyncio.wait_for(search_awaitable, timeout=30.0)
        logger.info("✅ Получен ответ от yt-dlp")
    except asyncio.TimeoutError:
        logger.error("⏱️ Timeout при поиске")
        try:
            await interaction.edit_original_response(content="⏱️ Поиск занял слишком долго.")
        except Exception:
            pass
        return
    except Exception as e:
        logger.error(f"❌ Ошибка yt-dlp: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ {e}")
        except Exception:
            pass
        return

    if not info:
        try:
            await interaction.edit_original_response(content="❌ Не найдено.")
        except Exception:
            pass
        return

    if "entries" in info and info["entries"]:
        total_entries = len(info["entries"])
        max_to_add = min(MAX_PLAYLIST_SIZE, MAX_QUEUE_SIZE - len(queue), total_entries)

        added_count = 0
        for i, entry in enumerate(info["entries"][:max_to_add]):
            if entry and entry.get("title"):
                has_full_info = bool(entry.get("url") and entry.get("webpage_url"))
                track_data = {
                    "title": entry.get("title", f"Track {i + 1}"),
                    "duration": entry.get("duration", 0),
                    "playlist_url": normalize_playlist_url(search_query),
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

        if added_count == 0:
            try:
                await interaction.edit_original_response(content="❌ Не удалось добавить треки.")
            except Exception:
                pass
            return

        if any(t.get("lazy_load") for t in queue):
            asyncio.create_task(preload_manager.preload_tracks(guild_id, 5))

        try:
            await interaction.edit_original_response(
                content=f"📃 **Добавлено {added_count}/{total_entries} треков** — очередь: {len(queue)}/{MAX_QUEUE_SIZE}"
            )
        except Exception:
            pass

    elif info.get("title"):
        dur = format_duration(info.get("duration", 0))
        track = {
            "title": info["title"],
            "url": info.get("url", ""),
            "webpage_url": info.get("webpage_url", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "requester": interaction.user.name,
            "lazy_load": False,
            "loaded": True,
        }
        queue.append(track)
        try:
            dur_str = f" `{dur}`" if dur else ""
            await interaction.edit_original_response(
                content=f"🎶 **{track['title']}**{dur_str} — очередь: {len(queue)}/{MAX_QUEUE_SIZE}"
            )
        except Exception:
            pass
    else:
        try:
            await interaction.edit_original_response(content="❌ Не удалось обработать результат.")
        except Exception:
            pass
        return

    player_channels[guild_id] = interaction.channel
    await create_new_player(guild_id, interaction.channel)

    if not vc.is_playing() and len(queue) > 0:
        await play_next(vc, guild_id)

async def play_next(vc, guild_id):
    lock = get_play_lock(guild_id)
    async with lock:
        try:
            queue = get_queue(guild_id)

            if not vc or not vc.is_connected():
                logger.warning("⚠️ Voice client отключен, попытка переподключения...")
                channel = getattr(vc, 'channel', None)
                if channel:
                    try:
                        vc = await safe_voice_connect(channel)
                    except Exception:
                        logger.error("❌ Не удалось переподключиться")
                        await cleanup_guild_data(guild_id)
                        return
                else:
                    await cleanup_guild_data(guild_id)
                    return

            current_track = current_tracks.get(guild_id)
            loop_mode = loop_modes.get(guild_id, "off")

            if current_track:
                if loop_mode == "track":
                    queue.insert(0, current_track)
                elif loop_mode == "queue":
                    queue.append(current_track)

            if not queue:
                current_tracks[guild_id] = None
                logger.info("📭 Очередь пуста")
                channel = player_channels.get(guild_id)
                if channel:
                    await create_new_player(guild_id, channel)
                return

            next_track = queue.pop(0)
            current_tracks[guild_id] = next_track
            logger.info(f"⏭️ Следующий: {next_track['title']}")

            remaining_lazy = [track for track in queue if track.get("lazy_load")]
            if remaining_lazy:
                asyncio.create_task(preload_manager.preload_tracks(guild_id, 3))

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
                            cache_manager.set(cache_key, full_info)
                            next_track.update(full_info)
                            next_track["loaded"] = True
                        else:
                            logger.warning(f"⚠️ Пропуск трека (не удалось загрузить): {next_track['title']}")
                            # create_task avoids deadlock: recursive await inside async with lock would block forever
                            asyncio.create_task(play_next_safe(vc, guild_id))
                            return

                except Exception as e:
                    logger.error(f"❌ Ошибка загрузки: {e}")
                    asyncio.create_task(play_next_safe(vc, guild_id))
                    return

            try:
                # Prefer webpage_url (stable YouTube watch URL) over url (expiring CDN URL).
                # get_audio_url will extract a fresh CDN URL from the watch URL at play time.
                play_url = next_track.get("webpage_url") or next_track.get("url")
                if play_url:
                    audio_url = await get_audio_url(play_url, next_track["title"])
                else:
                    logger.error(f"❌ Нет URL: {next_track['title']}")
                    asyncio.create_task(play_next_safe(vc, guild_id))
                    return

                source = create_source(audio_url)

                def after_play(error):
                    if error:
                        logger.error(f"❌ Ошибка воспроизведения: {error}")
                        # Re-queue the failed track so play_next can retry it.
                        # Dict/list ops are GIL-safe from the audio thread.
                        failed = current_tracks.pop(guild_id, None)
                        if failed:
                            retry_count = failed.get("_retry_count", 0) + 1
                            if retry_count <= 2:
                                failed["_retry_count"] = retry_count
                                play_url = failed.get("webpage_url") or failed.get("url")
                                if play_url:
                                    # Invalidate cached audio URL so retry fetches a fresh one
                                    cache_manager.set(f"audio_url:{play_url}", None, ttl=1)
                                get_queue(guild_id).insert(0, failed)
                                logger.info(f"🔄 Повтор трека (попытка {retry_count}): {failed.get('title')}")
                            else:
                                logger.warning(f"⚠️ Пропуск трека после {retry_count} неудач: {failed.get('title')}")
                    # run_coroutine_threadsafe is required here because after_play
                    # is called from the discord.py audio thread, not the event loop
                    asyncio.run_coroutine_threadsafe(play_next_safe(vc, guild_id), event_loop)

                if vc.is_playing():
                    vc.stop()
                    await asyncio.sleep(0.2)

                vc.play(source, after=after_play)
                logger.info(f"🎵 Играет: {next_track['title']}")

            except Exception as e:
                logger.error(f"❌ Ошибка воспроизведения: {e}")
                # Re-insert the failed track at front of queue so play_next retries it.
                # Clear current_tracks so play_next treats it as a fresh start (not a loop iteration).
                failed_track = current_tracks.pop(guild_id, None)
                if failed_track:
                    queue.insert(0, failed_track)
                asyncio.create_task(play_next_safe(vc, guild_id))
                return

            channel = player_channels.get(guild_id)
            if channel:
                await create_new_player(guild_id, channel)

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в play_next: {e}")

@tree.command(name="pause", description="Пауза")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Пауза", ephemeral=True)
        channel = player_channels.get(interaction.guild.id)
        if channel:
            await create_new_player(interaction.guild.id, channel)
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)

@tree.command(name="resume", description="Продолжить")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Продолжаем", ephemeral=True)
        channel = player_channels.get(interaction.guild.id)
        if channel:
            await create_new_player(interaction.guild.id, channel)
    else:
        await interaction.response.send_message("❌ Не на паузе", ephemeral=True)

@tree.command(name="stop", description="Остановить")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await interaction.response.send_message("⏹️ Останавливаем...", ephemeral=True)
        await safe_voice_disconnect(vc, interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Не подключен", ephemeral=True)

@tree.command(name="skip", description="Пропустить")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Скип", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)


@tree.command(name="loop", description="Режим повтора: трек / очередь / выкл")
async def loop_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/loop")
    guild_id = interaction.guild.id

    current = loop_modes.get(guild_id, "off")
    next_mode = {"off": "track", "track": "queue", "queue": "off"}[current]
    loop_modes[guild_id] = next_mode

    labels = {"off": "Выключен ❌", "track": "Трек 🔂", "queue": "Очередь 🔁"}
    await interaction.response.send_message(f"Повтор: **{labels[next_mode]}**", ephemeral=True)

    channel = player_channels.get(guild_id)
    if channel:
        await create_new_player(guild_id, channel)

@tree.command(name="queue", description="Показать очередь")
async def queue_cmd(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)

    if not queue:
        await interaction.response.send_message(f"📭 Очередь пуста (0/{MAX_QUEUE_SIZE})", ephemeral=True)
        return

    embed = discord.Embed(title=f"📃 Очередь ({len(queue)}/{MAX_QUEUE_SIZE})", color=0x2f3136)

    queue_text = ""
    for i, track in enumerate(queue[:10]):
        if track.get("preloading"):
            status_icon = "🚀"
        elif track.get("lazy_load") and not track.get("loaded"):
            status_icon = "⏳"
        else:
            status_icon = "✅"

        title = track['title'][:38] + ('...' if len(track['title']) > 38 else '')
        dur = format_duration(track.get("duration", 0))
        dur_str = f" `{dur}`" if dur else ""
        queue_text += f"`{i+1}.` {status_icon} **{title}**{dur_str}\n"

    if len(queue) > 10:
        queue_text += f"*... и еще {len(queue) - 10} треков*"

    embed.description = queue_text
    embed.set_footer(text="✅ Готов | 🚀 Загружается | ⏳ Ожидает")
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ DISCORD_TOKEN не найден")
        sys.exit(1)

    try:
        logger.info("🚀 Запуск бота...")
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("👋 Остановка по Ctrl+C")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
    finally:
        ytdl_pool.executor.shutdown(wait=True)
