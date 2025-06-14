import os
import logging
import discord
import asyncio
import sys
from discord.ext import commands
from discord import app_commands
import yt_dlp

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYLIST_SIZE = int(os.getenv("MAX_PLAYLIST_SIZE", "15"))

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
def get_ytdl_opts():
    """Получить ytdl_opts с поддержкой cookies"""
    ytdl_opts = {
        "format": "bestaudio",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "extract_flat": False,
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

class MusicPlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            await create_new_player(interaction.guild.id, interaction.channel)
            return
        
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
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.followup.send("❌ Сейчас ничего не играет.", ephemeral=True)
            return
        
        vc.stop()
        await interaction.followup.send("⏭️ Скип", ephemeral=True)
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return
        
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        current_tracks[interaction.guild.id] = None
        
        await interaction.followup.send("⏹️ Стоп", ephemeral=True)
        
        # ✅ безопасное удаление плеера
        await delete_old_player(interaction.guild.id)
        player_channels.pop(interaction.guild.id, None)
    
    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.secondary, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(interaction.guild.id)
        
        if not queue:
            await interaction.response.send_message("📭 Очередь пуста.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📃 Очередь треков",
            color=0x2f3136
        )
        
        queue_text = ""
        for i, track in enumerate(queue[:10]):
            queue_text += f"`{i+1}.` **{track['title'][:45]}{'...' if len(track['title']) > 45 else ''}**\n*Добавлено: {track['requester']}*\n\n"
        
        if len(queue) > 10:
            queue_text += f"*... и еще {len(queue) - 10} треков*"
        
        embed.description = queue_text if queue_text else "Очередь пуста"
        embed.set_footer(text=f"Всего треков в очереди: {len(queue)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
            value=f"{len(queue)} треков", 
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
                value=f"{len(queue)} треков", 
                inline=True
            )
        
        embed.add_field(
            name="🔊 Статус", 
            value="⏹️ Остановлен", 
            inline=True
        )
    
    return embed

async def delete_old_player(guild_id):
    """✅ Безопасное удаление старого плеера"""
    if guild_id in player_messages:
        try:
            await player_messages[guild_id].delete()
        except:
            pass
        # ✅ Используем pop() вместо del для безопасности
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
    """Создать новый плеер в конце канала"""
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
    """Обновить сообщение плеера или создать новый"""
    channel = player_channels.get(guild_id)
    
    if guild_id in player_messages:
        try:
            message = player_messages[guild_id]
            
            if channel:
                recent_messages = [msg async for msg in channel.history(limit=5)]
                if message not in recent_messages:
                    await create_new_player(guild_id, channel)
                    return
            
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
            await create_new_player(guild_id, channel)
    else:
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
        if len(vc.channel.members) == 1:  
            await vc.disconnect()
            logger.info(f"⏹️ Отключение из канала {vc.channel.name} на сервере {member.guild.name}")
            await delete_old_player(member.guild.id)
            player_channels.pop(member.guild.id, None)

@tree.command(name="play", description="Воспроизвести музыку или плейлист с YouTube")
@app_commands.describe(query="Ссылка или запрос")
async def play(interaction: discord.Interaction, query: str):
    log_command(interaction.user.name, "/play")

    vc = interaction.guild.voice_client
    if not vc:
        if interaction.user.voice and interaction.user.voice.channel:
            vc = await interaction.user.voice.channel.connect()
        else:
            await interaction.response.send_message("⚠️ Сначала зайдите в голосовой канал.", ephemeral=True)
            return

    try:
        await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)
    except (discord.HTTPException, discord.DiscordServerError):
        logger.error("Не удалось отправить ответ, Discord API недоступен")
        return

    search_query = f"ytsearch1:{query}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        logger.info(f"🔍 Обрабатываем запрос: {query}")
        info = await asyncio.to_thread(ytdl.extract_info, search_query, False)
        logger.info(f"✅ Получена информация от yt-dlp")
    except Exception as e:
        logger.error(f"❌ Ошибка yt-dlp: {str(e)}")
        try:
            if is_age_restricted_error(e):
                await interaction.edit_original_response(content="🔞 **Контент с возрастными ограничениями**\n❌ Этот контент недоступен.\n💡 Попробуйте найти другую версию: `cover`, `lyrics`, `instrumental`")
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

    queue = get_queue(interaction.guild.id)

    if "entries" in info and info["entries"]:
        # ✅ ИСПРАВЛЕНИЕ: Правильное ограничение плейлиста до 15 треков
        total_entries = len(info["entries"])
        logger.info(f"📃 Найден плейлист с {total_entries} треков")
        
        # ✅ КРИТИЧНО: правильно ограничиваем количество треков
        entries_to_process = info["entries"][:MAX_PLAYLIST_SIZE]
        logger.info(f"📦 Обрабатываем {len(entries_to_process)} из {total_entries} треков (лимит: {MAX_PLAYLIST_SIZE})")
        
        added_count = 0
        # ✅ ВАЖНО: используем ТОЛЬКО entries_to_process, НЕ info["entries"]!
        for entry in entries_to_process:
            if entry and entry.get("title") and entry.get("url"):
                queue.append({
                    "title": entry["title"],
                    "url": entry["url"],
                    "webpage_url": entry.get("webpage_url", ""),
                    "thumbnail": entry.get("thumbnail", ""),
                    "requester": interaction.user.name,
                })
                added_count += 1
                logger.info(f"  ➕ Добавлен трек {added_count}/{len(entries_to_process)}: {entry['title'][:50]}...")
        
        if added_count == 0:
            logger.error("❌ Нет валидных треков в плейлисте")
            try:
                await interaction.edit_original_response(content="❌ **Плейлист пуст**\nВ плейлисте нет доступных треков.")
            except:
                pass
            return
        
        try:
            if total_entries > MAX_PLAYLIST_SIZE:
                await interaction.edit_original_response(
                    content=f"📃 **Добавлено {added_count} из {total_entries} треков**\n"
                           f"💡 Лимит плейлиста: {MAX_PLAYLIST_SIZE} треков"
                )
                logger.info(f"✅ Плейлист обрезан: {added_count}/{total_entries} треков")
            else:
                await interaction.edit_original_response(content=f"📃 **Добавлен плейлист: {added_count} треков**")
                logger.info(f"✅ Плейлист добавлен полностью: {added_count} треков")
        except:
            pass
    elif info.get("title") and info.get("url"):
        track = {
            "title": info["title"],
            "url": info["url"],
            "webpage_url": info.get("webpage_url", ""),
            "thumbnail": info.get("thumbnail", ""),
            "requester": interaction.user.name,
        }
        queue.append(track)
        logger.info(f"🎶 Добавлен трек: {track['title']}")
        try:
            await interaction.edit_original_response(content=f"🎶 **Добавлен трек:** {track['title']}")
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
    queue = get_queue(guild_id)
    if not queue:
        current_tracks[guild_id] = None
        logger.info("📭 Очередь пуста")
        channel = player_channels.get(guild_id)
        if channel:
            await create_new_player(guild_id, channel)
        return

    next_track = queue.pop(0)
    current_tracks[guild_id] = next_track
    logger.info(f"⏭️ Следующий трек: {next_track['title']}")
    
    try:
        audio_info = await asyncio.to_thread(ytdl.extract_info, next_track["url"], False)
        if audio_info and audio_info.get("url"):
            source = create_source(audio_info["url"])
            
            def after_play(error):
                if error:
                    logger.error(f"❌ Ошибка воспроизведения: {error}")
                bot.loop.create_task(play_next(vc, guild_id))
            
            vc.play(source, after=after_play)
            logger.info(f"🎵 Играет: {next_track['title']}")
        else:
            logger.error(f"❌ Не удалось получить аудио для: {next_track['title']}")
            await play_next(vc, guild_id)
    except Exception as e:
        logger.error(f"❌ Ошибка воспроизведения {next_track['title']}: {str(e)}")
        await play_next(vc, guild_id)
    
    # Создаем новый плеер с новым треком
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
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        current_tracks[interaction.guild.id] = None
        await interaction.response.send_message("⏹️ Остановлено и отключено.", ephemeral=True)
        
        await delete_old_player(interaction.guild.id)
        # ✅ безопасное удаление
        player_channels.pop(interaction.guild.id, None)
    else:
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)

@tree.command(name="skip", description="Пропустить текущую песню")
async def skip(interaction: discord.Interaction):
    log_command(interaction.user.name, "/skip")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Трек пропущен.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)

@tree.command(name="queue", description="Показать текущую очередь")
async def queue_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/queue")
    queue = get_queue(interaction.guild.id)
    
    if not queue:
        await interaction.response.send_message("📭 Очередь пуста.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📃 Очередь треков",
        color=0x2f3136
    )
    
    queue_text = ""
    for i, track in enumerate(queue[:10]):
        queue_text += f"`{i+1}.` **{track['title'][:45]}{'...' if len(track['title']) > 45 else ''}**\n*Добавлено: {track['requester']}*\n\n"
    
    if len(queue) > 10:
        queue_text += f"*... и еще {len(queue) - 10} треков*"
    
    embed.description = queue_text
    embed.set_footer(text=f"Всего треков в очереди: {len(queue)}")
    
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
            "`/queue` — Показать очередь"
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
        name="ℹ️ Лимиты",
        value=f"Максимум треков из плейлиста: {MAX_PLAYLIST_SIZE}",
        inline=False
    )
    
    embed.set_footer(text="💡 Все ответы команд видны только вам!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(TOKEN)
