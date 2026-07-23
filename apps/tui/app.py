from __future__ import annotations

import json
import threading
import time
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from .api_client import ControlPlaneError
from .session import TuiSession


def parse_new_command(value: str) -> tuple[str, str]:
    try:
        source, requirement = value.split("|", 1)
    except ValueError as exc:
        raise ValueError("Use: /new <source directory> | <requirement>") from exc
    source = source.strip()
    requirement = requirement.strip()
    if not source or not requirement:
        raise ValueError("Source directory and requirement are both required")
    return source, requirement


class TuiApp:
    def __init__(self, session: TuiSession, console: Console | None = None) -> None:
        self.session = session
        self.console = console or Console()
        self._run_monitors: dict[str, threading.Thread] = {}
        self._monitor_lock = threading.Lock()

    def run(self) -> None:
        self.console.print("[bold]DataAgent[/bold]  [dim]TUI control plane[/dim]")
        conversation = self.session.ensure_conversation()
        self.console.print(
            f"[dim]conversation {conversation['id']}[/dim]\n"
            "你好，直接和我说就可以。数据任务的信息不完整时，我会继续问你。"
        )
        while True:
            try:
                line = self.console.input("[cyan]dataagent>[/cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return
            if not line:
                continue
            try:
                if not self.handle(line):
                    return
            except (ControlPlaneError, ValueError) as exc:
                self.console.print(f"[red]{exc}[/red]")

    def handle(self, line: str) -> bool:
        if not line.startswith("/"):
            self._chat(line)
            return True
        command, _, argument = line.partition(" ")
        command = command.lower()
        argument = argument.strip()
        if command in {"/exit", "/quit"}:
            return False
        if command == "/new":
            source, requirement = parse_new_command(argument)
            self._chat(
                f"请创建一个图片数据任务。数据目录是 {source}。需求是：{requirement}"
            )
        elif command == "/open":
            if not argument:
                raise ValueError("Work order id is required")
            self._render_turn(self.session.open(argument))
        elif command == "/status":
            self._render_turn(self.session.refresh())
        elif command == "/approve":
            strategy = argument or "balanced"
            self._chat(f"确认，选择 {strategy} 策略。")
        elif command == "/reject":
            self._chat(f"拒绝当前方案。原因：{argument or '不符合需求'}")
        elif command == "/submit":
            self._chat("开始运行当前任务。")
        elif command == "/runs":
            self._render_runs(self.session.runs())
        elif command == "/run":
            self._render_run(self.session.run(argument or None))
        elif command in {"/pause", "/resume", "/cancel"}:
            if argument:
                self.session.active_run_id = argument
            action = {"/pause": "暂停", "/resume": "恢复", "/cancel": "取消"}[command]
            self._chat(f"{action}当前 Run。")
        elif command == "/watch":
            self._watch(argument or None)
        elif command == "/audit":
            self._render_audit(self.session.audit(argument or None))
        elif command == "/result":
            self._render_result(*self.session.result())
        elif command == "/repair":
            result = self.session.repair(argument or None)
            self._render_repair_candidates(result["candidates"])
            if result.get("run"):
                self._render_run(result["run"])
                self._start_run_monitor(result["run"])
        elif command == "/exclude":
            if not argument:
                raise ValueError("Use: /exclude <dataset_version_id>")
            self._render_dataset_resolution(self.session.exclude(argument))
        elif command == "/export":
            try:
                dataset_version_id, destination = argument.split(" ", 1)
            except ValueError as exc:
                raise ValueError(
                    "Use: /export <dataset_version_id> <destination>"
                ) from exc
            self._render_export(
                self.session.export(dataset_version_id, destination.strip())
            )
        elif command == "/help":
            self._render_help()
        else:
            raise ValueError(f"Unknown command: {command}")
        return True

    def _chat(self, content: str) -> None:
        self.console.print("[dim]DataAgent 正在处理...[/dim]")
        response: dict[str, Any] | None = None
        streamed_actions = False
        for event in self.session.chat_stream(content):
            if event.get("type") == "action":
                self._render_action_trace(
                    [event["action"]],
                    show_heading=not streamed_actions,
                )
                streamed_actions = True
            elif event.get("type") == "final":
                response = event["response"]
        if response is None:
            raise ControlPlaneError("控制面对话流没有返回最终结果")
        self._render_conversation(
            response,
            render_actions=not streamed_actions,
        )

    def _render_conversation(
        self,
        response: dict[str, Any],
        *,
        render_actions: bool = True,
    ) -> None:
        if render_actions and response.get("action_trace"):
            self._render_action_trace(response["action_trace"])
        self.console.print("[green]DataAgent>[/green]")
        self.console.print(Markdown(response["reply"]))
        if response.get("turn"):
            self._render_turn(response["turn"])
        if response.get("run"):
            run = response["run"]
            self._render_run(run)
            self._start_run_monitor(run)

    def _render_action_trace(
        self,
        actions: list[dict[str, Any]],
        *,
        show_heading: bool = True,
    ) -> None:
        if show_heading:
            self.console.print("[bold]行动摘要[/bold]")
        for action in actions:
            status = str(action.get("status") or "unknown")
            marker = "✓" if status == "succeeded" else "!"
            style = "green" if status == "succeeded" else "yellow"
            self.console.print(
                f"[{style}]{marker}[/{style}] "
                f"{action.get('stage_label') or action.get('stage', '-')}"
            )
            self.console.print(
                f"  [dim]调用[/dim]  {action.get('display_name') or action.get('tool', '-')}"
            )
            parameters = json.dumps(
                action.get("parameters") or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            self.console.print(f"  [dim]参数[/dim]  {parameters}")
            self.console.print(
                f"  [dim]结果[/dim]  {action.get('summary', '-')}"
            )
            evidence = list(action.get("evidence_ids") or ())
            if evidence:
                visible = ", ".join(str(item) for item in evidence[:5])
                if len(evidence) > 5:
                    visible += f"，另 {len(evidence) - 5} 项"
                self.console.print(f"  [dim]证据[/dim]  {visible}")
            self.console.print(
                f"  [dim]耗时[/dim]  {action.get('duration_ms', 0)} ms"
            )

    def _start_run_monitor(self, run: dict[str, Any]) -> None:
        terminal = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "PAUSED"}
        run_id = str(run["id"])
        if run.get("status") in terminal:
            return
        with self._monitor_lock:
            current = self._run_monitors.get(run_id)
            if current is not None and current.is_alive():
                return
            monitor = threading.Thread(
                target=self._monitor_run,
                args=(run_id, run),
                name=f"dataagent-monitor-{run_id}",
                daemon=True,
            )
            self._run_monitors[run_id] = monitor
            monitor.start()

    def _monitor_run(self, run_id: str, initial: dict[str, Any]) -> None:
        terminal = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "PAUSED"}
        previous = (
            initial.get("status"),
            initial.get("progress"),
            initial.get("total"),
        )
        try:
            while previous[0] not in terminal:
                time.sleep(1)
                run = self.session.run(run_id)
                current = (run.get("status"), run.get("progress"), run.get("total"))
                if current != previous:
                    self.console.print(f"\n[dim]Run update: {run_id}[/dim]")
                    self._render_run(run)
                    previous = current
                if current[0] in terminal:
                    style = "green" if current[0] == "SUCCEEDED" else "yellow"
                    self.console.print(
                        f"[{style}]Run {run_id} finished with status {current[0]}.[/{style}]"
                    )
                    if run.get("dataset_version_id"):
                        self._render_result(*self.session.result(run_id))
                    return
        except Exception as exc:
            self.console.print(f"\n[yellow]Run monitor stopped: {exc}[/yellow]")
        finally:
            with self._monitor_lock:
                self._run_monitors.pop(run_id, None)

    def _render_turn(self, payload: dict[str, Any]) -> None:
        state = payload.get("state", {})
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("WorkOrder", payload.get("work_order_id", "-"))
        table.add_row("Thread", payload.get("thread_id", "-"))
        table.add_row("Agent", state.get("current_agent", "-"))
        table.add_row("Next", state.get("next_action", "-"))
        interrupts = payload.get("interrupts", [])
        table.add_row(
            "Waiting",
            interrupts[0]["value"].get("kind", "-") if interrupts else "none",
        )
        self.console.print(table)
        if interrupts and interrupts[0]["value"].get("kind") == "pipeline_approval":
            pipelines = interrupts[0]["value"].get("pipelines", [])
            choices = Table(
                "Strategy", "Version", "Runnable", "Blocked reason", "Pipeline"
            )
            for item in pipelines:
                eligibility = item.get("execution_eligibility", {})
                eligible = eligibility.get("eligible")
                violations = eligibility.get("violations") or []
                choices.add_row(
                    item["strategy"],
                    str(item["version"]),
                    "yes" if eligible is True else "no" if eligible is False else "unknown",
                    "; ".join(str(value) for value in violations) or "-",
                    item["id"],
                )
            self.console.print(choices)
            self._render_pipeline_details(pipelines)

    def _render_pipeline_details(self, pipelines: list[dict[str, Any]]) -> None:
        for pipeline in pipelines:
            nodes = Table(
                "Step",
                "Node",
                "Operator version",
                "Backend",
                "Status",
                "Parameters",
                title=f"{pipeline['strategy']} pipeline",
            )
            for index, node in enumerate(pipeline.get("nodes", []), start=1):
                parameters = node.get("parameters") or {}
                nodes.add_row(
                    str(index),
                    str(node.get("id", "-")),
                    str(node.get("operator_version_id", "-")),
                    str(node.get("runtime_backend", "-")),
                    str(node.get("operator_status", "unknown")),
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                )
            self.console.print(nodes)

        strategies = [str(item.get("strategy", "unknown")) for item in pipelines]
        parameters_by_node: dict[str, dict[str, str]] = {}
        for pipeline in pipelines:
            strategy = str(pipeline.get("strategy", "unknown"))
            for node in pipeline.get("nodes", []):
                parameters_by_node.setdefault(str(node.get("id", "-")), {})[
                    strategy
                ] = json.dumps(
                    node.get("parameters") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                )
        varying = {
            node_id: values
            for node_id, values in parameters_by_node.items()
            if len(set(values.values())) > 1
        }
        if varying:
            comparison = Table("Node", *strategies, title="Strategy differences")
            for node_id, values in varying.items():
                comparison.add_row(
                    node_id,
                    *(values.get(strategy, "{}") for strategy in strategies),
                )
            self.console.print(comparison)

    def _render_runs(self, runs: list[dict[str, Any]]) -> None:
        table = Table("Run", "Status", "Progress", "Dataset", "QC")
        for run in runs:
            table.add_row(
                run["id"],
                run["status"],
                f"{run['progress']}/{run['total']}",
                run.get("dataset_version_id") or "-",
                run.get("qc_report_id") or "-",
            )
        self.console.print(table)

    def _render_run(self, run: dict[str, Any]) -> None:
        self._render_runs([run])
        if run.get("error"):
            self.console.print(f"[red]{run['error']}[/red]")

    def _watch(self, run_id: str | None) -> None:
        terminal = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "PAUSED"}
        while True:
            run = self.session.run(run_id)
            self._render_run(run)
            if run["status"] in terminal:
                return
            time.sleep(1)

    def _render_result(
        self, dataset: dict[str, Any], report: dict[str, Any] | None
    ) -> None:
        if not dataset:
            self.console.print("[yellow]Dataset is not available yet[/yellow]")
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("Dataset", dataset["id"])
        table.add_row("Sources", str(dataset.get("source_count", "-")))
        table.add_row("Kept", str(dataset.get("kept_count", "-")))
        table.add_row("Rejected", str(dataset.get("rejected_count", "-")))
        table.add_row("Failed", str(dataset.get("failed_count", "-")))
        table.add_row("Manifest", str(dataset.get("manifest_uri", "-")))
        if report:
            table.add_row("QC", report["status"])
            metrics = report.get("metrics") or {}
            table.add_row(
                "Hard violations",
                str(metrics.get("hard_rule_violation_rate", "-")),
            )
            table.add_row(
                "Semantic verified",
                (
                    "yes"
                    if report.get("semantic_quality_verified") is True
                    else "no"
                    if report.get("semantic_quality_verified") is False
                    else "-"
                ),
            )
        self.console.print(table)

    def _render_audit(self, results: list[dict[str, Any]]) -> None:
        table = Table("Image", "Node", "Operator", "Status", "Decision", "Reason", "ms")
        for item in results:
            table.add_row(
                str(item.get("source_uri", "-")),
                str(item.get("node_id", "-")),
                str(item.get("operator_version_id", "-")),
                str(item.get("status", "-")),
                str(item.get("decision", "-")),
                ", ".join(item.get("reason_codes") or []) or str(item.get("error") or "-"),
                str(item.get("duration_ms", 0)),
            )
        self.console.print(table)

    def _render_repair_candidates(self, candidates: dict[str, Any]) -> None:
        summary = Table("Run", "Dataset", "Status", "Attempt", "Retry", "Next")
        summary.add_row(
            str(candidates.get("run_id") or "-"),
            str(candidates.get("dataset_version_id") or "-"),
            str(candidates.get("run_status") or "-"),
            (
                f"{candidates.get('repair_attempt', 0)}/"
                f"{candidates.get('maximum_repair_attempts', 3)}"
            ),
            "yes" if candidates.get("retry_allowed") else "no",
            ", ".join(candidates.get("next_actions") or []) or "-",
        )
        self.console.print(summary)
        assets = Table("Disposition", "Source", "Reasons", "Attempts")
        for disposition in (
            "still_failed",
            "abandoned_assets",
            "excluded_assets",
        ):
            for item in candidates.get(disposition, ()):
                assets.add_row(
                    disposition,
                    str(item.get("source_uri") or "-"),
                    ", ".join(item.get("reason_codes") or []) or "-",
                    str(item.get("repair_attempts", 0)),
                )
        self.console.print(assets)

    def _render_dataset_resolution(self, dataset: dict[str, Any]) -> None:
        self.console.print(
            f"DatasetVersion {dataset['id']} resolved: "
            f"still_failed={len(dataset.get('still_failed') or [])}, "
            f"abandoned={len(dataset.get('abandoned_assets') or [])}, "
            f"excluded={len(dataset.get('excluded_assets') or [])}."
        )

    def _render_export(self, exported: dict[str, Any]) -> None:
        self.console.print(
            f"Deliverable export {exported['id']}: {exported['root_uri']}\n"
            f"files={exported['file_count']}, manifest={exported['manifest_uri']}, "
            f"excluded={exported['excluded_assets_uri']}"
        )

    def _render_help(self) -> None:
        table = Table("Command", "Argument")
        for command, argument in (
            ("/new", "<source> | <requirement>"),
            ("/open", "<work_order_id>"),
            ("/status", ""),
            ("/approve", "retention_first | balanced | quality_first"),
            ("/reject", "reason (optional)"),
            ("/submit", ""),
            ("/runs", ""),
            ("/run", "run_id (optional)"),
            ("/pause | /resume | /cancel", "run_id (optional)"),
            ("/watch", "run_id (optional)"),
            ("/audit", "run_id (optional)"),
            ("/result", ""),
            ("/repair", "run_id | dataset_version_id (optional)"),
            ("/exclude", "<dataset_version_id>"),
            ("/export", "<dataset_version_id> <destination>"),
            ("/exit", ""),
        ):
            table.add_row(command, argument)
        self.console.print(table)
