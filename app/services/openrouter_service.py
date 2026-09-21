import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.config import settings
from app.utils.date_utils import get_now_yekt, parse_datetime_yekt

logger = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


class OpenRouterService:
    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.fallback_models = settings.fallback_models_list
        self.dynamic_free_models: List[str] = []
        self.model_cooldowns: Dict[str, float] = {}  # model_id -> time when cooldown expires
        self.permanently_unavailable: Set[str] = set()  # модели, вернувшие 404/400
        self.last_models_fetch_time: float = 0
        self.cache_ttl_seconds = 3600 * 4  # обновлять список моделей раз в 4 часа

    async def _fetch_free_models_from_api(self) -> List[str]:
        """Получает список всех актуальных бесплатных моделей из каталога OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://myzapis.bot",
            "X-Title": "MyZapis Bot",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{OPENROUTER_API_BASE}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    free_models = ["openrouter/free"]
                    for m in data:
                        model_id = m.get("id", "")
                        pricing = m.get("pricing", {})
                        is_free = (
                            ":free" in model_id
                            or (
                                str(pricing.get("prompt", "")).strip() in ["0", "0.0", "0.00"]
                                and str(pricing.get("completion", "")).strip() in ["0", "0.0", "0.00"]
                            )
                        )
                        if is_free and model_id not in free_models and model_id not in self.permanently_unavailable:
                            free_models.append(model_id)

                    # Сортируем модели по качеству/приоритету
                    priority_keywords = [
                        "openrouter/free", "qwen3.8", "qwen", "gemma-4", "glm", "nemotron-3", 
                        "nex", "liquid", "llama-3.3", "gemini-2.0", "mistral"
                    ]
                    
                    def score_model(mod: str) -> int:
                        for idx, kw in enumerate(priority_keywords):
                            if kw in mod.lower():
                                return idx
                        return 100

                    free_models.sort(key=score_model)
                    logger.info(f"OpenRouter: обнаружено {len(free_models)} бесплатных моделей.")
                    return free_models
                else:
                    logger.warning(f"Ошибка при запросе моделей OpenRouter: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Не удалось получить динамический список моделей OpenRouter: {e}")
        return []

    async def get_ordered_free_models(self) -> List[str]:
        """Возвращает приоритезированный список доступных бесплатных моделей без кулдауна."""
        now = time.time()
        if not self.dynamic_free_models or (now - self.last_models_fetch_time > self.cache_ttl_seconds):
            fetched = await self._fetch_free_models_from_api()
            if fetched:
                self.dynamic_free_models = fetched
                self.last_models_fetch_time = now

        # Приоритет: динамический список живых моделей, затем fallback
        all_models = []
        if "openrouter/free" not in self.permanently_unavailable:
            all_models.append("openrouter/free")

        if self.dynamic_free_models:
            for m in self.dynamic_free_models:
                if m not in all_models and m not in self.permanently_unavailable:
                    all_models.append(m)

        for m in self.fallback_models:
            if m not in all_models and m not in self.permanently_unavailable:
                all_models.append(m)

        # Фильтруем те, у которых не истек кулдаун (например, после 429 ошибки)
        available = [m for m in all_models if self.model_cooldowns.get(m, 0) < now]
        
        # Если все модели в кулдауне, сбрасываем кулдаун и пробуем снова
        if not available and all_models:
            logger.warning("Все модели были в кулдауне, принудительно сбрасываем ограничения.")
            self.model_cooldowns.clear()
            available = all_models

        return available or ["openrouter/free"]

    def mark_model_cooldown(self, model_id: str, duration_seconds: int = 180):
        """Временно помечает модель как недоступную (кулдаун при 429 или 5xx)."""
        logger.warning(f"Модель {model_id} отправлена в кулдаун на {duration_seconds} сек.")
        self.model_cooldowns[model_id] = time.time() + duration_seconds

    async def chat_completion_with_failover(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_retries: int = 12
    ) -> Tuple[str, str]:
        """
        Выполняет запрос к OpenRouter с автоматической ротацией бесплатных моделей.
        Возвращает (content, used_model_name).
        """
        models_to_try = await self.get_ordered_free_models()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://myzapis.bot",
            "X-Title": "MyZapis Bot",
            "Content-Type": "application/json"
        }

        attempts = 0
        last_error = None

        for model in models_to_try:
            if attempts >= max_retries:
                break

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }

            try:
                logger.info(f"OpenRouter: попытка запроса к модели {model}...")
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post(
                        f"{OPENROUTER_API_BASE}/chat/completions",
                        headers=headers,
                        json=payload
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if choices and "message" in choices[0]:
                            content = choices[0]["message"].get("content", "").strip()
                            if content:
                                logger.info(f"OpenRouter: успех с моделью {model}")
                                return content, model
                        # Пустой ответ от модели
                        logger.warning(f"Модель {model} вернула пустой ответ choices.")
                        self.mark_model_cooldown(model, 60)
                        attempts += 1
                    elif resp.status_code == 404 or (resp.status_code == 400 and ("not a valid model" in resp.text or "unavailable for free" in resp.text)):
                        # Модель не существует или больше не бесплатна на OpenRouter — удаляем навсегда
                        logger.warning(f"Модель {model} исключена из ротации (404/400): {resp.text[:120]}")
                        self.permanently_unavailable.add(model)
                        if model in self.dynamic_free_models:
                            self.dynamic_free_models.remove(model)
                        # Не увеличиваем attempts, сразу пробуем следующую
                    elif resp.status_code in (429, 500, 502, 503, 504):
                        logger.warning(f"Модель {model} ответила статусом {resp.status_code}: {resp.text[:120]}")
                        self.mark_model_cooldown(model, 180)
                        attempts += 1
                    else:
                        logger.error(f"Модель {model} вернула неожиданный статус {resp.status_code}: {resp.text[:150]}")
                        self.mark_model_cooldown(model, 300)
                        attempts += 1

            except httpx.TimeoutException:
                logger.warning(f"Таймаут запроса к модели {model}")
                self.mark_model_cooldown(model, 120)
                attempts += 1
            except Exception as e:
                logger.warning(f"Исключение при обращении к {model}: {e}")
                self.mark_model_cooldown(model, 120)
                last_error = e
                attempts += 1

        raise RuntimeError(f"Все доступные бесплатные модели OpenRouter завершились с ошибкой. Последняя: {last_error}")

    async def structure_note_and_tasks(
        self,
        raw_text: str,
        is_voice: bool = False,
        audio_duration_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Анализирует текст (или транскрипт аудио) и извлекает:
        - Заголовок (title)
        - Суть / Тезисы (summary)
        - Хронологический таймлайн (timeline), если есть цепочка мыслей/событий
        - Задачи (tasks) с вычисленной датой/временем в YEKT (UTC+5)
        - Теги (tags)
        """
        now = get_now_yekt()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        day_of_week = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][now.weekday()]

        system_prompt = f"""Ты — интеллектуальный ассистент по структурированию личных заметок, голосовых сообщений и задач.
Твоя цель: превратить сырой текст (или транскрипцию голосового сообщения) в идеально структурированную заметку с конкретными задачами и дедлайнами.

ТЕКУЩЕЕ ВРЕМЯ И ДАТА:
- Дата и время: {now_str}
- День недели: {day_of_week}
- Часовой пояс: UTC+5 (Екатеринбург, YEKT).
ВСЕ относительные даты («завтра», «в четверг», «через 2 часа», «в конце недели», «25 сентября в 18:00») ты ДОЛЖЕН перевести в точную дату и время в формате 'YYYY-MM-DD HH:MM:SS' по этому часовому поясу!
Если указано только время (например «в 16:00»), а текущее время уже позже — то это относится к завтрашнему дню!
Если дата не указана вовсе, ставь null.

ПРАВИЛА СТРУКТУРИРОВАНИЯ:
1. title: Краткий, содержательный заголовок заметки (до 6-8 слов).
2. summary: Главная мысль, краткая выжимка и ключевые тезисы (список markdown).
3. timeline: Если это голосовое сообщение, монолог мыслей или обсуждение встречи, составь хронологическую цепочку (ход мыслей или хронологию тем):
   Например:
   "• Начало: Обсуждение бюджета проекта
   • Далее: Замечания по дизайну интерфейса
   • Итог: Договорились подготовить договор до пятницы"
   Если это просто короткая фраза/заметка без хронологии — верни null.
4. tasks: Список задач и действий (action items), которые нужно сделать:
   - title: Четкая формулировка задачи в повелительной форме или инфинитиве (например: "Позвонить Сергею по поводу сметы").
   - due_date: Точная дата и время дедлайна/выполнения в формате 'YYYY-MM-DD HH:MM:SS' (или null).
   - priority: 'low', 'medium' или 'high'.
5. tags: Список релевантных тегов (без символа #, на русском, например: ["работа", "договор", "клиент"]).

ФОРМАТ ОТВЕТА:
Строго валидный JSON БЕЗ каких-либо комментариев до или после!

Пример JSON структуры:
{{
  "title": "Созвон с клиентом по договору",
  "summary": "Клиент согласовал второй этап. Нужно внести правки в смету и выслать финальный вариант.",
  "timeline": "• Обсудили результаты первого этапа\\n• Согласовали стоимость второго этапа\\n• Договорились о сроках отправки договора",
  "tasks": [
    {{
      "title": "Внести правки в смету",
      "due_date": "{now.strftime('%Y-%m-%d')} 15:00:00",
      "priority": "high"
    }}
  ],
  "tags": ["работа", "клиент", "договор"]
}}
"""

        user_content = f"Исходный текст {'(голосовое сообщение)' if is_voice else ''}:\n\"\"\"\n{raw_text}\n\"\"\""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        content, model_used = await self.chat_completion_with_failover(messages, temperature=0.1)

        # Очищаем Markdown json обертки
        clean_json = content
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0]
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0]

        clean_json = clean_json.strip()

        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError:
            # Попробуем извлечь JSON регуляркой
            match = re.search(r"\{[\s\S]*\}", clean_json)
            if match:
                data = json.loads(match.group(0))
            else:
                logger.error(f"Не удалось распарсить JSON от модели {model_used}: {content}")
                data = {
                    "title": raw_text[:50] + ("..." if len(raw_text) > 50 else ""),
                    "summary": raw_text,
                    "timeline": None,
                    "tasks": [],
                    "tags": ["заметка"]
                }

        # Валидация и парсинг дат в задачах
        tasks_list = []
        for t in data.get("tasks", []):
            if isinstance(t, dict) and t.get("title"):
                due_dt = parse_datetime_yekt(t.get("due_date"))
                tasks_list.append({
                    "title": t.get("title", "").strip(),
                    "due_date": due_dt,
                    "priority": t.get("priority", "medium") if t.get("priority") in ("low", "medium", "high") else "medium"
                })

        return {
            "title": data.get("title", "Заметка").strip(),
            "summary": data.get("summary", "").strip(),
            "timeline": data.get("timeline"),
            "tasks": tasks_list,
            "tags": data.get("tags", []),
            "model_used": model_used
        }

    async def ask_notes_rag(
        self,
        user_query: str,
        notes_context: List[Dict[str, Any]],
        tasks_context: List[Dict[str, Any]]
    ) -> Tuple[str, str]:
        """
        AI-поиск (RAG) по заметкам и задачам пользователя.
        """
        now = get_now_yekt()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        system_prompt = f"""Ты — персональный ассистент по базе знаний пользователя.
Текущее время: {now_str} (UTC+5, Екатеринбург).
Твоя задача — ответить на вопрос пользователя, опираясь на предоставленный контекст его заметок и задач.
Если в заметках есть точная информация — ответь максимально четко, укажи дату заметки и заголовок.
Если информации недостаточно или ее нет, честно скажи об этом, но подскажи, что наиболее близко найдено."""

        context_blocks = []
        if notes_context:
            context_blocks.append("=== НАЙДЕННЫЕ ЗАМЕТКИ ===")
            for n in notes_context:
                context_blocks.append(
                    f"[Заметка #{n.get('id')}] Дата: {n.get('created_at')}\n"
                    f"Заголовок: {n.get('title')}\n"
                    f"Теги: {n.get('tags')}\n"
                    f"Суть: {n.get('summary')}\n"
                    f"Текст: {n.get('raw_content')}\n"
                )

        if tasks_context:
            context_blocks.append("=== СВЯЗАННЫЕ ЗАДАЧИ ===")
            for t in tasks_context:
                context_blocks.append(
                    f"- Задача: {t.get('title')} (Срок: {t.get('due_date')}, Статус: {t.get('status')})"
                )

        full_context = "\n\n".join(context_blocks)
        user_message = f"КОНТЕКСТ:\n{full_context}\n\nВОПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_query}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        return await self.chat_completion_with_failover(messages, temperature=0.3)


# Экземпляр сервиса
ai_service = OpenRouterService()
