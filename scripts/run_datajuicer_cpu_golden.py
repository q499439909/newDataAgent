from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from dataagent.config import Settings
from dataagent.domain.common.models import new_id
from dataagent.domain.operators import RuntimeBackend
from dataagent.infrastructure import DomainVersionStore, SqliteDatabase
from dataagent.operators import (
    OperatorGoldenCase,
    OperatorGoldenSet,
    OperatorGoldenSetRunner,
    build_operator_library,
)
from dataagent.operators.protocol import OperatorContext


def main() -> None:
    root = Path.cwd().resolve()
    settings = Settings.load(root)
    settings.ensure_directories()
    if settings.datajuicer_python is None or settings.datajuicer_process_bin is None:
        raise RuntimeError("Data-Juicer provider paths are not configured")
    image_root = settings.home / "golden-sets" / "datajuicer-cpu-v1"
    image_root.mkdir(parents=True, exist_ok=True)
    square = image_root / "square.png"
    duplicate = image_root / "square-duplicate.png"
    wide = image_root / "wide.png"
    Image.new("RGB", (64, 64), (80, 120, 160)).save(square)
    duplicate.write_bytes(square.read_bytes())
    Image.new("RGB", (256, 32), (180, 100, 60)).save(wide)

    library = build_operator_library(
        include_datajuicer=True,
        allow_model_download=False,
        datajuicer_python=settings.datajuicer_python,
        datajuicer_process_bin=settings.datajuicer_process_bin,
        datajuicer_runtime_root=settings.home / "providers" / "datajuicer",
        datajuicer_timeout_seconds=settings.datajuicer_timeout_seconds,
    )
    provider = library.providers.get("datajuicer")
    golden_set = OperatorGoldenSet(
        id="golden_datajuicer_cpu_v1",
        version=1,
        created_by="system",
        change_reason="initial admitted Data-Juicer CPU golden set",
        provider_id="datajuicer",
        provider_version=provider.provider_version,
        runtime_backend=RuntimeBackend.CPU,
        cases=(
            OperatorGoldenCase(
                id="shape-boundary",
                operator_version_id="datajuicer.image_shape_filter:1",
                input_paths=(str(square), str(wide)),
                parameters={"min_width": 32, "max_width": 128, "min_height": 32, "max_height": 128},
                expected_decisions=("continue", "reject"),
                max_duration_seconds=120,
            ),
            OperatorGoldenCase(
                id="aspect-boundary",
                operator_version_id="datajuicer.image_aspect_ratio_filter:1",
                input_paths=(str(square), str(wide)),
                parameters={"min_ratio": 0.5, "max_ratio": 2.0},
                expected_decisions=("continue", "reject"),
                max_duration_seconds=120,
            ),
            OperatorGoldenCase(
                id="perceptual-duplicate",
                operator_version_id="datajuicer.image_deduplicator:1",
                input_paths=(str(square), str(duplicate)),
                parameters={"method": "phash", "consider_text": False},
                expected_decisions=("continue", "reject"),
                max_duration_seconds=120,
            ),
        ),
    )
    report = OperatorGoldenSetRunner(library.runtime).evaluate(
        golden_set,
        context=OperatorContext(
            run_id="benchmark_datajuicer_cpu_v1",
            work_order_id="benchmark",
            owner_id="system",
            purpose="development",
        ),
        report_id=new_id("benchmark_datajuicer_cpu"),
        actor="system",
    )
    report_root = root / "benchmarks"
    report_root.mkdir(exist_ok=True)
    output = report_root / "datajuicer-cpu-baseline-v1.json"
    output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    store = DomainVersionStore(SqliteDatabase(settings.home / "control.db"))
    store.save_if_absent(
        kind="operator_benchmark",
        owner_id="system",
        payload=report.model_dump(mode="json"),
    )
    print(output)
    print(f"passed={report.passed} assets_per_second={report.assets_per_second:.4f}")
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
