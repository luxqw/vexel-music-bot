import os
import logging
import discord
import asyncio
from discord.ext import commands
from discord import app_commands
import yt_dlp
from utils.cookie_manager import CookieManager
from utils.youtube_auth import YouTubeAuthenticator

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
queues = {}

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# Инициализация аутентификации
youtube_auth = YouTubeAuthenticator()

# Базовые опции для yt_dlp
base_ytdl_opts = {
    "format": "bestaudio",
    "noplaylist": False,
}

# Получаем опции с поддержкой аутентификации
ytdl_opts = youtube_auth.get_authenticated_ytdl_opts(base_ytdl_opts)
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
    
    # Тестируем аутентификацию при запуске
    print("🔐 Проверяем настройки аутентификации...")
    auth_test = youtube_auth.test_authentication()
    
    if auth_test["cookies_used"]:
        if auth_test["success"]:
            print("✅ Аутентификация YouTube настроена и работает")
        else:
            print(f"⚠️ Проблема с аутентификацией: {auth_test['message']}")
    else:
        print("ℹ️ Аутентификация YouTube не настроена (age-restricted контент недоступен)")
    
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
        # Проверяем, является ли это ошибкой age-restriction
        if youtube_auth.is_age_restricted_error(e):
            await interaction.followup.send(
                "🔞 **Контент с возрастными ограничениями**\n"
                "Этот контент требует аутентификации YouTube.\n"
                "Для воспроизведения 18+ контента администратор должен настроить cookies.\n"
                f"Ошибка: {str(e)[:200]}..."
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

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

# Добавляем команду для тестирования аутентификации (только для администраторов)
@tree.command(name="test_auth", description="Протестировать аутентификацию YouTube (только для администраторов)")
async def test_auth(interaction: discord.Interaction):
    # Проверяем права администратора
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Эта команда доступна только администраторам.", ephemeral=True)
        return
    
    log_command(interaction.user.name, "/test_auth")
    await interaction.response.send_message("🔐 Тестирую аутентификацию YouTube...")
    
    try:
        auth_result = youtube_auth.test_authentication()
        
        embed = discord.Embed(
            title="🔐 Результат теста аутентификации",
            color=0x00ff00 if auth_result["success"] else 0xff0000
        )
        
        embed.add_field(
            name="Статус",
            value="✅ Успешно" if auth_result["success"] else "❌ Ошибка",
            inline=True
        )
        
        embed.add_field(
            name="Cookies используются",
            value="✅ Да" if auth_result["cookies_used"] else "❌ Нет",
            inline=True
        )
        
        embed.add_field(
            name="Сообщение",
            value=auth_result["message"],
            inline=False
        )
        
        if auth_result["video_info"]:
            embed.add_field(
                name="Тестовое видео",
                value=f"**{auth_result['video_info']['title']}**\nДлительность: {auth_result['video_info']['duration']} сек",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка при тестировании: {str(e)}")

async def play_next(vc, guild_id):
    queue = get_queue(guild_id)
    if not queue:
        return

    next_track = queue.pop(0)
    source = create_source(next_track["url"])

    vc.play(source, after=lambda e: bot.loop.create_task(play_next(vc, guild_id)))

# Остальные команды остаются без изменений...
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
    
    embed = discord.Embed(
        title="📖 Команды бота",
        description="Доступные команды для управления музыкой",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🎵 Основные команды",
        value=(
            "`/play <url или запрос>` — Воспроизведение трека\n"
            "`/pause` — Приостановить текущую песню\n"
            "`/resume` — Продолжить воспроизведение\n"
            "`/stop` — Остановить воспроизведение и отключиться\n"
            "`/skip` — Пропустить текущую песню\n"
            "`/queue` — Показать текущую очередь"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔧 Администрирование",
        value="`/test_auth` — Протестировать аутентификацию YouTube",
        inline=False
    )
    
    embed.add_field(
        name="🔞 Age-restricted контент",
        value="Для воспроизведения контента 18+ требуется настройка YouTube cookies администратором.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)
