import os
import logging
import discord
import asyncio
from discord.ext import commands
from discord import app_commands
import yt_dlp

TOKEN = os.getenv("DISCORD_TOKEN")

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

logging.basicConfig(filename="bot.log", level=logging.INFO)

# Добавляем поддержку cookies
def get_ytdl_opts():
    """Получить ytdl_opts с поддержкой cookies"""
    ytdl_opts = {
        "format": "bestaudio",
        "noplaylist": False,
    }
    
    # Проверяем файл cookies
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ytdl_opts["cookiefile"] = cookies_file
        print(f"✅ Используем YouTube cookies: {cookies_file}")
    
    # Проверяем браузерные cookies
    browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
    if browser_cookies and not cookies_file:
        try:
            browser, profile = browser_cookies.split(",", 1)
            ytdl_opts["cookiesfrombrowser"] = (browser.strip(), profile.strip())
            print(f"✅ Используем cookies браузера: {browser} ({profile})")
        except ValueError:
            print(f"❌ Неверный формат YOUTUBE_BROWSER_COOKIES: {browser_cookies}")
    
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
        # ИСПРАВЛЕНО: Сначала defer, потом выполняем действия
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("❌ Бот не подключен к голосовому каналу.")
            await create_new_player(interaction.guild.id, interaction.channel)
            return
        
        if vc.is_playing():
            vc.pause()
            await interaction.followup.send("⏸️ Пауза")
        elif vc.is_paused():
            vc.resume()
            await interaction.followup.send("▶️ Продолжаем")
        else:
            await interaction.followup.send("❌ Сейчас ничего не играет.")
            return
        
        # Быстро обновляем только кнопки без пересоздания плеера
        await update_player_buttons(interaction.guild.id)
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.followup.send("❌ Сейчас ничего не играет.")
            return
        
        vc.stop()
        await interaction.followup.send("⏭️ Скип")
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("❌ Бот не подключен к голосовому каналу.")
            return
        
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        current_tracks[interaction.guild.id] = None
        
        await interaction.followup.send("⏹️ Стоп")
        
        # Удаляем плеер полностью
        await delete_old_player(interaction.guild.id)
        if interaction.guild.id in player_channels:
            del player_channels[interaction.guild.id]
    
    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.secondary, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(interaction.guild.id)
        
        if not queue:
            await interaction.response.send_message("📭 Очередь пуста.", ephemeral=True)
            return
        
        # Создаем красивый список очереди
        embed = discord.Embed(
            title="📃 Очередь треков",
            color=0x2f3136
        )
        
        # Показываем первые 10 треков
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
    
    # Нейтральная цветовая схема
    embed = discord.Embed(color=0x2f3136)  # Discord dark theme color
    
    if current_track:
        embed.title = "🎵 Сейчас играет"
        embed.description = f"**{current_track['title']}**"
        
        # Создаем поля в одну строку для компактности
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
        
        # Определяем статус
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
            
        # Добавляем миниатюру, если есть
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
    """Удалить старый плеер"""
    if guild_id in player_messages:
        try:
            await player_messages[guild_id].delete()
        except:
            pass  # Игнорируем ошибки удаления
        del player_messages[guild_id]

async def update_player_buttons(guild_id):
    """Быстро обновить только кнопки плеера без пересоздания"""
    if guild_id not in player_messages:
        return
    
    try:
        message = player_messages[guild_id]
        view = MusicPlayerView()
        
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        # Обновляем кнопку паузы/воспроизведения
        for item in view.children:
            if item.custom_id == "pause_resume":
                if vc and vc.is_playing():
                    item.emoji = "⏸️"
                else:
                    item.emoji = "▶️"
                break
        
        await message.edit(view=view)
    except:
        pass  # Если не получилось обновить, ничего страшного

async def create_new_player(guild_id, channel):
    """Создать новый плеер в конце канала"""
    if not channel:
        return
    
    # Удаляем старый плеер
    await delete_old_player(guild_id)
    
    # Создаем новый embed и view
    embed = create_player_embed(guild_id)
    view = MusicPlayerView()
    
    # Обновляем кнопки в зависимости от состояния
    guild = bot.get_guild(guild_id)
    vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
    
    # Обновляем иконку паузы/воспроизведения
    for item in view.children:
        if item.custom_id == "pause_resume":
            if vc and vc.is_playing():
                item.emoji = "⏸️"
            else:
                item.emoji = "▶️"
            break
    
    try:
        # Отправляем новый плеер
        player_msg = await channel.send(embed=embed, view=view)
        player_messages[guild_id] = player_msg
        player_channels[guild_id] = channel
        return True
    except discord.HTTPException:
        return False

async def update_player_message(guild_id):
    """Обновить сообщение плеера или создать новый"""
    # Проверяем, есть ли канал для плеера
    channel = player_channels.get(guild_id)
    
    if guild_id in player_messages:
        try:
            message = player_messages[guild_id]
            
            # Проверяем, в последних ли 5 сообщениях плеер
            if channel:
                recent_messages = [msg async for msg in channel.history(limit=5)]
                if message not in recent_messages:
                    # Плеер далеко, создаем новый
                    await create_new_player(guild_id, channel)
                    return
            
            # Пробуем обновить существующий
            embed = create_player_embed(guild_id)
            view = MusicPlayerView()
            
            guild = bot.get_guild(guild_id)
            vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
            
            # Обновляем иконку паузы/воспроизведения
            for item in view.children:
                if item.custom_id == "pause_resume":
                    if vc and vc.is_playing():
                        item.emoji = "⏸️"
                    else:
                        item.emoji = "▶️"
                    break
            
            await message.edit(embed=embed, view=view)
            
        except (discord.NotFound, discord.HTTPException):
            # Создаем новый плеер
            await create_new_player(guild_id, channel)
    else:
        # Создаем новый плеер
        await create_new_player(guild_id, channel)

@bot.event
async def on_ready():
    print(f"✅ Вошли как {bot.user}")
    
    # Проверяем настройки cookies при запуске
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
    browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
    
    if cookies_file and os.path.exists(cookies_file):
        print("🔐 YouTube cookies настроены (файл)")
    elif browser_cookies:
        print("🔐 YouTube cookies настроены (браузер)")
    else:
        print("ℹ️ YouTube cookies не настроены")
    
    # Добавляем persistent view
    bot.add_view(MusicPlayerView())
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="/help"
    ))
    
    try:
        synced = await tree.sync()
        print(f"📡 Синхронизированы {len(synced)} команд(ы)")
    except Exception as e:
        print(f"Ошибка sync: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    vc = discord.utils.get(bot.voice_clients, guild=member.guild)

    if vc and vc.channel and len(vc.channel.members) == 1: 
        if vc.is_playing():
            vc.pause() 
            print("⏸️ Музыка приостановлена, так как бот остался один в канале.")
            # Быстро обновляем кнопки
            await update_player_buttons(member.guild.id)

        await asyncio.sleep(60)  
        if len(vc.channel.members) == 1:  
            await vc.disconnect()
            print(f"⏹️ Отключение из канала {vc.channel.name} на сервере {member.guild.name}")
            # Полностью удаляем плеер при отключении
            await delete_old_player(member.guild.id)
            if member.guild.id in player_channels:
                del player_channels[member.guild.id]

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

    # ИСПРАВЛЕНО: Обработка ошибок Discord API
    try:
        await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)
    except (discord.HTTPException, discord.DiscordServerError):
        # Если Discord API недоступен, пропускаем ответ
        print("Не удалось отправить ответ, Discord API недоступен")
        return

    search_query = f"ytsearch1:{query}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        info = ytdl.extract_info(search_query, download=False)
    except Exception as e:
        try:
            if is_age_restricted_error(e):
                await interaction.edit_original_response(content="🔞 **Контент с возрастными ограничениями**\n❌ Этот контент недоступен.\n💡 Попробуйте найти другую версию: `cover`, `lyrics`, `instrumental`")
            else:
                await interaction.edit_original_response(content=f"❌ Ошибка при обработке запроса: {str(e)}")
        except:
            pass  # Игнорируем ошибки редактирования
        return

    queue = get_queue(interaction.guild.id)

    if "entries" in info:
        added_count = 0
        for entry in info["entries"]:
            queue.append({
                "title": entry["title"],
                "url": entry["url"],
                "webpage_url": entry.get("webpage_url", ""),
                "thumbnail": entry.get("thumbnail", ""),
                "requester": interaction.user.name,
            })
            added_count += 1
        
        try:
            await interaction.edit_original_response(content=f"📃 **Добавлен плейлист: {added_count} треков**")
        except:
            pass
    else:
        track = {
            "title": info["title"],
            "url": info["url"],
            "webpage_url": info.get("webpage_url", ""),
            "thumbnail": info.get("thumbnail", ""),
            "requester": interaction.user.name,
        }
        queue.append(track)
        try:
            await interaction.edit_original_response(content=f"🎶 **Добавлен трек:** {track['title']}")
        except:
            pass

    # Сохраняем канал плеера
    player_channels[interaction.guild.id] = interaction.channel
    
    # Создаем новый плеер
    await create_new_player(interaction.guild.id, interaction.channel)

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        current_tracks[guild_id] = None
        # Создаем новый плеер с обновленной информацией
        channel = player_channels.get(guild_id)
        if channel:
            await create_new_player(guild_id, channel)
        return

    next_track = queue.pop(0)
    current_tracks[guild_id] = next_track
    source = create_source(next_track["url"])

    def after_play(error):
        if error:
            print(f"Ошибка воспроизведения: {error}")
        bot.loop.create_task(play_next(vc, guild_id))

    vc.play(source, after=after_play)
    
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
        
        # Полностью удаляем плеер при stop команде
        await delete_old_player(interaction.guild.id)
        if interaction.guild.id in player_channels:
            del player_channels[interaction.guild.id]
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
    
    embed.set_footer(text="💡 Все ответы команд видны только вам!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(TOKEN)
