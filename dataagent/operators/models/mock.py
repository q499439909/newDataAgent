from __future__ import annotations

import hashlib
from typing import Any

from ...domain.operators import ModelRequirement, RuntimeBackend
from ..protocol import OperatorInput


class MockModelBackend:
    backend = RuntimeBackend.MOCK
    requires_download = False

    def infer(
        self,
        *,
        requirement: ModelRequirement,
        capability: str,
        input_data: OperatorInput,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        seed = "|".join(
            (
                requirement.model_id,
                requirement.revision,
                capability,
                input_data.current_path,
            )
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        unit_score = int(digest[:8], 16) / 0xFFFFFFFF
        return {
            "mock": True,
            "digest": digest,
            "unit_score": round(unit_score, 6),
            "parameters": dict(parameters),
        }


__all__ = ["MockModelBackend"]
