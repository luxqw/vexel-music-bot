import os
import logging
import discord
import asyncio
import random
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


async def connect_to_voice_channel_with_retry(channel, max_retries=3):
    """Connect to voice channel with retry logic and exponential backoff."""
    for attempt in range(max_retries):
        try:
            vc = await asyncio.wait_for(channel.connect(), timeout=10.0)
            logging.info(f"Successfully connected to voice channel {channel.name} on attempt {attempt + 1}")
            return vc
        except asyncio.TimeoutError:
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            logging.warning(f"Voice connection timeout on attempt {attempt + 1}, retrying in {wait_time:.1f}s")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
        except discord.ConnectionClosed as e:
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            logging.warning(f"Voice connection closed (code: {e.code}) on attempt {attempt + 1}, retrying in {wait_time:.1f}s")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
        except Exception as e:
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            logging.error(f"Voice connection error on attempt {attempt + 1}: {e}, retrying in {wait_time:.1f}s")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
    
    raise discord.ConnectionClosed(None, 4006, "Failed to connect after maximum retries")


async def ensure_voice_client_connected(vc):
    """Ensure voice client is still connected and functional."""
    if not vc or not vc.is_connected():
        return False
    
    try:
        # Test if the connection is alive
        return vc.channel is not None
    except Exception:
        return False


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
    """Handle voice state updates with improved error handling and cleanup."""
    if member.bot:
        return

    try:
        vc = discord.utils.get(bot.voice_clients, guild=member.guild)
        
        # If no voice client, nothing to do
        if not vc or not vc.channel:
            return
            
        # Check if bot is alone in the channel
        if len(vc.channel.members) == 1:
            try:
                # Pause if playing
                if vc.is_playing():
                    vc.pause()
                    print("⏸️ Музыка приостановлена, так как бот остался один в канале.")
                
                # Wait for 60 seconds to see if anyone joins back
                await asyncio.sleep(60)
                
                # Re-check if still alone (someone might have joined during sleep)
                updated_vc = discord.utils.get(bot.voice_clients, guild=member.guild)
                if updated_vc and updated_vc.channel and len(updated_vc.channel.members) == 1:
                    # Clean up and disconnect
                    if updated_vc.is_playing():
                        updated_vc.stop()
                    
                    # Clear the queue for this guild
                    if member.guild.id in queues:
                        queues[member.guild.id] = []
                    
                    await updated_vc.disconnect()
                    print(f"⏹️ Отключение из канала {updated_vc.channel.name} на сервере {member.guild.name}")
                elif updated_vc and updated_vc.is_paused():
                    # Someone joined back, resume if paused
                    updated_vc.resume()
                    print("▶️ Музыка возобновлена - пользователи вернулись в канал.")
                    
            except discord.ConnectionClosed:
                # Connection already closed, clean up queues
                if member.guild.id in queues:
                    queues[member.guild.id] = []
                print(f"🔌 Соединение было закрыто для сервера {member.guild.name}")
            except Exception as e:
                logging.error(f"Error in voice state cleanup: {e}")
                # Try to disconnect anyway
                try:
                    if vc.is_connected():
                        await vc.disconnect()
                except:
                    pass
                        
    except Exception as e:
        logging.error(f"Error in on_voice_state_update: {e}")
        # Don't let errors in this handler crash the bot


@tree.command(name="play", description="Воспроизвести музыку или плейлист с YouTube")
@app_commands.describe(query="Ссылка или запрос")
async def play(interaction: discord.Interaction, query: str):
    log_command(interaction.user.name, "/play")

    vc = interaction.guild.voice_client
    
    # Check if we need to connect to a voice channel
    if not vc or not await ensure_voice_client_connected(vc):
        if interaction.user.voice and interaction.user.voice.channel:
            try:
                # Use retry logic for connecting
                vc = await connect_to_voice_channel_with_retry(interaction.user.voice.channel)
            except discord.ConnectionClosed as e:
                await interaction.response.send_message(
                    f"❌ Не удалось подключиться к голосовому каналу. WebSocket ошибка: {e.code}"
                )
                return
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Ошибка подключения к голосовому каналу: {str(e)}"
                )
                return
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

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)


async def play_next(vc, guild_id):
    """Play next song with improved error handling for voice connections."""
    try:
        # Check if voice client is still connected
        if not await ensure_voice_client_connected(vc):
            logging.warning(f"Voice client disconnected for guild {guild_id}, clearing queue")
            queues[guild_id] = []
            return

        queue = get_queue(guild_id)
        if not queue:
            return

        next_track = queue.pop(0)
        
        try:
            source = create_source(next_track["url"])
            
            def after_playing(error):
                if error:
                    logging.error(f"Player error: {error}")
                    # Try to continue with next track despite error
                bot.loop.create_task(play_next(vc, guild_id))
            
            vc.play(source, after=after_playing)
            
        except Exception as e:
            logging.error(f"Error creating audio source for {next_track['title']}: {e}")
            # Skip this track and try the next one
            await play_next(vc, guild_id)
            
    except discord.ConnectionClosed as e:
        logging.warning(f"Voice connection closed (code: {e.code}) while playing next track for guild {guild_id}")
        # Clear the queue since we can't continue playing
        queues[guild_id] = []
    except Exception as e:
        logging.error(f"Unexpected error in play_next for guild {guild_id}: {e}")
        # Try to continue despite the error
        await asyncio.sleep(1)  # Brief delay before retry
        try:
            await play_next(vc, guild_id)
        except:
            # If we still can't continue, clear the queue
            queues[guild_id] = []


@tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    log_command(interaction.user.name, "/pause")
    vc = interaction.guild.voice_client
    
    if not vc or not await ensure_voice_client_connected(vc):
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.")
        return
        
    if vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Воспроизведение приостановлено.")
    else:
        await interaction.response.send_message("❌ Сейчас ничего не играет.")


@tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    log_command(interaction.user.name, "/resume")
    vc = interaction.guild.voice_client
    
    if not vc or not await ensure_voice_client_connected(vc):
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.")
        return
        
    if vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Воспроизведение продолжено.")
    else:
        await interaction.response.send_message("❌ Музыка не приостановлена.")


@tree.command(name="stop", description="Остановить воспроизведение и очистить очередь")
async def stop(interaction: discord.Interaction):
    log_command(interaction.user.name, "/stop")
    vc = interaction.guild.voice_client
    
    if vc:
        try:
            if vc.is_playing():
                vc.stop()
            if vc.is_connected():
                await vc.disconnect()
        except discord.ConnectionClosed:
            # Already disconnected, that's fine
            pass
        except Exception as e:
            logging.error(f"Error stopping voice client: {e}")
        
        # Clear queue regardless of connection status
        queues[interaction.guild.id] = []
        await interaction.response.send_message("⏹️ Остановлено и отключено.")
    else:
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.")


@tree.command(name="skip", description="Пропустить текущую песню")
async def skip(interaction: discord.Interaction):
    log_command(interaction.user.name, "/skip")
    vc = interaction.guild.voice_client
    
    if not vc or not await ensure_voice_client_connected(vc):
        await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.")
        return
        
    if vc.is_playing():
        vc.stop()  # This will trigger the after callback which will play next
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
