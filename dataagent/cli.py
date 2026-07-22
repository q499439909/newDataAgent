from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .config import Settings
from .models import TaskSpec
from .pipelines import strategy_score
from .service import DataAgentService


app = typer.Typer(
    name="dataagent",
    help="DataAgent 图片数据生产 CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
task_app = typer.Typer(help="创建、确认和查看图片任务", no_args_is_help=True)
trial_app = typer.Typer(help="运行和查看候选 Pipeline 试跑", no_args_is_help=True)
review_app = typer.Typer(help="审核边界样本", no_args_is_help=True)
run_app = typer.Typer(help="执行和查看全量任务", no_args_is_help=True)
pipeline_app = typer.Typer(help="查看可复用 Pipeline", no_args_is_help=True)
dataset_app = typer.Typer(help="查看数据版本", no_args_is_help=True)
milvus_app = typer.Typer(help="探测和验证 Milvus 数据源", no_args_is_help=True)
provider_app = typer.Typer(help="安装、验证和检查外部算子 Provider", no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(trial_app, name="trial")
app.add_typer(review_app, name="review")
app.add_typer(run_app, name="run")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(dataset_app, name="dataset")
app.add_typer(milvus_app, name="milvus")
app.add_typer(provider_app, name="provider")
console = Console()


def service() -> DataAgentService:
    return DataAgentService(Settings.load())


def fail(exc: Exception) -> None:
    encoding = console.encoding or "utf-8"
    message = str(exc).encode(encoding, errors="replace").decode(encoding, errors="replace")
    console.print(f"[bold red]错误：[/bold red]{message}")
    raise typer.Exit(1)


def _progress_callback(progress: Progress, task_id: int):
    def callback(current: int, total: int, label: str) -> None:
        progress.update(task_id, total=total, completed=current, description=label)

    return callback


@app.command()
def version() -> None:
    """显示 DataAgent CLI 版本。"""
    console.print(f"DataAgent {__version__}")


@app.command()
def setup(
    with_provider: Annotated[
        str,
        typer.Option(
            "--with-provider",
            help="要安装的冻结 Provider，例如 datajuicer@1.5.3",
        ),
    ] = "datajuicer@1.5.3",
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            help="auto、catalog、cpu、remote 或 linux-gpu",
        ),
    ] = "auto",
    existing_python: Annotated[
        Path | None,
        typer.Option(
            "--existing-python",
            help="注册并验证已有隔离环境；省略时自动创建新环境",
        ),
    ] = None,
    wheelhouse: Annotated[
        Path | None,
        typer.Option("--wheelhouse", help="离线 Wheelhouse 目录"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="重建 DataAgent 管理的 Provider 目录"),
    ] = False,
) -> None:
    """安装并注册受治理的 Data-Juicer Provider。"""
    try:
        from .distribution import DATAJUICER_VERSION, DataJuicerInstaller

        provider_name, separator, requested_version = with_provider.partition("@")
        if provider_name.strip().lower() not in {"datajuicer", "data-juicer"}:
            raise ValueError(f"当前不支持 Provider：{provider_name}")
        if separator and requested_version != DATAJUICER_VERSION:
            raise ValueError(
                f"当前冻结的 Data-Juicer Provider 版本是 {DATAJUICER_VERSION}，"
                f"不能安装 {requested_version}"
            )
        settings = Settings.load()
        with console.status("正在安装并验证 Data-Juicer Provider..."):
            result = DataJuicerInstaller(settings.home).install(
                profile=profile,
                existing_python=existing_python,
                wheelhouse=wheelhouse,
                force=force,
            )
        _print_provider_result(result, title="Data-Juicer Provider 已注册")
    except Exception as exc:
        fail(exc)


def _print_provider_result(result: dict, *, title: str) -> None:
    registration = result["registration"]
    report = result["report"]
    counts = report["counts"]
    table = Table(title=title, show_header=False)
    table.add_column("项目", style="cyan")
    table.add_column("值", overflow="fold")
    table.add_row("Provider", f"datajuicer@{registration['provider_version']}")
    table.add_row("Profile", str(registration["profile"]))
    table.add_row("Python", str(registration["python"]))
    table.add_row("dj-process", str(registration["process_bin"]))
    table.add_row("Catalog", f"{counts['discovered']} operators")
    table.add_row("Catalog digest", str(registration["catalog_digest"]))
    table.add_row("CPU candidates", str(counts["local_cpu_candidates"]))
    table.add_row("Remote candidates", str(counts["remote_api_candidates"]))
    table.add_row("Linux GPU candidates", str(counts["linux_gpu_candidates"]))
    table.add_row("Governed executable now", str(counts["governed_executable_now"]))
    table.add_row("Capability report", str(registration["capability_report"]))
    console.print(table)


@provider_app.command("verify")
def provider_verify() -> None:
    """重新发现 Catalog，并验证已注册 Data-Juicer Provider。"""
    try:
        from .distribution import DataJuicerInstaller

        settings = Settings.load()
        with console.status("正在验证 Data-Juicer Provider..."):
            result = DataJuicerInstaller(settings.home).verify()
        _print_provider_result(result, title="Data-Juicer Provider 验证通过")
    except Exception as exc:
        fail(exc)


@provider_app.command("report")
def provider_report(
    blocked_only: Annotated[
        bool,
        typer.Option("--blocked-only", help="只显示当前受治理执行仍被阻塞的算子"),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 217,
) -> None:
    """查看每个算子的 Runtime、治理状态和阻塞原因。"""
    try:
        from .distribution import DataJuicerInstaller

        settings = Settings.load()
        report = DataJuicerInstaller(settings.home).report()
        operators = report["operators"]
        if blocked_only:
            operators = [item for item in operators if item["blocked_reasons"]]
        table = Table(title="Data-Juicer Provider Capability Report")
        table.add_column("Operator", overflow="fold")
        table.add_column("Status")
        table.add_column("Runtime")
        table.add_column("Executable now")
        table.add_column("Blocked reason", overflow="fold")
        for item in operators[:limit]:
            table.add_row(
                str(item["operator_ref"]),
                str(item["governance_status"]),
                ", ".join(item["runtime_candidates"]) or "unclassified",
                ", ".join(item["governed_executable_profiles"]) or "-",
                ", ".join(item["blocked_reasons"]) or "-",
            )
        console.print(table)
        console.print(
            f"Catalog digest: {report['catalog_digest']} · "
            f"showing {min(limit, len(operators))}/{len(operators)}"
        )
    except Exception as exc:
        fail(exc)


@app.command()
def doctor(
    check_api: Annotated[bool, typer.Option("--check-api", help="发送一个最小模型请求验证配置")] = False,
) -> None:
    """检查本地目录和模型配置，不打印密钥。"""
    settings = Settings.load()
    rows = [
        ("DataAgent Home", str(settings.home), True),
        ("配置文件", str(settings.env_path) if settings.env_path else "未找到", bool(settings.env_path)),
        ("API Key", "已配置" if settings.api_key else "未配置", bool(settings.api_key)),
        ("Base URL", settings.base_url, bool(settings.base_url)),
        ("规划模型", settings.planning_model, bool(settings.planning_model)),
        ("视觉模型", settings.vision_model, bool(settings.vision_model)),
    ]
    table = Table(title="DataAgent 环境检查")
    table.add_column("项目")
    table.add_column("值")
    table.add_column("状态")
    for name, value, ok in rows:
        table.add_row(name, value, "[green]OK[/green]" if ok else "[red]缺失[/red]")
    console.print(table)
    if check_api:
        try:
            result = ModelCheck(settings)
            console.print(
                f"[green]模型接口可用[/green] · {result.model} · "
                f"input {result.usage.input_tokens} / output {result.usage.output_tokens} tokens"
            )
        except Exception as exc:
            fail(exc)


def ModelCheck(settings: Settings):
    from .gateway import ModelGateway

    return ModelGateway(settings).healthcheck()


@task_app.command("create")
def task_create(
    source: Annotated[Path, typer.Option("--source", "-s", help="本地图片目录")],
    requirement: Annotated[str, typer.Option("--requirement", "-r", help="自然语言数据需求")],
    no_model: Annotated[bool, typer.Option("--no-model", help="不调用规划模型")] = False,
) -> None:
    """创建任务并生成 TaskSpec 草案。"""
    try:
        with console.status("正在分析需求并生成 TaskSpec..."):
            task, used_model, warning = service().create_task(requirement, source, not no_model)
        console.print(f"[bold green]任务已创建：[/bold green]{task['id']}")
        console.print(f"状态：{task['state']} · 规划方式：{'模型' if used_model else '本地保守解析'}")
        if warning:
            console.print(f"[yellow]模型提示：{warning}[/yellow]")
        _print_spec(TaskSpec.model_validate(task["spec"]))
        console.print(f"下一步：[bold]dataagent task confirm {task['id']}[/bold]")
    except Exception as exc:
        fail(exc)


@task_app.command("list")
def task_list() -> None:
    """列出当前用户的任务。"""
    try:
        tasks = service().store.list_tasks(Settings.load().owner)
        table = Table(title="图片任务")
        table.add_column("ID")
        table.add_column("状态")
        table.add_column("需求", overflow="fold")
        table.add_column("数据目录", overflow="fold")
        for item in tasks:
            table.add_row(item["id"], item["state"], item["requirement"], item["source_path"])
        console.print(table)
    except Exception as exc:
        fail(exc)


@task_app.command("show")
def task_show(task_id: str) -> None:
    """查看任务和 TaskSpec。"""
    try:
        task = service().store.get_task(task_id, Settings.load().owner)
        console.print(Panel(f"{task['requirement']}\n状态：{task['state']}\n目录：{task['source_path']}", title=task_id))
        if task["spec"]:
            _print_spec(TaskSpec.model_validate(task["spec"]))
    except Exception as exc:
        fail(exc)


def _print_spec(spec: TaskSpec) -> None:
    table = Table(title="TaskSpec 草案", show_header=False)
    table.add_column("字段", style="cyan", width=14)
    table.add_column("内容", overflow="fold")
    table.add_row("目标", spec.objective)
    table.add_row("操作", "、".join(spec.output_actions) or "无")
    table.add_row("硬约束", json.dumps(spec.hard_constraints.model_dump(exclude_none=True), ensure_ascii=False))
    table.add_row("语义要求", "；".join(spec.semantic_requirements) or "无")
    table.add_row("排除要求", "；".join(spec.exclusion_requirements) or "无")
    table.add_row("待确认歧义", "；".join(spec.ambiguities) or "无")
    console.print(table)


@task_app.command("confirm")
def task_confirm(task_id: str) -> None:
    """确认 TaskSpec，允许进入试跑。"""
    try:
        task = service().confirm_spec(task_id)
        console.print(f"[green]TaskSpec 已确认。[/green]状态：{task['state']}")
        console.print(f"下一步：[bold]dataagent trial run {task_id}[/bold]")
    except Exception as exc:
        fail(exc)


@task_app.command("update-spec")
def task_update_spec(
    task_id: str,
    file: Annotated[Path, typer.Option("--file", "-f", help="修改后的 TaskSpec JSON 文件")],
) -> None:
    """在确认前用 JSON 文件修正 TaskSpec。"""
    try:
        svc = service()
        task = svc.store.get_task(task_id, Settings.load().owner)
        if task["state"] != "WAITING_SPEC_CONFIRMATION":
            raise ValueError("只有等待确认的 TaskSpec 可以修改")
        spec = TaskSpec.model_validate_json(file.read_text(encoding="utf-8"))
        spec.source_path = task["source_path"]
        spec.source_type = "local"
        svc.store.save_spec(task_id, Settings.load().owner, spec.model_dump(mode="json"))
        console.print(f"[green]TaskSpec 已更新：{task_id}[/green]")
        _print_spec(spec)
    except Exception as exc:
        fail(exc)


@trial_app.command("run")
def trial_run(
    task_id: str,
    sample_size: Annotated[int, typer.Option("--sample-size", min=1, max=300)] = 100,
    vision_limit: Annotated[int, typer.Option("--vision-limit", min=0, max=50)] = 8,
) -> None:
    """在同一批代表性样本上运行三类候选 Pipeline。"""
    try:
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), console=console
        ) as progress:
            progress_id = progress.add_task("准备试跑", total=sample_size)
            bundle = service().run_trial(
                task_id,
                sample_size,
                vision_limit,
                _progress_callback(progress, progress_id),
            )
        _print_trial(bundle)
        console.print(f"下一步：[bold]dataagent review start {task_id}[/bold]")
    except Exception as exc:
        fail(exc)


@trial_app.command("report")
def trial_report(task_id: str) -> None:
    """重新显示候选试跑报告。"""
    try:
        _print_trial(service().get_trial(task_id))
    except Exception as exc:
        fail(exc)


def _print_trial(bundle) -> None:
    table = Table(title=f"候选 Pipeline 效果 · {bundle.task_id}")
    table.add_column("方案")
    table.add_column("Pipeline ID")
    table.add_column("保留")
    table.add_column("剔除")
    table.add_column("保留率")
    table.add_column("硬约束通过")
    table.add_column("语义评估")
    table.add_column("人工验收")
    table.add_column("代理评分")
    for report in bundle.candidates:
        semantic = (
            f"{report.semantic_pass_rate:.1%} / {report.semantic_evaluated}"
            if report.semantic_pass_rate is not None
            else "未评估"
        )
        table.add_row(
            report.display_name,
            report.pipeline_id,
            str(report.kept),
            str(report.rejected),
            f"{report.retention_rate:.1%}",
            f"{report.hard_constraint_pass_rate:.1%}",
            semantic,
            (
                f"{report.human_acceptance_rate:.1%} / {report.human_reviewed}"
                if report.human_acceptance_rate is not None
                else "未审核"
            ),
            f"{strategy_score(report):.3f}",
        )
    console.print(table)
    if all(item.proxy_only for item in bundle.candidates):
        console.print("[yellow]当前指标属于代理评估；完成人工边界审核后才能作为任务验收依据。[/yellow]")
    else:
        console.print("[green]至少一个候选已具备人工边界样本验收结果。[/green]")


@review_app.command("start")
def review_start(
    task_id: str,
    pipeline: Annotated[str, typer.Option("--pipeline", "-p", help="Pipeline ID；默认均衡方案")] = "",
    limit: Annotated[int, typer.Option("--limit", min=1, max=50)] = 20,
    accept_all: Annotated[bool, typer.Option("--accept-all", help="将展示的边界样本全部标记合格")] = False,
) -> None:
    """审核指定候选的边界样本。"""
    try:
        svc = service()
        bundle = svc.get_trial(task_id)
        candidate = next(
            (
                item
                for item in bundle.candidates
                if item.pipeline_id == pipeline or (not pipeline and item.strategy == "balanced")
            ),
            None,
        )
        if not candidate:
            raise ValueError("Pipeline 不属于该任务")
        verdicts: dict[str, bool] = {}
        for path in candidate.boundary_paths[:limit]:
            decision = next(item for item in candidate.decisions if item.path == path)
            console.print(
                Panel(
                    f"路径：{path}\nPipeline 判断：{'保留' if decision.keep else '剔除'}\n"
                    f"原因：{'；'.join(decision.reasons) or '通过'}\n"
                    f"尺寸：{decision.metrics.width}×{decision.metrics.height} · "
                    f"亮度：{decision.metrics.brightness:.1f} · 清晰度：{decision.metrics.blur_score:.1f}\n"
                    f"视觉评估：{decision.metrics.semantic_reason or '未调用'}",
                    title="边界样本",
                )
            )
            verdicts[path] = True if accept_all else Confirm.ask("该 Pipeline 对这张图片的判断是否合格？")
        count = svc.review(task_id, candidate.pipeline_id, verdicts)
        accepted = sum(verdicts.values())
        console.print(f"[green]已提交 {count} 条审核：{accepted} 合格，{count - accepted} 不合格。[/green]")
        console.print(
            f"下一步：[bold]dataagent run start {task_id} --pipeline {candidate.pipeline_id}[/bold]"
        )
    except Exception as exc:
        fail(exc)


@run_app.command("start")
def run_start(
    task_id: str,
    pipeline: Annotated[str, typer.Option("--pipeline", "-p", help="已审核的 Pipeline ID")],
    skip_review_gate: Annotated[bool, typer.Option("--skip-review-gate", hidden=True)] = False,
    skip_semantic: Annotated[
        bool,
        typer.Option(
            "--skip-semantic",
            help="显式跳过全量视觉判定；结果不能声称满足语义要求",
        ),
    ] = False,
) -> None:
    """使用已审核 Pipeline 执行全量任务并冻结数据版本。"""
    try:
        svc = service()
        task = svc.store.get_task(task_id, Settings.load().owner)
        total = len(__import__("dataagent.imaging", fromlist=["scan_images"]).scan_images(Path(task["source_path"])))
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), console=console
        ) as progress:
            progress_id = progress.add_task("全量执行", total=total)
            run_id, dataset_id = svc.execute(
                task_id,
                pipeline,
                _progress_callback(progress, progress_id),
                require_review=not skip_review_gate,
                evaluate_semantic=not skip_semantic,
            )
        dataset = svc.store.get_dataset(dataset_id)
        console.print(
            Panel(
                f"Run：{run_id}\nDataset：{dataset_id}\nManifest：{dataset['manifest_path']}\n"
                f"保留：{dataset['summary']['kept_count']} / {dataset['summary']['source_count']}\n"
                "原图校验：未改变",
                title="[green]数据版本已冻结[/green]",
            )
        )
    except Exception as exc:
        fail(exc)


@run_app.command("show")
def run_show(run_id: str) -> None:
    """查看一次全量运行。"""
    try:
        console.print_json(data=service().store.get_run(run_id))
    except Exception as exc:
        fail(exc)


@pipeline_app.command("list")
def pipeline_list() -> None:
    """列出当前用户已验证并可复用的 Pipeline。"""
    try:
        rows = service().store.list_reusable_pipelines(Settings.load().owner)
        table = Table(title="可复用 Pipeline")
        table.add_column("ID")
        table.add_column("策略")
        table.add_column("原始需求", overflow="fold")
        table.add_column("创建时间")
        for row in rows:
            table.add_row(row["id"], row["strategy"], row["requirement"], row["created_at"])
        console.print(table)
    except Exception as exc:
        fail(exc)


@pipeline_app.command("reuse")
def pipeline_reuse(
    pipeline_id: str,
    source: Annotated[Path, typer.Option("--source", "-s", help="新任务的图片目录")],
    requirement: Annotated[str, typer.Option("--requirement", "-r", help="可选的新需求；默认沿用原需求")] = "",
) -> None:
    """基于已验证 Pipeline 创建回归试跑任务。"""
    try:
        task = service().reuse_pipeline(pipeline_id, source, requirement or None)
        console.print(
            f"[green]已创建 Pipeline 复用任务：{task['id']}[/green]\n"
            f"状态：{task['state']}\n下一步：[bold]dataagent task confirm {task['id']}[/bold]"
        )
    except Exception as exc:
        fail(exc)


@dataset_app.command("show")
def dataset_show(dataset_id: str) -> None:
    """查看不可变数据版本。"""
    try:
        console.print_json(data=service().store.get_dataset(dataset_id))
    except Exception as exc:
        fail(exc)


@milvus_app.command("discover")
def milvus_discover(
    uri: Annotated[str, typer.Option("--uri", help="Milvus URI，例如 http://127.0.0.1:19530")],
    collection: Annotated[str, typer.Option("--collection", "-c")],
    database: Annotated[str, typer.Option("--database")] = "default",
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="保存 Token 的环境变量名；不会输出变量值"),
    ] = "MILVUS_TOKEN",
) -> None:
    """探测 Collection Schema，并识别向量和图片路径候选字段。"""
    try:
        from .milvus import MilvusAdapter

        result = MilvusAdapter(uri, os.getenv(token_env), database).discover_schema(collection)
        table = Table(title=f"Milvus Schema · {collection}")
        table.add_column("字段")
        table.add_column("类型")
        table.add_column("主键")
        table.add_column("维度")
        for field in result["fields"]:
            table.add_row(
                str(field["name"]),
                str(field["type"]),
                "是" if field["is_primary"] else "",
                str(field["dimension"] or ""),
            )
        console.print(table)
        console.print(f"向量字段：{', '.join(result['vector_fields']) or '未发现'}")
        console.print(f"路径候选：{', '.join(result['path_candidates']) or '未发现'}")
        if result["processing_ready"]:
            console.print("[green]Collection 具备向量字段和图片定位候选，可继续配置字段映射。[/green]")
        else:
            console.print("[yellow]当前 Schema 不能同时确认向量和原图定位字段，禁止直接启动处理任务。[/yellow]")
    except Exception as exc:
        fail(exc)


if __name__ == "__main__":
    app()
