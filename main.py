import os
import logging
import discord
import asyncio
import yt_dlp
import time
from discord.ext import commands
from discord import app_commands
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

# Конфигурация
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = 100
QUEUE_PAGE_SIZE = 10

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# Глобальные данные
guild_data = {}  # {guild_id: GuildMusicData}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

@dataclass
class Track:
    """Класс для представления трека"""
    title: str
    url: str
    webpage_url: str
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None
    requester: str = "Unknown"
    requested_at: datetime = None
    
    def __post_init__(self):
        if self.requested_at is None:
            self.requested_at = datetime.utcnow()
    
    @property
    def duration_str(self) -> str:
        """Форматированная длительность"""
        if not self.duration:
            return "неизвестно"
        
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes}:{seconds:02d}"

class MusicQueue:
    """Улучшенная очередь музыки"""
    
    def __init__(self):
        self.tracks: List[Track] = []
        self.history: List[Track] = []
        self.current: Optional[Track] = None
        self.loop_mode = "none"  # none, track, queue
        
    def add(self, track: Track):
        """Добавить трек в очередь"""
        self.tracks.append(track)
    
    def add_multiple(self, tracks: List[Track]):
        """Добавить несколько треков"""
        self.tracks.extend(tracks)
    
    def next(self) -> Optional[Track]:
        """Получить следующий трек"""
        if self.current:
            self.history.append(self.current)
        
        if self.loop_mode == "track" and self.current:
            return self.current
        
        if not self.tracks:
            if self.loop_mode == "queue" and self.history:
                self.tracks = self.history.copy()
                self.history.clear()
            else:
                self.current = None
                return None
        
        self.current = self.tracks.pop(0)
        return self.current
    
    def clear(self):
        """Очистить очередь"""
        self.tracks.clear()
        self.current = None
    
    def remove(self, index: int) -> bool:
        """Удалить трек по индексу"""
        if 0 <= index < len(self.tracks):
            self.tracks.pop(index)
            return True
        return False
    
    def shuffle(self):
        """Перемешать очередь"""
        import random
        random.shuffle(self.tracks)
    
    @property
    def is_empty(self) -> bool:
        return len(self.tracks) == 0 and self.current is None

@dataclass
class GuildMusicData:
    """Данные музыки для сервера"""
    queue: MusicQueue
    voice_client: Optional[discord.VoiceClient] = None
    now_playing_message: Optional[discord.Message] = None
    volume: float = 0.5
    start_time: Optional[float] = None
    
    def __post_init__(self):
        if self.queue is None:
            self.queue = MusicQueue()

# YouTube DL настройки
ytdl_opts = {
    "format": "bestaudio/best",
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": False,  # Разрешаем плейлисты
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

ytdl = yt_dlp.YoutubeDL(ytdl_opts)

def get_guild_data(guild_id: int) -> GuildMusicData:
    """Получить данные сервера"""
    if guild_id not in guild_data:
        guild_data[guild_id] = GuildMusicData(queue=MusicQueue())
    return guild_data[guild_id]

def create_progress_bar(current: int, total: int, length: int = 20) -> str:
    """Создать прогресс-бар"""
    if total <= 0:
        return "▱" * length
    
    filled = int((current / total) * length)
    bar = "▰" * filled + "▱" * (length - filled)
    return bar

def create_now_playing_embed(track: Track, guild_data: GuildMusicData) -> discord.Embed:
    """Создать embed для текущего трека"""
    embed = discord.Embed(
        title="🎵 Сейчас играет",
        description=f"**[{track.title}]({track.webpage_url})**",
        color=discord.Color.green()
    )
    
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    
    # Прогресс бар
    if guild_data.start_time and track.duration:
        elapsed = int(time.time() - guild_data.start_time)
        progress = create_progress_bar(elapsed, track.duration)
        
        elapsed_str = f"{elapsed // 60}:{elapsed % 60:02d}"
        duration_str = track.duration_str
        
        embed.add_field(
            name="⏱️ Прогресс",
            value=f"`{elapsed_str}` {progress} `{duration_str}`",
            inline=False
        )
    
    # Дополнительная информация
    embed.add_field(name="👤 Запросил", value=track.requester, inline=True)
    
    if track.uploader:
        embed.add_field(name="📺 Канал", value=track.uploader, inline=True)
    
    embed.add_field(name="🔊 Громкость", value=f"{int(guild_data.volume * 100)}%", inline=True)
    
    # Очередь
    queue_size = len(guild_data.queue.tracks)
    if queue_size > 0:
        next_track = guild_data.queue.tracks[0]
        embed.add_field(
            name=f"📃 Следующий ({queue_size} в очереди)",
            value=next_track.title[:50] + ("..." if len(next_track.title) > 50 else ""),
            inline=False
        )
    
    embed.set_footer(text=f"Режим повтора: {guild_data.queue.loop_mode}")
    embed.timestamp = datetime.utcnow()
    
    return embed

class MusicControlView(discord.ui.View):
    """View с кнопками управления музыкой"""
    
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)  # 5 минут
        self.guild_id = guild_id
    
    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Пауза/продолжить"""
        guild_data = get_guild_data(self.guild_id)
        vc = guild_data.voice_client
        
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к каналу", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            button.label = "▶️"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⏸️ Пауза", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            button.label = "⏸️"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("▶️ Продолжено", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Нечего воспроизводить", ephemeral=True)
    
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Пропустить трек"""
        guild_data = get_guild_data(self.guild_id)
        vc = guild_data.voice_client
        
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Нечего пропускать", ephemeral=True)
            return
        
        vc.stop()  # Это вызовет play_next через after callback
        await interaction.response.send_message("⏭️ Трек пропущен", ephemeral=True)
    
    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Остановить воспроизведение"""
        guild_data = get_guild_data(self.guild_id)
        vc = guild_data.voice_client
        
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен", ephemeral=True)
            return
        
        guild_data.queue.clear()
        vc.stop()
        await vc.disconnect()
        guild_data.voice_client = None
        
        await interaction.response.send_message("⏹️ Остановлено и отключено", ephemeral=True)
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
    
    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Перемешать очередь"""
        guild_data = get_guild_data(self.guild_id)
        
        if len(guild_data.queue.tracks) < 2:
            await interaction.response.send_message("❌ Недостаточно треков для перемешивания", ephemeral=True)
            return
        
        guild_data.queue.shuffle()
        await interaction.response.send_message("🔀 Очередь перемешана", ephemeral=True)
    
    @discord.ui.button(label="🔁", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Переключить режим повтора"""
        guild_data = get_guild_data(self.guild_id)
        
        if guild_data.queue.loop_mode == "none":
            guild_data.queue.loop_mode = "track"
            button.label = "🔂"
            mode_text = "🔂 Повтор трека"
        elif guild_data.queue.loop_mode == "track":
            guild_data.queue.loop_mode = "queue"
            button.label = "🔁"
            mode_text = "🔁 Повтор очереди"
        else:
            guild_data.queue.loop_mode = "none"
            button.label = "🔁"
            mode_text = "▶️ Без повтора"
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(mode_text, ephemeral=True)

async def extract_track_info(url_or_query: str, requester: str) -> tuple[List[Track], bool]:
    """Извлечь информацию о треке(ах) асинхронно"""
    
    def extract():
        try:
            # Определяем тип запроса
            if not (url_or_query.startswith("http://") or url_or_query.startswith("https://")):
                # Это поисковый запрос
                search_query = f"ytsearch1:{url_or_query}"
            else:
                search_query = url_or_query
            
            info = ytdl.extract_info(search_query, download=False)
            return info
        except Exception as e:
            raise e
    
    # Выполняем в отдельном потоке
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, extract)
    except yt_dlp.utils.ExtractorError as e:
        error_msg = str(e).lower()
        if "private" in error_msg:
            raise Exception("❌ Видео недоступно (приватное или удалено)")
        elif "region" in error_msg:
            raise Exception("❌ Видео недоступно в вашем регионе")
        else:
            raise Exception(f"❌ Ошибка при извлечении: {str(e)}")
    except Exception as e:
        raise Exception(f"❌ Неожиданная ошибка: {str(e)}")
    
    tracks = []
    is_playlist = False
    
    if "entries" in info:
        entries = info["entries"]
        if len(entries) > 1:
            is_playlist = True
            # Ограничиваем размер плейлиста
            if len(entries) > MAX_PLAYLIST_SIZE:
                entries = entries[:MAX_PLAYLIST_SIZE]
        
        for entry in entries:
            if entry:  # Проверяем что entry не None
                track = Track(
                    title=entry.get("title", "Неизвестный трек"),
                    url=entry.get("url", ""),
                    webpage_url=entry.get("webpage_url", ""),
                    duration=entry.get("duration"),
                    thumbnail=entry.get("thumbnail"),
                    uploader=entry.get("uploader"),
                    requester=requester
                )
                tracks.append(track)
    else:
        # Одиночный трек
        track = Track(
            title=info.get("title", "Неизвестный трек"),
            url=info.get("url", ""),
            webpage_url=info.get("webpage_url", ""),
            duration=info.get("duration"),
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader"),
            requester=requester
        )
        tracks.append(track)
    
    return tracks, is_playlist

def create_source(url: str, volume: float = 0.5):
    """Создать аудио источник"""
    return discord.FFmpegPCMAudio(
        url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options=f'-vn -filter:a "volume={volume}"'
    )

async def play_next(guild_id: int):
    """Воспроизвести следующий трек"""
    guild_data = get_guild_data(guild_id)
    vc = guild_data.voice_client
    
    if not vc:
        return
    
    next_track = guild_data.queue.next()
    if not next_track:
        # Очередь пуста
        embed = discord.Embed(
            title="🎵 Очередь завершена",
            description="Все треки воспроизведены!",
            color=discord.Color.blue()
        )
        
        if guild_data.now_playing_message:
            try:
                await guild_data.now_playing_message.edit(embed=embed, view=None)
            except:
                pass
        return
    
    try:
        source = create_source(next_track.url, guild_data.volume)
        guild_data.start_time = time.time()
        
        def after_track(error):
            if error:
                logging.error(f"Player error: {error}")
            else:
                # Планируем следующий трек
                asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
        
        vc.play(source, after=after_track)
        
        # Обновляем сообщение now playing
        embed = create_now_playing_embed(next_track, guild_data)
        view = MusicControlView(guild_id)
        
        if guild_data.now_playing_message:
            try:
                await guild_data.now_playing_message.edit(embed=embed, view=view)
            except:
                guild_data.now_playing_message = None
        
    except Exception as e:
        logging.error(f"Error playing track: {e}")
        # Пропускаем проблемный трек и пробуем следующий
        await play_next(guild_id)

@bot.event
async def on_ready():
    print(f"✅ Вошли как {bot.user}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/play | Улучшенный бот!"
    ))
    try:
        synced = await tree.sync()
        print(f"📡 Синхронизированы {len(synced)} команд(ы)")
    except Exception as e:
        print(f"Ошибка sync: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Обработка изменений голосового статуса"""
    if member.bot:
        return

    # Проверяем все голосовые клиенты бота
    for vc in bot.voice_clients:
        if vc.guild == member.guild and vc.channel:
            # Считаем количество не-ботов в канале
            human_members = [m for m in vc.channel.members if not m.bot]
            
            if len(human_members) == 0:
                # Бот остался один
                if vc.is_playing():
                    vc.pause()
                    print(f"⏸️ Музыка приостановлена в {vc.channel.name} - бот остался один")
                
                # Ждем 60 секунд и отключаемся если никто не вернулся
                await asyncio.sleep(60)
                
                # Проверяем еще раз
                human_members = [m for m in vc.channel.members if not m.bot]
                if len(human_members) == 0:
                    guild_data = get_guild_data(vc.guild.id)
                    guild_data.queue.clear()
                    await vc.disconnect()
                    guild_data.voice_client = None
                    print(f"⏹️ Отключился из {vc.channel.name} - никого нет")
            
            elif len(human_members) > 0 and vc.is_paused():
                # Кто-то вернулся - возобновляем воспроизведение
                vc.resume()
                print(f"▶️ Музыка возобновлена в {vc.channel.name}")

@tree.command(name="play", description="🎵 Воспроизвести музыку с YouTube")
@app_commands.describe(query="Ссылка на YouTube или поисковый запрос")
async def play(interaction: discord.Interaction, query: str):
    """Команда воспроизведения музыки"""
    await interaction.response.defer()
    
    guild_data = get_guild_data(interaction.guild.id)
    
    # Подключаемся к голосовому каналу если нужно
    if not guild_data.voice_client:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ Сначала зайдите в голосовой канал!")
            return
        
        try:
            guild_data.voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            await interaction.followup.send(f"❌ Не удалось подключиться к каналу: {e}")
            return
    
    # Показываем что обрабатываем запрос
    processing_embed = discord.Embed(
        title="🔍 Обработка запроса...",
        description=f"Ищу: `{query}`",
        color=discord.Color.orange()
    )
    await interaction.followup.send(embed=processing_embed)
    
    try:
        # Извлекаем информацию о треках
        tracks, is_playlist = await extract_track_info(query, interaction.user.display_name)
        
        if not tracks:
            await interaction.edit_original_response(
                embed=discord.Embed(title="❌ Ничего не найдено", color=discord.Color.red())
            )
            return
        
        # Добавляем треки в очередь
        was_empty = guild_data.queue.is_empty
        
        if is_playlist:
            guild_data.queue.add_multiple(tracks)
            result_embed = discord.Embed(
                title="📃 Плейлист добавлен!",
                description=f"**{len(tracks)}** треков добавлено в очередь",
                color=discord.Color.green()
            )
        else:
            guild_data.queue.add(tracks[0])
            result_embed = discord.Embed(
                title="🎵 Трек добавлен!",
                description=f"**{tracks[0].title}**",
                color=discord.Color.green()
            )
            if tracks[0].thumbnail:
                result_embed.set_thumbnail(url=tracks[0].thumbnail)
        
        result_embed.add_field(
            name="📍 Позиция в очереди",
            value=f"{len(guild_data.queue.tracks) - len(tracks) + 1}" if len(guild_data.queue.tracks) > len(tracks) else "Сейчас играет",
            inline=True
        )
        
        await interaction.edit_original_response(embed=result_embed)
        
        # Начинаем воспроизведение если очередь была пуста
        if was_empty and not guild_data.voice_client.is_playing():
            await play_next(interaction.guild.id)
            
            # Создаем новое сообщение для now playing
            if guild_data.queue.current:
                embed = create_now_playing_embed(guild_data.queue.current, guild_data)
                view = MusicControlView(interaction.guild.id)
                guild_data.now_playing_message = await interaction.followup.send(embed=embed, view=view)
    
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Ошибка",
            description=str(e),
            color=discord.Color.red()
        )
        await interaction.edit_original_response(embed=error_embed)

@tree.command(name="queue", description="📃 Показать очередь треков")
@app_commands.describe(page="Номер страницы (по 10 треков)")
async def queue_command(interaction: discord.Interaction, page: int = 1):
    """Показать очередь"""
    guild_data = get_guild_data(interaction.guild.id)
    
    if guild_data.queue.is_empty:
        embed = discord.Embed(
            title="📭 Очередь пуста",
            description="Используйте `/play` чтобы добавить музыку!",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    tracks = guild_data.queue.tracks
    total_pages = (len(tracks) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE
    
    if total_pages == 0:
        total_pages = 1
    
    if page < 1 or page > total_pages:
        await interaction.response.send_message(f"❌ Неверная страница. Доступно: 1-{total_pages}")
        return
    
    start_idx = (page - 1) * QUEUE_PAGE_SIZE
    end_idx = min(start_idx + QUEUE_PAGE_SIZE, len(tracks))
    
    embed = discord.Embed(
        title=f"📃 Очередь - Страница {page}/{total_pages}",
        color=discord.Color.blue()
    )
    
    # Текущий трек
    if guild_data.queue.current:
        embed.add_field(
            name="🎵 Сейчас играет",
            value=f"**{guild_data.queue.current.title}**\nЗапросил: {guild_data.queue.current.requester}",
            inline=False
        )
    
    # Треки в очереди
    if tracks:
        queue_text = ""
        for i in range(start_idx, end_idx):
            track = tracks[i]
            position = i + 1
            duration = f" ({track.duration_str})" if track.duration else ""
            queue_text += f"`{position}.` **{track.title[:40]}{'...' if len(track.title) > 40 else ''}**{duration}\n"
            queue_text += f"     └ {track.requester}\n"
        
        embed.add_field(
            name=f"📋 Следующие треки (всего: {len(tracks)})",
            value=queue_text or "Пусто",
            inline=False
        )
    
    # Статистика
    total_duration = sum(t.duration for t in tracks if t.duration)
    if total_duration > 0:
        hours = total_duration // 3600
        minutes = (total_duration % 3600) // 60
        duration_text = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"
        embed.set_footer(text=f"Общая длительность: {duration_text} | Режим: {guild_data.queue.loop_mode}")
    
    await interaction.response.send_message(embed=embed)

@tree.command(name="nowplaying", description="🎵 Показать текущий трек")
async def nowplaying(interaction: discord.Interaction):
    """Показать информацию о текущем треке"""
    guild_data = get_guild_data(interaction.guild.id)
    
    if not guild_data.queue.current:
        embed = discord.Embed(
            title="🎵 Ничего не играет",
            description="Используйте `/play` чтобы включить музыку!",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    embed = create_now_playing_embed(guild_data.queue.current, guild_data)
    view = MusicControlView(interaction.guild.id)
    
    await interaction.response.send_message(embed=embed, view=view)

# Остальные команды...
@tree.command(name="stop", description="⏹️ Остановить музыку и отключиться")
async def stop(interaction: discord.Interaction):
    guild_data = get_guild_data(interaction.guild.id)
    
    if not guild_data.voice_client:
        await interaction.response.send_message("❌ Бот не подключен к каналу")
        return
    
    guild_data.queue.clear()
    guild_data.voice_client.stop()
    await guild_data.voice_client.disconnect()
    guild_data.voice_client = None
    
    embed = discord.Embed(
        title="⏹️ Остановлено",
        description="Музыка остановлена и бот отключен",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="skip", description="⏭️ Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    guild_data = get_guild_data(interaction.guild.id)
    
    if not guild_data.voice_client or not guild_data.voice_client.is_playing():
        await interaction.response.send_message("❌ Нечего пропускать")
        return
    
    guild_data.voice_client.stop()
    
    embed = discord.Embed(
        title="⏭️ Трек пропущен",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    bot.run(TOKEN)
