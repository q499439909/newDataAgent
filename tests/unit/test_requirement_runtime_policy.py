from dataagent.agents.requirement.guards import allowed_requirement_actions
from dataagent.agents.requirement.runtime import decide_requirement_agent_turn


def test_explicit_retry_state_has_one_machine_action() -> None:
    state = {
        "task_spec": {"id": "spec_1"},
        "task_spec_confirmed": True,
        "retrieval_plan": {"sufficient": False},
        "candidate_sufficient": False,
        "next_action": "run_retrieval_agent",
    }

    assert allowed_requirement_actions(state) == ("run_retrieval_agent",)


def test_single_governed_action_does_not_call_model() -> None:
    class PlannerMustNotRun:
        def decide(self, request):
            raise AssertionError("A single machine action needs no LLM routing")

    result = decide_requirement_agent_turn(
        {
            "task_spec": {"id": "spec_1"},
            "task_spec_confirmed": True,
            "retrieval_plan": {"sufficient": False},
            "candidate_sufficient": False,
            "next_action": "run_retrieval_agent",
        },
        planner=PlannerMustNotRun(),
    )

    assert result["requirement_agent_action"] == "run_retrieval_agent"
    assert result["requirement_agent_decisions"][-1]["source"] == "policy"
