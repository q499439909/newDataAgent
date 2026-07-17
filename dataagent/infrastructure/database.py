from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class AgentThreadRow(Base):
    __tablename__ = "agent_threads"

    work_order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class DomainVersionRow(Base):
    __tablename__ = "domain_versions"
    __table_args__ = (UniqueConstraint("kind", "entity_id", name="uq_domain_version"),)

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class SqliteDatabase:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.session_factory()


class AgentThreadStore:
    def __init__(self, database: SqliteDatabase):
        self.database = database

    def add(self, *, work_order_id: str, thread_id: str, owner_id: str) -> None:
        with self.database.session() as session, session.begin():
            if session.get(AgentThreadRow, work_order_id):
                raise ValueError(f"Work order already has an agent thread: {work_order_id}")
            session.add(
                AgentThreadRow(
                    work_order_id=work_order_id,
                    thread_id=thread_id,
                    owner_id=owner_id,
                )
            )

    def get(self, work_order_id: str) -> dict[str, str]:
        with self.database.session() as session:
            row = session.get(AgentThreadRow, work_order_id)
            if row is None:
                raise KeyError(f"Work order not found: {work_order_id}")
            return {
                "work_order_id": row.work_order_id,
                "thread_id": row.thread_id,
                "owner_id": row.owner_id,
            }


class DomainVersionStore:
    """Append-only JSON store for formal versioned domain objects."""

    def __init__(self, database: SqliteDatabase):
        self.database = database

    def save_if_absent(self, *, kind: str, owner_id: str, payload: dict[str, Any]) -> bool:
        entity_id = str(payload["id"])
        with self.database.session() as session, session.begin():
            existing = session.scalar(
                select(DomainVersionRow).where(
                    DomainVersionRow.kind == kind,
                    DomainVersionRow.entity_id == entity_id,
                )
            )
            if existing is not None:
                # The same immutable object may be present in later graph snapshots.
                if json.loads(existing.payload_json) != payload:
                    raise ValueError(f"Immutable version payload changed: {kind}/{entity_id}")
                return False
            session.add(
                DomainVersionRow(
                    kind=kind,
                    entity_id=entity_id,
                    owner_id=owner_id,
                    parent_version_id=payload.get("parent_version_id"),
                    version=int(payload.get("version", 1)),
                    payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
            )
            return True

    def get(self, *, kind: str, entity_id: str, owner_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.scalar(
                select(DomainVersionRow).where(
                    DomainVersionRow.kind == kind,
                    DomainVersionRow.entity_id == entity_id,
                    DomainVersionRow.owner_id == owner_id,
                )
            )
            if row is None:
                raise KeyError(f"Domain version not found: {kind}/{entity_id}")
            return json.loads(row.payload_json)

    def list_for_owner(self, *, kind: str, owner_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DomainVersionRow)
                .where(DomainVersionRow.kind == kind, DomainVersionRow.owner_id == owner_id)
                .order_by(DomainVersionRow.sequence)
            ).all()
            return [json.loads(row.payload_json) for row in rows]
