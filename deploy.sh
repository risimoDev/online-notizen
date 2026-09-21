#!/usr/bin/env bash
# ==============================================================================
# Скрипт быстрого развертывания и обновления бота myzapis на сервере Ubuntu 24.04
# ==============================================================================

set -e

echo "🚀 Начало развертывания Telegram-бота myzapis..."

# 1. Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    echo "⚠️ Docker не найден. Устанавливаем Docker..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl enable --now docker
    echo "✅ Docker успешно установлен."
fi

# 2. Проверка файла .env
if [ ! -f ".env" ]; then
    echo "⚠️ Файл .env не найден! Создаем из шаблона .env.example..."
    cp .env.example .env
    echo "❗ Пожалуйста, откройте файл .env (например: nano .env) и заполните BOT_TOKEN и OPENROUTER_API_KEY."
    echo "Затем повторно запустите: bash deploy.sh"
    exit 1
fi

# 3. Создание необходимых директорий
mkdir -p data temp_audio
chmod 777 data temp_audio

# 4. Сборка и запуск контейнера
echo "📦 Сборка Docker-образа и перезапуск контейнера..."
docker compose down || true
docker compose up -d --build

echo ""
echo "🎉 Бот myzapis успешно запущен в изолированном контейнере!"
echo "📊 Статус контейнера:"
docker compose ps

echo ""
echo "💡 Для просмотра логов в реальном времени выполните команду:"
echo "   docker compose logs -f"
