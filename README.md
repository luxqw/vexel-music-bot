<h1 align="center">🎧 Vexel Music Bot</h1>
<p align="center">
  Powerful, Dockerized YouTube music bot for Discord built in Python — with slash commands, playlist support, queue/user limits, requesters display, admin logs, and more.
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/forks/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/issues/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/luxqw/vexel-music-bot?style=for-the-badge" />
  <a href="https://discord.gg/jZtxj9Stak">
    <img src="https://img.shields.io/badge/Discord-Join%20Server-blue?style=for-the-badge&logo=discord" />
  </a>
</p>

---

## 🌐 Language / Язык

- [English](#-features)
- [Русский](#-особенности)

---

## ✨ Features

- ✅ Slash commands (`/play`, `/pause`, etc.)
- 📃 YouTube playlist support, with per-playlist and global queue limits (configurable)
- 📝 Display of track requesters in the queue UI
- 💤 Automatically pauses and disconnects when no users are in the voice channel
- 📜 Admin command logging
- 🔄 Automatic reconnection in case of network issues
- 🎶 High-quality audio streaming
- 🛠️ Easy deployment with Docker & Docker Compose
- 🐳 Ready for production and development with environment variable support
- 🦺 Improved error handling, player cleanup, and queue management

---

## 📦 Quick Start (Docker)

### 1. Clone the repository

```bash
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

### 2. Create .env

```env
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
MAX_QUEUE_SIZE=50
MAX_PLAYLIST_SIZE=15
```
You can use `.env.example` as a template.

### 3. Start with Docker Compose

```bash
docker compose up -d
```

---

## 🔄 Updating the Bot

If you need to update the bot to the latest version, you have two options:

### Pull the latest image from GitHub:

```bash
docker pull ghcr.io/luxqw/vexel-music-bot:latest
docker compose up -d
```

### Build the image locally:

```bash
docker build -t vexel-music-bot .
docker compose up -d
```

---

## 🧠 Slash Commands

```plaintext
/play [url]   Play audio from YouTube URL or playlist
/pause        Pause current playback
/resume       Resume playback
/stop         Stop playback and clear queue
/skip         Skip current song
/queue        View the current queue (with requester display)
```

---

## 📁 Project Structure

```plaintext
vexel-music-bot/
├── bot/                  # Discord bot logic
│   ├── commands/         # Slash commands implementations
│   ├── player/           # Music player + queue system
│   ├── utils/            # Helper functions, logging
│   ├── youtube_auth.py   # YouTube authentication (cookies, etc.)
│   └── cookie_manager.py # Cookie management for YouTube
├── .env.example         
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 🇷🇺 Особенности

- ✅ Слэш-команды (`/play`, `/pause`, и т.д.)
- 📃 Поддержка плейлистов YouTube + ограничения на размер плейлиста и очереди
- 📝 В очереди видно, кто заказал трек
- 💤 Автоматическая пауза и отключение, если в голосовом канале никого нет
- 📜 Логирование команд администраторов
- 🔄 Автоматическое переподключение при сетевых сбоях
- 🎶 Стриминг аудио высокого качества
- 🛠️ Простая установка с помощью Docker и Docker Compose
- 🐳 Готов для продакшена и разработки, поддержка переменных окружения
- 🦺 Улучшена обработка ошибок, очистка плеера и управление очередью

---

## 📦 Быстрый старт (Docker)

### 1. Клонировать репозиторий

```bash
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

### 2. Создать .env

```env
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
MAX_QUEUE_SIZE=50
MAX_PLAYLIST_SIZE=15
```

Вы можете использовать `.env.example` как шаблон.

### 3. Запустить с помощью Docker Compose

```bash
docker compose up -d
```

---

## 🔄 Обновление бота

Если вы хотите обновить бота до последней версии, у вас есть два варианта:

### Скачать последнюю версию образа с GitHub:

```bash
docker pull ghcr.io/luxqw/vexel-music-bot:latest
docker compose up -d
```

### Собрать образ локально:

```bash
docker build -t vexel-music-bot .
docker compose up -d
```

---

## 🧠 Слэш-команды

```plaintext
/play [url]   Воспроизведение аудио с YouTube или плейлиста
/pause        Поставить текущий трек на паузу
/resume       Возобновить воспроизведение
/stop         Остановить воспроизведение и очистить очередь
/skip         Пропустить текущую песню
/queue        Показать текущую очередь (с отображением заказчиков)
```

---

## 📁 Структура проекта

```plaintext
vexel-music-bot/
├── bot/                  # Логика Discord-бота
│   ├── commands/         # Слэш-команды
│   ├── player/           # Музыкальный плеер + система очереди
│   ├── utils/            # Вспомогательные функции, логирование
│   ├── youtube_auth.py   # Авторизация YouTube (cookie и др.)
│   └── cookie_manager.py # Работа с cookie для YouTube
├── .env.example          
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 📣 Release Notes & Changelog

See the [releases page](https://github.com/luxqw/vexel-music-bot/releases) for full release notes and changelog.

---

**Note:**  
This README may not reflect the absolute latest changes.  
For the freshest updates, see the [commits](https://github.com/luxqw/vexel-music-bot/commits/main) and [releases](https://github.com/luxqw/vexel-music-bot/releases).
