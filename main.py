import os
import logging
import discord
import asyncio
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


@bot.event
async def on_ready():
    print(f"✅ Вошли как {bot.user}")
    print(f"📊 Лимит плейлиста установлен: {MAX_PLAYLIST_SIZE} треков")
    print(f"📊 Лимит очереди установлен: {MAX_QUEUE_SIZE} треков")
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
        info = ytdl.extract_info(search_query, download=False)
    except Exception as e:
        await interaction.followup.send_message(f"❌ Ошибка при обработке запроса: {str(e)}")
        return

    queue = get_queue(interaction.guild.id)
    
    # ✅ НОВАЯ ПРОВЕРКА: Лимит общей очереди
    if len(queue) >= MAX_QUEUE_SIZE:
        await interaction.followup.send(
            f"❌ **Очередь переполнена!**\n"
            f"💡 Максимум треков в очереди: {MAX_QUEUE_SIZE}\n"
            f"📊 Сейчас в очереди: {len(queue)} треков\n"
            f"🎵 Дождитесь окончания нескольких треков или используйте `/skip`"
        )
        return

    if "entries" in info and info["entries"]:
        # ✅ ОБНОВИТЬ: При добавлении плейлиста учитывать оба лимита
        remaining_slots = MAX_QUEUE_SIZE - len(queue)
        
        # Ограничить как по лимиту плейлиста, так и по свободным местам в очереди
        total_entries = len(info["entries"])
        max_to_add = min(MAX_PLAYLIST_SIZE, remaining_slots)
        entries_to_process = info["entries"][:max_to_add]
        
        added_count = 0
        for entry in entries_to_process:
            if entry and entry.get("title") and entry.get("url"):
                queue.append({
                    "title": entry["title"],
                    "url": entry["url"],
                    "requester": interaction.user.name,
                })
                added_count += 1
        
        # Обновить сообщение с информацией о лимитах
        if total_entries > max_to_add:
            await interaction.followup.send(
                f"📃 **Добавлено {added_count} из {total_entries} треков**\n"
                f"💡 Лимит плейлиста: {MAX_PLAYLIST_SIZE} треков\n"
                f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE} треков"
            )
        else:
            await interaction.followup.send(
                f"📃 **Добавлен плейлист: {added_count} треков**\n"
                f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE} треков"
            )
    else:
        # Для одиночных треков добавляем с проверкой лимита
        track = {
            "title": info["title"],
            "url": info["url"],
            "requester": interaction.user.name,
        }
        queue.append(track)
        await interaction.followup.send(
            f"🎶 **Добавлен трек:** {track['title']}\n"
            f"📊 Очередь: {len(queue)}/{MAX_QUEUE_SIZE} треков"
        )

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)


async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        return

    next_track = queue.pop(0)
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
        # Показать первые 10 треков + информацию о лимитах
        display_tracks = queue[:10]
        text = "\n".join([
            f"{i+1}. {song['title'][:50]}{'...' if len(song['title']) > 50 else ''}"
            for i, song in enumerate(display_tracks)
        ])
        
        additional_info = ""
        if len(queue) > 10:
            additional_info = f"\n... и еще {len(queue) - 10} треков"
        
        await interaction.response.send_message(
            f"📃 **Очередь треков** ({len(queue)}/{MAX_QUEUE_SIZE}):\n"
            f"```\n{text}{additional_info}\n```"
        )
    else:
        await interaction.response.send_message(
            f"📭 **Очередь пуста** (0/{MAX_QUEUE_SIZE})"
        )


@tree.command(name="status", description="Показать статус бота и очереди")
async def status(interaction: discord.Interaction):
    log_command(interaction.user.name, "/status")
    queue = get_queue(interaction.guild.id)
    vc = interaction.guild.voice_client
    
    status_text = "🤖 **Статус бота**\n"
    
    if vc and vc.is_connected():
        channel_name = vc.channel.name
        if vc.is_playing():
            status_text += f"🎵 Играет в канале: **{channel_name}**\n"
        elif vc.is_paused():
            status_text += f"⏸️ На паузе в канале: **{channel_name}**\n"
        else:
            status_text += f"⏹️ Подключен к каналу: **{channel_name}**\n"
    else:
        status_text += "🔌 Не подключен к голосовому каналу\n"
    
    status_text += f"📊 Очередь: **{len(queue)}/{MAX_QUEUE_SIZE}** треков\n"
    status_text += f"⚙️ Лимит плейлиста: **{MAX_PLAYLIST_SIZE}** треков"
    
    await interaction.response.send_message(status_text)


@tree.command(name="help", description="Показать справку по командам")
async def help_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/help")
    await interaction.response.send_message(f"""📖 **Команды бота**

- `/play <url или запрос>` — Воспроизведение трека
- `/pause` — Приостановить текущую песню
- `/resume` — Продолжить воспроизведение
- `/stop` — Остановить воспроизведение и отключиться
- `/skip` — Пропустить текущую песню
- `/queue` — Показать текущую очередь
- `/status` — Показать статус бота и очереди

**💡 Лимиты:**
- Максимум треков из плейлиста: {MAX_PLAYLIST_SIZE}
- Максимум треков в очереди: {MAX_QUEUE_SIZE}
""")


bot.run(TOKEN)
