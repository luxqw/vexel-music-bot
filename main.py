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
        print("ℹ️ YouTube cookies не настроены (age-restricted контент недоступен)")
    
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
        # Улучшенная обработка ошибок age-restriction
        if is_age_restricted_error(e):
            cookies_configured = os.getenv("YOUTUBE_COOKIES_FILE") or os.getenv("YOUTUBE_BROWSER_COOKIES")
            if cookies_configured:
                await interaction.followup.send(
                    "🔞 **Контент с возрастными ограничениями**\n"
                    "❌ Не удалось получить доступ к контенту, несмотря на настроенные cookies.\n"
                    "🔄 Возможно, cookies устарели или недействительны.\n"
                    "💡 Попробуйте обновить cookies или найти другую версию контента."
                )
            else:
                await interaction.followup.send(
                    "🔞 **Контент с возрастными ограничениями**\n"
                    "❌ Этот контент требует аутентификации YouTube.\n"
                    "🔐 Для воспроизведения 18+ контента администратор должен настроить cookies.\n"
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

    if not vc.is_playing():
        await play_next(vc, interaction.guild.id)

# Добавляем команду для тестирования cookies (только для администраторов)
@tree.command(name="test_cookies", description="Протестировать YouTube cookies (только для администраторов)")
async def test_cookies(interaction: discord.Interaction):
    # Проверяем права администратора
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Эта команда доступна только администраторам.", ephemeral=True)
        return
    
    log_command(interaction.user.name, "/test_cookies")
    await interaction.response.send_message("🔐 Тестирую YouTube cookies...")
    
    # Тестируем с известным age-restricted видео
    test_url = "https://www.youtube.com/watch?v=UazDDkQ8Ra8"  # age-restricted
    
    try:
        test_info = ytdl.extract_info(test_url, download=False)
        
        embed = discord.Embed(
            title="🔐 Результат теста cookies",
            color=0x00ff00
        )
        
        embed.add_field(
            name="✅ Тест успешен",
            value="Age-restricted контент доступен!",
            inline=False
        )
        
        embed.add_field(
            name="🎵 Тестовое видео",
            value=f"**{test_info.get('title', 'Неизвестно')}**",
            inline=False
        )
        
        cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
        browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
        
        if cookies_file:
            embed.add_field(name="🍪 Используемые cookies", value=f"Файл: `{cookies_file}`", inline=False)
        elif browser_cookies:
            embed.add_field(name="🍪 Используемые cookies", value=f"Браузер: `{browser_cookies}`", inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="🔐 Результат теста cookies", 
            color=0xff0000
        )
        
        if is_age_restricted_error(e):
            embed.add_field(
                name="❌ Тест неудачен",
                value="Age-restricted контент недоступен",
                inline=False
            )
            
            cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
            browser_cookies = os.getenv("YOUTUBE_BROWSER_COOKIES")
            
            if cookies_file or browser_cookies:
                embed.add_field(
                    name="🔍 Возможные причины",
                    value="• Cookies устарели\n• Неверный формат cookies\n• Cookies от аккаунта без возрастной верификации",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔍 Причина",
                    value="Cookies не настроены",
                    inline=False
                )
        else:
            embed.add_field(
                name="❌ Ошибка теста",
                value=f"```{str(e)[:200]}...```",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)

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
        value="`/test_cookies` — Протестировать YouTube cookies",
        inline=False
    )
    
    embed.add_field(
        name="🔞 Age-restricted контент",
        value="Для воспроизведения контента 18+ требуется настройка YouTube cookies администратором.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)
