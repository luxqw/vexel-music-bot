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

logging.basicConfig(filename="bot.log", level=logging.INFO)
logger = logging.getLogger("VexelBot")

ytdl_opts = {
    "format": "bestaudio",
    "noplaylist": False,
}
ytdl = yt_dlp.YoutubeDL(ytdl_opts)


def log_command(user, command):
    logging.info(f"{user} использовал {command}")


def get_queue(guild_id):
    return queues.setdefault(guild_id, [])


def create_source(video_url, title="Unknown"):
    """
    Create audio source for Discord with fresh stream URL extraction
    """
    try:
        # Create a fresh yt-dlp instance with optimal options for stream extraction
        ytdl_stream_opts = {
            "format": "bestaudio",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        
        logger.info(f"🔍 Получаем свежий stream URL для: {title}")
        ytdl_stream = yt_dlp.YoutubeDL(ytdl_stream_opts)
        
        # Extract fresh stream info
        stream_info = ytdl_stream.extract_info(video_url, download=False)
        
        if stream_info and stream_info.get("url"):
            stream_url = stream_info["url"]
            logger.info(f"✅ Получен свежий stream URL для: {title}")
            
            return discord.FFmpegPCMAudio(
                stream_url,
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                options='-vn'
            )
        else:
            logger.error(f"❌ Не удалось получить stream URL для: {title}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка при получении stream URL для '{title}': {str(e)}")
        return None


@bot.event
async def on_ready():
    print(f"✅ Вошли как {bot.user}")
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

        await asyncio.sleep(60)  
        if len(vc.channel.members) == 1:  
            await vc.disconnect()
            print(f"⏹️ Отключение из канала {vc.channel.name} на сервере {member.guild.name}")


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
        logger.info(f"🔍 Обрабатываем запрос: {search_query}")
        info = ytdl.extract_info(search_query, download=False)
        logger.info("✅ Получена информация от yt-dlp")
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка при обработке запроса: {str(e)}")
        return

    queue = get_queue(interaction.guild.id)

    if "entries" in info:
        for entry in info["entries"]:
            queue.append({
                "title": entry["title"],
                "original_url": entry.get("original_url", entry.get("webpage_url", entry["url"])),  # Store original URL
                "requester": interaction.user.name,
            })
        await interaction.followup.send(f"📃 Добавлен плейлист: {len(info['entries'])} треков.")
    else:
        track = {
            "title": info["title"],
            "original_url": info.get("original_url", info.get("webpage_url", info["url"])),  # Store original URL
            "requester": interaction.user.name,
        }
        queue.append(track)
        logger.info(f"🎶 Добавлен трек: {track['title']}")
        await interaction.followup.send(f"🎶 Добавлен трек: {track['title']}")

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)


async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        return

    next_track = queue.pop(0)
    logger.info(f"⏭ Следующий трек: {next_track['title']}")
    
    # Extract fresh stream URL for the track
    source = create_source(next_track["original_url"], next_track["title"])
    
    if source is None:
        logger.error(f"❌ Не удалось получить аудио для: {next_track['title']}")
        # Try the next track in queue if this one failed
        if queue:
            await play_next(vc, guild_id)
        return
    
    def after_playing(error):
        if error:
            logger.error(f"❌ Ошибка при воспроизведении: {error}")
        bot.loop.create_task(play_next(vc, guild_id))
    
    try:
        vc.play(source, after=after_playing)
        logger.info(f"🎵 Играет: {next_track['title']}")
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске воспроизведения: {str(e)}")
        # Try the next track if this one failed
        if queue:
            await play_next(vc, guild_id)


@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    log_command(interaction.user.name, "/pause")
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Воспроизведение приостановлено.")
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.")


@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    log_command(interaction.user.name, "/resume")
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Воспроизведение продолжено.")
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
