# Используем стабильный и легковесный базовый образ Python 3.11
FROM python:3.11-slim

# Устанавливаем системные зависимости (ffmpeg для обработки голосовых аудиосообщений)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код приложения
COPY app/ ./app/

# Создаем директории для базы данных и временных аудиофайлов
RUN mkdir -p /app/data /app/temp_audio

# Задаем переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Yekaterinburg

# Точка входа для запуска бота
CMD ["python", "-m", "app.main"]
