from __future__ import annotations

import pytest

from dataagent.application.work_order_runtime import WorkOrderRuntime


class FailingGraph:
    def invoke(self, state, config):
        del state, config
        raise RuntimeError("planning failed")


def test_failed_work_order_start_rolls_back_the_thread_record() -> None:
    runtime = WorkOrderRuntime(include_datajuicer=False)
    runtime.graph = FailingGraph()

    with pytest.raises(RuntimeError, match="planning failed"):
        runtime.start(
            owner_id="owner_1",
            work_order_id="work_order_transaction",
            requirement="Apply constraint C.",
            data_sources=[
                {
                    "type": "local_directory",
                    "uri": "D:/generic/source",
                    "mapping": {},
                }
            ],
        )

    with pytest.raises(KeyError, match="Work order not found"):
        runtime.state(
            work_order_id="work_order_transaction",
            owner_id="owner_1",
        )
