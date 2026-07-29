from __future__ import annotations

from typing import Any

from ...domain.common import new_id
from ...domain.pipelines import PipelineVersion
from ...domain.plans import SamplingPlanVersion
from ...domain.specs import TaskSpecVersion
from ..runner import AgentPlanner, AgentRunner, AgentTool
from ..shared import WorkOrderGraphState, append_trace


def _default_sampling_plan(
    state: WorkOrderGraphState,
    spec: TaskSpecVersion,
) -> SamplingPlanVersion:
    return SamplingPlanVersion(
        id=new_id("sampling_plan"),
        version=1,
        created_by=state["owner_id"],
        change_reason="initial data strategy",
        task_spec_version_id=spec.id,
        quota=spec.quotas,
        priority_weights={"uncertain_sample": 1.5, "failure_neighbor": 1.8},
        diversity_constraints={
            "max_per_visual_cluster": 50,
            "cross_split_near_duplicate": False,
        },
        random_seed=42,
    )


def generate_sampling_plan(
    state: WorkOrderGraphState,
    *,
    planner: AgentPlanner | None = None,
) -> dict:
    spec = TaskSpecVersion.model_validate(state["task_spec"])
    if planner is None:
        plan = _default_sampling_plan(state, spec)
        return {
            "sampling_plan": plan.model_dump(mode="json"),
            "current_agent": "strategy",
            "next_action": "submit_dataset_run",
            "trace": append_trace(state, "strategy:sampling_plan_generated"),
        }

    built: list[SamplingPlanVersion] = []

    def inspect_approved_pipeline(_payload: dict[str, Any]) -> dict[str, Any]:
        payload = state.get("approved_pipeline")
        if not payload:
            raise ValueError("An approved Pipeline is required before strategy planning.")
        pipeline = PipelineVersion.model_validate(payload)
        return {
            "pipeline": {
                "id": pipeline.id,
                "strategy": pipeline.strategy.value,
                "node_count": len(pipeline.nodes),
                "constraint_coverage_count": len(pipeline.constraint_coverage),
                "approved": pipeline.approved,
            }
        }

    def build_sampling_plan(payload: dict[str, Any]) -> dict[str, Any]:
        quota = payload.get("quota", spec.quotas)
        if not isinstance(quota, dict):
            raise ValueError("quota must be an object")
        priority_weights = payload.get(
            "priority_weights",
            {"uncertain_sample": 1.5, "failure_neighbor": 1.8},
        )
        if not isinstance(priority_weights, dict):
            raise ValueError("priority_weights must be an object")
        diversity_constraints = payload.get(
            "diversity_constraints",
            {
                "max_per_visual_cluster": 50,
                "cross_split_near_duplicate": False,
            },
        )
        if not isinstance(diversity_constraints, dict):
            raise ValueError("diversity_constraints must be an object")
        plan = SamplingPlanVersion(
            id=new_id("sampling_plan"),
            version=1,
            created_by=state["owner_id"],
            change_reason="Strategy Agent built plan through governed tool loop",
            task_spec_version_id=spec.id,
            quota=quota,
            priority_weights=priority_weights,
            diversity_constraints=diversity_constraints,
            random_seed=int(payload.get("random_seed", 42)),
        )
        built.clear()
        built.append(plan)
        return {
            "ok": True,
            "sampling_plan": plan.model_dump(mode="json"),
        }

    loop = AgentRunner(
        agent_name="strategy",
        planner=planner,
        tools=(
            AgentTool(
                name="inspect_approved_pipeline",
                description=(
                    "Inspect the approved PipelineArtifact before deciding "
                    "sampling and execution strategy."
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                execute=inspect_approved_pipeline,
            ),
            AgentTool(
                name="build_sampling_plan",
                description=(
                    "Build and validate a SamplingPlan from model-selected "
                    "quota, priorities, diversity constraints, and random seed."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "quota": {"type": "object"},
                        "priority_weights": {"type": "object"},
                        "diversity_constraints": {"type": "object"},
                        "random_seed": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                execute=build_sampling_plan,
            ),
        ),
        max_iterations=8,
        finish_validator=lambda _payload: {
            "ok": bool(built),
            "errors": []
            if built
            else [
                {
                    "code": "NO_SAMPLING_PLAN",
                    "message": "Call build_sampling_plan before finishing.",
                }
            ],
        },
    )
    result = loop.run(
        goal=(
            "Prepare a data strategy for the approved Pipeline. Use observations "
            "from the approved Pipeline and current task state before finishing."
        ),
        context={
            "task_spec": spec.model_dump(mode="json"),
            "approved_pipeline": state.get("approved_pipeline"),
            "selected_pipeline_id": state.get("selected_pipeline_id"),
            "observations": state.get("agent_observations", ()),
        },
    )
    if result.status != "finished" or not built:
        raise ValueError("Strategy Agent finished without a SamplingPlan")
    plan = built[0]
    observation = {
        "agent": "strategy",
        "status": result.status,
        "summary": result.decisions[-1].reason_summary,
        "tool_observations": [
            item.model_dump(mode="json") for item in result.observations
        ],
    }
    return {
        "sampling_plan": plan.model_dump(mode="json"),
        "agent_observations": [
            *state.get("agent_observations", ()),
            observation,
        ],
        "current_agent": "strategy",
        "next_action": "submit_dataset_run",
        "trace": append_trace(state, "strategy:agent_loop_finished"),
    }
