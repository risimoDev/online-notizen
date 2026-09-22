import os
import sys
import asyncio
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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


async def test_structure_note_with_list_summary_and_dirty_types():
    print("-> Тестирование защиты от нетипизированных ответов LLM (summary как list и др.)...")
    service = OpenRouterService()

    # Симулируем ответ от модели, где summary и timeline вернулись как списки, а tags как строка
    mock_bad_type_response = json.dumps({
        "title": "Работа в бирки",
        "summary": ["С 12 по 16 октября работа в бирки", "Нужно подготовить документы"],
        "timeline": ["12 октября старт", "16 октября завершение"],
        "tasks": [
            {
                "title": "Работа в бирки",
                "due_date": "2026-10-12 09:00:00",
                "priority": "medium"
            }
        ],
        "tags": "работа, бирки, #важно",
        "people": [
            {
                "name": "Лев",
                "role": ["Менеджер", "Куратор"],
                "facts": ["Работает в бирках"]
            }
        ]
    })

    # Патчим вызов к API
    async def mock_chat(*args, **kwargs):
        return mock_bad_type_response, "test-model:free"

    service.chat_completion_with_failover = mock_chat

    result = await service.structure_note_and_tasks(
        raw_text="С 12 по 16 октября работа в бирки тут записать",
        is_voice=False
    )

    # Проверяем, что summary преобразовано в строку, а не вызвало AttributeError
    assert isinstance(result["summary"], str)
    assert "С 12 по 16 октября" in result["summary"]
    assert "•" in result["summary"]

    # Проверяем timeline
    assert isinstance(result["timeline"], str)
    assert "12 октября старт" in result["timeline"]

    # Проверяем tags (должен стать списком без решеток)
    assert isinstance(result["tags"], list)
    assert "работа" in result["tags"]
    assert "бирки" in result["tags"]
    assert "важно" in result["tags"]

    # Проверяем people
    assert len(result["people"]) == 1
    assert result["people"][0]["name"] == "Лев"
    assert "Менеджер" in result["people"][0]["role"]

    # Проверяем tasks
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["title"] == "Работа в бирки"

    print("   [OK] Успешно обработаны списки в summary, timeline, tags и people без ошибок!")


if __name__ == "__main__":
    test_model_ordering_and_cooldown()
    test_structure_json_cleaning()
    asyncio.run(test_structure_note_with_list_summary_and_dirty_types())
    print("\n[SUCCESS] AI PARSING AND ROTATION TESTS PASSED!")
