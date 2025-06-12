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

# Словарь для хранения каналов плееров по гильдиям
player_channels = {}
# Хранение текущих треков
current_tracks = {}

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

class MusicPlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Пауза", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Продолжено", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)
            return
        
        # Создаем новый плеер после изменения состояния
        await create_new_player(interaction.guild.id)
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)
            return
        
        # НЕ отправляем уведомление, так как новый трек сам покажется в плеере
        await interaction.response.defer(ephemeral=True)
        vc.stop()
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ Бот не подключен", ephemeral=True)
            return
        
        vc.stop()
        await vc.disconnect()
        queues[interaction.guild.id] = []
        current_tracks[interaction.guild.id] = None
        
        await interaction.response.send_message("⏹️ Остановлено", ephemeral=True)
        await create_new_player(interaction.guild.id)
    
    @discord.ui.button(emoji="📃", style=discord.ButtonStyle.secondary, custom_id="queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(interaction.guild.id)
        
        if not queue:
            await interaction.response.send_message("📭 Очередь пуста", ephemeral=True)
            return
        
        # Создаем красивый список очереди
        embed = discord.Embed(
            title="📃 Очередь треков",
            color=0x2f3136
        )
        
        # Показываем первые 10 треков
        queue_text = ""
        for i, track in enumerate(queue[:10]):
            queue_text += f"`{i+1}.` **{track['title'][:40]}{'...' if len(track['title']) > 40 else ''}**\n*{track['requester']}*\n\n"
        
        if len(queue) > 10:
            queue_text += f"*... и еще {len(queue) - 10} треков*"
        
        embed.description = queue_text if queue_text else "Очередь пуста"
        embed.set_footer(text=f"Всего: {len(queue)} треков")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

def create_player_embed(guild_id):
    """Создать embed для плеера"""
    current_track = current_tracks.get(guild_id)
    queue = get_queue(guild_id)
    
    embed = discord.Embed(color=0x2f3136)
    
    if current_track:
        embed.title = "🎵 Сейчас играет"
        embed.description = f"**{current_track['title']}**"
        
        embed.add_field(
            name="👤 Заказал", 
            value=current_track['requester'], 
            inline=True
        )
        embed.add_field(
            name="📃 В очереди", 
            value=f"{len(queue)}", 
            inline=True
        )
        
        # Определяем статус
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        if vc:
            if vc.is_playing():
                embed.add_field(name="🔊 Статус", value="▶️ Играет", inline=True)
            elif vc.is_paused():
                embed.add_field(name="🔊 Статус", value="⏸️ Пауза", inline=True)
            else:
                embed.add_field(name="🔊 Статус", value="⏹️ Остановлено", inline=True)
        else:
            embed.add_field(name="🔊 Статус", value="🔌 Отключен", inline=True)
            
        # Добавляем миниатюру
        if 'thumbnail' in current_track and current_track['thumbnail']:
            embed.set_thumbnail(url=current_track['thumbnail'])
    else:
        embed.title = "🎵 Музыкальный плеер"
        embed.description = "*Готов к воспроизведению*"
        
        if queue:
            embed.add_field(
                name="📃 В очереди", 
                value=f"{len(queue)}", 
                inline=True
            )
            embed.add_field(
                name="🔊 Статус", 
                value="⏹️ Остановлен", 
                inline=True
            )
        else:
            embed.add_field(
                name="🔊 Статус", 
                value="⏹️ Пусто", 
                inline=True
            )
    
    return embed

async def create_new_player(guild_id):
    """Создать новый плеер в конце чата"""
    if guild_id not in player_channels:
        return
    
    channel = player_channels[guild_id]
    if not channel:
        return
    
    try:
        embed = create_player_embed(guild_id)
        view = MusicPlayerView()
        
        # Обновляем кнопку паузы/воспроизведения
        guild = bot.get_guild(guild_id)
        vc = discord.utils.get(bot.voice_clients, guild=guild) if guild else None
        
        for item in view.children:
            if item.custom_id == "pause_resume":
                if vc and vc.is_playing():
                    item.emoji = "⏸️"
                else:
                    item.emoji = "▶️"
                break
        
        await channel.send(embed=embed, view=view)
        
    except discord.HTTPException as e:
        print(f"Ошибка создания плеера: {e}")

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
    
    # Добавляем persistent view
    bot.add_view(MusicPlayerView())
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType
