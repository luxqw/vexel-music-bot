<h1 align="center">🎧 Vexel Music Bot </h1>
<p align="center">
  Advanced, Dockerized YouTube music bot for Discord built in Python — with slash commands, queue limits, requesters display, and more. <br>
  <b>This is the development branch (dev)</b>
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/luxqw/vexel-music-bot?style=for-the-badge" />
  <img src="https://img.shields.io/github/issues/luxqw/vexel-music-bot?style=for-the-badge" />
  <a href="https://discord.gg/jZtxj9Stak">
    <img src="https://img.shields.io/badge/Discord-Join%20Server-blue?style=for-the-badge&logo=discord" />
  </a>
</p>

---

## 🌐 Language / Язык

- [English](#-features)
- [Русский](#-особенности)

---

## 📦 Quick Start (Docker, dev branch)

### 1. Clone the repository

```bash
git clone -b dev https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

### 2. Create .env

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

## 📦 Быстрый старт (Docker, dev ветка)

### 1. Клонировать репозиторий dev-ветки

```bash
git clone -b dev https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
```

### 2. Создать .env

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
