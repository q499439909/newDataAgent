from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from dataagent.application.agent_runtime import AgentRuntime
from dataagent.config import Settings
from dataagent.domain.operators import OperatorCategory


class StartAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1)
    data_sources: list[dict[str, Any]] = Field(min_length=1)
    work_order_id: str | None = None


class ResumeAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: dict[str, Any]


class BuildPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_version_id: str
    source_path: str


class AgentTurnResponse(BaseModel):
    work_order_id: str
    thread_id: str
    state: dict[str, Any]
    interrupts: list[dict[str, Any]]


def require_owner(x_owner_id: Annotated[str, Header(min_length=1)]) -> str:
    return x_owner_id


def create_app(runtime: AgentRuntime | None = None) -> FastAPI:
    app = FastAPI(title="DataAgent Control Plane", version="0.2.0")
    app.state.agent_runtime = runtime or AgentRuntime(Settings.load().home / "platform")

    def get_runtime() -> AgentRuntime:
        return app.state.agent_runtime

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/operator-categories")
    def operator_categories(
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> dict[str, int]:
        del owner_id
        return {
            category.value: count
            for category, count in agent_runtime.operator_registry.categories().items()
        }

    @app.get("/api/operators")
    def list_operators(
        category: OperatorCategory | None = None,
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> list[dict[str, Any]]:
        del owner_id
        return [
            item.model_dump(mode="json")
            for item in agent_runtime.operator_registry.search(category=category)
        ]

    @app.post(
        "/api/work-orders/agent/start",
        response_model=AgentTurnResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_agent(
        request: StartAgentRequest,
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> dict[str, Any]:
        try:
            return agent_runtime.start(
                owner_id=owner_id,
                requirement=request.requirement,
                data_sources=request.data_sources,
                work_order_id=request.work_order_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/work-orders/{work_order_id}/agent/resume",
        response_model=AgentTurnResponse,
    )
    def resume_agent(
        work_order_id: str,
        request: ResumeAgentRequest,
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> dict[str, Any]:
        try:
            return agent_runtime.resume(
                work_order_id=work_order_id,
                owner_id=owner_id,
                decision=request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/work-orders/{work_order_id}/agent/state",
        response_model=AgentTurnResponse,
    )
    def get_agent_state(
        work_order_id: str,
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> dict[str, Any]:
        try:
            return agent_runtime.state(work_order_id=work_order_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/work-orders/{work_order_id}/previews")
    def build_preview(
        work_order_id: str,
        request: BuildPreviewRequest,
        owner_id: str = Depends(require_owner),
        agent_runtime: AgentRuntime = Depends(get_runtime),
    ) -> dict[str, Any]:
        try:
            return agent_runtime.build_node_preview(
                work_order_id=work_order_id,
                owner_id=owner_id,
                pipeline_version_id=request.pipeline_version_id,
                source_path=request.source_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
