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

async def update_player_message(guild_id, current_track=None):
    """Обновить сообщение плеера"""
    if guild_id not in player_messages:
        return
    
    try:
        message = player_messages[guild_id]
        queue = get_queue(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=message.guild)
        
        embed = discord.Embed(color=0x0099ff)
        
        if current_track:
            embed.title = "🎵 Сейчас играет"
            embed.description = f"**{current_track['title']}**\nДобавлено пользователем: {current_track['requester']}"
            
            if vc:
                if vc.is_playing():
                    embed.color = 0x00ff00  # Зеленый - играет
                    embed.set_footer(text="▶️ Воспроизводится")
                elif vc.is_paused():
                    embed.color = 0xffaa00  # Оранжевый - пауза
                    embed.set_footer(text="⏸️ На паузе")
        else:
            embed.title = "🎵 Плеер"
            embed.description = "Ничего не играет"
            embed.color = 0x808080  # Серый
            embed.set_footer(text="⏹️ Остановлено")
        
        # Показываем очередь (первые 5 треков)
        if queue:
            queue_text = ""
            for i, track in enumerate(queue[:5]):
                queue_text += f"{i+1}. {track['title'][:40]}{'...' if len(track['title']) > 40 else ''}\n"
            
            if len(queue) > 5:
                queue_text += f"... и еще {len(queue) - 5} треков"
            
            embed.add_field(
                name=f"📃 Очередь ({len(queue)} треков)",
                value=queue_text if queue_text else "Пусто",
                inline=False
            )
        
        await message.edit(embed=embed)
        
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
            await interaction.response.send_message("⚠️ Сначала зайдите в голосовой канал.")
            return

    if query.startswith("http://") or query.startswith("https://"):
        await interaction.response.send_message(f"🔗 Добавляю по ссылке: {query}")
    else:
        await interaction.response.send_message(f"🔍 Ищу: {query}")

    search_query = f"ytsearch1:{query}" if not (query.startswith("http://") or query.startswith("https://")) else query

    try:
        info = ytdl.extract_info(search_query, download=False)
    except Exception as e:
        # Улучшенная обработка ошибок age-restriction
        if is_age_restricted_error(e):
            await interaction.followup.send(
                "🔞 **Контент с возрастными ограничениями**\n"
                "❌ Этот контент недоступен.\n"
                "💡 Попробуйте найти другую версию: `cover`, `lyrics`, `instrumental`"
            )
        else:
            await interaction.followup.send(f"❌ Ошибка при обработке запроса: {str(e)}")
        return

    queue = get_queue(interaction.guild.id)

    if "entries" in info:
        for entry in info["entries"]:
            queue.append({
                "title": entry["title"],
                "url": entry["url"],
                "requester": interaction.user.name,
            })
        await interaction.followup.send(f"📃 Добавлен плейлист: {len(info['entries'])} треков.")
    else:
        track = {
            "title": info["title"],
            "url": info["url"],
            "requester": interaction.user.name,
        }
        queue.append(track)
        await interaction.followup.send(f"🎶 Добавлен трек: {track['title']}")

    # Создаем или обновляем плеер, если его нет
    if interaction.guild.id not in player_messages:
        embed = discord.Embed(
            title="🎵 Плеер",
            description="Инициализация...",
            color=0x0099ff
        )
        player_msg = await interaction.followup.send(embed=embed)
        player_messages[interaction.guild.id] = player_msg

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        await update_player_message(guild_id)
        return

    next_track = queue.pop(0)
    source = create_source(next_track["url"])

    def after_play(error):
        if error:
            print(f"Ошибка воспроизведения: {error}")
        bot.loop.create_task(play_next(vc, guild_id))

    vc.play(source, after=after_play)
    
    # Обновляем плеер с текущим треком
    await update_player_message(guild_id, next_track)

@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    log_command(interaction.user.name, "/pause")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Воспроизведение приостановлено.")
        await update_player_message(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.")

@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    log_command(interaction.user.name, "/resume")
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Воспроизведение продолжено.")
        await update_player_message(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Музыка не приостановлена.")

@tree.command(name="stop", description="Остановить воспроизведение и очистить очередь")
async def stop(interaction: discord.Interaction):
    log_command(interaction.user.name, "/stop")
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        await interaction.response.send_message("⏹️ Остановлено и отключено.")
        await update_player_message(interaction.guild.id)
    else:
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.")

@tree.command(name="skip", description="Пропустить текущую песню")
async def skip(interaction: discord.Interaction):
    log_command(interaction.user.name, "/skip")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Трек пропущен.")
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.")

@tree.command(name="queue", description="Показать текущую очередь")
async def queue_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/queue")
    queue = get_queue(interaction.guild.id)
    if queue:
        text = "\n".join([f"{i+1}. {song['title']} (от {song['requester']})" for i, song in enumerate(queue)])
        await interaction.response.send_message(f"📃 Очередь:\n{text}")
    else:
        await interaction.response.send_message("📭 Очередь пуста.")

@tree.command(name="help", description="Показать справку по командам")
async def help_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/help")
    await interaction.response.send_message("""📖 **Команды бота**

- `/play <url или запрос>` — Воспроизведение трека
- `/pause` — Приостановить текущую песню
- `/resume` — Продолжить воспроизведение
- `/stop` — Остановить воспроизведение и отключиться
- `/skip` — Пропустить текущую песню
- `/queue` — Показать текущую очередь
""")

bot.run(TOKEN)
