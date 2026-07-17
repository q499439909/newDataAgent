from __future__ import annotations

import json
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
    select,
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


class RunSourceRow(Base):
    __tablename__ = "run_sources"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_source_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_relative_path: Mapped[str] = mapped_column(Text, nullable=False)


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
    ) -> dict[str, Any]:
        with self.database.session() as session, session.begin():
            existing = session.scalar(
                select(RunRow).where(
                    RunRow.owner_id == owner_id,
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                expected = (work_order_id, pipeline_version_id, task_spec_version_id)
                actual = (
                    existing.work_order_id,
                    existing.pipeline_version_id,
                    existing.task_spec_version_id,
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
                    .values(status="RUNNING", updated_at=now)
                )
                if claimed.rowcount == 1:
                    row.status = "RUNNING"
                    row.updated_at = now
                    return self._run_dict(row)

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
        with self.database.session() as session, session.begin():
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
        )

    def mark_evaluating(self, run_id: str) -> None:
        self._update(run_id, status="EVALUATING", control_requested=False)

    def mark_failed(self, run_id: str, error: str) -> None:
        self._update(run_id, status="FAILED", error=error, control_requested=False)

    def _update(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = datetime.now(UTC)
        with self.database.session() as session, session.begin():
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
    def _source_dict(row: RunSourceRow) -> dict[str, Any]:
        return {
            "sequence": row.sequence,
            "source_uri": row.source_uri,
            "source_sha256": row.source_sha256,
            "output_relative_path": row.output_relative_path,
        }
