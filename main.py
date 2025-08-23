import os
import logging
import discord
import asyncio
import sys
import re
from discord.ext import commands
from discord import app_commands
import yt_dlp

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

# ✅ Правильное логирование с выводом в консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("VexelBot")

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
    }
    
    # Проверяем файл cookies
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ytdl_opts["cookiefile"] = cookies_file
        logger.info(f"✅ Используем YouTube cookies: {cookies_file}")
    
    # Проверяем браузерные cookies
    browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
    if browser_cookies and not cookies_file:
        try:
            browser, profile = browser_cookies.split(",", 1)
            ytdl_opts["cookiesfrombrowser"] = (browser.strip(), profile.strip())
            logger.info(f"✅ Используем cookies браузера: {browser} ({profile})")
        except ValueError:
            logger.error(f"❌ Неверный формат YOUTUBE_BROWSER_COOKIES: {browser_cookies}")
    
    return ytdl_opts

# Инициализируем ytdl с cookies
ytdl_opts = get_ytdl_opts()
ytdl = yt_dlp.YoutubeDL(ytdl_opts)

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

async def get_audio_url(track_url, title="Unknown"):
    """Получить аудио URL с fallback опциями"""
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
            
            ytdl_temp = yt_dlp.YoutubeDL(opts)
            info = await asyncio.to_thread(ytdl_temp.extract_info, track_url, False)
            
            if info and info.get("url"):
                logger.info(f"✅ Получен аудио URL для {title} с форматом: {format_selector}")
                return info["url"]
                
        except Exception as e:
            logger.warning(f"⚠️ Формат {format_selector} не работает для {title}: {str(e)}")
            continue
    
    raise Exception(f"Не удалось получить аудио URL для {title} со всеми форматами")

# Функции для ленивой загрузки треков
async def load_track_from_playlist(playlist_url, index):
    """Загрузить конкретный трек из плейлиста"""
    try:
        opts = get_ytdl_opts(extract_flat=False)  # Полная загрузка для конкретного трека
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = await asyncio.to_thread(ytdl_temp.extract_info, playlist_url, False)
        
        if "entries" in info and len(info["entries"]) > index:
            entry = info["entries"][index]
            return {
                "url": entry.get("url", ""),
                "webpage_url": entry.get("webpage_url", ""),
                "thumbnail": entry.get("thumbnail", ""),
                "title": entry.get("title", "Unknown Track")
            }
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки трека {index}: {e}")
        raise

class MusicPlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except (discord.HTTPException, discord.DiscordServerError):
            logger.error("Не удалось отправить defer, Discord API недоступен")
            return
        
        vc = interaction.guild.voice_client
        if not vc:
            try:
                await interaction.followup.send("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
                await create_new_player(interaction.guild.id, interaction.channel)
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
            try:
                await interaction.followup.send("❌ Ошибка управления воспроизведением.", ephemeral=True)
            except:
                pass
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except (discord.HTTPException, discord.DiscordServerError):
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
        except (discord.HTTPException, discord.DiscordServerError):
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
            status_icon = "⏳" if track.get("lazy_load") and not track.get("loaded") else "✅"
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
    
    guild = bot.get_guild(guild_id)
    vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    
    for item in view.children:
        if item.custom_id == "pause_resume":
            if vc and vc.is_playing():
                item.emoji = "⏸️"
            else:
                item.emoji = "▶️"
            break
    
    try:
        player_msg = await channel.send(embed=embed, view=view)
        player_messages[guild_id] = player_msg
        player_channels[guild_id] = channel
        return True
    except discord.HTTPException:
        return False

async def update_player_message(guild_id):
    """Обновить сообщение плеера"""
    if guild_id in player_messages:
        try:
            message = player_messages[guild_id]
            embed = create_player_embed(guild_id)
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
            
            await message.edit(embed=embed, view=view)
            
        except (discord.NotFound, discord.HTTPException):
            channel = player_channels.get(guild_id)
            if channel:
                await create_new_player(guild_id, channel)

@bot.event
async def on_ready():
    logger.info(f"✅ Вошли как {bot.user}")
    
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
    
    if cookies_file and os.path.exists(cookies_file):
        logger.info("🔐 YouTube cookies настроены (файл)")
    elif browser_cookies:
        logger.info("🔐 YouTube cookies настроены (браузер)")
    else:
        logger.info("ℹ️ YouTube cookies не настроены")
    
    logger.info(f"📊 Лимит плейлиста установлен: {MAX_PLAYLIST_SIZE} треков")
    logger.info(f"📊 Лимит очереди установлен: {MAX_QUEUE_SIZE} треков")
    
    bot.add_view(MusicPlayerView())
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/help"
    ))
    
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
            await update_player_buttons(member.guild.id)

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

    # Проверка лимита очереди
    queue = get_queue(interaction.guild.id)
    
    if len(queue) >= MAX_QUEUE_SIZE:
        await interaction.response.send_message(
            f"❌ **Очередь переполнена!**\n"
            f"💡 Максимум треков в очереди: {MAX_QUEUE_SIZE}\n"
            f"📊 Сейчас в очереди: {len(queue)} треков\n"
            f"🎵 Дождитесь окончания нескольких треков или используйте `/skip`",
            ephemeral=True
        )
        return

    try:
        await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)
    except (discord.HTTPException, discord.DiscordServerError):
        logger.error("Не удалось отправить ответ, Discord API недоступен")
        return

    search_query = f"ytsearch1:{clean_search_query(query)}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        logger.info(f"🔍 Обрабатываем запрос: {query}")
        
        # Для плейлистов используем extract_flat=True для быстрой загрузки
        opts = get_ytdl_opts(extract_flat=True)
        ytdl_temp = yt_dlp.YoutubeDL(opts)
        info = await asyncio.to_thread(ytdl_temp.extract_info, search_query, False)
        
        logger.info(f"✅ Получена информация от yt-dlp")
    except Exception as e:
        logger.error(f"❌ Ошибка yt-dlp: {str(e)}")
        try:
            error_msg = str(e).lower()
            if is_age_restricted_error(e):
                await interaction.edit_original_response(content="🔞 **Контент с возрастными ограничениями**\n❌ Этот контент недоступен.")
            elif "requested format is not available" in error_msg or "format not available" in error_msg:
                await interaction.edit_original_response(content="❌ **Формат недоступен**\n💡 Этот трек недоступен для воспроизведения.")
            elif "video unavailable" in error_msg or "private video" in error_msg:
                await interaction.edit_original_response(content="❌ **Видео недоступно**\n💡 Видео может быть приватным или удаленным.")
            else:
                await interaction.edit_original_response(content=f"❌ Ошибка при обработке запроса: {str(e)}")
        except:
            pass
        return

    if not info:
        logger.error("❌ yt-dlp вернул None")
        try:
            await interaction.edit_original_response(
                content="❌ **Не найдено**\n"
                       "Не удалось найти или загрузить информацию о треке.\n"
                       "💡 Попробуйте другой запрос или проверьте ссылку."
            )
        except:
            pass
        return

    if "entries" in info and info["entries"]:
        # Ленивая загрузка плейлистов
        total_entries = len(info["entries"])
        logger.info(f"📃 Найден плейлист с {total_entries} треков")
        
        remaining_slots = MAX_QUEUE_SIZE - len(queue)
        
        if remaining_slots <= 0:
            await interaction.edit_original_response(
                content=f"❌ **Очередь полная!** ({len(queue)}/{MAX_QUEUE_SIZE})\n"
                        f"🎵 Освободите место перед добавлением новых треков."
            )
            return
        
        max_to_add = min(MAX_PLAYLIST_SIZE, remaining_slots)
        entries_to_process = info["entries"][:max_to_add]
        
        logger.info(f"📦 Обрабатываем {len(entries_to_process)} из {total_entries} треков")
        
        valid_entries = []
        for entry in entries_to_process:
            if entry and entry.get("title") and (entry.get("url") or entry.get("webpage_url")):
                valid_entries.append(entry)
        
        if not valid_entries:
            await interaction.edit_original_response(
                content="❌ **Нет доступных треков**\n"
                       "В плейлисте нет треков доступных для воспроизведения."
            )
            return
        
        added_count = 0
        for i, entry in enumerate(valid_entries):
            queue.append({
                "title": entry.get("title", f"Track {i+1}"),
                "playlist_url": search_query,
                "playlist_index": i,
                "lazy_load": True,
                "loaded": False,
                "requester": interaction.user.name,
            })
            added_count += 1
        
        try:
            message_parts = []
            message_parts.append(f"📃 **Добавлено {added_count} из {total_entries} треков**")
            
            if total_entries > max_to_add:
                if remaining_slots < MAX_PLAYLIST_SIZE:
                    message_parts.append(f"💡 Ограничено лимитом очереди: {remaining_slots} свободных мест")
                else:
                    message_parts.append(f"💡 Ограничено лимитом плейлиста: {MAX_PLAYLIST_SIZE} треков")
            
            message_parts.append(f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE} треков")
            
            await interaction.edit_original_response(content="\n".join(message_parts))
            logger.info(f"✅ Плейлист добавлен: {added_count}/{total_entries} треков")
        except:
            pass
    elif info.get("title"):
        if len(queue) >= MAX_QUEUE_SIZE:
            await interaction.edit_original_response(
                content=f"❌ **Очередь полная!** ({len(queue)}/{MAX_QUEUE_SIZE})"
            )
            return
            
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
        logger.info(f"🎶 Добавлен трек: {track['title']}")
        try:
            await interaction.edit_original_response(
                content=f"🎶 **Добавлен трек:** {track['title']}\n"
                        f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE} треков"
            )
        except:
            pass
    else:
        logger.error("❌ Неполные данные от yt-dlp")
        try:
            await interaction.edit_original_response(
                content="❌ **Неполные данные**\n"
                       "Получены неполные данные о треке.\n"
                       "💡 Возможно видео недоступно или заблокировано."
            )
        except:
            pass
        return

    player_channels[interaction.guild.id] = interaction.channel
    await create_new_player(interaction.guild.id, interaction.channel)

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

async def play_next(vc, guild_id):
    """Воспроизвести следующий трек с обработкой ошибок соединения"""
    queue = get_queue(guild_id)
    if not queue:
        current_tracks[guild_id] = None
        logger.info("📭 Очередь пуста")
        channel = player_channels.get(guild_id)
        if channel:
            await create_new_player(guild_id, channel)
        return

    # Проверяем состояние voice client
    if not vc or not vc.is_connected():
        logger.warning("⚠️ Voice client отключен, прекращаем воспроизведение")
        await cleanup_guild_data(guild_id)
        return

    next_track = queue.pop(0)
    current_tracks[guild_id] = next_track
    logger.info(f"⏭️ Следующий трек: {next_track['title']}")
    
    # Ленивая загрузка
    if next_track.get("lazy_load") and not next_track.get("loaded"):
        try:
            logger.info(f"⏳ Загружаем полную информацию для: {next_track['title']}")
            full_info = await load_track_from_playlist(
                next_track["playlist_url"], 
                next_track["playlist_index"]
            )
            next_track.update(full_info)
            next_track["loaded"] = True
            logger.info(f"✅ Загружена полная информация для: {next_track['title']}")
        except Exception as e:
            logger.error(f"❌ Не удалось загрузить трек: {e}")
            await play_next(vc, guild_id)
            return
    
    try:
        # Получаем аудио URL с улучшенной обработкой
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
                # Проверяем тип ошибки
                error_str = str(error).lower()
                if "4006" in error_str or "connection" in error_str:
                    logger.error("🚨 Обнаружена ошибка соединения Discord voice (4006)")
                    # Попытка переподключения будет в следующем цикле
            
            # Планируем следующий трек только если voice client еще подключен
            if vc and vc.is_connected():
                bot.loop.create_task(play_next(vc, guild_id))
            else:
                logger.warning("⚠️ Voice client отключен, прекращаем цикл воспроизведения")
                bot.loop.create_task(cleanup_guild_data(guild_id))
        
        vc.play(source, after=after_play)
        logger.info(f"🎵 Играет: {next_track['title']}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка воспроизведения {next_track['title']}: {str(e)}")
        # Пробуем следующий трек
        await play_next(vc, guild_id)
        return
    
    # Обновляем плеер
    channel = player_channels.get(guild_id)
    if channel:
        await create_new_player(guild_id, channel)

@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    log_command(interaction.user.name, "/pause")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Воспроизведение приостановлено.", ephemeral=True)
        await update_player_buttons(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)

@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    log_command(interaction.user.name, "/resume")
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Воспроизведение продолжено.", ephemeral=True)
        await update_player_buttons(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Музыка не приостановлена.", ephemeral=True)

@tree.command(name="stop", description="Остановить воспроизведение и очистить очередь")
async def stop(interaction: discord.Interaction):
    log_command(interaction.user.name, "/stop")
    vc = interaction.guild.voice_client
    if vc:
        await interaction.response.send_message("⏹️ Останавливаем...", ephemeral=True)
        await safe_voice_disconnect(vc, interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)

@tree.command(name="skip", description="Пропустить текущую песню")
async def skip(interaction: discord.Interaction):
    log_command(interaction.user.name, "/skip")
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Трек пропущен.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)

@tree.command(name="queue", description="Показать текущую очередь")
async def queue_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/queue")
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
        status_icon = "⏳" if track.get("lazy_load") and not track.get("loaded") else "✅"
        title_display = track['title'][:45] + ('...' if len(track['title']) > 45 else '')
        queue_text += f"`{i+1}.` {status_icon} **{title_display}**\n*Заказал: {track['requester']}*\n\n"
    
    if len(queue) > 10:
        queue_text += f"*... и еще {len(queue) - 10} треков*"
    
    embed.description = queue_text
    embed.set_footer(text=f"Всего треков в очереди: {len(queue)}/{MAX_QUEUE_SIZE}")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="status", description="Показать статус бота и очереди")
async def status(interaction: discord.Interaction):
    log_command(interaction.user.name, "/status")
    queue = get_queue(interaction.guild.id)
    vc = interaction.guild.voice_client
    current_track = current_tracks.get(interaction.guild.id)
    
    embed = discord.Embed(
        title="🤖 Статус бота",
        color=0x2f3136
    )
    
    # Статус подключения и воспроизведения
    if vc and vc.is_connected():
        channel_name = vc.channel.name
        if vc.is_playing():
            embed.add_field(name="🔊 Статус", value=f"🎵 Играет в канале: **{channel_name}**", inline=False)
        elif vc.is_paused():
            embed.add_field(name="🔊 Статус", value=f"⏸️ На паузе в канале: **{channel_name}**", inline=False)
        else:
            embed.add_field(name="🔊 Статус", value=f"⏹️ Подключен к каналу: **{channel_name}**", inline=False)
    else:
        embed.add_field(name="🔊 Статус", value="🔌 Не подключен к голосовому каналу", inline=False)
    
    # Текущий трек
    if current_track:
        embed.add_field(
            name="🎵 Сейчас играет", 
            value=f"**{current_track['title']}**\n*Заказал: {current_track['requester']}*", 
            inline=False
        )
    
    # Статистика очереди
    embed.add_field(name="📊 Очередь", value=f"**{len(queue)}/{MAX_QUEUE_SIZE}** треков", inline=True)
    embed.add_field(name="⚙️ Лимит плейлиста", value=f"**{MAX_PLAYLIST_SIZE}** треков", inline=True)
    
    # Статистика ленивой загрузки
    if queue:
        lazy_count = sum(1 for track in queue if track.get("lazy_load") and not track.get("loaded"))
        if lazy_count > 0:
            embed.add_field(name="⏳ К загрузке", value=f"**{lazy_count}** треков", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="help", description="Показать справку по командам")
async def help_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/help")
    
    embed = discord.Embed(
        title="📖 Команды бота",
        description="Управляйте музыкой с помощью команд или кнопок плеера",
        color=0x2f3136
    )
    
    embed.add_field(
        name="🎵 Основные команды",
        value=(
            "`/play <запрос>` — Воспроизвести трек\n"
            "`/pause` — Приостановить\n"
            "`/resume` — Продолжить\n"
            "`/stop` — Остановить и отключиться\n"
            "`/skip` — Пропустить трек\n"
            "`/queue` — Показать очередь\n"
            "`/status` — Статус бота"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎛️ Интерактивный плеер",
        value=(
            "⏸️/▶️ — Пауза/Воспроизведение\n"
            "⏭️ — Пропустить трек\n"
            "⏹️ — Остановить\n"
            "📃 — Показать очередь"
        ),
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ Лимиты и особенности",
        value=(
            f"📊 Максимум треков в очереди: **{MAX_QUEUE_SIZE}**\n"
            f"📃 Максимум треков из плейлиста: **{MAX_PLAYLIST_SIZE}**\n"
            "⚡ Быстрая загрузка плейлистов (Lazy Loading)\n"
            "👤 Отображение заказчиков треков"
        ),
        inline=False
    )
    
    embed.set_footer(text="💡 Все ответы команд видны только вам!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(TOKEN)
