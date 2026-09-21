from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship
from app.utils.date_utils import get_now_yekt

Base = declarative_base()


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False, default="Без названия")
    raw_content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    timeline = Column(Text, nullable=True)  # Markdown или текст с хронологией обсуждения
    tags = Column(String(255), nullable=True)  # Хранится через запятую, e.g. "работа, идеи"
    audio_duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=get_now_yekt, nullable=False, index=True)

    tasks = relationship("Task", back_populates="note", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self):
        return f"<Note id={self.id} title={self.title!r} user_id={self.user_id}>"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    note_id = Column(Integer, ForeignKey("notes.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    due_date = Column(DateTime, nullable=True, index=True)      # Срок выполнения задачи (в YEKT)
    remind_at = Column(DateTime, nullable=True, index=True)     # Момент срабатывания напоминания
    status = Column(String(20), default="pending", nullable=False)  # pending, completed, cancelled
    priority = Column(String(20), default="medium", nullable=False) # low, medium, high
    reminded = Column(Boolean, default=False, nullable=False)   # Было ли отправлено напоминание
    created_at = Column(DateTime, default=get_now_yekt, nullable=False)

    note = relationship("Note", back_populates="tasks")

    def __repr__(self):
        return f"<Task id={self.id} title={self.title!r} due_date={self.due_date} status={self.status}>"
