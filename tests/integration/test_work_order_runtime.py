from dataagent.application.work_order_runtime import WorkOrderRuntime


YIFU_REQUIREMENT = (
    r"D:\data\yifu 筛选满足以下条件的图片："
    "1. 图片宽高均不少于 64 像素；"
    "2. 宽高比在 0.3 到 3.5 之间；"
    "3. 文件大小在 1KB 到 20MB 之间；"
    "4. 图片中的人脸数量在2个及以下；"
    "5. 筛选出主体穿黑色衣服的图片；"
    "6. 去除重复图片"
)


def test_main_agent_preserves_every_yifu_constraint_before_confirmation() -> None:
    result = WorkOrderRuntime(include_datajuicer=False).start(
        owner_id="user_1",
        requirement=YIFU_REQUIREMENT,
        data_sources=[
            {"type": "local_directory", "uri": r"D:\data\yifu"},
        ],
    )

    task_spec = result["state"]["task_spec"]
    constraints = {
        item["id"]: (
            item["field"],
            item["operator"],
            item["value"],
            item["unit"],
        )
        for item in task_spec["constraints"]
    }

    assert constraints == {
        "C01": ("width_px", "gte", 64, "px"),
        "C02": ("height_px", "gte", 64, "px"),
        "C03": ("aspect_ratio", "gte", 0.3, "ratio"),
        "C04": ("aspect_ratio", "lte", 3.5, "ratio"),
        "C05": ("file_size_bytes", "gte", 1024, "bytes"),
        "C06": ("file_size_bytes", "lte", 20 * 1024 * 1024, "bytes"),
        "C07": ("face_count", "lte", 2, "count"),
        "C08": (
            "primary_subject_garment_color",
            "eq",
            "black",
            "label",
        ),
        "C09": ("exact_duplicate_count", "eq", 0, "count"),
        "C10": (
            "perceptual_duplicate_policy_applied",
            "eq",
            True,
            "boolean",
        ),
        "C11": ("source_assets_immutable", "eq", True, "boolean"),
    }
    assert task_spec["semantic_requirements"] == [
        "The primary visible subject's dominant visible garment is black."
    ]
    assert task_spec["ambiguities"] == []
    assert result["interrupts"][0]["value"]["task_spec"]["constraints"] == (
        task_spec["constraints"]
    )
