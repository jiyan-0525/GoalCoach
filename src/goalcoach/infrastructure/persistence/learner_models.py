"""SQLAlchemy ORM models for mutable learner state and immutable event audit logs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LearnerBase(DeclarativeBase):
    """Base declarative class for the learner state database."""


class LearnerStateORM(LearnerBase):
    """Stores full aggregate state snapshots of learners."""

    __tablename__ = "learner_states"

    learner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningEventORM(LearnerBase):
    """Immutable audit event ledger recording all study and grading events."""

    __tablename__ = "learning_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    learner_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    plan_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    concept_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_seconds: Mapped[int] = mapped_column(Integer, default=60)
    engagement_score: Mapped[float] = mapped_column(Float, default=1.0)
    grading_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Backwards compatibility alias
LearnerStateRecord = LearnerStateORM
