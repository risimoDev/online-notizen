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


def _clean_str(val: Any, default: str = "") -> str:
    """Безопасно преобразует любое значение (строку, список, число, словарь) в очищенную строку."""
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (list, tuple)):
        items = [_clean_str(x) for x in val if x is not None]
        items = [x for x in items if x]
        if items:
            return "\n".join(f"• {x}" if not x.startswith("•") and not x.startswith("-") else x for x in items)
        return default
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return str(val).strip()


def _clean_str_or_none(val: Any) -> Optional[str]:
    res = _clean_str(val, default="")
    return res if res else None


def _clean_tags(val: Any) -> List[str]:
    """Преобразует строку, список или None в чистый список тегов."""
    if not val:
        return []
    if isinstance(val, str):
        return [t.strip().lstrip("#") for t in val.replace(";", ",").split(",") if t.strip()]
    if isinstance(val, (list, tuple)):
        clean = []
        for item in val:
            if isinstance(item, str):
                t = item.strip().lstrip("#")
                if t:
                    clean.append(t)
            elif item is not None:
                s = str(item).strip().lstrip("#")
                if s:
                    clean.append(s)
        return clean
    return []


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
6. people: Список людей, упомянутых в тексте/голосовом (если никто конкретно не упомянут — пустой список []):
   - name: Имя или имя и фамилия (например: "Андрей", "Мария", "Сергей Петрович").
   - role: Кем работает, должность или статус (или null).
   - facts: Ключевые факты о человеке, новости, увлечения, семья (или null).
   - agreements: Взаимные обещания или договоренности ("обещал прислать смету", "договорились созвониться в ноябре") (или null).
   - birthday: День рождения, если упоминался (или null).
   - contact_info: Телефон, @username, email если упоминались (или null).

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
  "tags": ["работа", "клиент", "договор"],
  "people": [
    {{
      "name": "Алексей",
      "role": "Директор по закупкам",
      "facts": "Утверждает бюджет на Q4",
      "agreements": "Обещал выслать реквизиты до четверга",
      "birthday": null,
      "contact_info": null
    }}
  ]
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
        except Exception:
            # Попробуем извлечь JSON регуляркой
            match = re.search(r"\{[\s\S]*\}", clean_json)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception:
                    data = {}
            else:
                logger.error(f"Не удалось распарсить JSON от модели {model_used}: {content}")
                data = {}

        if not isinstance(data, dict):
            data = {}

        # Безопасная нормализация заголовка
        title = _clean_str(data.get("title"))
        if not title:
            title = raw_text[:50] + ("..." if len(raw_text) > 50 else "")

        # Безопасная нормализация summary, timeline и tags
        summary = _clean_str(data.get("summary"), default=raw_text)
        timeline = _clean_str_or_none(data.get("timeline"))
        tags = _clean_tags(data.get("tags"))
        if not tags:
            tags = ["заметка"]

        # Валидация и парсинг дат в задачах
        tasks_list = []
        tasks_raw = data.get("tasks", [])
        if isinstance(tasks_raw, list):
            for t in tasks_raw:
                if isinstance(t, dict):
                    t_title = _clean_str(t.get("title"))
                    if t_title:
                        due_raw = t.get("due_date")
                        due_dt = parse_datetime_yekt(due_raw) if isinstance(due_raw, str) else None
                        priority = str(t.get("priority", "medium")).lower().strip()
                        if priority not in ("low", "medium", "high"):
                            priority = "medium"
                        tasks_list.append({
                            "title": t_title,
                            "due_date": due_dt,
                            "priority": priority
                        })

        # Обработка людей
        people_list = []
        people_raw = data.get("people", [])
        if isinstance(people_raw, list):
            for p in people_raw:
                if isinstance(p, dict):
                    p_name = _clean_str(p.get("name"))
                    if len(p_name) >= 2:
                        people_list.append({
                            "name": p_name,
                            "role": _clean_str_or_none(p.get("role")),
                            "facts": _clean_str_or_none(p.get("facts")),
                            "agreements": _clean_str_or_none(p.get("agreements")),
                            "birthday": _clean_str_or_none(p.get("birthday")),
                            "contact_info": _clean_str_or_none(p.get("contact_info"))
                        })

        return {
            "title": title,
            "summary": summary,
            "timeline": timeline,
            "tasks": tasks_list,
            "tags": tags,
            "people": people_list,
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

    async def find_serendipity_links(
        self,
        new_title: str,
        new_content: str,
        past_notes: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Ищет неочевидные концептуальные связи между новой мыслью и прошлыми заметками.
        Возвращает dict {"matched_note_id": int, "matched_note_title": str, "insight": str} или None.
        """
        if not past_notes or len(past_notes) < 1:
            return None

        system_prompt = """Ты — генератор эвристических озарений и связей (Serendipity Engine).
Твоя задача — сопоставить НОВУЮ заметку пользователя с его ПРОШЛЫМИ заметками и найти нетривиальную, неочевидную концептуальную связь, аналогию, скрытый синергетический эффект или полезную параллель.

ПРАВИЛА:
1. Не притягивай за уши очевидные или тривиальные вещи (например, если обе заметки содержат слово "купить").
2. Ищи параллели: похожие механики решений в разных сферах, возвращение к давней идее в новом контексте, взаимно дополняющие мысли.
3. Если настоящей полезной связи нет — строго верни JSON с matched_note_id: null.
4. Если связь есть:
   - matched_note_id: ID связанной заметки (число)
   - matched_note_title: заголовок связанной заметки
   - insight: 1-2 предложения, в чем именно инсайт и почему это ценно связать.

ФОРМАТ JSON:
{
  "matched_note_id": 12,
  "matched_note_title": "Идея стартапа в сфере образования",
  "insight": "Эта новая мысль о геймификации отлично дополняет вашу августовскую идею: вы можете применить этот механизм вовлечения в том проекте."
}
или:
{
  "matched_note_id": null
}
"""

        past_context = []
        for n in past_notes[:10]:
            past_context.append(
                f"[ID {n.get('id')}] «{n.get('title')}» ({n.get('created_at')}): {n.get('summary') or n.get('raw_content', '')[:150]}"
            )

        user_content = f"НОВАЯ ЗАМЕТКА:\nЗаголовок: {new_title}\nТекст: {new_content}\n\nПРОШЛЫЕ ЗАМЕТКИ ДЛЯ АНАЛИЗА:\n" + "\n".join(past_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        try:
            content, _ = await self.chat_completion_with_failover(messages, temperature=0.3)
            clean = content.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            
            data = json.loads(clean)
            if isinstance(data, dict):
                matched_id = data.get("matched_note_id")
                insight = _clean_str(data.get("insight"))
                if matched_id is not None and insight:
                    try:
                        return {
                            "matched_note_id": int(matched_id),
                            "matched_note_title": _clean_str(data.get("matched_note_title"), "Заметка"),
                            "insight": insight
                        }
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.warning(f"Serendipity Engine exception: {e}")
        return None

    async def plan_day_schedule(
        self,
        tasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Интеллектуальное планирование дня (AI Day Planner).
        Распределяет задачи пользователя по умным тайм-слотам с учетом биоритмов и дедлайнов.
        """
        now = get_now_yekt()
        now_str = now.strftime("%Y-%m-%d %H:%M")
        today_date_str = now.strftime("%Y-%m-%d")

        system_prompt = f"""Ты — персональный исполнительный коуч и специалист по тайм-менеджменту.
Текущее время: {now_str} (UTC+5, Екатеринбург).
Твоя задача — составить для пользователя реалистичный, сбалансированный и продуктивный распорядок дня из переданного списка задач.

ПРИНЦИПЫ УМНОГО ПЛАНИРОВАНИЯ:
1. Deep Work (Утро / Первая половина дня, обычно 10:00 - 12:30): сложные мыслительные задачи, аналитика, подготовка ключевых документов.
2. Communications (День, обычно 13:30 - 16:00): звонки, встречи, согласования, ответы на письма.
3. Routine & Wrap-up (Вечер, обычно 16:30 - 18:30): оплата счетов, мелкие рутинные дела, подведение итогов.
4. Учитывай время дня: если сейчас уже 14:00, не ставь задачи на утро — планируй начиная от текущего времени!
5. Для каждой задачи с ID сформируй рекомендованное точное время выполнения в формате 'YYYY-MM-DD HH:MM:00'.

ФОРМАТ ОТВЕТА JSON:
{{
  "overview": "Краткое бодрое напутствие и стратегия на сегодня (1-2 предложения)",
  "schedule_blocks": [
    {{
      "block_title": "🧠 Deep Work (10:00 - 12:30)",
      "description": "Фокус на ключевой задаче дня без отвлечений",
      "task_ids": [1, 3]
    }},
    {{
      "block_title": "📞 Коммуникации и встречи (14:00 - 16:00)",
      "description": "Созвоны и согласования",
      "task_ids": [2]
    }}
  ],
  "task_timings": [
    {{
      "task_id": 1,
      "due_date": "{today_date_str} 10:00:00",
      "time_label": "10:00"
    }},
    {{
      "task_id": 3,
      "due_date": "{today_date_str} 11:30:00",
      "time_label": "11:30"
    }},
    {{
      "task_id": 2,
      "due_date": "{today_date_str} 14:00:00",
      "time_label": "14:00"
    }}
  ]
}}
"""
        tasks_text = []
        for t in tasks:
            tasks_text.append(
                f"- [ID {t['id']}] «{t['title']}» (Приоритет: {t.get('priority')}, Текущий дедлайн: {t.get('due_date') or 'нет'})"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "ЗАДАЧИ НА СЕГОДНЯ:\n" + "\n".join(tasks_text)}
        ]

        try:
            content, model_used = await self.chat_completion_with_failover(messages, temperature=0.2)
            clean = content.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()

            try:
                data = json.loads(clean)
            except Exception:
                match = re.search(r"\{[\s\S]*\}", clean)
                data = json.loads(match.group(0)) if match else {}

            if not isinstance(data, dict):
                data = {}

            data["model_used"] = model_used
            data["overview"] = _clean_str(data.get("overview"), "План на сегодня сформирован.")
            if not isinstance(data.get("schedule_blocks"), list):
                data["schedule_blocks"] = []
            if not isinstance(data.get("task_timings"), list):
                data["task_timings"] = []
            return data
        except Exception as e:
            logger.error(f"Ошибка в Day Planner: {e}")
            return {
                "overview": "Не удалось сформировать автоматический график.",
                "schedule_blocks": [],
                "task_timings": []
            }

    async def generate_meeting_protocol(
        self,
        raw_text: str,
        duration_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Формирует подробный официальный протокол совещания (Meeting Minutes).
        """
        now = get_now_yekt()
        now_str = now.strftime("%Y-%m-%d %H:%M")

        system_prompt = f"""Ты — профессиональный секретарь и корпоративный аналитик.
Текущее время: {now_str} (UTC+5, Екатеринбург).
Твоя задача — проанализировать стенограмму/запись встречи и составить идеальный структурированный протокол совещания (Meeting Minutes).

ОБЯЗАТЕЛЬНЫЕ РАЗДЕЛЫ В JSON:
1. topic: Тема и цель встречи (кратко и емко).
2. participants: Список участников, упомянутых на встрече, с должностями/ролями если понятно (например ["Дмитрий (директор)", "Анна (маркетолог)"]).
3. timeline: Хронологическая цепочка обсуждения (поэтапно: что за чем шло, ключевые реплики).
4. decisions: Список принятых решений и утвержденных договоренностей (что решено делать, а что отклонено).
5. action_items: Список конкретных поручений:
   - task: четкая формулировка задачи в повелительной форме
   - assignee: ответственное лицо (имя) или "Не назначен"
   - due_date: дата/время в формате 'YYYY-MM-DD HH:MM:SS' если упоминались, или null
   - priority: low, medium, high
6. unresolved: Открытые или спорные вопросы, перенесенные на следующий созвон (или пустой список []).

ФОРМАТ СТРОГО JSON:
{{
  "topic": "Обсуждение редизайна и запуска рекламной кампании",
  "participants": ["Иван (Team Lead)", "Ольга (Дизайнер)", "Максим"],
  "timeline": [
    "Обсудили текущие замечания клиентов по интерфейсу",
    "Ольга показала новые макеты мобильной версии",
    "Максим предложил перенести запуск рекламы на следующую неделю"
  ],
  "decisions": [
    "Утвердить макеты экрана оформления заказа",
    "Перенести дату старта кампании на 28 сентября"
  ],
  "action_items": [
    {{
      "task": "Передать макеты разработчикам",
      "assignee": "Ольга",
      "due_date": "{now.strftime('%Y-%m-%d')} 18:00:00",
      "priority": "high"
    }}
  ],
  "unresolved": [
    "Согласование дополнительного бюджета на трафик"
  ]
}}
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"ТЕКСТ ВСТРЕЧИ:\n\"\"\"\n{raw_text}\n\"\"\""}
        ]

        content, model_used = await self.chat_completion_with_failover(messages, temperature=0.1)
        clean = content.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(clean)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", clean)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception:
                    data = {}
            else:
                data = {}

        if not isinstance(data, dict):
            data = {}

        topic = _clean_str(data.get("topic"), "Протокол встречи")
        participants = []
        if isinstance(data.get("participants"), list):
            participants = [_clean_str(p) for p in data.get("participants") if _clean_str(p)]
        elif isinstance(data.get("participants"), str):
            participants = [_clean_str(p) for p in data.get("participants").split(",") if _clean_str(p)]

        timeline = []
        if isinstance(data.get("timeline"), list):
            timeline = [_clean_str(tl) for tl in data.get("timeline") if _clean_str(tl)]
        elif isinstance(data.get("timeline"), str):
            timeline = [_clean_str(tl) for tl in data.get("timeline").split("\n") if _clean_str(tl)]

        decisions = []
        if isinstance(data.get("decisions"), list):
            decisions = [_clean_str(d) for d in data.get("decisions") if _clean_str(d)]
        elif isinstance(data.get("decisions"), str):
            decisions = [_clean_str(d) for d in data.get("decisions").split("\n") if _clean_str(d)]

        unresolved = []
        if isinstance(data.get("unresolved"), list):
            unresolved = [_clean_str(u) for u in data.get("unresolved") if _clean_str(u)]
        elif isinstance(data.get("unresolved"), str):
            unresolved = [_clean_str(u) for u in data.get("unresolved").split("\n") if _clean_str(u)]

        action_items = []
        if isinstance(data.get("action_items"), list):
            for ai in data.get("action_items"):
                if isinstance(ai, dict):
                    t = _clean_str(ai.get("task"))
                    if t:
                        action_items.append({
                            "task": t,
                            "assignee": _clean_str(ai.get("assignee"), "Не назначен"),
                            "due_date": ai.get("due_date"),
                            "priority": ai.get("priority", "medium")
                        })

        return {
            "topic": topic,
            "participants": participants,
            "timeline": timeline,
            "decisions": decisions,
            "action_items": action_items,
            "unresolved": unresolved,
            "model_used": model_used
        }


# Экземпляр сервиса
ai_service = OpenRouterService()
