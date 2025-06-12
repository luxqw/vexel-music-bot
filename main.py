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
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.send_message("⏸️ Воспроизведение приостановлено.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            await interaction.response.send_message("▶️ Воспроизведение продолжено.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)
            return
        
        await update_player_message(interaction.guild.id)
        # Обновляем кнопки
        if interaction.guild.id in player_messages:
            try:
                await player_messages[interaction.guild.id].edit(view=self)
            except:
                pass
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)
            return
        
        vc.stop()
        await interaction.response.send_message("⏭️ Трек пропущен.", ephemeral=True)
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return
        
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        current_tracks[interaction.guild.id] = None
        
        await interaction.response.send_message("⏹️ Остановлено и отключено.", ephemeral=True)
        await update_player_message(interaction.guild.id)
    
    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.success, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(interaction.guild.id)
        
        if not queue:
            await interaction.response.send_message("📭 Очередь пуста.", ephemeral=True)
            return
        
        # Создаем красивый список очереди
        embed = discord.Embed(
            title="📃 Очередь треков",
            color=0x0099ff
        )
        
        # Показываем первые 10 треков
        queue_text = ""
        for i, track in enumerate(queue[:10]):
            queue_text += f"`{i+1}.` **{track['title'][:50]}{'...' if len(track['title']) > 50 else ''}**\n└ *Добавлено: {track['requester']}*\n\n"
        
        if len(queue) > 10:
            queue_text += f"*... и еще {len(queue) - 10} треков*"
        
        embed.description = queue_text if queue_text else "Очередь пуста"
        embed.set_footer(text=f"Всего треков в очереди: {len(queue)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="shuffle")
    async def shuffle_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        import random
        queue = get_queue(interaction.guild.id)
        
        if len(queue) < 2:
            await interaction.response.send_message("❌ В очереди недостаточно треков для перемешивания.", ephemeral=True)
            return
        
        random.shuffle(queue)
        await interaction.response.send_message(f"🔀 Очередь перемешана! ({len(queue)} треков)", ephemeral=True)
        await update_player_message(interaction.guild.id)

def create_player_embed(guild_id):
    """Создать embed для плеера"""
    current_track = current_tracks.get(guild_id)
    queue = get_queue(guild_id)
    
    embed = discord.Embed()
    
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
        embed.add_field(
            name="🔗 Ссылка", 
            value=f"[YouTube]({current_track.get('webpage_url', 'N/A')})", 
            inline=True
        )
        
        # Определяем статус
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        if vc:
            if vc.is_playing():
                embed.color = 0x00ff00  # Зеленый
                embed.set_footer(text="▶️ Воспроизводится", icon_url="https://cdn.discordapp.com/emojis/899330171486187530.gif")
            elif vc.is_paused():
                embed.color = 0xffaa00  # Оранжевый
                embed.set_footer(text="⏸️ На паузе")
            else:
                embed.color = 0xff0000  # Красный
                embed.set_footer(text="⏹️ Остановлено")
        else:
            embed.color = 0x808080  # Серый
            embed.set_footer(text="🔌 Не подключен")
    else:
        embed.title = "🎵 Музыкальный плеер"
        embed.description = "*Ничего не играет*"
        embed.color = 0x808080
        embed.set_footer(text="⏹️ Готов к воспроизведению")
        
        if queue:
            embed.add_field(
                name="📃 В очереди", 
                value=f"{len(queue)} треков", 
                inline=True
            )
    
    # Добавляем миниатюру (если есть)
    if current_track and 'thumbnail' in current_track:
        embed.set_thumbnail(url=current_track['thumbnail'])
    
    return embed

async def update_player_message(guild_id):
    """Обновить сообщение плеера"""
    if guild_id not in player_messages:
        return
    
    try:
        message = player_messages[guild_id]
        embed = create_player_embed(guild_id)
        
        # Обновляем кнопки в зависимости от состояния
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
        
    except discord.NotFound:
        # Сообщение было удалено, убираем из словаря
        if guild_id in player_messages:
            del player_messages[guild_id]
    except Exception as e:
        print(f"Ошибка обновления плеера: {e}")

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
            await update_player_message(member.guild.id)

        await asyncio.sleep(60)  
        if len(vc.channel.members) == 1:  
            await vc.disconnect()
            print(f"⏹️ Отключение из канала {vc.channel.name} на сервере {member.guild.name}")
            await update_player_message(member.guild.id)

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

    # Отвечаем сразу, чтобы избежать таймаута
    await interaction.response.send_message("🔍 Обрабатываю запрос...", ephemeral=True)

    search_query = f"ytsearch1:{query}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        info = ytdl.extract_info(search_query, download=False)
    except Exception as e:
        if is_age_restricted_error(e):
            await interaction.edit_original_response(content="🔞 **Контент с возрастными ограничениями**\n❌ Этот контент недоступен.\n💡 Попробуйте найти другую версию: `cover`, `lyrics`, `instrumental`")
        else:
            await interaction.edit_original_response(content=f"❌ Ошибка при обработке запроса: {str(e)}")
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
        
        await interaction.edit_original_response(content=f"📃 **Добавлен плейлист: {added_count} треков**")
    else:
        track = {
            "title": info["title"],
            "url": info["url"],
            "webpage_url": info.get("webpage_url", ""),
            "thumbnail": info.get("thumbnail", ""),
            "requester": interaction.user.name,
        }
        queue.append(track)
        await interaction.edit_original_response(content=f"🎶 **Добавлен трек:** {track['title']}")

    # Создаем плеер, если его нет
    if interaction.guild.id not in player_messages:
        embed = create_player_embed(interaction.guild.id)
        view = MusicPlayerView()
        player_msg = await interaction.followup.send(embed=embed, view=view)
        player_messages[interaction.guild.id] = player_msg

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        current_tracks[guild_id] = None
        await update_player_message(guild_id)
        return

    next_track = queue.pop(0)
    current_tracks[guild_id] = next_track
    source = create_source(next_track["url"])

    def after_play(error):
        if error:
            print(f"Ошибка воспроизведения: {error}")
        bot.loop.create_task(play_next(vc, guild_id))

    vc.play(source, after=after_play)
    await update_player_message(guild_id)

@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    log_command(interaction.user.name, "/pause")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Воспроизведение приостановлено.", ephemeral=True)
        await update_player_message(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)

@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    log_command(interaction.user.name, "/resume")
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Воспроизведение продолжено.", ephemeral=True)
        await update_player_message(interaction.guild.id)
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
        await update_player_message(interaction.guild.id)
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
        color=0x0099ff
    )
    
    queue_text = ""
    for i, track in enumerate(queue[:10]):
        queue_text += f"`{i+1}.` **{track['title'][:50]}{'...' if len(track['title']) > 50 else ''}**\n└ *Добавлено: {track['requester']}*\n\n"
    
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
        color=0x0099ff
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
            "📃 — Показать очередь\n"
            "🔀 — Перемешать очередь"
        ),
        inline=False
    )
    
    embed.set_footer(text="💡 Все ответы команд видны только вам!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(TOKEN)
