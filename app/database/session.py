import os
from datetime import datetime
from typing import Any, List, Optional, Tuple
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, update, delete, or_, and_, desc
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Base, Note, Task, Contact, ContactInteraction
from app.utils.date_utils import get_now_yekt, calculate_remind_at

# Убеждаемся, что папка для базы данных существует
db_file_path = Path(settings.db_path)
db_file_path.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """Создание таблиц базы данных при старте приложения."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# -------------------- Notes Operations --------------------

async def create_note_with_tasks(
    user_id: int,
    title: str,
    raw_content: str,
    summary: Optional[str] = None,
    timeline: Optional[str] = None,
    tags: Optional[List[str]] = None,
    audio_duration_seconds: Optional[int] = None,
    tasks_data: Optional[List[dict]] = None
) -> Tuple[Note, List[Task]]:
    """Создает заметку и связанные с ней задачи в одной транзакции."""
    tags_str = ", ".join([str(t).strip().lstrip("#") for t in tags if str(t).strip()]) if tags else None
    
    async with async_session_maker() as session:
        async with session.begin():
            note = Note(
                user_id=user_id,
                title=title or "Заметка",
                raw_content=raw_content,
                summary=summary,
                timeline=timeline,
                tags=tags_str,
                audio_duration_seconds=audio_duration_seconds,
                created_at=get_now_yekt()
            )
            session.add(note)
            await session.flush()  # получаем note.id

            created_tasks: List[Task] = []
            if tasks_data:
                for td in tasks_data:
                    due_date = td.get("due_date")
                    remind_at = td.get("remind_at")
                    if due_date and not remind_at:
                        remind_at = calculate_remind_at(due_date)

                    task = Task(
                        user_id=user_id,
                        note_id=note.id,
                        note=note,
                        title=td.get("title", "Задача"),
                        due_date=due_date,
                        remind_at=remind_at,
                        priority=td.get("priority", "medium"),
                        status="pending",
                        reminded=False,
                        created_at=get_now_yekt()
                    )
                    session.add(task)
                    created_tasks.append(task)

            await session.flush()
            # Перечитываем Note с tasks
            stmt = select(Note).options(selectinload(Note.tasks)).where(Note.id == note.id)
            res = await session.execute(stmt)
            saved_note = res.scalar_one()

            return saved_note, created_tasks


async def get_note_by_id(note_id: int) -> Optional[Note]:
    async with async_session_maker() as session:
        stmt = select(Note).options(selectinload(Note.tasks)).where(Note.id == note_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def get_user_notes(user_id: int, limit: int = 10, offset: int = 0) -> List[Note]:
    async with async_session_maker() as session:
        stmt = (
            select(Note)
            .options(selectinload(Note.tasks))
            .where(Note.user_id == user_id)
            .order_by(desc(Note.created_at))
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def search_notes(user_id: int, query: str, limit: int = 15) -> List[Note]:
    """Поиск по заголовку, содержанию, тезисам и тегам."""
    pattern = f"%{query.strip()}%"
    async with async_session_maker() as session:
        stmt = (
            select(Note)
            .options(selectinload(Note.tasks))
            .where(
                and_(
                    Note.user_id == user_id,
                    or_(
                        Note.title.ilike(pattern),
                        Note.raw_content.ilike(pattern),
                        Note.summary.ilike(pattern),
                        Note.tags.ilike(pattern),
                        Note.timeline.ilike(pattern)
                    )
                )
            )
            .order_by(desc(Note.created_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def delete_note_by_id(note_id: int, user_id: int) -> bool:
    async with async_session_maker() as session:
        async with session.begin():
            stmt = delete(Note).where(and_(Note.id == note_id, Note.user_id == user_id))
            res = await session.execute(stmt)
            return res.rowcount > 0


# -------------------- Tasks Operations --------------------

async def create_single_task(
    user_id: int,
    title: str,
    due_date: Optional[datetime] = None,
    remind_at: Optional[datetime] = None,
    note_id: Optional[int] = None,
    priority: str = "medium"
) -> Task:
    if due_date and not remind_at:
        remind_at = calculate_remind_at(due_date)

    async with async_session_maker() as session:
        async with session.begin():
            task = Task(
                user_id=user_id,
                note_id=note_id,
                title=title,
                due_date=due_date,
                remind_at=remind_at,
                priority=priority,
                status="pending",
                reminded=False,
                created_at=get_now_yekt()
            )
            session.add(task)
            await session.flush()
            await session.refresh(task)
            return task


async def get_task_by_id(task_id: int) -> Optional[Task]:
    async with async_session_maker() as session:
        stmt = select(Task).options(selectinload(Task.note)).where(Task.id == task_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def get_user_tasks(
    user_id: int,
    status: Optional[str] = "pending",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50
) -> List[Task]:
    async with async_session_maker() as session:
        filters = [Task.user_id == user_id]
        if status:
            filters.append(Task.status == status)
        if date_from:
            filters.append(Task.due_date >= date_from)
        if date_to:
            filters.append(Task.due_date <= date_to)

        stmt = (
            select(Task)
            .options(selectinload(Task.note))
            .where(and_(*filters))
            .order_by(Task.due_date.asc().nullslast(), desc(Task.created_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def update_task_status(task_id: int, status: str) -> bool:
    async with async_session_maker() as session:
        async with session.begin():
            stmt = update(Task).where(Task.id == task_id).values(status=status)
            res = await session.execute(stmt)
            return res.rowcount > 0


async def update_task_timing(
    task_id: int,
    due_date: Optional[datetime],
    remind_at: Optional[datetime] = None
) -> bool:
    if due_date and remind_at is None:
        remind_at = calculate_remind_at(due_date)

    async with async_session_maker() as session:
        async with session.begin():
            stmt = update(Task).where(Task.id == task_id).values(
                due_date=due_date,
                remind_at=remind_at,
                reminded=False  # сбрасываем флаг, чтобы напоминание снова сработало
            )
            res = await session.execute(stmt)
            return res.rowcount > 0


async def delete_task_by_id(task_id: int, user_id: int) -> bool:
    async with async_session_maker() as session:
        async with session.begin():
            stmt = delete(Task).where(and_(Task.id == task_id, Task.user_id == user_id))
            res = await session.execute(stmt)
            return res.rowcount > 0


async def get_due_reminders(now: datetime) -> List[Task]:
    """Возвращает задачи, у которых наступило время remind_at и статус pending."""
    async with async_session_maker() as session:
        stmt = (
            select(Task)
            .options(selectinload(Task.note))
            .where(
                and_(
                    Task.status == "pending",
                    Task.reminded == False,
                    Task.remind_at != None,
                    Task.remind_at <= now
                )
            )
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def mark_task_reminded(task_id: int):
    async with async_session_maker() as session:
        async with session.begin():
            stmt = update(Task).where(Task.id == task_id).values(reminded=True)
            await session.execute(stmt)


# -------------------- CRM (Contacts) Operations --------------------

def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (list, tuple)):
        return ", ".join(str(x).strip() for x in val if str(x).strip())
    return str(val).strip()


async def upsert_contact_from_ai(
    user_id: int,
    person_data: dict,
    note_id: Optional[int] = None
) -> Optional[Contact]:
    """Создает или дополняет карточку контакта на основе распознанных ИИ данных."""
    name = _safe_str(person_data.get("name"))
    if not name or len(name) < 2:
        return None

    normalized = name.lower()
    now = get_now_yekt()
    now_str = now.strftime("%d.%m.%Y")

    new_facts = _safe_str(person_data.get("facts"))
    new_agreements = _safe_str(person_data.get("agreements"))
    new_role = _safe_str(person_data.get("role"))
    new_info = _safe_str(person_data.get("contact_info"))
    new_bday = _safe_str(person_data.get("birthday"))

    async with async_session_maker() as session:
        async with session.begin():
            stmt = (
                select(Contact)
                .options(selectinload(Contact.interactions))
                .where(and_(Contact.user_id == user_id, Contact.normalized_name == normalized))
            )
            res = await session.execute(stmt)
            contact = res.scalar_one_or_none()

            if contact:
                contact.last_interaction = now
                if new_role and not contact.role_or_company:
                    contact.role_or_company = new_role
                if new_info and not contact.contact_info:
                    contact.contact_info = new_info
                if new_bday and not contact.birthday:
                    contact.birthday = new_bday

                if new_facts:
                    entry = f"• [{now_str}]: {new_facts}"
                    if contact.notes_summary:
                        contact.notes_summary = f"{contact.notes_summary}\n{entry}"
                    else:
                        contact.notes_summary = entry

                if new_agreements:
                    agr_entry = f"• [{now_str}]: {new_agreements}"
                    if contact.agreements:
                        contact.agreements = f"{contact.agreements}\n{agr_entry}"
                    else:
                        contact.agreements = agr_entry
            else:
                initial_summary = f"• [{now_str}]: {new_facts}" if new_facts else None
                initial_agr = f"• [{now_str}]: {new_agreements}" if new_agreements else None
                contact = Contact(
                    user_id=user_id,
                    name=name,
                    normalized_name=normalized,
                    role_or_company=new_role or None,
                    contact_info=new_info or None,
                    birthday=new_bday or None,
                    notes_summary=initial_summary,
                    agreements=initial_agr,
                    last_interaction=now,
                    created_at=now
                )
                session.add(contact)
                await session.flush()

            # Добавляем запись взаимодействия
            interaction = ContactInteraction(
                contact_id=contact.id,
                note_id=note_id,
                summary=new_facts or None,
                agreements=new_agreements or None,
                interaction_date=now
            )
            session.add(interaction)
            await session.flush()
            await session.refresh(contact)
            return contact


async def get_user_contacts(user_id: int, limit: int = 50) -> List[Contact]:
    """Возвращает контакты пользователя, отсортированные по последнему общению."""
    async with async_session_maker() as session:
        stmt = (
            select(Contact)
            .options(selectinload(Contact.interactions))
            .where(Contact.user_id == user_id)
            .order_by(desc(Contact.last_interaction))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def get_contact_by_name(user_id: int, query_name: str) -> Optional[Contact]:
    """Поиск контакта по имени (точное совпадение или подстрока)."""
    norm = query_name.strip().lower()
    async with async_session_maker() as session:
        # Сначала точное совпадение
        stmt = (
            select(Contact)
            .options(selectinload(Contact.interactions))
            .where(and_(Contact.user_id == user_id, Contact.normalized_name == norm))
        )
        res = await session.execute(stmt)
        c = res.scalar_one_or_none()
        if c:
            return c

        # Иначе поиск по подстроке
        stmt = (
            select(Contact)
            .options(selectinload(Contact.interactions))
            .where(and_(Contact.user_id == user_id, Contact.normalized_name.ilike(f"%{norm}%")))
            .order_by(desc(Contact.last_interaction))
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def get_contact_by_id(contact_id: int, user_id: int) -> Optional[Contact]:
    async with async_session_maker() as session:
        stmt = (
            select(Contact)
            .options(selectinload(Contact.interactions))
            .where(and_(Contact.id == contact_id, Contact.user_id == user_id))
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def delete_contact_by_id(contact_id: int, user_id: int) -> bool:
    async with async_session_maker() as session:
        async with session.begin():
            stmt = delete(Contact).where(and_(Contact.id == contact_id, Contact.user_id == user_id))
            res = await session.execute(stmt)
            return res.rowcount > 0


# -------------------- Day Planner Operations --------------------

async def batch_update_task_timings(user_id: int, task_timings: List[dict]) -> int:
    """Обновляет due_date и remind_at для нескольких задач сразу."""
    count = 0
    async with async_session_maker() as session:
        async with session.begin():
            for item in task_timings:
                tid = item.get("task_id")
                due = item.get("due_date")
                remind = item.get("remind_at")
                if due and not remind:
                    remind = calculate_remind_at(due)

                stmt = (
                    update(Task)
                    .where(and_(Task.id == tid, Task.user_id == user_id))
                    .values(due_date=due, remind_at=remind, reminded=False)
                )
                res = await session.execute(stmt)
                count += res.rowcount
    return count

