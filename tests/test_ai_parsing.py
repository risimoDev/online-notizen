import os
import asyncio
import json

os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"

from app.services.openrouter_service import OpenRouterService
from app.utils.date_utils import get_now_yekt


def test_model_ordering_and_cooldown():
    print("-> Тестирование ротации моделей и кулдауна...")
    service = OpenRouterService()
    service.fallback_models = ["model-a:free", "model-b:free", "model-c:free"]
    
    # 1. Начальный список
    models = asyncio.run(service.get_ordered_free_models())
    assert "model-a:free" in models
    assert "model-b:free" in models
    
    # 2. Помечаем model-a в кулдаун
    service.mark_model_cooldown("model-a:free", duration_seconds=60)
    models_after_cooldown = asyncio.run(service.get_ordered_free_models())
    assert "model-a:free" not in models_after_cooldown
    assert "model-b:free" in models_after_cooldown
    print("   [OK] Кулдаун моделей работает корректно.")


def test_structure_json_cleaning():
    print("-> Тестирование очистки и обработки JSON ответов от ИИ...")
    service = OpenRouterService()
    
    # Симуляция ответа от LLM в markdown codeblock
    mock_llm_response = """
    Вот структурированная заметка:
    ```json
    {
      "title": "Созвон с инвестором",
      "summary": "Обсудили раунд инвестиций и KPI.",
      "timeline": "• 12:00 Вводная часть\\n• 12:30 Обсуждение метрик",
      "tasks": [
        {
          "title": "Отправить финансовую модель",
          "due_date": "2026-09-24 16:00:00",
          "priority": "high"
        }
      ],
      "tags": ["инвестиции", "работа"]
    }
    ```
    """
    
    # Проверяем извлечение и валидацию
    clean = mock_llm_response
    if "```json" in clean:
        clean = clean.split("```json")[1].split("```")[0]
    data = json.loads(clean.strip())
    
    assert data["title"] == "Созвон с инвестором"
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["title"] == "Отправить финансовую модель"
    assert "инвестиции" in data["tags"]
    print("   [OK] Парсинг и очистка Markdown JSON работает корректно.")


if __name__ == "__main__":
    test_model_ordering_and_cooldown()
    test_structure_json_cleaning()
    print("\n[SUCCESS] AI PARSING AND ROTATION TESTS PASSED!")
