from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dataagent.agents.requirement import build_requirement_graph
from dataagent.agents.requirement.nodes import generate_task_spec
from dataagent.agents.runner import AgentDecision
from dataagent.domain.specs import RequirementDraft


class ScriptedPlanner:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decisions.pop(0)


class GapResolvingRequirementPlanner:
    def __init__(self) -> None:
        self.plan_calls = 0
        self.resolve_calls = 0

    def plan(self, request):
        self.plan_calls += 1
        return RequirementDraft.model_validate(
            _draft(
                request.requirement,
                with_gap=self.plan_calls == 1,
            )
        )

    def resolve_gaps(
        self,
        *,
        gaps,
        answer,
        messages,
        draft,
        requirement,
        data_sources,
    ):
        del draft, requirement, data_sources
        self.resolve_calls += 1
        resolved = "version 7" in answer.lower()
        revised_draft = None
        if resolved:
            revised_draft = RequirementDraft.model_validate(
                {
                    "objective": "Apply policy version 7 to the records.",
                    "constraints": [],
                    "clause_traces": [
                        {
                            "source_text": messages[0]["content"],
                            "role": "context",
                        },
                        {
                            "source_text": answer,
                            "role": "context",
                        },
                    ],
                    "gaps": [],
                }
            ).model_dump(mode="json")
        return {
            "reply": (
                "The policy reference is now complete."
                if resolved
                else "I still need the policy version."
            ),
            "requirement_relevant": resolved,
            "resolutions": [
                {
                    "gap_id": gaps[0]["id"],
                    "resolved": resolved,
                    "normalized_answer": answer if resolved else "",
                }
            ],
            "revised_draft": revised_draft,
        }


def _draft(requirement: str, *, with_gap: bool) -> dict:
    payload = {
        "objective": "Apply the requested policy to the records.",
        "constraints": [],
        "clause_traces": [
            {
                "source_text": requirement,
                "role": "context",
                "constraint_refs": [],
            }
        ],
        "gaps": [],
    }
    if with_gap:
        payload["gaps"] = [
            {
                "id": "gap_reference",
                "kind": "missing_reference",
                "source_texts": [requirement],
                "description": "The referenced policy is not identified.",
                "blocking_reason": (
                    "The acceptance condition cannot be evaluated without it."
                ),
                "question": "Which policy or policy version should be applied?",
                "answer_schema": {"type": "string", "minLength": 1},
            }
        ]
    return RequirementDraft.model_validate(payload).model_dump(mode="json")


@pytest.mark.parametrize(
    "requirement",
    [
        "Process the records using the approved policy.",
        "Score each audio clip against the selected rubric.",
        "Retain documents that satisfy the referenced standard.",
    ],
)
def test_model_authored_requirement_gap_drives_clarification(
    requirement: str,
) -> None:
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Validate the interpreted requirement.",
                tool_name="validate_requirement_draft",
                tool_input=_draft(requirement, with_gap=True),
            ),
            AgentDecision(
                action="finish",
                reason_summary="A blocking reference is missing.",
                output={"reply": "I need one clarification."},
            ),
        ]
    )

    result = generate_task_spec(
        {
            "work_order_id": "work_order_1",
            "owner_id": "user_1",
            "requirement": requirement,
            "requirement_messages": [{"role": "user", "content": requirement}],
            "data_sources": [
                {"type": "local_directory", "uri": "D:/records", "mapping": {}}
            ],
            "trace": [],
        },
        agent_planner=planner,
    )

    assert result["next_action"] == "clarify_requirement"
    assert result["requirement_clarification_request"]["questions"] == [
        {
            "gap_id": "gap_reference",
            "question": "Which policy or policy version should be applied?",
            "answer_schema": {"type": "string", "minLength": 1},
        }
    ]
    assert "task_spec" not in result


def test_clarification_answer_is_preserved_as_a_new_requirement_message() -> None:
    requirement = "Process the records using the approved policy."
    answer = "Use policy version 7."
    resolved_requirement = f"{requirement}\n{answer}"
    resolved = _draft(resolved_requirement, with_gap=False)
    resolved["clause_traces"] = [
        {
            "source_text": requirement,
            "role": "context",
            "constraint_refs": [],
        },
        {
            "source_text": answer,
            "role": "context",
            "constraint_refs": [],
        },
    ]
    planner = ScriptedPlanner(
        [
            AgentDecision(
                action="tool",
                reason_summary="Validate the first interpretation.",
                tool_name="validate_requirement_draft",
                tool_input=_draft(requirement, with_gap=True),
            ),
            AgentDecision(
                action="finish",
                reason_summary="The reference is missing.",
                output={"reply": "I need one clarification."},
            ),
            AgentDecision(
                action="tool",
                reason_summary="Validate the clarified interpretation.",
                tool_name="validate_requirement_draft",
                tool_input=resolved,
            ),
            AgentDecision(
                action="finish",
                reason_summary="The requirement is now complete.",
                output={"reply": "The TaskSpec is ready for confirmation."},
            ),
        ]
    )
    graph = build_requirement_graph(
        agent_planner=planner,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "requirement-gap-thread"}}
    initial = {
        "work_order_id": "work_order_1",
        "owner_id": "user_1",
        "requirement": requirement,
        "requirement_messages": [{"role": "user", "content": requirement}],
        "data_sources": [
            {"type": "local_directory", "uri": "D:/records", "mapping": {}}
        ],
        "trace": [],
    }

    waiting = graph.invoke(initial, config)
    assert waiting["__interrupt__"][0].value["kind"] == "requirement_clarification"

    result = graph.invoke(Command(resume={"answer": answer}), config)

    assert result["requirement"] == requirement
    assert result["requirement_messages"][-1] == {
        "role": "user",
        "content": answer,
        "responding_to": ["gap_reference"],
    }
    assert result["task_spec"]["gaps"] == []
    assert result["next_action"] == "confirm_task_spec"


def test_unclear_clarification_reasks_without_regenerating_the_draft() -> None:
    planner = GapResolvingRequirementPlanner()
    graph = build_requirement_graph(
        requirement_planner=planner,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "unclear-gap-thread"}}
    initial = {
        "work_order_id": "work_order_1",
        "owner_id": "user_1",
        "requirement": "Process the records using the approved policy.",
        "requirement_messages": [
            {
                "role": "user",
                "content": "Process the records using the approved policy.",
            }
        ],
        "data_sources": [
            {"type": "local_directory", "uri": "D:/records", "mapping": {}}
        ],
        "trace": [],
    }

    waiting = graph.invoke(initial, config)
    repeated = graph.invoke(Command(resume={"answer": "Why is this slow?"}), config)

    assert waiting["__interrupt__"][0].value["kind"] == "requirement_clarification"
    assert repeated["__interrupt__"][0].value["kind"] == "requirement_clarification"
    assert "仍有 1 项需求缺口没有满足" in (
        repeated["__interrupt__"][0].value["summary"]
    )
    assert planner.plan_calls == 1
    assert planner.resolve_calls == 1


def test_clear_clarification_revises_persisted_draft_without_replanning() -> None:
    planner = GapResolvingRequirementPlanner()
    graph = build_requirement_graph(
        requirement_planner=planner,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "resolved-gap-thread"}}
    initial = {
        "work_order_id": "work_order_1",
        "owner_id": "user_1",
        "requirement": "Process the records using the approved policy.",
        "requirement_messages": [
            {
                "role": "user",
                "content": "Process the records using the approved policy.",
            }
        ],
        "data_sources": [
            {"type": "local_directory", "uri": "D:/records", "mapping": {}}
        ],
        "trace": [],
    }

    graph.invoke(initial, config)
    result = graph.invoke(Command(resume={"answer": "Use policy version 7."}), config)

    assert result["next_action"] == "confirm_task_spec"
    assert result["requirement_messages"][-1]["content"] == "Use policy version 7."
    assert planner.plan_calls == 1
    assert planner.resolve_calls == 1


def test_requirement_planning_does_not_silently_fallback_to_keywords() -> None:
    with pytest.raises(RuntimeError, match="REQUIREMENT_PLANNER_UNAVAILABLE"):
        generate_task_spec(
            {
                "work_order_id": "work_order_1",
                "owner_id": "user_1",
                "requirement": "A completely new kind of task.",
                "data_sources": [
                    {"type": "local_directory", "uri": "D:/records", "mapping": {}}
                ],
                "trace": [],
            }
        )


def test_specialized_requirement_planner_precedes_generic_agent_planner() -> None:
    requirement = "Process records using the approved policy."
    draft = RequirementDraft.model_validate(
        _draft(requirement, with_gap=True)
    )

    class SpecializedPlanner:
        def plan(self, request):
            assert request.messages[-1]["content"] == requirement
            return draft

    class GenericPlanner:
        def decide(self, request):
            raise AssertionError(
                "Generic Agent planning should not shadow the specialized port"
            )

    result = generate_task_spec(
        {
            "work_order_id": "work_order_priority",
            "owner_id": "user_1",
            "requirement": requirement,
            "requirement_messages": [{"role": "user", "content": requirement}],
            "data_sources": [
                {"type": "local_directory", "uri": "D:/records", "mapping": {}}
            ],
            "trace": [],
        },
        requirement_planner=SpecializedPlanner(),
        agent_planner=GenericPlanner(),
    )

    assert result["next_action"] == "clarify_requirement"
    assert result["requirement_clarification_request"]["questions"][0][
        "gap_id"
    ] == "gap_reference"
