<h1 align="center">🎧 Vexel Music Bot</h1>
<p align="center">
  Powerful, Dockerized YouTube music bot for Discord built in Python — with slash commands, playlist support, auto-disconnect, admin logs, and more.
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/forks/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/issues/luxqw/vexel-music-bot?style=for-the-badge" />
</p>

---

## ✨ Features

- ✅ Slash commands (`/play`, `/pause`, etc.)
- 📃 YouTube playlist support
- 💤 Auto-leaves voice channel when kicked or moved
- 📜 Admin command logging
---

## 📦 Quickstart (Docker)

### 1. Clone the repository

```bash
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```
### 2. Create .env
```
DISCORD_TOKEN=your_bot_token
YDL_OPTS='{"format": "bestaudio"}'
```
You can use .env.example as a template.

### 3. Start with Docker Compose
```
docker compose up -d
```
### 🧠 Slash Commands
```
/play [url]	Play audio from YouTube URL
/pause	Pause current playback
/resume	Resume playback
/stop	Stop playback and clear queue
/skip	Skip current song
/queue	View the current queue
/leave	Disconnect the bot from voice channel
```
### 🐙 You can pull the image directly from GitHub:
```
docker pull ghcr.io/luxqw/vexel-music-bot:latest
```
Or build your own:
```
docker build -t vexel-music-bot .
```

### 📁 Project Structure
```
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
