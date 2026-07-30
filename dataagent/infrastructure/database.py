from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
    text,
    update,
)
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


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_run_request"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pipeline_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_spec_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kept: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    operation_kind: Mapped[str] = mapped_column(
        String(32), default="production", nullable=False
    )
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_dataset_version_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    repair_scope_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    repair_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claim_protocol_version: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class RunItemRow(Base):
    __tablename__ = "run_items"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_item_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)


class RunNodeResultRow(Base):
    __tablename__ = "run_node_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "asset_sequence", "node_id", name="uq_run_asset_node_result"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    asset_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operator_version_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class RunSourceRow(Base):
    __tablename__ = "run_sources"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_source_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_relative_path: Mapped[str] = mapped_column(Text, nullable=False)


class RunEventRow(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ConversationThreadRow(Base):
    __tablename__ = "conversation_threads"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    work_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    context_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_conversation_message_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
        # WAL lets concurrent readers coexist with a single writer; busy_timeout
        # makes a contending writer wait instead of failing with "database is
        # locked". Both are applied per-connection so worker threads stay safe.
        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        # Serializes writers across threads within this process so the count
        # recompute in RunStore.add_item and similar read-modify-write blocks
        # cannot race. WAL allows readers to proceed without the lock.
        self._write_lock = threading.RLock()
        self._migrate_run_lineage()
        self._install_worker_claim_guard()

    @property
    def write_lock(self) -> threading.RLock:
        return self._write_lock

    def session(self) -> Session:
        return self.session_factory()

    def _migrate_run_lineage(self) -> None:
        columns = {item["name"] for item in inspect(self.engine).get_columns("runs")}
        additions = {
            "operation_kind": "VARCHAR(32) NOT NULL DEFAULT 'production'",
            "parent_run_id": "VARCHAR(128)",
            "parent_dataset_version_id": "VARCHAR(128)",
            "repair_scope_json": "TEXT NOT NULL DEFAULT '[]'",
            "repair_attempt": "INTEGER NOT NULL DEFAULT 0",
            "claim_protocol_version": "INTEGER NOT NULL DEFAULT 0",
        }
        with self.engine.begin() as connection:
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
                    )

    def _install_worker_claim_guard(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS require_worker_claim_protocol"))
            connection.execute(
                text(
                    """
                    CREATE TRIGGER require_worker_claim_protocol
                    BEFORE UPDATE OF status ON runs
                    WHEN OLD.status = 'QUEUED'
                      AND NEW.status = 'RUNNING'
                      AND NEW.claim_protocol_version < 2
                    BEGIN
                      SELECT RAISE(
                        ABORT,
                        'Worker is too old for the current queue protocol'
                      );
                    END
                    """
                )
            )


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

    def delete(self, work_order_id: str) -> None:
        with self.database.session() as session, session.begin():
            row = session.get(AgentThreadRow, work_order_id)
            if row is not None:
                session.delete(row)


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


class RunStore:
    """Durable queue and progress store used by the control plane and worker."""

    def __init__(self, database: SqliteDatabase):
        self.database = database

    def create(
        self,
        *,
        run_id: str,
        work_order_id: str,
        owner_id: str,
        pipeline_version_id: str,
        task_spec_version_id: str,
        idempotency_key: str,
        operation_kind: str = "production",
        parent_run_id: str | None = None,
        parent_dataset_version_id: str | None = None,
        repair_scope: tuple[dict[str, Any], ...] = (),
        repair_attempt: int = 0,
    ) -> dict[str, Any]:
        if operation_kind == "production":
            if parent_run_id or parent_dataset_version_id or repair_scope or repair_attempt:
                raise ValueError("Production Run cannot carry repair lineage")
        elif operation_kind == "repair":
            if not parent_run_id or not repair_scope or repair_attempt < 1:
                raise ValueError(
                    "Repair Run requires parent_run_id, repair_scope, and repair_attempt"
                )
        else:
            raise ValueError(f"Unsupported Run operation kind: {operation_kind}")
        with self.database.session() as session, session.begin():
            existing = session.scalar(
                select(RunRow).where(
                    RunRow.owner_id == owner_id,
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                expected = (
                    work_order_id,
                    pipeline_version_id,
                    task_spec_version_id,
                    operation_kind,
                    parent_run_id,
                    parent_dataset_version_id,
                    list(repair_scope),
                    repair_attempt,
                )
                actual = (
                    existing.work_order_id,
                    existing.pipeline_version_id,
                    existing.task_spec_version_id,
                    existing.operation_kind,
                    existing.parent_run_id,
                    existing.parent_dataset_version_id,
                    json.loads(existing.repair_scope_json),
                    existing.repair_attempt,
                )
                if actual != expected:
                    raise ValueError("Idempotency key was already used for another run request")
                return self._run_dict(existing)
            row = RunRow(
                id=run_id,
                work_order_id=work_order_id,
                owner_id=owner_id,
                pipeline_version_id=pipeline_version_id,
                task_spec_version_id=task_spec_version_id,
                status="QUEUED",
                idempotency_key=idempotency_key,
                operation_kind=operation_kind,
                parent_run_id=parent_run_id,
                parent_dataset_version_id=parent_dataset_version_id,
                repair_scope_json=json.dumps(
                    repair_scope, ensure_ascii=False, sort_keys=True
                ),
                repair_attempt=repair_attempt,
            )
            session.add(row)
            session.flush()
            return self._run_dict(row)

    def get(self, run_id: str, owner_id: str | None = None) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(f"Run not found: {run_id}")
            if owner_id is not None and row.owner_id != owner_id:
                raise PermissionError("Run belongs to another owner")
            return self._run_dict(row)

    def list_for_work_order(self, work_order_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RunRow)
                .where(
                    RunRow.work_order_id == work_order_id,
                    RunRow.owner_id == owner_id,
                )
                .order_by(RunRow.created_at.desc())
            ).all()
            return [self._run_dict(row) for row in rows]

    def claim_next(self) -> dict[str, Any] | None:
        while True:
            with self.database.session() as session, session.begin():
                row = session.scalar(
                    select(RunRow)
                    .where(RunRow.status == "QUEUED")
                    .order_by(RunRow.created_at)
                    .limit(1)
                )
                if row is None:
                    return None
                now = datetime.now(UTC)
                claimed = session.execute(
                    update(RunRow)
                    .where(RunRow.id == row.id, RunRow.status == "QUEUED")
                    .values(
                        status="RUNNING",
                        updated_at=now,
                        claim_protocol_version=2,
                    )
                )
                if claimed.rowcount == 1:
                    row.status = "RUNNING"
                    row.updated_at = now
                    return self._run_dict(row)

    def pending_outcome_notifications(self) -> list[dict[str, Any]]:
        terminal_statuses = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")
        with self.database.session() as session:
            rows = session.scalars(
                select(RunRow)
                .where(RunRow.status.in_(terminal_statuses))
                .order_by(RunRow.updated_at)
            ).all()
            pending: list[dict[str, Any]] = []
            for row in rows:
                requested = session.scalar(
                    select(RunEventRow.id)
                    .where(
                        RunEventRow.run_id == row.id,
                        RunEventRow.event_type
                        == "run_outcome_notification_requested",
                    )
                    .limit(1)
                )
                notified = session.scalar(
                    select(RunEventRow.id)
                    .where(
                        RunEventRow.run_id == row.id,
                        RunEventRow.event_type == "run_outcome_notified",
                    )
                    .limit(1)
                )
                if requested is not None and notified is None:
                    pending.append(self._run_dict(row))
            return pending

    def recover_interrupted(self) -> None:
        """Return runs left by a dead worker to a controllable durable state."""
        now = datetime.now(UTC)
        with self.database.session() as session, session.begin():
            session.execute(
                update(RunRow)
                .where(RunRow.status.in_(("RUNNING", "EVALUATING")))
                .values(status="QUEUED", updated_at=now)
            )
            session.execute(
                update(RunRow)
                .where(RunRow.status == "PAUSING")
                .values(status="PAUSED", updated_at=now)
            )
            session.execute(
                update(RunRow)
                .where(RunRow.status == "CANCELLING")
                .values(status="CANCELLED", updated_at=now)
            )

    def set_total(self, run_id: str, total: int) -> None:
        self._update(run_id, total=total)

    def initialize_plan(
        self, run_id: str, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        with self.database.session() as session, session.begin():
            existing = session.scalars(
                select(RunSourceRow)
                .where(RunSourceRow.run_id == run_id)
                .order_by(RunSourceRow.sequence)
            ).all()
            if existing:
                return [self._source_dict(row) for row in existing]
            for item in sources:
                session.add(
                    RunSourceRow(
                        run_id=run_id,
                        sequence=int(item["sequence"]),
                        source_uri=item["source_uri"],
                        source_sha256=item["source_sha256"],
                        output_relative_path=item["output_relative_path"],
                    )
                )
            session.flush()
            return sources

    def plan(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RunSourceRow)
                .where(RunSourceRow.run_id == run_id)
                .order_by(RunSourceRow.sequence)
            ).all()
            return [self._source_dict(row) for row in rows]

    def add_item(self, run_id: str, item: dict[str, Any]) -> None:
        with self.database.write_lock, self.database.session() as session, session.begin():
            session.add(
                RunItemRow(
                    run_id=run_id,
                    sequence=int(item["sequence"]),
                    source_uri=item["source_uri"],
                    source_sha256=item["source_sha256"],
                    output_relative_path=item.get("output_relative_path"),
                    output_sha256=item.get("output_sha256"),
                    decision=item["decision"],
                    reason_codes_json=json.dumps(item.get("reason_codes", [])),
                    metrics_json=json.dumps(item.get("metrics", {}), ensure_ascii=False),
                    labels_json=json.dumps(item.get("labels", {}), ensure_ascii=False),
                )
            )
            session.flush()
            counts = self._item_counts(session, run_id)
            session.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(progress=sum(counts.values()), updated_at=datetime.now(UTC), **counts)
            )

    def items(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RunItemRow)
                .where(RunItemRow.run_id == run_id)
                .order_by(RunItemRow.sequence)
            ).all()
            return [self._item_dict(row) for row in rows]

    def add_node_result(self, run_id: str, result: dict[str, Any]) -> None:
        with self.database.write_lock, self.database.session() as session, session.begin():
            session.add(
                RunNodeResultRow(
                    run_id=run_id,
                    asset_sequence=int(result["asset_sequence"]),
                    source_uri=str(result["source_uri"]),
                    node_id=str(result["node_id"]),
                    operator_version_id=str(result["operator_version_id"]),
                    status=str(result["status"]),
                    decision=str(result["decision"]),
                    reason_codes_json=json.dumps(result.get("reason_codes", [])),
                    metrics_json=json.dumps(
                        result.get("metrics", {}), ensure_ascii=False, default=str
                    ),
                    labels_json=json.dumps(
                        result.get("labels", {}), ensure_ascii=False, default=str
                    ),
                    error=result.get("error"),
                    duration_ms=int(result.get("duration_ms", 0)),
                )
            )

    def node_results(
        self, run_id: str, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        self.get(run_id, owner_id)
        with self.database.session() as session:
            rows = session.scalars(
                select(RunNodeResultRow)
                .where(RunNodeResultRow.run_id == run_id)
                .order_by(RunNodeResultRow.asset_sequence, RunNodeResultRow.id)
            ).all()
            return [self._node_result_dict(row) for row in rows]

    def add_event(
        self,
        run_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        *,
        node_id: str | None = None,
        provider_id: str | None = None,
        message: str = "",
        progress: int | None = None,
    ) -> dict[str, Any]:
        with self.database.write_lock, self.database.session() as session, session.begin():
            if session.get(RunRow, run_id) is None:
                raise KeyError(f"Run not found: {run_id}")
            row = RunEventRow(
                run_id=run_id,
                event_type=event_type,
                node_id=node_id,
                provider_id=provider_id,
                message=message,
                progress=progress,
                details_json=json.dumps(details or {}, ensure_ascii=False, default=str),
            )
            session.add(row)
            session.flush()
            return self._event_dict(row)

    def request_outcome_notification(
        self,
        run_id: str,
        *,
        work_order_id: str,
    ) -> None:
        with self.database.write_lock, self.database.session() as session, session.begin():
            existing = session.scalar(
                select(RunEventRow.id)
                .where(
                    RunEventRow.run_id == run_id,
                    RunEventRow.event_type
                    == "run_outcome_notification_requested",
                )
                .limit(1)
            )
            if existing is not None:
                return
            session.add(
                RunEventRow(
                    run_id=run_id,
                    event_type="run_outcome_notification_requested",
                    details_json=json.dumps(
                        {"work_order_id": work_order_id},
                        ensure_ascii=False,
                    ),
                )
            )

    def events(self, run_id: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        self.get(run_id, owner_id)
        with self.database.session() as session:
            rows = session.scalars(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id)
                .order_by(RunEventRow.id)
            ).all()
            return [self._event_dict(row) for row in rows]

    def request_pause(self, run_id: str, owner_id: str) -> dict[str, Any]:
        run = self.get(run_id, owner_id)
        if run["status"] == "QUEUED":
            self._update(run_id, status="PAUSED", control_requested=True)
        elif run["status"] == "RUNNING":
            self._update(run_id, status="PAUSING", control_requested=True)
        elif run["status"] not in {"PAUSING", "PAUSED"}:
            raise ValueError(f"Run cannot be paused from {run['status']}")
        return self.get(run_id, owner_id)

    def resume(self, run_id: str, owner_id: str) -> dict[str, Any]:
        run = self.get(run_id, owner_id)
        if run["status"] != "PAUSED":
            raise ValueError(f"Run cannot be resumed from {run['status']}")
        self._update(run_id, status="QUEUED", control_requested=False, error=None)
        return self.get(run_id, owner_id)

    def request_cancel(self, run_id: str, owner_id: str) -> dict[str, Any]:
        run = self.get(run_id, owner_id)
        if run["status"] in {"QUEUED", "PAUSED"}:
            self._update(run_id, status="CANCELLED", control_requested=True)
        elif run["status"] in {"RUNNING", "PAUSING"}:
            self._update(run_id, status="CANCELLING", control_requested=True)
        elif run["status"] not in {"CANCELLING", "CANCELLED"}:
            raise ValueError(f"Run cannot be cancelled from {run['status']}")
        return self.get(run_id, owner_id)

    def mark_paused(self, run_id: str) -> None:
        self._update(run_id, status="PAUSED", control_requested=True)

    def mark_cancelled(self, run_id: str) -> None:
        self._update(run_id, status="CANCELLED", control_requested=True)

    def mark_succeeded(self, run_id: str, dataset_version_id: str) -> None:
        self._update(
            run_id,
            status="SUCCEEDED",
            dataset_version_id=dataset_version_id,
            control_requested=False,
            error=None,
        )

    def mark_partial(
        self, run_id: str, dataset_version_id: str, error: str
    ) -> None:
        self._update(
            run_id,
            status="PARTIAL",
            dataset_version_id=dataset_version_id,
            error=error,
            control_requested=False,
        )

    def mark_evaluating(self, run_id: str) -> None:
        self._update(run_id, status="EVALUATING", control_requested=False)

    def mark_failed(self, run_id: str, error: str) -> None:
        self._update(run_id, status="FAILED", error=error, control_requested=False)

    def mark_quality_failed(
        self, run_id: str, dataset_version_id: str, error: str
    ) -> None:
        self._update(
            run_id,
            status="FAILED",
            dataset_version_id=dataset_version_id,
            error=error,
            control_requested=False,
        )

    def _update(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = datetime.now(UTC)
        with self.database.write_lock, self.database.session() as session, session.begin():
            result = session.execute(update(RunRow).where(RunRow.id == run_id).values(**values))
            if result.rowcount != 1:
                raise KeyError(f"Run not found: {run_id}")

    @staticmethod
    def _item_counts(session: Session, run_id: str) -> dict[str, int]:
        rows = session.scalars(select(RunItemRow).where(RunItemRow.run_id == run_id)).all()
        return {
            "kept": sum(row.decision == "keep" for row in rows),
            "rejected": sum(row.decision == "reject" for row in rows),
            "failed": sum(row.decision == "failed" for row in rows),
        }

    @staticmethod
    def _run_dict(row: RunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "work_order_id": row.work_order_id,
            "owner_id": row.owner_id,
            "pipeline_version_id": row.pipeline_version_id,
            "task_spec_version_id": row.task_spec_version_id,
            "status": row.status,
            "progress": row.progress,
            "total": row.total,
            "kept": row.kept,
            "rejected": row.rejected,
            "failed": row.failed,
            "idempotency_key": row.idempotency_key,
            "dataset_version_id": row.dataset_version_id,
            "error": row.error,
            "operation_kind": row.operation_kind,
            "parent_run_id": row.parent_run_id,
            "parent_dataset_version_id": row.parent_dataset_version_id,
            "repair_scope": json.loads(row.repair_scope_json),
            "repair_attempt": row.repair_attempt,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _item_dict(row: RunItemRow) -> dict[str, Any]:
        return {
            "sequence": row.sequence,
            "source_uri": row.source_uri,
            "source_sha256": row.source_sha256,
            "output_relative_path": row.output_relative_path,
            "output_sha256": row.output_sha256,
            "decision": row.decision,
            "reason_codes": json.loads(row.reason_codes_json),
            "metrics": json.loads(row.metrics_json),
            "labels": json.loads(row.labels_json),
        }

    @staticmethod
    def _node_result_dict(row: RunNodeResultRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "asset_sequence": row.asset_sequence,
            "source_uri": row.source_uri,
            "node_id": row.node_id,
            "operator_version_id": row.operator_version_id,
            "status": row.status,
            "decision": row.decision,
            "reason_codes": json.loads(row.reason_codes_json),
            "metrics": json.loads(row.metrics_json),
            "labels": json.loads(row.labels_json),
            "error": row.error,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at,
        }

    @staticmethod
    def _source_dict(row: RunSourceRow) -> dict[str, Any]:
        return {
            "sequence": row.sequence,
            "source_uri": row.source_uri,
            "source_sha256": row.source_sha256,
            "output_relative_path": row.output_relative_path,
        }

    @staticmethod
    def _event_dict(row: RunEventRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "event_type": row.event_type,
            "node_id": row.node_id,
            "provider_id": row.provider_id,
            "message": row.message,
            "progress": row.progress,
            "details": json.loads(row.details_json),
            "created_at": row.created_at,
        }


class ConversationStore:
    def __init__(self, database: SqliteDatabase):
        self.database = database

    def create(self, *, thread_id: str, owner_id: str) -> dict[str, Any]:
        with self.database.session() as session, session.begin():
            row = ConversationThreadRow(id=thread_id, owner_id=owner_id)
            session.add(row)
            session.flush()
            return self._thread_dict(row)

    def get(self, thread_id: str, owner_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ConversationThreadRow, thread_id)
            if row is None:
                raise KeyError(f"Conversation not found: {thread_id}")
            if row.owner_id != owner_id:
                raise PermissionError("Conversation belongs to another owner")
            return self._thread_dict(row)

    def update(
        self,
        *,
        thread_id: str,
        owner_id: str,
        context: dict[str, Any],
        work_order_id: str | None = None,
    ) -> dict[str, Any]:
        self.get(thread_id, owner_id)
        values: dict[str, Any] = {
            "context_json": json.dumps(context, ensure_ascii=False),
            "updated_at": datetime.now(UTC),
        }
        if work_order_id is not None:
            values["work_order_id"] = work_order_id
        with self.database.session() as session, session.begin():
            session.execute(
                update(ConversationThreadRow)
                .where(ConversationThreadRow.id == thread_id)
                .values(**values)
            )
        return self.get(thread_id, owner_id)

    def add_message(
        self,
        *,
        thread_id: str,
        owner_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        self.get(thread_id, owner_id)
        with self.database.session() as session, session.begin():
            last = session.scalar(
                select(ConversationMessageRow.sequence)
                .where(ConversationMessageRow.thread_id == thread_id)
                .order_by(ConversationMessageRow.sequence.desc())
                .limit(1)
            )
            row = ConversationMessageRow(
                thread_id=thread_id,
                sequence=(last or 0) + 1,
                role=role,
                content=content,
                intent=intent,
                model=model,
            )
            session.add(row)
            session.flush()
            return self._message_dict(row)

    def messages(self, thread_id: str, owner_id: str) -> list[dict[str, Any]]:
        self.get(thread_id, owner_id)
        with self.database.session() as session:
            rows = session.scalars(
                select(ConversationMessageRow)
                .where(ConversationMessageRow.thread_id == thread_id)
                .order_by(ConversationMessageRow.sequence)
            ).all()
            return [self._message_dict(row) for row in rows]

    @staticmethod
    def _thread_dict(row: ConversationThreadRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "owner_id": row.owner_id,
            "work_order_id": row.work_order_id,
            "context": json.loads(row.context_json),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _message_dict(row: ConversationMessageRow) -> dict[str, Any]:
        return {
            "sequence": row.sequence,
            "role": row.role,
            "content": row.content,
            "intent": row.intent,
            "model": row.model,
            "created_at": row.created_at,
        }
