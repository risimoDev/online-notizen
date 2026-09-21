from datetime import datetime, timedelta
from typing import Optional
import pytz
from dateutil import parser
from app.config import settings

def get_tz() -> pytz.BaseTzInfo:
    return pytz.timezone(settings.timezone)

def get_now_yekt() -> datetime:
    """Возвращает текущую дату и время в таймзоне пользователя (UTC+5 / YEKT)."""
    return datetime.now(get_tz())

def parse_datetime_yekt(val: Optional[str]) -> Optional[datetime]:
    """
    Парсит строку даты/времени от ИИ в таймзону YEKT.
    Ожидаемые форматы: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', ISO.
    """
    if not val:
        return None
    try:
        dt = parser.parse(val)
        tz = get_tz()
        if dt.tzinfo is None:
            # Если таймзона не указана, считаем что это местное время пользователя
            return tz.localize(dt)
        return dt.astimezone(tz)
    except Exception:
        return None

def calculate_remind_at(due_date: Optional[datetime], lead_minutes: Optional[int] = None) -> Optional[datetime]:
    """
    Рассчитывает время напоминания с учетом упреждения (lead_minutes).
    Если не указано, берется settings.default_reminder_lead_minutes.
    """
    if not due_date:
        return None
    
    if lead_minutes is None:
        lead_minutes = settings.default_reminder_lead_minutes
        
    remind_at = due_date - timedelta(minutes=lead_minutes)
    now = get_now_yekt()
    
    # Если напоминание за 15 мин уже в прошлом, но дедлайн еще в будущем, ставим напоминание на сам дедлайн или через 1 мин
    if remind_at < now:
        if due_date > now:
            return due_date
        else:
            return None
    return remind_at

def format_datetime_human(dt: Optional[datetime]) -> str:
    """
    Красивое форматирование даты и времени на русском языке.
    Например: 'Сегодня в 14:30', 'Завтра в 09:00', '25.09.2026 в 18:00'.
    """
    if not dt:
        return "Без срока"
    
    tz = get_tz()
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    else:
        dt = dt.astimezone(tz)
        
    now = get_now_yekt()
    today = now.date()
    target_date = dt.date()
    
    time_str = dt.strftime("%H:%M")
    
    if target_date == today:
        diff_mins = int((dt - now).total_seconds() // 60)
        if 0 < diff_mins < 60:
            return f"Сегодня в {time_str} (через {diff_mins} мин)"
        return f"Сегодня в {time_str}"
    elif target_date == today + timedelta(days=1):
        return f"Завтра в {time_str}"
    elif target_date == today + timedelta(days=2):
        return f"Послезавтра в {time_str}"
    elif target_date == today - timedelta(days=1):
        return f"Вчера в {time_str} (просрочено)"
    elif target_date < today:
        return f"{dt.strftime('%d.%m.%Y')} в {time_str} (просрочено)"
    else:
        # День недели
        days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_name = days_ru[dt.weekday()]
        return f"{day_name}, {dt.strftime('%d.%m.%Y')} в {time_str}"
