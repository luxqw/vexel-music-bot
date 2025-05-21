import os
import logging
import discord
from discord.ext import commands, tasks
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
volumes = {}

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


def get_volume(guild_id):
    return volumes.get(guild_id, 0.5)


def create_source(url, volume):
    return discord.FFmpegPCMAudio(
        url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options=f'-vn -filter:a "volume={volume}"'
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
    vol = get_volume(interaction.guild.id)

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
    source = create_source(next_track["url"], get_volume(guild_id))

    vc.play(source, after=lambda e: bot.loop.create_task(play_next(vc, guild_id)))


@tree.command(name="volume", description="Установить громкость (0–100%)")
@app_commands.describe(level="Громкость от 0 до 100")
async def volume(interaction: discord.Interaction, level: int):
    log_command(interaction.user.name, "/volume")
    if 0 <= level <= 100:
        volumes[interaction.guild.id] = level / 100
        await interaction.response.send_message(f"🔊 Громкость установлена на {level}%")
    else:
        await interaction.response.send_message("⚠️ Укажите громкость от 0 до 100.")


@tree.command(name="help", description="Показать справку по командам")
async def help_cmd(interaction: discord.Interaction):
    log_command(interaction.user.name, "/help")
    await interaction.response.send_message("""📖 **Команды Vexel Music Bot**

- `/play <url или запрос>` — Воспроизведение трека
- `/volume <0-100>` — Установить громкость
- Поддержка плейлистов, авто-выход и логирование включены.

👉 Команды на английском. Интерфейс — на русском.
""")

bot.run(TOKEN)
