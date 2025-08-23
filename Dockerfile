FROM python:3.11-slim

WORKDIR /app

# Обновляем систему и устанавливаем зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    libffi-dev \
    libssl3 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Создаем необходимые папки
RUN mkdir -p /app/cache /app/logs /app/cookies

# Копируем и устанавливаем Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY main.py .

# Создаем пользователя для безопасности
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONIOENCODING=utf-8

# Проверка здоровья
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD pgrep python3 > /dev/null || exit 1

CMD ["python3", "main.py"]	
