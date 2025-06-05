## 🧪 Development Environment

### Quick Start for Dev Branch

```bash
# 1. Клонируем и переключаемся на dev
git clone https://github.com/luxqw/vexel-music-bot.git
cd vexel-music-bot
git checkout dev

# 2. Настраиваем dev environment
cp .env.dev.example .env.dev
# Редактируем .env.dev с вашими токенами

# 3. Запускаем dev версию
chmod +x scripts/deploy-dev.sh
./scripts/deploy-dev.sh
```

### Dev CI/CD Features

- **Automatic builds** on push to `dev` branch
- **Separate Docker registry**: `ghcr.io/luxqw/vexel-music-bot-dev`
- **Dev tags**: `dev-v1.0.0`, `dev-latest`, `dev-<commit>`
- **Discord notifications** to dev channel
- **Manual deployment** via GitHub Actions

### Dev Docker Images

```bash
# Latest dev build
docker pull ghcr.io/luxqw/vexel-music-bot-dev:dev-latest

# Specific dev version
docker pull ghcr.io/luxqw/vexel-music-bot-dev:dev-v1.2.3

# Specific commit
docker pull ghcr.io/luxqw/vexel-music-bot-dev:dev-a1b2c3d
```

### Development Commands

```bash
# View dev logs
docker-compose -f docker-compose.dev.yml logs -f

# Update dev environment
docker-compose -f docker-compose.dev.yml pull
docker-compose -f docker-compose.dev.yml up -d
```
