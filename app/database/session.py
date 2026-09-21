import os
from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, update, delete, or_, and_, desc
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Base, Note, Task
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
    tags_str = ", ".join([t.strip().lstrip("#") for t in tags if t.strip()]) if tags else None
    
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

            return saved_note, list(saved_note.tasks)


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
