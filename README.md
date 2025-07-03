<h1 align="center">🎧 Vexel Music Bot [dev]</h1>
<p align="center">
  Advanced, Dockerized YouTube music bot for Discord built in Python — with slash commands, playlist & queue limits, requesters display, modular code, and more. <br>
  <b>This is the development branch (dev)</b>
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

## ✨ Features (dev branch)

- ✅ Slash commands (`/play`, `/pause`, etc.)
- 📃 YouTube playlist support, with per-playlist and global queue limits (configurable)
- 📝 Track requesters: see who added each track in the queue
- 💤 Auto-pause & disconnect if voice channel is empty
- 📜 Admin command logging
- 🔄 Automatic reconnection/network handling
- 🎶 High-quality audio streaming
- 🛠️ Modular codebase: separation into commands, player, utils, etc.
- 🐳 Docker & Docker Compose support for fast dev & production
- ⚙️ Environment variable management via `.env`
- 🦺 Improved error handling, player cleanup, and queue management
- 🧱 Architectural groundwork for lazy playlist loading (not implemented yet)
- ❗ New: Proper removal of player when bot leaves, enhanced queue logic, and user notifications

---

## 📦 Quick Start (Docker, dev branch)

### 1. Clone the repository

```bash
git clone -b dev https://github.com/luxqw/vexel-music-bot.git
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

## 🔄 Updating the Bot (dev branch)

To update the dev version to the latest code:

### Pull the latest changes:

```bash
git pull origin dev
docker compose up -d --build
```

### Or rebuild the image:

```bash
docker build -t vexel-music-bot:dev .
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

## 📁 Project Structure (dev branch)

```plaintext
vexel-music-bot/
├── bot/                  # Discord bot logic
│   ├── commands/         # Slash commands implementations
│   ├── player/           # Music player + queue system
│   ├── utils/            # Helper functions, logging
│   ├── youtube_auth.py   # YouTube authentication (cookies)
│   └── cookie_manager.py # Cookie management for YouTube
├── .env.example         
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 🇷🇺 Особенности (dev ветка)

- ✅ Слэш-команды (`/play`, `/pause`, и др.)
- 📃 Поддержка YouTube-плейлистов + лимиты на плейлист и очередь (конфигурируемо)
- 📝 В очереди видно, кто заказал каждый трек
- 💤 Автопауза и отключение при отсутствии людей в голосовом канале
- 📜 Логирование админ-команд
- 🔄 Авто-переподключение при сетевых сбоях
- 🎶 Стриминг высокого качества
- 🛠️ Модульная архитектура: команды, плеер, утилиты и т.д.
- 🐳 Docker & Docker Compose для быстрой разработки и продакшена
- ⚙️ Управление настройками через .env
- 🦺 Улучшена обработка ошибок, очистка плеера и управление очередью
- 🧱 Подготовлена архитектура для ленивой загрузки плейлистов (ещё не реализовано)
- ❗ Новое: Корректное удаление плеера при выходе бота, информирование о лимитах и заказчиках треков

---

## 📦 Быстрый старт (Docker, dev ветка)

### 1. Клонировать репозиторий dev-ветки

```bash
git clone -b dev https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

### 2. Создать .env

```env
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
MAX_QUEUE_SIZE=50
MAX_PLAYLIST_SIZE=15
```

Шаблон — `.env.example`.

### 3. Запустить с помощью Docker Compose

```bash
docker compose up -d
```

---

## 🔄 Обновление бота (dev ветка)

Чтобы обновить dev-версию до последнего кода:

### Получить последние изменения:

```bash
git pull origin dev
docker compose up -d --build
```

### Или пересобрать образ:

```bash
docker build -t vexel-music-bot:dev .
docker compose up -d
```

---

## 🧠 Слэш-команды

```plaintext
/play [url]   Воспроизведение аудио с YouTube или плейлиста
/pause        Пауза текущего воспроизведения
/resume       Возобновить проигрывание
/stop         Остановить и очистить очередь
/skip         Пропустить текущий трек
/queue        Показать очередь (с заказчиками)
```

---

## 📁 Структура проекта (dev)

```plaintext
vexel-music-bot/
├── bot/                  # Логика Discord-бота
│   ├── commands/         # Слэш-команды
│   ├── player/           # Музыкальный плеер + очередь
│   ├── utils/            # Утилиты и логирование
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

See the [releases page](https://github.com/luxqw/vexel-music-bot/releases) or [commits on dev](https://github.com/luxqw/vexel-music-bot/commits/dev) for full release notes and changelog.

---

**Note:**  
This README is for the development branch and may change frequently.  
For stable instructions, refer to the `main` branch README.
