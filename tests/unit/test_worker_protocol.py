from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from dataagent.infrastructure import RunStore, SqliteDatabase
from dataagent.worker_lease import WorkerLeaseError, WorkerProcessLease


def _queued_run(store: RunStore) -> dict:
    return store.create(
        run_id="run_1",
        work_order_id="work_order_1",
        owner_id="user_1",
        pipeline_version_id="pipeline_1",
        task_spec_version_id="spec_1",
        idempotency_key="request_1",
    )


def test_queue_rejects_legacy_worker_claim_and_accepts_current_protocol(
    tmp_path,
) -> None:
    database = SqliteDatabase(tmp_path / "control.db")
    store = RunStore(database)
    _queued_run(store)

    with pytest.raises(
        IntegrityError,
        match="Worker is too old for the current queue protocol",
    ):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE runs SET status = 'RUNNING' "
                    "WHERE id = 'run_1' AND status = 'QUEUED'"
                )
            )

    claimed = store.claim_next()

    assert claimed is not None
    assert claimed["id"] == "run_1"
    with database.engine.connect() as connection:
        protocol = connection.execute(
            text(
                "SELECT claim_protocol_version FROM runs WHERE id = 'run_1'"
            )
        ).scalar_one()
    assert protocol == 2


def test_worker_process_lease_is_single_owner_and_reclaims_stale_file(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "worker.lock.json"
    first = WorkerProcessLease(path)
    first.acquire()
    try:
        with pytest.raises(WorkerLeaseError, match="already active"):
            WorkerProcessLease(path).acquire()
    finally:
        first.release()

    path.write_text(
        json.dumps({"pid": os.getpid() + 1_000_000, "token": "stale"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "dataagent.worker_lease._pid_is_alive",
        lambda pid: False,
    )

    with WorkerProcessLease(path):
        assert path.is_file()

    assert not path.exists()
