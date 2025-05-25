import os
import logging
import discord
import asyncio
import aiohttp
from discord.ext import commands
from discord import app_commands
import yt_dlp

TOKEN = os.getenv("DISCORD_TOKEN")
MUSIC_CONTROL_ROLE = os.getenv("MUSIC_CONTROL_ROLE")
COVERS_DIR = "covers"

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


def user_can_control(member):
    if not MUSIC_CONTROL_ROLE:
        return True
    return any(role.name == MUSIC_CONTROL_ROLE for role in member.roles)


async def download_cover(url, track_id):
    if not os.path.exists(COVERS_DIR):
        os.makedirs(COVERS_DIR)
    filename = os.path.join(COVERS_DIR, f"{track_id}.jpg")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                with open(filename, "wb") as f:
                    f.write(await resp.read())
                return filename
    return None


def create_source(url):
    return discord.FFmpegPCMAudio(
        url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options='-vn'
    )


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
    if not user_can_control(interaction.user):
        await interaction.response.send_message("🚫 У вас нет прав для управления музыкой.")
        return

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
        await interaction.followup.send_message(f"❌ Ошибка при получении информации: {e}")
        return

    track = info["entries"][0] if "entries" in info else info

    cover_path = None
    if "thumbnail" in track:
        cover_path = await download_cover(track["thumbnail"], track["id"])

    embed = discord.Embed(title=track["title"], url=track["webpage_url"])
    if cover_path:
        file = discord.File(cover_path, filename="cover.jpg")
        embed.set_image(url="attachment://cover.jpg")
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)

    source = create_source(track["url"])
    vc.play(source)


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    message = reaction.message
    if not user_can_control(user):
        return

    vc = discord.utils.get(bot.voice_clients, guild=message.guild)
    if not vc:
        return

    if reaction.emoji == "⏯️":
        if vc.is_playing():
            vc.pause()
        else:
            vc.resume()
    elif reaction.emoji == "⏭️":
        vc.stop()
    elif reaction.emoji == "⏮️":
        # Implement logic for "previous" if required
        pass


bot.run(TOKEN)
