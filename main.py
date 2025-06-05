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
from datetime import datetime

# Конфигурация
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = 100

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# Глобальные данные
guild_data = {}

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
    webpage_url: str = ""
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None
    requester: str = "Unknown"
    requested_at: datetime = None
    
    def __post_init__(self):
        if self.requested_at is None:
            self.requested_at = datetime.utcnow()
        if not self.webpage_url:
            self.webpage_url = self.url
    
    @property
    def duration_str(self) -> str:
        """Форматированная длительность"""
        if not self.duration:
            return "00:00"
        
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes:02d}:{seconds:02d}"

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
    player_message: Optional[discord.Message] = None
    volume: int = 100
    start_time: Optional[float] = None
    is_paused: bool = False
    
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
    "noplaylist": False,
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

def create_player_embed(track: Track, guild_data: GuildMusicData) -> discord.Embed:
    """Создать embed для плеера"""
    embed = discord.Embed(
        title="Сейчас играет",
        color=0x2F3136
    )
    
    # Основная информация о треке
    track_info = f"**{track.title}**\n{track.uploader or 'Неизвестный исполнитель'}"
    embed.description = track_info
    
    # Миниатюра
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    
    # Длительность
    embed.add_field(
        name="Длительность:",
        value=track.duration_str,
        inline=False
    )
    
    # Источник
    embed.add_field(
        name="",
        value=f"YouTube (добавлен: @{track.requester})",
        inline=False
    )
    
    # Треков в очереди
    queue_count = len(guild_data.queue.tracks)
    embed.add_field(
        name="",
        value=f"**Треков в очереди:** {queue_count}",
        inline=False
    )
    
    return embed

def create_idle_embed() -> discord.Embed:
    """Embed когда музыка не играет"""
    embed = discord.Embed(
        title="Плеер остановлен",
        description="Используйте /play чтобы включить музыку",
        color=0x2F3136
    )
    return embed

async def send_temp_message(interaction: discord.Interaction, content: str, embed: discord.Embed = None, ephemeral: bool = True, delete_after: int = 5):
    """Отправить временное сообщение которое удалится через указанное время"""
    try:
        if embed:
            message = await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            message = await interaction.followup.send(content, ephemeral=ephemeral)
        
        # Если ephemeral=True, Discord сам удалит сообщение, поэтому не пытаемся удалить
        if not ephemeral and delete_after > 0:
            await asyncio.sleep(delete_after)
            try:
                await message.delete()
            except:
                pass
    except:
        # Fallback на обычную отправку
        if embed:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

class MusicPlayerView(discord.ui.View):
    """Упрощенные кнопки управления музыкой"""
    
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.setup_buttons()
    
    def setup_buttons(self):
        """Настройка кнопок"""
        guild_data_obj = get_guild_data(self.guild_id)
        
        # Очищаем кнопки
        self.clear_items()
        
        # Ряд 1: Shuffle, Volume down, Volume up, Repeat
        shuffle_btn = discord.ui.Button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
        shuffle_btn.callback = self.shuffle_callback
        self.add_item(shuffle_btn)
        
        vol_down_btn = discord.ui.Button(emoji="🔉", style=discord.ButtonStyle.secondary, row=0)
        vol_down_btn.callback = self.volume_down_callback
        self.add_item(vol_down_btn)
        
        vol_up_btn = discord.ui.Button(emoji="🔊", style=discord.ButtonStyle.secondary, row=0)
        vol_up_btn.callback = self.volume_up_callback
        self.add_item(vol_up_btn)
        
        # Кнопка повтора
        loop_emoji = "🔁"
        if guild_data_obj.queue.loop_mode == "track":
            loop_emoji = "🔂"
        repeat_btn = discord.ui.Button(emoji=loop_emoji, style=discord.ButtonStyle.secondary, row=0)
        repeat_btn.callback = self.repeat_callback
        self.add_item(repeat_btn)
        
        # Ряд 2: Play/Pause, Stop, Next
        # Объединенная кнопка Play/Pause
        if guild_data_obj.voice_client and guild_data_obj.voice_client.is_playing():
            play_pause_btn = discord.ui.Button(emoji="⏸️", style=discord.ButtonStyle.secondary, row=1)
        else:
            play_pause_btn = discord.ui.Button(emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
        play_pause_btn.callback = self.play_pause_callback
        self.add_item(play_pause_btn)
        
        stop_btn = discord.ui.Button(emoji="⏹️", style=discord.ButtonStyle.secondary, row=1)
        stop_btn.callback = self.stop_callback
        self.add_item(stop_btn)
        
        next_btn = discord.ui.Button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=1)
        next_btn.callback = self.next_callback
        self.add_item(next_btn)
        
        # Ряд 3: Queue, Leave
        queue_btn = discord.ui.Button(emoji="📋", style=discord.ButtonStyle.secondary, row=2)
        queue_btn.callback = self.queue_callback
        self.add_item(queue_btn)
        
        leave_btn = discord.ui.Button(emoji="🚪", style=discord.ButtonStyle.danger, row=2)
        leave_btn.callback = self.leave_callback
        self.add_item(leave_btn)

    async def shuffle_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        if len(guild_data_obj.queue.tracks) < 2:
            await interaction.response.send_message("Недостаточно треков для перемешивания", ephemeral=True)
            return
        
        guild_data_obj.queue.shuffle()
        await interaction.response.send_message("Очередь перемешана", ephemeral=True)

    async def volume_down_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        guild_data_obj.volume = max(0, guild_data_obj.volume - 10)
        await self.update_player_message(interaction)
        await interaction.response.send_message(f"Громкость: {guild_data_obj.volume}%", ephemeral=True)

    async def volume_up_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        guild_data_obj.volume = min(200, guild_data_obj.volume + 10)
        await self.update_player_message(interaction)
        await interaction.response.send_message(f"Громкость: {guild_data_obj.volume}%", ephemeral=True)

    async def repeat_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        
        if guild_data_obj.queue.loop_mode == "none":
            guild_data_obj.queue.loop_mode = "track"
            mode_text = "Повтор трека включен"
        elif guild_data_obj.queue.loop_mode == "track":
            guild_data_obj.queue.loop_mode = "queue"
            mode_text = "Повтор очереди включен"
        else:
            guild_data_obj.queue.loop_mode = "none"
            mode_text = "Повтор выключен"
        
        await self.update_player_message(interaction)
        await interaction.response.send_message(mode_text, ephemeral=True)

    async def play_pause_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        vc = guild_data_obj.voice_client
        
        if not vc:
            await interaction.response.send_message("Бот не подключен к каналу", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            guild_data_obj.is_paused = True
            await self.update_player_message(interaction)
            await interaction.response.send_message("Пауза", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            guild_data_obj.is_paused = False
            await self.update_player_message(interaction)
            await interaction.response.send_message("Продолжено", ephemeral=True)
        else:
            await interaction.response.send_message("Нечего воспроизводить", ephemeral=True)

    async def stop_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        vc = guild_data_obj.voice_client
        
        if not vc:
            await interaction.response.send_message("Бот не подключен", ephemeral=True)
            return
        
        vc.stop()
        guild_data_obj.queue.clear()
        await self.update_player_message(interaction, stopped=True)
        await interaction.response.send_message("Воспроизведение остановлено", ephemeral=True)

    async def next_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        vc = guild_data_obj.voice_client
        
        if not vc or not vc.is_playing():
            await interaction.response.send_message("Нечего пропускать", ephemeral=True)
            return
        
        vc.stop()
        await interaction.response.send_message("Трек пропущен", ephemeral=True)

    async def queue_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        
        if guild_data_obj.queue.is_empty:
            await interaction.response.send_message("Очередь пуста", ephemeral=True)
            return
        
        tracks = guild_data_obj.queue.tracks[:10]
        queue_text = ""
        
        for i, track in enumerate(tracks, 1):
            queue_text += f"`{i}.` **{track.title[:40]}{'...' if len(track.title) > 40 else ''}**\n"
        
        embed = discord.Embed(
            title="Очередь треков",
            description=queue_text,
            color=0x2F3136
        )
        
        if len(guild_data_obj.queue.tracks) > 10:
            embed.set_footer(text=f"Показано 10 из {len(guild_data_obj.queue.tracks)} треков")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def leave_callback(self, interaction: discord.Interaction):
        guild_data_obj = get_guild_data(self.guild_id)
        vc = guild_data_obj.voice_client
        
        if not vc:
            await interaction.response.send_message("Бот не подключен", ephemeral=True)
            return
        
        guild_data_obj.queue.clear()
        vc.stop()
        await vc.disconnect()
        guild_data_obj.voice_client = None
        
        # Обновляем плеер
        if guild_data_obj.player_message:
            embed = create_idle_embed()
            view = MusicPlayerView(self.guild_id)
            try:
                await guild_data_obj.player_message.edit(embed=embed, view=view)
            except:
                pass
        
        await interaction.response.send_message("Бот отключен от канала", ephemeral=True)

    async def update_player_message(self, interaction: discord.Interaction, stopped: bool = False):
        """Обновить сообщение плеера"""
        guild_data_obj = get_guild_data(self.guild_id)
        
        if not guild_data_obj.player_message:
            return
        
        if stopped or not guild_data_obj.queue.current:
            embed = create_idle_embed()
        else:
            embed = create_player_embed(guild_data_obj.queue.current, guild_data_obj)
        
        # Обновляем кнопки
        self.setup_buttons()
        
        try:
            await guild_data_obj.player_message.edit(embed=embed, view=self)
        except:
            pass

async def extract_track_info(url_or_query: str, requester: str) -> tuple[List[Track], bool]:
    """Извлечь информацию о треке(ах) асинхронно"""
    
    def extract():
        try:
            if not (url_or_query.startswith("http://") or url_or_query.startswith("https://")):
                search_query = f"ytsearch1:{url_or_query}"
            else:
                search_query = url_or_query
            
            info = ytdl.extract_info(search_query, download=False)
            return info
        except Exception as e:
            raise e
    
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, extract)
    except yt_dlp.utils.ExtractorError as e:
        error_msg = str(e).lower()
        if "private" in error_msg:
            raise Exception("Видео недоступно (приватное или удалено)")
        elif "region" in error_msg:
            raise Exception("Видео недоступно в вашем регионе")
        else:
            raise Exception(f"Ошибка при извлечении: {str(e)}")
    except Exception as e:
        raise Exception(f"Неожиданная ошибка: {str(e)}")
    
    tracks = []
    is_playlist = False
    
    if "entries" in info:
        entries = info["entries"]
        entries = [entry for entry in entries if entry is not None]
        
        if len(entries) > 1:
            is_playlist = True
            if len(entries) > MAX_PLAYLIST_SIZE:
                entries = entries[:MAX_PLAYLIST_SIZE]
        
        for entry in entries:
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

def create_source(url: str, volume: int = 100):
    """Создать аудио источник"""
    volume_float = volume / 100.0
    return discord.FFmpegPCMAudio(
        url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options=f'-vn -filter:a "volume={volume_float}"'
    )

async def play_next(guild_id: int):
    """Воспроизвести следующий трек"""
    guild_data_obj = get_guild_data(guild_id)
    vc = guild_data_obj.voice_client
    
    if not vc:
        return
    
    next_track = guild_data_obj.queue.next()
    if not next_track:
        # Очередь пуста - обновляем плеер
        if guild_data_obj.player_message:
            embed = create_idle_embed()
            view = MusicPlayerView(guild_id)
            try:
                await guild_data_obj.player_message.edit(embed=embed, view=view)
            except:
                pass
        return
    
    try:
        source = create_source(next_track.url, guild_data_obj.volume)
        guild_data_obj.start_time = time.time()
        guild_data_obj.is_paused = False
        
        def after_track(error):
            if error:
                logging.error(f"Player error: {error}")
            else:
                asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
        
        vc.play(source, after=after_track)
        
        # Обновляем плеер
        if guild_data_obj.player_message:
            embed = create_player_embed(next_track, guild_data_obj)
            view = MusicPlayerView(guild_id)
            try:
                await guild_data_obj.player_message.edit(embed=embed, view=view)
            except:
                pass
        
    except Exception as e:
        logging.error(f"Error playing track: {e}")
        await play_next(guild_id)

@bot.event
async def on_ready():
    print(f"Вошли как {bot.user}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/play"
    ))
    try:
        synced = await tree.sync()
        print(f"Синхронизированы {len(synced)} команд")
    except Exception as e:
        print(f"Ошибка sync: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Обработка изменений голосового статуса"""
    if member.bot:
        return

    for vc in bot.voice_clients:
        if vc.guild == member.guild and vc.channel:
            human_members = [m for m in vc.channel.members if not m.bot]
            
            if len(human_members) == 0:
                if vc.is_playing():
                    vc.pause()
                
                await asyncio.sleep(60)
                
                human_members = [m for m in vc.channel.members if not m.bot]
                if len(human_members) == 0:
                    guild_data_obj = get_guild_data(vc.guild.id)
                    guild_data_obj.queue.clear()
                    await vc.disconnect()
                    guild_data_obj.voice_client = None
            
            elif len(human_members) > 0 and vc.is_paused():
                vc.resume()

@tree.command(name="play", description="Воспроизвести музыку")
@app_commands.describe(query="Ссылка на YouTube или поисковый запрос")
async def play(interaction: discord.Interaction, query: str):
    """Команда воспроизведения музыки"""
    await interaction.response.defer()
    
    guild_data_obj = get_guild_data(interaction.guild.id)
    
    # Подключаемся к голосовому каналу
    if not guild_data_obj.voice_client:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("Сначала зайдите в голосовой канал", ephemeral=True)
            return
        
        try:
            guild_data_obj.voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            await interaction.followup.send(f"Не удалось подключиться к каналу: {e}", ephemeral=True)
            return
    
    try:
        # Извлекаем информацию о треках
        tracks, is_playlist = await extract_track_info(query, interaction.user.display_name)
        
        if not tracks:
            embed = discord.Embed(
                title="Ошибка",
                description="Ничего не найдено по вашему запросу",
                color=0xFF0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Добавляем треки в очередь
        was_empty = guild_data_obj.queue.is_empty
        
        if is_playlist:
            guild_data_obj.queue.add_multiple(tracks)
            success_text = f"Добавлен плейлист: {len(tracks)} треков"
        else:
            guild_data_obj.queue.add(tracks[0])
            success_text = f"Добавлен трек: {tracks[0].title}"
        
        # Отправляем уведомление о добавлении
        await interaction.followup.send(success_text, ephemeral=True)
        
        # Создаем или обновляем плеер
        if not guild_data_obj.player_message:
            if guild_data_obj.queue.current:
                embed = create_player_embed(guild_data_obj.queue.current, guild_data_obj)
            else:
                embed = create_idle_embed()
            
            view = MusicPlayerView(interaction.guild.id)
            guild_data_obj.player_message = await interaction.followup.send(embed=embed, view=view)
        
        # Начинаем воспроизведение если нужно
        if was_empty and not guild_data_obj.voice_client.is_playing():
            await play_next(interaction.guild.id)
    
    except Exception as e:
        embed = discord.Embed(
            title="Ошибка",
            description=str(e),
            color=0xFF0000
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="player", description="Показать музыкальный плеер")
async def player(interaction: discord.Interaction):
    """Показать плеер"""
    guild_data_obj = get_guild_data(interaction.guild.id)
    
    if guild_data_obj.queue.current:
        embed = create_player_embed(guild_data_obj.queue.current, guild_data_obj)
    else:
        embed = create_idle_embed()
    
    view = MusicPlayerView(interaction.guild.id)
    
    # Если уже есть сообщение плеера, обновляем его
    if guild_data_obj.player_message:
        try:
            await guild_data_obj.player_message.edit(embed=embed, view=view)
            await interaction.response.send_message("Плеер обновлен", ephemeral=True)
        except:
            guild_data_obj.player_message = await interaction.response.send_message(embed=embed, view=view)
    else:
        guild_data_obj.player_message = await interaction.response.send_message(embed=embed, view=view)

@tree.command(name="help", description="Показать справку по командам")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Справка по командам",
        description="Музыкальный бот для Discord",
        color=0x2F3136
    )
    
    embed.add_field(
        name="Команды",
        value="""
/play <запрос> - Добавить музыку
/player - Показать плеер с кнопками управления
/help - Показать эту справку
        """,
        inline=False
    )
    
    embed.add_field(
        name="Управление",
        value="Используйте кнопки в плеере для управления воспроизведением",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
