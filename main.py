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
player_messages = {}  # Track player message IDs per guild

logging.basicConfig(filename="bot.log", level=logging.INFO)

ytdl_opts = {
    "format": "bestaudio",
    "noplaylist": False,
}
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


async def load_track_details(track_url):
    """Load full track details for lazy-loaded tracks"""
    try:
        full_ytdl = yt_dlp.YoutubeDL(ytdl_opts)
        return await asyncio.to_thread(full_ytdl.extract_info, track_url, False)
    except Exception as e:
        logging.error(f"Error loading track details for {track_url}: {e}")
        return None


async def cleanup_old_player_messages(guild_id, channel):
    """Clean up old player messages in the guild"""
    if guild_id in player_messages:
        for message_id in player_messages[guild_id]:
            try:
                old_message = await channel.fetch_message(message_id)
                await old_message.delete()
            except discord.NotFound:
                # Message already deleted, ignore
                pass
            except Exception as e:
                logging.warning(f"Error deleting old player message {message_id}: {e}")
        player_messages[guild_id] = []


async def send_public_notification(channel, message):
    """Send a public notification message and track it for cleanup"""
    try:
        public_message = await channel.send(message)
        guild_id = channel.guild.id
        if guild_id not in player_messages:
            player_messages[guild_id] = []
        player_messages[guild_id].append(public_message.id)
        return public_message
    except Exception as e:
        logging.error(f"Error sending public notification: {e}")
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
            # Clean up player messages before disconnecting
            text_channel = member.guild.system_channel or member.guild.text_channels[0] if member.guild.text_channels else None
            if text_channel:
                await cleanup_old_player_messages(member.guild.id, text_channel)
            
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
            await interaction.response.send_message("⚠️ Сначала зайдите в голосовой канал.", ephemeral=True)
            return

    if query.startswith("http://") or query.startswith("https://"):
        await interaction.response.send_message(f"🔗 Добавляю по ссылке: {query}", ephemeral=True)
    else:
        await interaction.response.send_message(f"🔍 Ищу: {query}", ephemeral=True)

    search_query = f"ytsearch1:{query}" if not (query.startswith("http://") or query.startswith("https://")) else query
    
    # Clean up old player messages before adding new content
    await cleanup_old_player_messages(interaction.guild.id, interaction.channel)

    try:
        # Check if this is a playlist URL for lazy loading
        is_playlist = ("playlist" in query.lower() and ("youtube.com" in query.lower() or "youtu.be" in query.lower())) or "list=" in query
        
        if is_playlist:
            # Use lazy loading for playlists
            ytdl_flat = yt_dlp.YoutubeDL({**ytdl_opts, 'extract_flat': True})
            info = await asyncio.to_thread(ytdl_flat.extract_info, search_query, False)
        else:
            # Use full extraction for single tracks
            info = await asyncio.to_thread(ytdl.extract_info, search_query, False)
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка при обработке запроса: {str(e)}", ephemeral=True)
        return

    queue = get_queue(interaction.guild.id)

    if "entries" in info:
        # Handle playlist
        track_count = 0
        for i, entry in enumerate(info["entries"]):
            if entry:  # Some entries can be None
                track_info = {
                    "title": entry.get("title", f"Track {i+1}"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                    "requester": interaction.user.name,
                    "lazy_load": is_playlist,
                    "loaded": not is_playlist
                }
                queue.append(track_info)
                track_count += 1
        
        # Send ephemeral confirmation to command user
        await interaction.followup.send(f"📃 Добавлен плейлист: {track_count} треков.", ephemeral=True)
        
        # Send public notification
        playlist_title = info.get("title", "Плейлист")
        await send_public_notification(
            interaction.channel,
            f"📃 **{interaction.user.display_name}** добавил плейлист: **{playlist_title}** ({track_count} треков)"
        )
    else:
        # Handle single track
        track = {
            "title": info["title"],
            "url": info["url"],
            "requester": interaction.user.name,
            "lazy_load": False,
            "loaded": True
        }
        queue.append(track)
        
        # Send ephemeral confirmation to command user
        await interaction.followup.send(f"🎶 Добавлен трек: {track['title']}", ephemeral=True)
        
        # Send public notification
        await send_public_notification(
            interaction.channel,
            f"🎶 **{interaction.user.display_name}** добавил трек: **{track['title']}**"
        )

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)


async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        return

    next_track = queue.pop(0)
    
    # Handle lazy-loaded tracks
    if next_track.get("lazy_load", False) and not next_track.get("loaded", False):
        # Load full track details
        track_info = await load_track_details(next_track["url"])
        if track_info:
            next_track["url"] = track_info["url"]
            next_track["loaded"] = True
        else:
            # Skip this track if loading failed, try next
            await play_next(vc, guild_id)
            return
    
    source = create_source(next_track["url"])
    vc.play(source, after=lambda e: bot.loop.create_task(play_next(vc, guild_id)))


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
        
        # Clean up player messages
        await cleanup_old_player_messages(interaction.guild.id, interaction.channel)
        
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
