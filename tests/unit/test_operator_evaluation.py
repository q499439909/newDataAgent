from __future__ import annotations

import hashlib

import pytest

from dataagent.domain.operators import RuntimeBackend
from dataagent.operators import (
    ModelEvaluationEvidence,
    assert_model_release_eligible,
    build_operator_library,
    model_release_violations,
)


def test_model_release_requires_independent_gpu_evidence() -> None:
    library = build_operator_library(include_datajuicer=False)
    spec = next(item.spec for item in library.operators if item.spec.model_requirement)

    violations = model_release_violations(spec, None)

    assert violations == ("independent GPU worker evidence is missing",)
    with pytest.raises(ValueError, match="GPU worker evidence"):
        assert_model_release_eligible(spec, None)


def test_matching_frozen_model_evidence_can_pass_release_gate() -> None:
    library = build_operator_library(include_datajuicer=False)
    original = next(item.spec for item in library.operators if item.spec.model_requirement)
    requirement = original.model_requirement.model_copy(
        update={
            "revision": "0123456789abcdef",
            "sha256": hashlib.sha256(b"checkpoint").hexdigest(),
            "code_license": "Apache-2.0",
            "checkpoint_license": "Apache-2.0",
        }
    )
    spec = original.model_copy(update={"model_requirement": requirement})
    evidence = ModelEvaluationEvidence(
        worker_id="gpu-worker-1",
        runtime_backend=RuntimeBackend.CUDA,
        model_id=requirement.model_id,
        revision=requirement.revision,
        sha256=requirement.sha256,
        code_license=requirement.code_license,
        checkpoint_license=requirement.checkpoint_license,
        license_reviewed=True,
        golden_set_sha256=hashlib.sha256(b"golden-set").hexdigest(),
        benchmark_passed=True,
    )

    assert_model_release_eligible(spec, evidence)
    assert model_release_violations(spec, evidence) == ()
