from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dataagent.domain.operators import (
    ExecutionScope,
    OperatorCategory,
    RuntimeBackend,
)
from dataagent.graph import build_main_graph
from dataagent.agents.requirement.clarification import recommended_clarification_patch
from dataagent.operators import (
    OperatorLibrary,
    OperatorRegistry,
    OperatorRuntime,
    build_operator_library,
)
from dataagent.operators.providers import (
    DataJuicerOperatorProvider,
    ProviderOperatorDescriptor,
    build_datajuicer_proxy_operators,
)


def _library_with_vlm_variants() -> OperatorLibrary:
    base = build_operator_library(include_datajuicer=False)
    provider = DataJuicerOperatorProvider(provider_version="1.5.3")
    descriptor = ProviderOperatorDescriptor(
        provider_id="datajuicer",
        provider_version="1.5.3",
        provider_operator_ref="image_tagging_vlm_mapper",
        provider_operator_type="mapper",
        display_name="image_tagging_vlm_mapper",
        description="Generate image tags with a VLM.",
        parameter_schema={"type": "object", "properties": {}},
        tags=frozenset({"api", "gpu", "multimodal", "vllm"}),
        source_digest="raw-vlm-digest",
        suggested_category=OperatorCategory.UNDERSTANDING,
        suggested_secondary_category="object_detection",
        suggested_execution_scope=ExecutionScope.ASSET,
        supported_runtime_backends=(RuntimeBackend.CUDA,),
    )
    proxies = build_datajuicer_proxy_operators(provider, [descriptor])
    operators = (*base.operators, *proxies)
    base.providers.register(provider)
    return OperatorLibrary(
        operators=operators,
        registry=OperatorRegistry(item.spec for item in operators),
        runtime=OperatorRuntime(operators),
        providers=base.providers,
    )


def _state(requirement: str) -> dict:
    return {
        "work_order_id": "work_order_1",
        "owner_id": "user_1",
        "requirement": requirement,
        "data_sources": [
            {
                "type": "local_directory",
                "uri": "D:/images",
                "mapping": {},
            }
        ],
        "trace": [],
    }


def test_resolution_retry_enables_remote_and_reenters_retrieval() -> None:
    graph = build_main_graph(
        InMemorySaver(),
        operator_library=_library_with_vlm_variants(),
        allow_draft_datajuicer_candidates=True,
    )
    config = {"configurable": {"thread_id": "thread_retry"}}
    graph.invoke(_state("把猫和狗的图片分类"), config)

    blocked = graph.invoke(Command(resume={"approved": True}), config)

    resolution = blocked["__interrupt__"][0].value
    assert resolution["kind"] == "capability_resolution"
    assert resolution["gaps"][0]["capability"] == "image_classification"
    assert resolution["gaps"][0]["status"] == "blocked"

    recovered = graph.invoke(
        Command(
            resume={
                "action": "retry",
                "enable_runtime_backends": ["remote"],
            }
        ),
        config,
    )

    assert recovered["__interrupt__"][0].value["kind"] == "pipeline_approval"
    assert recovered["candidate_sufficient"] is True
    assert recovered["retrieval_plan"]["version"] == 2
    assert recovered["retrieval_plan"]["parent_version_id"] is not None
    assert recovered["runtime_backend_overrides"] == ["remote"]
    selected = {
        item["capability"]: item for item in recovered["capability_coverage"]
    }
    assert selected["image_classification"]["selected_operator_version_id"] == (
        "datajuicer.image_tagging_vlm_mapper.remote_api:2"
    )


def test_resolution_can_return_work_order_to_task_spec_revision() -> None:
    graph = build_main_graph(
        InMemorySaver(),
        operator_library=_library_with_vlm_variants(),
        allow_draft_datajuicer_candidates=True,
        available_runtime_backends=frozenset(
            {RuntimeBackend.CPU}
        ),
    )
    config = {"configurable": {"thread_id": "thread_revise"}}
    draft = graph.invoke(
        _state("去掉里面不真实、不清晰的图片，把猫和狗的图片分开"),
        config,
    )
    graph.invoke(
        Command(
            resume={
                "action": "edit_spec",
                "task_spec_patch": recommended_clarification_patch(
                    tuple(draft["task_spec"]["ambiguities"])
                ),
            }
        ),
        config,
    )
    blocked = graph.invoke(Command(resume={"approved": True}), config)

    assert blocked["__interrupt__"][0].value["kind"] == "capability_resolution"
    final = graph.invoke(Command(resume={"action": "revise_task"}), config)

    assert "__interrupt__" not in final
    assert final["next_action"] == "edit_task_spec"
    assert final.get("terminated", False) is False
    assert "hitl:capability_resolution_revise_task" in final["trace"]
