from __future__ import annotations

import json
import os
import uuid
import ctypes
from datetime import UTC, datetime
from pathlib import Path


class WorkerLeaseError(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class WorkerProcessLease:
    """Cross-platform single-process lease for one local durable queue."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> "WorkerProcessLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                owner = self._read_owner()
                pid = int(owner.get("pid", 0) or 0)
                if _pid_is_alive(pid):
                    raise WorkerLeaseError(
                        f"A DataAgent Worker is already active for {self.path.parent} "
                        f"(pid {pid})."
                    )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            payload = {
                "pid": os.getpid(),
                "token": self.token,
                "started_at": datetime.now(UTC).isoformat(),
            }
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
            self.acquired = True
            return
        raise WorkerLeaseError(f"Could not acquire Worker lease: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self._read_owner()
        if owner.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def _read_owner(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


__all__ = ["WorkerLeaseError", "WorkerProcessLease"]
