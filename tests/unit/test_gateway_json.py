from __future__ import annotations

import pytest

from dataagent.gateway import ModelGatewayError, _extract_json


def test_extract_json_repairs_common_llm_syntax_errors() -> None:
    payload = _extract_json(
        """
        ```json
        {
          "intent": "QUERY_CONTROL_FACTS"
          "facets": ["task_spec",],
          "reply": "展示当前草案"
        }
        ```
        """
    )

    assert payload == {
        "intent": "QUERY_CONTROL_FACTS",
        "facets": ["task_spec"],
        "reply": "展示当前草案",
    }


def test_extract_json_still_rejects_non_object_output() -> None:
    with pytest.raises(ModelGatewayError, match="JSON object"):
        _extract_json("This response contains no structured action.")
