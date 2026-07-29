from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse, StreamingResponse
from starlette.staticfiles import StaticFiles

from dataagent.application.work_order_runtime import WorkOrderRuntime
from dataagent.application.agent_sessions import (
    ConversationStoreAgentSessionRepository,
)
from dataagent.application.agent_turns import WorkOrderRuntimeRootAgent
from dataagent.application.control_tools import WorkOrderControlTools
from dataagent.agents.loop import AgentLoop
from dataagent.agents.requirement import GatewayRequirementPlanner
from dataagent.agents.runner import GatewayAgentPlanner
from dataagent.local_stack import health_payload
from dataagent.application.conversation import ConversationService
from dataagent.config import Settings
from dataagent.domain.operators import OperatorCategory, RuntimeBackend
from dataagent.gateway import ModelGateway


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


class RunControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "cancel"]


class RunFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reusable: bool = False
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = ""


class DatasetExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class DatasetExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1)


class ProviderExecuteRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_operator_ref: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    runtime_backend: RuntimeBackend = RuntimeBackend.CPU


class ConversationMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class ConversationWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_id: str = Field(min_length=1)


class AgentTurnResponse(BaseModel):
    work_order_id: str
    thread_id: str
    state: dict[str, Any]
    interrupts: list[dict[str, Any]]


def require_owner(x_owner_id: Annotated[str, Header(min_length=1)]) -> str:
    return x_owner_id


def create_app(
    runtime: WorkOrderRuntime | None = None,
    conversation_service: ConversationService | None = None,
) -> FastAPI:
    app = FastAPI(title="DataAgent Control Plane", version="0.3.0")
    web_dir = Path(__file__).resolve().parents[1] / "web"
    if web_dir.exists():
        app.mount("/web", StaticFiles(directory=web_dir, html=True), name="web")

        @app.get("/web")
        def web_index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        @app.get("/app")
        def app_index() -> FileResponse:
            return FileResponse(web_dir / "index.html")
    settings = Settings.load()
    gateway = ModelGateway(settings)
    vlm_gateway = gateway.call_vision_model_json if settings.api_key else None
    app.state.work_order_runtime = runtime or WorkOrderRuntime(
        settings.home / "platform",
        include_datajuicer=settings.datajuicer_enabled,
        allow_model_download=settings.allow_model_download,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
        remote_asset_timeout_seconds=settings.remote_asset_timeout_seconds,
        allow_datajuicer_candidate_execution=(
            settings.allow_datajuicer_candidate_execution
        ),
        remote_operator_available=bool(settings.api_key),
        vision_model=settings.vision_model,
        vision_api_base_url=settings.vision_api_base_url,
        vlm_gateway=vlm_gateway,
        requirement_planner=(
            GatewayRequirementPlanner(gateway) if gateway.configured else None
        ),
        agent_planner=(
            GatewayAgentPlanner(gateway) if gateway.configured else None
        ),
        enable_pipeline_trials=gateway.configured,
    )
    work_order_control_tools = WorkOrderControlTools(
        app.state.work_order_runtime
    )
    app.state.work_order_control_tools = work_order_control_tools
    if conversation_service is not None:
        app.state.conversation_service = conversation_service
    elif app.state.work_order_runtime.conversation_store is not None:
        agent_loop = AgentLoop(
            root_agent=WorkOrderRuntimeRootAgent(
                runtime=app.state.work_order_runtime,
                planner=app.state.work_order_runtime.agent_planner,
                control_tools=work_order_control_tools,
            ),
            sessions=ConversationStoreAgentSessionRepository(
                app.state.work_order_runtime.conversation_store
            ),
        )
        app.state.conversation_service = ConversationService(
            store=app.state.work_order_runtime.conversation_store,
            work_order_runtime=app.state.work_order_runtime,
            agent_loop=agent_loop,
        )
    else:
        app.state.conversation_service = None

    def get_work_order_runtime() -> WorkOrderRuntime:
        return app.state.work_order_runtime

    def get_work_order_control_tools() -> WorkOrderControlTools:
        return app.state.work_order_control_tools

    def get_conversation_service() -> ConversationService:
        service = app.state.conversation_service
        if service is None:
            raise HTTPException(
                status_code=422, detail="Persistent runtime is required for conversations"
            )
        return service

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload(role="api")

    @app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
    def create_conversation(
        owner_id: str = Depends(require_owner),
        service: ConversationService = Depends(get_conversation_service),
    ) -> dict[str, Any]:
        return service.create(owner_id)

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(
        conversation_id: str,
        owner_id: str = Depends(require_owner),
        service: ConversationService = Depends(get_conversation_service),
    ) -> dict[str, Any]:
        try:
            return service.get(conversation_id, owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/conversations/{conversation_id}/messages")
    def send_conversation_message(
        conversation_id: str,
        request: ConversationMessageRequest,
        owner_id: str = Depends(require_owner),
        service: ConversationService = Depends(get_conversation_service),
    ) -> dict[str, Any]:
        try:
            return service.send(
                thread_id=conversation_id,
                owner_id=owner_id,
                content=request.content,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/conversations/{conversation_id}/messages/stream")
    def stream_conversation_message(
        conversation_id: str,
        request: ConversationMessageRequest,
        owner_id: str = Depends(require_owner),
        service: ConversationService = Depends(get_conversation_service),
    ) -> StreamingResponse:
        try:
            service.get(conversation_id, owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        def events():
            event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

            def send() -> None:
                try:
                    response = service.send(
                        thread_id=conversation_id,
                        owner_id=owner_id,
                        content=request.content,
                        action_sink=lambda action: event_queue.put(
                            {"type": "action", "action": action}
                        ),
                    )
                    event_queue.put({"type": "final", "response": response})
                except Exception as exc:
                    event_queue.put(
                        {
                            "type": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
                finally:
                    event_queue.put(None)

            threading.Thread(
                target=send,
                name=f"conversation-stream-{conversation_id}",
                daemon=True,
            ).start()
            while True:
                event = event_queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/conversations/{conversation_id}/work-order")
    def bind_conversation_work_order(
        conversation_id: str,
        request: ConversationWorkOrderRequest,
        owner_id: str = Depends(require_owner),
        service: ConversationService = Depends(get_conversation_service),
    ) -> dict[str, Any]:
        try:
            return service.bind_work_order(
                thread_id=conversation_id,
                owner_id=owner_id,
                work_order_id=request.work_order_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/operator-categories")
    def operator_categories(
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, int]:
        del owner_id
        return {
            category.value: count
            for category, count in work_order_runtime.operator_registry.categories().items()
        }

    @app.get("/api/operators")
    def list_operators(
        category: OperatorCategory | None = None,
        include_drafts: bool = False,
        provider_id: str | None = None,
        tag: str | None = None,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        del owner_id
        operators = work_order_runtime.operator_registry.search(
            category=category,
            tags={tag.lower()} if tag else None,
            include_drafts=include_drafts,
        )
        if provider_id:
            operators = [
                item for item in operators if item.provider.provider_id == provider_id
            ]
        return [
            item.model_dump(mode="json")
            for item in operators
        ]

    @app.get("/api/operator-providers")
    def operator_providers(
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        del owner_id
        return work_order_runtime.provider_health()

    @app.get("/api/operator-providers/{provider_id}/operators")
    def provider_operators(
        provider_id: str,
        query: str | None = None,
        limit: int = 100,
        operator_type: str | None = None,
        tag: str | None = None,
        refresh: bool = False,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        del owner_id
        try:
            return work_order_runtime.provider_operators(
                provider_id=provider_id,
                query=query,
                limit=limit,
                operator_type=operator_type,
                tag=tag,
                refresh=refresh,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post(
        "/api/work-orders/{work_order_id}/operator-providers/{provider_id}/execute"
    )
    def execute_provider_operator(
        work_order_id: str,
        provider_id: str,
        request: ProviderExecuteRequestBody,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.execute_provider_operator(
                work_order_id=work_order_id,
                owner_id=owner_id,
                provider_id=provider_id,
                provider_operator_ref=request.provider_operator_ref,
                source_path=request.source_path,
                parameters=request.parameters,
                runtime_backend=request.runtime_backend,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/work-orders/agent/start",
        response_model=AgentTurnResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_agent(
        request: StartAgentRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.start(
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
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
        control_tools: WorkOrderControlTools = Depends(
            get_work_order_control_tools
        ),
    ) -> dict[str, Any]:
        try:
            work_order_runtime.state(
                work_order_id=work_order_id,
                owner_id=owner_id,
            )
            result, _ = control_tools.execute(
                name="resume_work_order",
                owner_id=owner_id,
                raw_input={
                    "work_order_id": work_order_id,
                    "decision": request.decision,
                },
            )
            if not result.ok:
                raise ValueError(result.summary)
            return result.data
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
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.state(work_order_id=work_order_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/work-orders/{work_order_id}/previews")
    def build_preview(
        work_order_id: str,
        request: BuildPreviewRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.build_node_preview(
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

    @app.post("/api/work-orders/{work_order_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def submit_dataset_run(
        work_order_id: str,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
        control_tools: WorkOrderControlTools = Depends(
            get_work_order_control_tools
        ),
    ) -> dict[str, Any]:
        try:
            work_order_runtime.state(
                work_order_id=work_order_id,
                owner_id=owner_id,
            )
            result, _ = control_tools.execute(
                name="submit_dataset_run",
                owner_id=owner_id,
                raw_input={
                    "work_order_id": work_order_id,
                    "idempotency_key": idempotency_key,
                },
            )
            if not result.ok:
                raise ValueError(result.summary)
            return result.data
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/work-orders/{work_order_id}/runs")
    def list_dataset_runs(
        work_order_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        try:
            return work_order_runtime.list_runs(work_order_id=work_order_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_dataset_run(
        run_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.get_run(run_id=run_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/control")
    def control_dataset_run(
        run_id: str,
        request: RunControlRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.control_run(
                run_id=run_id, owner_id=owner_id, action=request.action
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/feedback", status_code=status.HTTP_201_CREATED)
    def record_dataset_run_feedback(
        run_id: str,
        request: RunFeedbackRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.record_run_feedback(
                run_id=run_id,
                owner_id=owner_id,
                accepted=request.accepted,
                reusable=request.reusable,
                rating=request.rating,
                comment=request.comment,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/events")
    def get_dataset_run_events(
        run_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        try:
            return work_order_runtime.get_run_events(run_id=run_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/node-results")
    def get_dataset_run_node_results(
        run_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> list[dict[str, Any]]:
        try:
            return work_order_runtime.get_run_node_results(run_id=run_id, owner_id=owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/repair-candidates")
    def get_run_repair_candidates(
        run_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.repair_candidates(
                reference_id=run_id,
                owner_id=owner_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/repairs", status_code=status.HTTP_202_ACCEPTED)
    def retry_run_failed_assets(
        run_id: str,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.retry_failed_assets(
                previous_run_id=run_id,
                owner_id=owner_id,
                idempotency_key=idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/datasets/{dataset_version_id}")
    def get_dataset_version(
        dataset_version_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.get_dataset(
                dataset_version_id=dataset_version_id, owner_id=owner_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/datasets/{dataset_version_id}/repair-candidates")
    def get_dataset_repair_candidates(
        dataset_version_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.repair_candidates(
                reference_id=dataset_version_id,
                owner_id=owner_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/datasets/{dataset_version_id}/exclude-abandoned")
    def exclude_dataset_abandoned_assets(
        dataset_version_id: str,
        request: DatasetExclusionRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.exclude_abandoned_assets(
                dataset_version_id=dataset_version_id,
                owner_id=owner_id,
                confirmed=request.confirmed,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/datasets/{dataset_version_id}/exports")
    def export_dataset_deliverable(
        dataset_version_id: str,
        request: DatasetExportRequest,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.export_deliverable_dataset(
                dataset_version_id=dataset_version_id,
                owner_id=owner_id,
                destination=Path(request.destination),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/qc-reports/{qc_report_id}")
    def get_qc_report(
        qc_report_id: str,
        owner_id: str = Depends(require_owner),
        work_order_runtime: WorkOrderRuntime = Depends(get_work_order_runtime),
    ) -> dict[str, Any]:
        try:
            return work_order_runtime.get_qc_report(
                qc_report_id=qc_report_id, owner_id=owner_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
