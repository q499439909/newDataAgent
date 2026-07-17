from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import TaskState, utc_now


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskState.DRAFT: {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.PLANNING: {TaskState.WAITING_SPEC_CONFIRMATION, TaskState.FAILED},
    TaskState.WAITING_SPEC_CONFIRMATION: {TaskState.TRIAL_RUNNING, TaskState.CANCELLED},
    TaskState.TRIAL_RUNNING: {TaskState.WAITING_REVIEW, TaskState.FAILED},
    TaskState.WAITING_REVIEW: {TaskState.FULL_RUNNING, TaskState.CANCELLED},
    TaskState.FULL_RUNNING: {TaskState.EVALUATING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.EVALUATING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.FAILED: {TaskState.TRIAL_RUNNING, TaskState.FULL_RUNNING, TaskState.CANCELLED},
    TaskState.COMPLETED: set(),
    TaskState.CANCELLED: set(),
}


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    requirement TEXT NOT NULL,
                    source_path TEXT,
                    state TEXT NOT NULL,
                    spec_json TEXT,
                    selected_pipeline_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipelines (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    trial_json TEXT,
                    reusable INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    dataset_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def create_task(self, owner: str, requirement: str, source_path: str) -> dict[str, Any]:
        task_id = self.new_id("task")
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (task_id, owner, requirement, source_path, TaskState.DRAFT, now, now),
            )
        self.audit(owner, "task.created", "task", task_id, {"source_path": source_path})
        return self.get_task(task_id, owner)

    def get_task(self, task_id: str, owner: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, owner)
            ).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        result = dict(row)
        result["spec"] = json.loads(result.pop("spec_json")) if result["spec_json"] else None
        return result

    def list_tasks(self, owner: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, requirement, source_path, state, created_at, updated_at "
                "FROM tasks WHERE owner=? ORDER BY created_at DESC",
                (owner,),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, task_id: str, owner: str, target: TaskState) -> None:
        task = self.get_task(task_id, owner)
        current = task["state"]
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"Illegal task transition: {current} -> {target}")
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET state=?, updated_at=? WHERE id=? AND owner=?",
                (target, utc_now(), task_id, owner),
            )
        self.audit(owner, "task.transition", "task", task_id, {"from": current, "to": target})

    def save_spec(self, task_id: str, owner: str, spec: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET spec_json=?, updated_at=? WHERE id=? AND owner=?",
                (json.dumps(spec, ensure_ascii=False), utc_now(), task_id, owner),
            )
        self.audit(owner, "task.spec_saved", "task", task_id, {})

    def add_pipeline(self, task_id: str, definition: dict[str, Any]) -> str:
        pipeline_id = self.new_id("pipe")
        with self.connect() as db:
            db.execute(
                "INSERT INTO pipelines VALUES (?, ?, ?, ?, NULL, 0, ?)",
                (
                    pipeline_id,
                    task_id,
                    definition["strategy"],
                    json.dumps(definition, ensure_ascii=False),
                    utc_now(),
                ),
            )
        return pipeline_id

    def pipelines_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM pipelines WHERE task_id=? ORDER BY created_at", (task_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["definition"] = json.loads(item.pop("definition_json"))
            item["trial"] = json.loads(item.pop("trial_json")) if item["trial_json"] else None
            result.append(item)
        return result

    def get_pipeline(self, pipeline_id: str, task_id: str | None = None) -> dict[str, Any]:
        sql = "SELECT * FROM pipelines WHERE id=?"
        params: tuple[Any, ...] = (pipeline_id,)
        if task_id:
            sql += " AND task_id=?"
            params = (pipeline_id, task_id)
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
        if not row:
            raise KeyError(f"Pipeline not found: {pipeline_id}")
        item = dict(row)
        item["definition"] = json.loads(item.pop("definition_json"))
        item["trial"] = json.loads(item.pop("trial_json")) if item["trial_json"] else None
        return item

    def save_trial(self, pipeline_id: str, report: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE pipelines SET trial_json=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), pipeline_id),
            )

    def add_review(
        self, task_id: str, pipeline_id: str, image_path: str, verdict: str
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?)",
                (self.new_id("review"), task_id, pipeline_id, image_path, verdict, utc_now()),
            )

    def reviews_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM reviews WHERE task_id=? ORDER BY created_at", (task_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def select_pipeline(self, task_id: str, owner: str, pipeline_id: str) -> None:
        self.get_pipeline(pipeline_id, task_id)
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET selected_pipeline_id=?, updated_at=? WHERE id=? AND owner=?",
                (pipeline_id, utc_now(), task_id, owner),
            )
        self.audit(owner, "pipeline.selected", "pipeline", pipeline_id, {"task_id": task_id})

    def create_run(self, task_id: str, pipeline_id: str, total: int) -> str:
        run_id = self.new_id("run")
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, 0, ?, NULL, NULL, ?, ?)",
                (run_id, task_id, pipeline_id, "RUNNING", total, now, now),
            )
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {"state", "progress", "total", "dataset_id", "error"}
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(
                f"UPDATE runs SET {assignments} WHERE id=?", (*values.values(), run_id)
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Run not found: {run_id}")
        return dict(row)

    def add_dataset(
        self,
        dataset_id: str,
        task_id: str,
        pipeline_id: str,
        root_path: str,
        manifest_path: str,
        summary: dict[str, Any],
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    task_id,
                    pipeline_id,
                    root_path,
                    manifest_path,
                    json.dumps(summary, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise KeyError(f"Dataset not found: {dataset_id}")
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json"))
        return item

    def list_reusable_pipelines(self, owner: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT p.id, p.strategy, p.definition_json, p.created_at, t.requirement "
                "FROM pipelines p JOIN tasks t ON t.id=p.task_id "
                "WHERE p.reusable=1 AND t.owner=? ORDER BY p.created_at DESC",
                (owner,),
            ).fetchall()
        return [
            {**dict(row), "definition": json.loads(row["definition_json"])} for row in rows
        ]

    def mark_pipeline_reusable(self, pipeline_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE pipelines SET reusable=1 WHERE id=?", (pipeline_id,))

    def audit(
        self,
        owner: str,
        action: str,
        entity_type: str,
        entity_id: str,
        detail: dict[str, Any],
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_logs(owner, action, entity_type, entity_id, detail_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    owner,
                    action,
                    entity_type,
                    entity_id,
                    json.dumps(detail, ensure_ascii=False),
                    utc_now(),
                ),
            )
