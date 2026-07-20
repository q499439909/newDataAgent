from __future__ import annotations

from dataagent.agents.requirement.nodes import generate_task_spec
from dataagent.domain.specs import TaskSpecVersion
from dataagent.operators.planning import decompose_task_capabilities


CAT_DOG_REQUIREMENT = "去掉里面不真实、不清晰的图片，把猫和狗的图片分开"


def test_cat_dog_requirement_decomposes_to_capability_dag() -> None:
    capabilities = decompose_task_capabilities(CAT_DOG_REQUIREMENT)

    assert [item.capability for item in capabilities] == [
        "image_decode",
        "image_quality",
        "authenticity_assessment",
        "image_classification",
        "class_resolution",
        "dataset_partition",
        "manifest",
    ]
    assert capabilities[0].depends_on == ()
    assert capabilities[-1].depends_on == ("dataset_partition",)


def test_requirement_node_persists_capabilities_and_output_actions() -> None:
    result = generate_task_spec(
        {
            "work_order_id": "work_order_1",
            "owner_id": "user_1",
            "requirement": CAT_DOG_REQUIREMENT,
            "data_sources": [
                {
                    "type": "local_directory",
                    "uri": "D:/images",
                    "mapping": {},
                }
            ],
            "trace": [],
        }
    )
    spec = TaskSpecVersion.model_validate(result["task_spec"])

    assert spec.output_actions == ("filter", "classify", "partition", "manifest")
    assert [item.id for item in spec.capability_requirements] == [
        "image_decode",
        "image_quality",
        "authenticity_assessment",
        "image_classification",
        "class_resolution",
        "dataset_partition",
        "manifest",
    ]
