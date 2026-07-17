from __future__ import annotations

import time
from typing import Any

from rich.console import Console
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

    def run(self) -> None:
        self.console.print("[bold]DataAgent[/bold]  [dim]TUI control plane[/dim]")
        self.console.print("你好，请描述你想生产的图片数据；输入 [bold]/help[/bold] 可查看控制命令。")
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
            self._handle_natural_language(line)
            return True
        command, _, argument = line.partition(" ")
        command = command.lower()
        argument = argument.strip()
        if command in {"/exit", "/quit"}:
            return False
        if command == "/new":
            source, requirement = parse_new_command(argument)
            self._render_turn(self.session.start(requirement=requirement, source=source))
        elif command == "/open":
            if not argument:
                raise ValueError("Work order id is required")
            self._render_turn(self.session.open(argument))
        elif command == "/status":
            self._render_turn(self.session.refresh())
        elif command == "/approve":
            self._render_turn(self.session.approve(argument or "balanced"))
        elif command == "/reject":
            self._render_turn(self.session.reject(argument or "rejected from tui"))
        elif command == "/submit":
            self._render_run(self.session.submit_run())
        elif command == "/runs":
            self._render_runs(self.session.runs())
        elif command == "/run":
            self._render_run(self.session.run(argument or None))
        elif command in {"/pause", "/resume", "/cancel"}:
            self._render_run(self.session.control(command[1:], argument or None))
        elif command == "/watch":
            self._watch(argument or None)
        elif command == "/result":
            self._render_result(*self.session.result())
        elif command == "/help":
            self._render_help()
        else:
            raise ValueError(f"Unknown command: {command}")
        return True

    def _handle_natural_language(self, text: str) -> None:
        normalized = text.strip().lower()
        greeting = normalized.strip("!！。,.，~～ ")
        if greeting in {"你好", "您好", "嗨", "hi", "hello", "hey"}:
            self.console.print("你好。请告诉我数据目标，例如：筛选清晰的人像图片并去重。")
            return
        if not self.session.work_order_id:
            self.console.print("需求已记录。接下来请提供这批图片所在的本地目录。")
            source = self.console.input(
                "[cyan]图片目录（例如 D:\\images\\incoming）>[/cyan] "
            ).strip()
            if not source:
                raise ValueError("图片目录不能为空")
            self._render_turn(self.session.start(requirement=text, source=source))
            return
        if normalized in {"确认", "批准", "同意", "approve", "yes", "y"}:
            self._render_turn(self.session.approve())
            return
        if normalized in {"拒绝", "不同意", "reject", "no", "n"}:
            self._render_turn(self.session.reject(text))
            return
        raise ValueError("This milestone supports natural-language creation and approval controls")

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
            choices = Table("Strategy", "Version", "Pipeline")
            for item in pipelines:
                choices.add_row(item["strategy"], str(item["version"]), item["id"])
            self.console.print(choices)

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
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "PAUSED"}
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
        table.add_row("Sources", str(dataset["source_count"]))
        table.add_row("Kept", str(dataset["kept_count"]))
        table.add_row("Rejected", str(dataset["rejected_count"]))
        table.add_row("Manifest", dataset["manifest_uri"])
        if report:
            table.add_row("QC", report["status"])
            table.add_row(
                "Hard violations",
                str(report["metrics"]["hard_rule_violation_rate"]),
            )
            table.add_row(
                "Semantic verified",
                "yes" if report["semantic_quality_verified"] else "no",
            )
        self.console.print(table)

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
            ("/result", ""),
            ("/exit", ""),
        ):
            table.add_row(command, argument)
        self.console.print(table)
