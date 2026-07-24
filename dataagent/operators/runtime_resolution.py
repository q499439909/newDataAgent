from __future__ import annotations

from ..domain.operators import (
    RuntimeBackend,
    RuntimeResolution,
    RuntimeResolutionOption,
)


def unavailable_runtime_resolution(backend: RuntimeBackend) -> RuntimeResolution:
    if backend == RuntimeBackend.CUDA:
        return RuntimeResolution(
            code="CUDA_WORKER_UNAVAILABLE",
            reasons=("需要 CUDA", "当前 Worker 没有 GPU"),
            options=(
                RuntimeResolutionOption(
                    id="use_gpu_worker",
                    label="使用 GPU Worker 执行",
                ),
                RuntimeResolutionOption(
                    id="use_remote_variant",
                    label="使用 Remote API 版本",
                ),
                RuntimeResolutionOption(
                    id="skip_capability",
                    label="跳过该步骤",
                ),
            ),
        )
    if backend == RuntimeBackend.REMOTE:
        return RuntimeResolution(
            code="REMOTE_RUNTIME_UNAVAILABLE",
            reasons=("需要 Remote API", "当前 Worker 未启用远程运行环境或缺少凭据"),
            options=(
                RuntimeResolutionOption(
                    id="configure_remote_runtime",
                    label="配置 Remote API 后重试",
                ),
                RuntimeResolutionOption(
                    id="use_local_variant",
                    label="使用本地 CPU 或 GPU 版本",
                ),
                RuntimeResolutionOption(
                    id="skip_capability",
                    label="跳过该步骤",
                ),
            ),
        )
    if backend == RuntimeBackend.CPU:
        return RuntimeResolution(
            code="CPU_WORKER_UNAVAILABLE",
            reasons=("需要 CPU Worker", "当前没有可用的 CPU 执行环境"),
            options=(
                RuntimeResolutionOption(
                    id="use_cpu_worker",
                    label="启动 CPU Worker 后重试",
                ),
                RuntimeResolutionOption(
                    id="use_remote_variant",
                    label="使用 Remote API 版本",
                ),
                RuntimeResolutionOption(
                    id="skip_capability",
                    label="跳过该步骤",
                ),
            ),
        )
    return RuntimeResolution(
        code="RUNTIME_UNAVAILABLE",
        reasons=(f"需要 {backend.value} 运行环境", "当前 Worker 不支持该运行环境"),
        options=(
            RuntimeResolutionOption(id="retry", label="更换 Worker 后重试"),
            RuntimeResolutionOption(id="skip_capability", label="跳过该步骤"),
        ),
    )


__all__ = ["unavailable_runtime_resolution"]
