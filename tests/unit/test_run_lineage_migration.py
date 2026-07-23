from __future__ import annotations

import sqlite3
from pathlib import Path

from dataagent.infrastructure import RunStore, SqliteDatabase


def test_existing_run_table_is_migrated_without_losing_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "control.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                id VARCHAR(128) PRIMARY KEY,
                work_order_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(128) NOT NULL,
                pipeline_version_id VARCHAR(128) NOT NULL,
                task_spec_version_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                kept INTEGER NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                idempotency_key VARCHAR(128) NOT NULL,
                dataset_version_id VARCHAR(128),
                error TEXT,
                control_requested BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runs (
                id, work_order_id, owner_id, pipeline_version_id,
                task_spec_version_id, status, idempotency_key,
                created_at, updated_at
            ) VALUES (
                'run_existing', 'work_order_1', 'owner_1', 'pipeline_1',
                'task_spec_1', 'SUCCEEDED', 'existing-request',
                '2026-07-23 12:00:00', '2026-07-23 12:00:00'
            )
            """
        )

    database = SqliteDatabase(database_path)
    run = RunStore(database).get("run_existing", "owner_1")

    assert run["operation_kind"] == "production"
    assert run["parent_run_id"] is None
    assert run["parent_dataset_version_id"] is None
    assert run["repair_scope"] == []
    assert run["repair_attempt"] == 0
