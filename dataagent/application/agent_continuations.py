from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any

from ..domain.common import new_id


@dataclass
class _Continuation:
    id: str
    work_order_id: str
    owner_id: str
    status: str
    turn: dict[str, Any]
    operation: str = "resume"
    error: str | None = None


class AgentContinuationManager:
    """Run slow graph continuations outside the request/response lifecycle."""

    def __init__(self, runtime: Any, *, max_workers: int = 4) -> None:
        self.runtime = runtime
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dataagent-turn",
        )
        self._lock = Lock()
        self._turns: dict[str, _Continuation] = {}
        self._active_by_work_order: dict[str, str] = {}

    def submit(
        self,
        *,
        work_order_id: str,
        owner_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.runtime.state(
            work_order_id=work_order_id,
            owner_id=owner_id,
        )
        with self._lock:
            active_id = self._active_by_work_order.get(work_order_id)
            if active_id:
                active = self._turns[active_id]
                if active.owner_id != owner_id:
                    raise PermissionError("WorkOrder is owned by another user")
                return self._public(active)
            self._assert_expected_boundary(current, decision)
            turn_id = new_id("agent_turn")
            queued_turn = self._running_overlay(current, status="queued")
            continuation = _Continuation(
                id=turn_id,
                work_order_id=work_order_id,
                owner_id=owner_id,
                status="queued",
                turn=queued_turn,
                operation="resume",
            )
            self._turns[turn_id] = continuation
            self._active_by_work_order[work_order_id] = turn_id
        self._executor.submit(
            self._run,
            turn_id,
            decision,
        )
        return self._public(continuation)

    def submit_continue(
        self,
        *,
        work_order_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        current = self.runtime.state(
            work_order_id=work_order_id,
            owner_id=owner_id,
        )
        with self._lock:
            active_id = self._active_by_work_order.get(work_order_id)
            if active_id:
                active = self._turns[active_id]
                if active.owner_id != owner_id:
                    raise PermissionError("WorkOrder is owned by another user")
                return self._public(active)
            turn_id = new_id("agent_turn")
            continuation = _Continuation(
                id=turn_id,
                work_order_id=work_order_id,
                owner_id=owner_id,
                status="queued",
                turn=self._running_overlay(current, status="queued"),
                operation="continue",
            )
            self._turns[turn_id] = continuation
            self._active_by_work_order[work_order_id] = turn_id
        self._executor.submit(self._run, turn_id, None)
        return self._public(continuation)

    def get(self, *, turn_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock:
            continuation = self._turns[turn_id]
            if continuation.owner_id != owner_id:
                raise PermissionError("Agent turn is owned by another user")
            return self._public(continuation)

    def active(
        self, *, work_order_id: str, owner_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            turn_id = self._active_by_work_order.get(work_order_id)
            if not turn_id:
                return None
            continuation = self._turns[turn_id]
            if continuation.owner_id != owner_id:
                raise PermissionError("WorkOrder is owned by another user")
            return self._public(continuation)

    def _run(
        self, turn_id: str, decision: dict[str, Any] | None
    ) -> None:
        with self._lock:
            continuation = self._turns[turn_id]
            continuation.status = "running"
            continuation.turn = self._running_overlay(
                continuation.turn,
                status="running",
            )
        try:
            if continuation.operation == "continue":
                result = self.runtime.continue_work_order(
                    work_order_id=continuation.work_order_id,
                    owner_id=continuation.owner_id,
                )
            else:
                result = self.runtime.resume(
                    work_order_id=continuation.work_order_id,
                    owner_id=continuation.owner_id,
                    decision=decision or {},
                )
        except Exception as exc:
            try:
                authoritative = self.runtime.state(
                    work_order_id=continuation.work_order_id,
                    owner_id=continuation.owner_id,
                )
            except Exception:
                authoritative = continuation.turn
            with self._lock:
                continuation.status = "failed"
                continuation.error = f"{type(exc).__name__}: {exc}"
                continuation.turn = authoritative
                self._active_by_work_order.pop(
                    continuation.work_order_id, None
                )
            return
        with self._lock:
            continuation.status = "completed"
            continuation.turn = result
            self._active_by_work_order.pop(continuation.work_order_id, None)

    @staticmethod
    def _assert_expected_boundary(
        turn: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        expected_kind = decision.get("expected_interrupt_kind")
        expected_id = decision.get("expected_interrupt_id")
        if not expected_kind and not expected_id:
            return
        interrupts = turn.get("interrupts") or ()
        if not interrupts:
            raise ValueError("The expected user boundary is no longer active")
        active = interrupts[0]
        value = active.get("value") or {}
        if expected_kind and value.get("kind") != expected_kind:
            raise ValueError(
                "Stale approval boundary: expected "
                f"{expected_kind}, active boundary is {value.get('kind')}"
            )
        if expected_id and active.get("id") != expected_id:
            raise ValueError(
                "Stale approval boundary: interrupt id no longer matches"
            )

    @staticmethod
    def _running_overlay(
        turn: dict[str, Any], *, status: str
    ) -> dict[str, Any]:
        state = dict(turn.get("state") or {})
        interrupts = turn.get("interrupts") or ()
        interrupt_kind = (
            (interrupts[0].get("value") or {}).get("kind")
            if interrupts
            else None
        )
        if (
            not interrupt_kind
            and state.get("turn_status") in {"queued", "running"}
            and state.get("current_agent")
            and state.get("next_action")
        ):
            current_agent = str(state["current_agent"])
            next_action = str(state["next_action"])
            previous_observations = state.get("agent_observations") or ()
            summary = (
                str(previous_observations[-1].get("summary") or "")
                if previous_observations
                else "The background Agent turn is still running."
            )
        elif interrupt_kind == "task_spec_confirmation":
            current_agent = "retrieval"
            next_action = "run_retrieval_agent"
            summary = (
                "The confirmed TaskSpec is being matched against governed "
                "Operator capabilities."
            )
        elif (
            state.get("candidate_sufficient")
            and not state.get("operator_plan")
        ):
            current_agent = "retrieval"
            next_action = "run_retrieval_agent"
            summary = (
                "Legacy shallow candidates are being upgraded to a "
                "self-contained OperatorPlan."
            )
        elif interrupt_kind == "pipeline_approval":
            current_agent = "processing"
            next_action = "trial_selected_pipeline"
            summary = "The selected Pipeline is running a bounded trial."
        else:
            current_agent = "processing"
            next_action = "run_processing_agent"
            summary = (
                "Processing is compiling Pipeline candidates from the "
                "confirmed OperatorPlan."
            )
        observations = list(state.get("agent_observations") or ())
        if not observations or observations[-1].get("turn_id") != turn.get(
            "turn_id"
        ):
            observations.append(
                {
                    "agent": current_agent,
                    "status": status,
                    "summary": summary,
                }
            )
        state.update(
            {
                "current_agent": current_agent,
                "next_action": next_action,
                "turn_status": status,
                "waiting": None,
                "agent_observations": observations,
            }
        )
        return {
            "work_order_id": turn.get("work_order_id"),
            "thread_id": turn.get("thread_id"),
            "state": state,
            "interrupts": [],
        }

    @staticmethod
    def _public(continuation: _Continuation) -> dict[str, Any]:
        return {
            "turn_id": continuation.id,
            "work_order_id": continuation.work_order_id,
            "status": continuation.status,
            "turn": continuation.turn,
            "error": continuation.error,
        }


__all__ = ["AgentContinuationManager"]
