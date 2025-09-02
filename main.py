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
from concurrent.futures import ThreadPoolExecutor

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = int(os.getenv("MAX_PLAYLIST_SIZE", "15"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))

logging.basicConfig(
    level=logging.INFO,
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

queues = {}
player_messages = {}
current_tracks = {}
player_channels = {}
track_history = {}
play_next_locks = {}

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
            
        self.db_path = "/app/cache/bot_cache.db"
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
                
            logger.info(f"✅ Кэш инициализирован")
            
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
    
    def set(self, key, data, ttl=3600):
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
            
            def cleanup_task(fut):
                with self.task_lock:
                    self.active_tasks.pop(task_id, None)
            
            future.add_done_callback(cleanup_task)
            return future

ytdl_pool = YTDLPPool(max_workers=6)

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
        try:
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

preload_manager = PreloadManager()

def get_ytdl_opts(extract_flat=False):
    ytdl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
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
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ytdl_opts["cookiefile"] = cookies_file
    
    return ytdl_opts

def log_command(user, command):
    logger.info(f"{user} использовал {command}")

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

def get_history(guild_id):
    return track_history.setdefault(guild_id, [])

def add_to_history(guild_id, track):
    history = get_history(guild_id)
    history_track = track.copy()
    history.append(history_track)
    if len(history) > 20:
        history.pop(0)

def get_play_lock(guild_id):
    if guild_id not in play_next_locks:
        play_next_locks[guild_id] = asyncio.Lock()
    return play_next_locks[guild_id]

def create_source(url):
    return discord.FFmpegPCMAudio(
        url,
        before_options=(
            "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
            "-reconnect_at_eof 1 -multiple_requests 1 -rw_timeout 10000000"
        ),
        options='-vn -bufsize 512k'
    )

def clean_search_query(query):
    cleaned = re.sub(r'[^\w\s\-.,!?]', '', query)
    return cleaned.strip()

async def safe_voice_connect(channel, max_retries=3):
    for attempt in range(max_retries):
        try:
            logger.info(f"🔌 Подключение к {channel.name}")
            
            existing_vc = discord.utils.get(bot.voice_clients, guild=channel.guild)
            if existing_vc:
                if existing_vc.channel == channel:
                    return existing_vc
                else:
                    await existing_vc.move_to(channel)
                    return existing_vc
            
            vc = await channel.connect(timeout=10.0, reconnect=True)
            logger.info(f"✅ Подключен к {channel.name}")
            return vc
            
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
        await delete_old_player(guild_id)  # <--- теперь всегда удаляем плеер при выходе
        player_channels.pop(guild_id, None)
        current_tracks.pop(guild_id, None)
        queues.pop(guild_id, None)
        play_next_locks.pop(guild_id, None)
        preload_manager.preload_locks.pop(guild_id, None)
        logger.info(f"🧹 Данные очищены")
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")

async def get_audio_url(track_url, title="Unknown", use_cache=True):
    cache_key = f"audio_url:{track_url}"
    
    if use_cache:
        cached_url = cache_manager.get(cache_key)
        if cached_url:
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
            
            task_id = f"audio_url:{track_url}:{format_selector}"
            future = ytdl_pool.submit_task(task_id, _extract_audio_url, opts, track_url)
            
            audio_url = await asyncio.wrap_future(future)
            
            if audio_url:
                if use_cache:
                    cache_manager.set(cache_key, audio_url, ttl=1800)
                return audio_url
                
        except Exception as e:
            logger.warning(f"⚠️ Формат {format_selector} не работает: {e}")
            continue
    
    raise Exception(f"Не удалось получить аудио URL для {title}")

def _extract_audio_url(opts, track_url):
    ytdl_temp = yt_dlp.YoutubeDL(opts)
    info = ytdl_temp.extract_info(track_url, download=False)
    return info.get("url") if info else None

def _extract_info_with_cache(search_query):
    cache_key = f"search:{search_query}"
    
    cached_info = cache_manager.get(cache_key)
    if cached_info:
        return cached_info
    
    is_playlist = "list=" in search_query or "playlist" in search_query.lower()
    
    if is_playlist:
        opts = get_ytdl_opts(extract_flat=True)
        opts["playlistend"] = MAX_PLAYLIST_SIZE
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = ytdl_temp.extract_info(search_query, download=False)
        
        if info and "entries" in info and info["entries"]:
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
                    except Exception as e:
                        logger.warning(f"⚠️ Предзагрузка трека {i}: {e}")
    else:
        opts = get_ytdl_opts(extract_flat=False)
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = ytdl_temp.extract_info(search_query, download=False)
    
    if info:
        cache_manager.set(cache_key, info, ttl=600)
    
    return info

class MusicPlayerView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc:
                await interaction.followup.send("❌ Не подключен.", ephemeral=True)
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

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="resume")
    async def resume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc:
                await interaction.followup.send("❌ Не подключен.", ephemeral=True)
                return
            if vc.is_paused():
                vc.resume()
                await interaction.followup.send("▶️ Возобновлено", ephemeral=True)
            else:
                await interaction.followup.send("❌ Не на паузе.", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка resume: {e}")

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

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            vc = interaction.guild.voice_client
            if not vc:
                await interaction.followup.send("❌ Не подключен.", ephemeral=True)
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
                await interaction.response.send_message(f"📭 **Очередь пуста** (0/{MAX_QUEUE_SIZE})", ephemeral=True)
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
                queue_text += f"`{i+1}.` {status_icon} **{title_display}**\n*{track['requester']}*\n\n"
            if len(queue) > 10:
                queue_text += f"*... и еще {len(queue) - 10} треков*"
            embed.description = queue_text
            embed.set_footer(text=f"✅ Готов | 🚀 Загружается | ⏳ Ожидает")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Ошибка show_queue: {e}")

def create_player_embed(guild_id):
    current_track = current_tracks.get(guild_id)
    queue = get_queue(guild_id)
    history = get_history(guild_id)
    
    embed = discord.Embed(color=0x2f3136)
    
    if current_track:
        embed.title = "🎵 Сейчас играет"
        embed.description = f"**{current_track['title']}**"
        
        embed.add_field(name="👤 Заказал", value=current_track['requester'], inline=True)
        embed.add_field(name="📃 В очереди", value=f"{len(queue)}/{MAX_QUEUE_SIZE}", inline=True)
        embed.add_field(name="📚 История", value=f"{len(history)}", inline=True)
        
        if 'thumbnail' in current_track and current_track['thumbnail']:
            embed.set_thumbnail(url=current_track['thumbnail'])
    else:
        embed.title = "🎵 Музыкальный плеер"
        embed.description = "*Готов к воспроизведению*"
        
        if queue:
            embed.add_field(name="📃 В очереди", value=f"{len(queue)}/{MAX_QUEUE_SIZE}", inline=True)
        if history:
            embed.add_field(name="📚 История", value=f"{len(history)}", inline=True)
    
    return embed

async def delete_old_player(guild_id):
    if guild_id in player_messages:
        try:
            await player_messages[guild_id].delete()
        except:
            pass
        player_messages.pop(guild_id, None)

async def create_new_player(guild_id, channel):
    if not channel:
        return
    
    await delete_old_player(guild_id)
    
    embed = create_player_embed(guild_id)
    view = MusicPlayerView(guild_id)
    
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
    logger.info(f"✅ Запущен: {bot.user}")
    logger.info(f"📊 Лимиты: плейлист {MAX_PLAYLIST_SIZE}, очередь {MAX_QUEUE_SIZE}")
    
    bot.add_view(MusicPlayerView(None))
    
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

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    vc = discord.utils.get(bot.voice_clients, guild=member.guild)

    if vc and vc.channel and len(vc.channel.members) == 1: 
        if vc.is_playing():
            vc.pause() 
            logger.info("⏸️ Пауза - бот один в канале")

        await asyncio.sleep(60)  
        if vc.channel and len(vc.channel.members) == 1:  
            logger.info(f"⏹️ Отключение от {member.guild.name}")
            await safe_voice_disconnect(vc, member.guild.id)

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
    except:
        pass

@tree.command(name="play", description="Воспроизвести музыку")
@app_commands.describe(query="Ссылка или запрос")
async def play(interaction: discord.Interaction, query: str):
    log_command(interaction.user.name, "/play")

    vc = interaction.guild.voice_client
    if not vc:
        if interaction.user.voice and interaction.user.voice.channel:
            try:
                vc = await safe_voice_connect(interaction.user.voice.channel)
            except Exception as e:
                await interaction.response.send_message(f"❌ Ошибка подключения: {str(e)}", ephemeral=True)
                return
        else:
            await interaction.response.send_message("⚠️ Зайдите в голосовой канал.", ephemeral=True)
            return

    queue = get_queue(interaction.guild.id)
    
    if len(queue) >= MAX_QUEUE_SIZE:
        await interaction.response.send_message(f"❌ Очередь полная! ({len(queue)}/{MAX_QUEUE_SIZE})", ephemeral=True)
        return

    try:
        await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)
    except Exception:
        return

    search_query = f"ytsearch1:{clean_search_query(query)}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        logger.info(f"🔍 Запрос: {query}")
        
        task_id = f"search:{search_query}"
        future = ytdl_pool.submit_task(task_id, _extract_info_with_cache, search_query)
        info = await asyncio.wrap_future(future)
        
        logger.info(f"✅ Получен ответ от yt-dlp")
    except Exception as e:
        logger.error(f"❌ Ошибка yt-dlp: {str(e)}")
        try:
            await interaction.edit_original_response(content=f"❌ Ошибка: {str(e)}")
        except:
            pass
        return

    if not info:
        try:
            await interaction.edit_original_response(content="❌ **Не найдено**")
        except:
            pass
        return

    if "entries" in info and info["entries"]:
        # Плейлист
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
        
        lazy_tracks = [track for track in queue if track.get("lazy_load")]
        if lazy_tracks:
            asyncio.create_task(preload_manager.preload_tracks(interaction.guild.id, 5))
        
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
    lock = get_play_lock(guild_id)
    async with lock:
        try:
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

            current_track = current_tracks.get(guild_id)
            if current_track:
                add_to_history(guild_id, current_track)

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
                            cache_manager.set(cache_key, full_info, ttl=3600)
                            next_track.update(full_info)
                            next_track["loaded"] = True
                        else:
                            raise Exception("Не удалось загрузить трек")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка загрузки: {e}")
                    await play_next(vc, guild_id)
                    return
            
            try:
                if next_track.get("url"):
                    audio_url = await get_audio_url(next_track["url"], next_track["title"])
                else:
                    logger.error(f"❌ Нет URL: {next_track['title']}")
                    await play_next(vc, guild_id)
                    return
                
                source = create_source(audio_url)
                
                def after_play(error):
                    if error:
                        logger.error(f"❌ Ошибка воспроизведения: {error}")
                    
                    bot.loop.create_task(play_next_safe(vc, guild_id))
                
                if vc.is_playing():
                    vc.stop()
                    await asyncio.sleep(0.2)
                
                vc.play(source, after=after_play)
                logger.info(f"🎵 Играет: {next_track['title']}")
                
            except Exception as e:
                logger.error(f"❌ Ошибка воспроизведения: {e}")
                await play_next(vc, guild_id)
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
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)

@tree.command(name="resume", description="Продолжить")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Продолжаем", ephemeral=True)
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
        
        title = track['title'][:40] + ('...' if len(track['title']) > 40 else '')
        queue_text += f"`{i+1}.` {status_icon} **{title}**\n"
    
    if len(queue) > 10:
        queue_text += f"*... и еще {len(queue) - 10} треков*"
    
    embed.description = queue_text
    embed.set_footer(text="✅ Готов | 🚀 Загружается | ⏳ Ожидает")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="history", description="История треков")
async def history_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/history")
    
    history = get_history(interaction.guild.id)
    
    if not history:
        await interaction.response.send_message("📚 История пуста.", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"📚 История ({len(history)})", color=0x2f3136)
    
    history_text = ""
    for i, track in enumerate(reversed(history[-10:])):
        title = track['title'][:40] + ('...' if len(track['title']) > 40 else '')
        history_text += f"`{len(history)-i}.` **{title}**\n"
    
    if len(history) > 10:
        history_text += f"*... и еще {len(history) - 10} треков*"
    
    embed.description = history_text
    embed.set_footer(text="Последние 10 треков")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="help", description="Справка")
async def help_cmd(interaction: discord.Interaction):
    try:
        embed = discord.Embed(title="📖 Команды", color=0x2f3136)
        embed.add_field(
            name="🎵 Управление",
            value="`/play` - Воспроизвести\n`/pause` - Пауза\n`/resume` - Продолжить\n`/skip` - Скип\n`/stop` - Стоп",
            inline=False
        )
        embed.add_field(
            name="📃 Информация",
            value="`/queue` - Очередь\n`/history` - История",
            inline=False
        )
        embed.add_field(
            name="⚙️ Лимиты",
            value=f"• Очередь: {MAX_QUEUE_SIZE}\n• Плейлист: {MAX_PLAYLIST_SIZE}",
            inline=False
        )
        
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)
            
    except discord.NotFound:
        logger.warning("⚠️ /help: взаимодействие истекло")
    except Exception as e:
        logger.error(f"❌ Ошибка /help: {e}")

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
