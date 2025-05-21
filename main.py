import os
import logging
import discord
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


@bot.event
async def on_ready():
    print(f"✅ Вошли как {bot.user}")
    try:
        synced = await tree.sync()
        print(f"📡 Синхронизированы {len(synced)} команд(ы)")
    except Exception as e:
        print(f"Ошибка sync: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    if not member.bot:
        return
    vc = discord.utils.get(bot.voice_clients, guild=member.guild)
    if vc and len(vc.channel.members) == 1:
        await vc.disconnect()
        print(f"⛔ Авто-выход из {member.guild.name}")


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

    await interaction.response.send_message(f"🔍 Ищу: {query}")
    info = ytdl.extract_info(query, download=False)

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
        queue.append({
            "title": info["title"],
            "url": info["url"],
            "requester": interaction.user.name,
        })
        await interaction.followup.send(f"🎶 Добавлен: {info['title']}")

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
