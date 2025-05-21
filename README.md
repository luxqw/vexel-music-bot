<h1 align="center">🎧 Vexel Music Bot</h1>
<p align="center">
  Powerful, Dockerized YouTube music bot for Discord built in Python — with slash commands, playlist support, auto-disconnect, admin logs, and more.
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/forks/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/issues/luxqw/vexel-music-bot?style=for-the-badge" />
  <a href="https://discord.gg/jZtxj9Stak">
    <img src="https://img.shields.io/badge/Discord-Join%20Server-blue?style=for-the-badge&logo=discord" />
  </a>
</p>

---

## 🌐 Language / Язык

- [English](#features)
- [Русский](https://github.com/luxqw/vexel-music-bot?tab=readme-ov-file#-%D0%BE%D1%81%D0%BE%D0%B1%D0%B5%D0%BD%D0%BD%D0%BE%D1%81%D1%82%D0%B8)

---

## ✨ Features

- ✅ Slash commands (`/play`, `/pause`, etc.)
- 📃 YouTube playlist support
- 💤 Automatically pauses and disconnects when no users are in the voice channel
- 📜 Admin command logging
- 🔄 Automatic reconnection in case of network issues
- 🎶 High-quality audio streaming
- 🛠️ Easy deployment with Docker

---

## 📦 Quickstart (Docker)

#### 1. Clone the repository

```bash
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

#### 2. Create .env

```env
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
```

You can use `.env.example` as a template.

#### 3. Start with Docker Compose

```bash
docker compose up -d
```

### 🧠 Slash Commands

```plaintext
/play [url]   Play audio from YouTube URL
/pause        Pause current playback
/resume       Resume playback
/stop         Stop playback and clear queue
/skip         Skip current song
/queue        View the current queue
/leave        Disconnect the bot from voice channel
```

### 🐙 Pull the image directly from GitHub:

```bash
docker pull ghcr.io/luxqw/vexel-music-bot:latest
```

Or build your own:

```bash
docker build -t vexel-music-bot .
```

---

### 📁 Project Structure

```plaintext
vexel-music-bot/
├── bot/                  # Discord bot logic
│   ├── commands/         # Slash commands
│   ├── player/           # Music player + queue system
│   └── utils/            # Helper functions, logging
├── .env.example          # Token config (safe to share)
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

### 🔗 Join our Discord Server

| **Community** | **Link**                                                                 |
|---------------|--------------------------------------------------------------------------|
| Discord       | [Join Discord Server](https://discord.gg/example)                        |

---

## 🇷🇺 Особенности

- ✅ Слэш-команды (`/play`, `/pause`, и т.д.)
- 📃 Поддержка плейлистов YouTube
- 💤 Автоматическая пауза и отключение, если в голосовом канале никого нет
- 📜 Логирование команд администраторов
- 🔄 Автоматическое переподключение при сетевых сбоях
- 🎶 Стриминг аудио высокого качества
- 🛠️ Простая установка с помощью Docker

### 📦 Быстрый старт (Docker)

#### 1. Клонировать репозиторий

```bash
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

#### 2. Создать .env

```env
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
```

Вы можете использовать `.env.example` как шаблон.

#### 3. Запустить с помощью Docker Compose

```bash
docker compose up -d
```

### 🧠 Слэш-команды

```plaintext
/play [url]   Воспроизведение аудио с YouTube
/pause        Поставить текущий трек на паузу
/resume       Возобновить воспроизведение
/stop         Остановить воспроизведение и очистить очередь
/skip         Пропустить текущую песню
/queue        Показать текущую очередь
/leave        Отключить бота от голосового канала
```

### 🐙 Скачать образ напрямую из GitHub:

```bash
docker pull ghcr.io/luxqw/vexel-music-bot:latest
```

Или собрать свой образ:

```bash
docker build -t vexel-music-bot .
```

---

### 📁 Структура проекта

```plaintext
vexel-music-bot/
├── bot/                  # Логика Discord-бота
│   ├── commands/         # Слэш-команды
│   ├── player/           # Музыкальный плеер + система очереди
│   └── utils/            # Вспомогательные функции, логирование
├── .env.example          # Конфиг токенов (безопасно для публикации)
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---
