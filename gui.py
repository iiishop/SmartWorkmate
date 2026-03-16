from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import flet as ft


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        process.terminate()


def _parse_json_payload(stdout: str) -> dict[str, Any]:
    clean = ANSI_ESCAPE_RE.sub("", stdout or "")
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            continue
        candidate = "\n".join(lines[index:]).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            return {"payload": parsed}
        except json.JSONDecodeError:
            continue
    return {}


def _run_command_capture(
    command: list[str],
    *,
    cwd: Path,
    log_queue: queue.Queue[str],
    process_state: dict[str, Any],
) -> tuple[int, str]:
    log_queue.put(f"$ {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    process_state["process"] = process
    output = ""
    while True:
        if process_state.get("stop_requested"):
            _kill_process_tree(process)
            break
        try:
            out, _ = process.communicate(timeout=0.4)
            output = out or ""
            break
        except subprocess.TimeoutExpired:
            continue

    code = process.wait()
    process_state["process"] = None
    lines = [line for line in output.splitlines() if line.strip()]
    if lines:
        log_queue.put(f"[输出] 共 {len(lines)} 行，展示最近 15 行：")
        for line in lines[-15:]:
            log_queue.put(line)
    log_queue.put(f"[exit={code}] {' '.join(command)}")
    return code, output


def main(page: ft.Page) -> None:
    page.title = "SmartWorkmate 指挥中心"
    page.padding = 0
    page.window_width = 1300
    page.window_height = 900
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.bgcolor = "#F6F7FB"

    repo_root = Path(__file__).resolve().parent
    log_queue: queue.Queue[str] = queue.Queue()
    process_state: dict[str, Any] = {"process": None, "busy": False, "stop_requested": False}

    status_text = ft.Text("就绪", color="#0F9D58", weight=ft.FontWeight.BOLD)
    mode_dropdown = ft.Dropdown(
        value="execute_daemon",
        label="运行模式",
        options=[
            ft.dropdown.Option("execute_daemon", "执行守护模式（推荐）"),
            ft.dropdown.Option("execute_once", "执行一次"),
            ft.dropdown.Option("dry_run_once", "干跑一次"),
        ],
        width=260,
        dense=True,
    )
    user_field = ft.TextField(label="用户", value="iiishop", width=220, dense=True)
    root_field = ft.TextField(label="根目录（可选）", value="", expand=True, dense=True)
    interval_field = ft.TextField(label="守护间隔(秒)", value="300", width=150, dense=True)
    opencode_global_checkbox = ft.Checkbox(label="使用 OpenCode 已索引项目", value=True)
    dark_mode_toggle = ft.Switch(label="夜间主题", value=False)

    cycle_value = ft.Text("-", size=26, weight=ft.FontWeight.BOLD)
    project_value = ft.Text("-", size=26, weight=ft.FontWeight.BOLD)
    countdown_value = ft.Text("-", size=26, weight=ft.FontWeight.BOLD)
    last_run_value = ft.Text("-", size=13, color="#5F6368")
    stats_value = ft.Text("派发=0 | 活跃=0 | 自动任务=0 | PR事件=0 | 异常=0", size=13)

    dispatch_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    active_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    auto_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    pr_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    error_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    policy_col = ft.Column(spacing=4, height=180, scroll=ft.ScrollMode.AUTO)
    history_list = ft.ListView(height=260, spacing=4, auto_scroll=True)
    activity_list = ft.ListView(height=320, spacing=6, auto_scroll=False)
    project_cards = ft.Row(wrap=True, spacing=10, run_spacing=10)
    log_list = ft.ListView(height=220, spacing=2, auto_scroll=True)

    def log_color(line: str) -> str:
        upper = line.upper()
        if "[ERROR]" in upper or "阻塞" in line:
            return "#B00020"
        if "[WARN]" in upper:
            return "#C77700"
        if "[INFO]" in upper:
            return "#0B57D0"
        return "#2D2D2D"

    def append_log(line: str) -> None:
        log_list.controls.append(ft.Text(line, size=12, selectable=True, color=log_color(line)))
        if len(log_list.controls) > 260:
            log_list.controls = log_list.controls[-260:]

    async def log_pump() -> None:
        while True:
            dirty = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                append_log(line)
                dirty = True
            if dirty:
                page.update()
            await asyncio.sleep(0.15)

    page.run_task(log_pump)

    def apply_theme() -> None:
        if dark_mode_toggle.value:
            page.theme_mode = ft.ThemeMode.DARK
            page.bgcolor = "#12151D"
        else:
            page.theme_mode = ft.ThemeMode.LIGHT
            page.bgcolor = "#F6F7FB"
        page.update()

    dark_mode_toggle.on_change = lambda _e: apply_theme()

    def set_busy(is_busy: bool, status: str, color: str) -> None:
        process_state["busy"] = is_busy
        process_state["stop_requested"] = False if is_busy else process_state["stop_requested"]
        start_button.disabled = is_busy
        stop_button.disabled = not is_busy
        status_text.value = status
        status_text.color = color
        page.update()

    def _set_section(column: ft.Column, lines: list[str], *, default: str = "- 无") -> None:
        column.controls.clear()
        for line in (lines or [default])[:40]:
            color = "#B00020" if "阻塞" in line or "failed" in line else None
            column.controls.append(ft.Text(line, size=12, selectable=True, color=color))

    def _add_history(lines: list[str]) -> None:
        for line in lines[:12]:
            history_list.controls.append(ft.Text(line, size=12, selectable=True))
        if len(history_list.controls) > 180:
            history_list.controls = history_list.controls[-180:]

    def _build_cycle_command(*, execute: bool) -> list[str]:
        command = [
            "uv",
            "run",
            "python",
            "-m",
            "smartworkmate.cli",
            "start",
            "--once",
            "--no-live",
            "--user",
            user_field.value.strip() or "iiishop",
            "--interval",
            str(max(30, int(interval_field.value.strip() or "300"))),
        ]
        root_value = root_field.value.strip()
        if root_value:
            command.extend(["--root", root_value])
        command.append("--execute" if execute else "--dry-run")
        if opencode_global_checkbox.value:
            command.append("--opencode-global")
        return command

    def _extract_dashboard(payload: dict[str, Any]) -> None:
        cycles = payload.get("cycles", [])
        if not isinstance(cycles, list) or not cycles:
            return
        latest = cycles[-1]
        processed = latest.get("processed", []) if isinstance(latest, dict) else []
        if not isinstance(processed, list):
            processed = []

        dispatch_lines: list[str] = []
        active_lines: list[str] = []
        auto_lines: list[str] = []
        pr_lines: list[str] = []
        error_lines: list[str] = []
        policy_lines: list[str] = []
        project_summary: dict[str, dict[str, Any]] = {}

        counts = {"dispatch": 0, "active": 0, "auto": 0, "pr": 0, "error": 0}

        for item in processed:
            if not isinstance(item, dict):
                continue
            project = str(item.get("project", ""))
            label = Path(project).name or project
            project_summary.setdefault(label, {"dispatch": 0, "auto": 0, "errors": 0, "active": 0})

            policy = item.get("policy")
            if isinstance(policy, dict):
                policy_lines.append(
                    f"- {label}: backend={policy.get('backend','')}, worktree={policy.get('require_worktree_isolation','')}, auto_commit={policy.get('auto_commit','')}"
                )

            mode = str(item.get("mode", ""))
            if mode in {"kimaki", "opencode"}:
                line = f"- {label}: {mode} -> {item.get('task_id','')}"
                dispatch_lines.append(line)
                activity_list.controls.insert(0, ft.Text(line, size=12, selectable=True))
                counts["dispatch"] += 1
                project_summary[label]["dispatch"] += 1

            if item.get("result") == "waiting_active_tasks":
                ids = item.get("active_task_ids", [])
                if isinstance(ids, list) and ids:
                    line = f"- {label}: {', '.join(str(x) for x in ids)}"
                    active_lines.append(line)
                    counts["active"] += len(ids)
                    project_summary[label]["active"] += len(ids)

            if mode == "idle_task":
                result = item.get("result", {})
                if isinstance(result, dict):
                    line = f"- {label}: 自动任务 {result.get('result', '')}"
                    auto_lines.append(line)
                    counts["auto"] += 1
                    project_summary[label]["auto"] += 1

            if mode == "reconcile":
                events = item.get("events", [])
                if isinstance(events, list):
                    for event in events:
                        if not isinstance(event, dict):
                            continue
                        task_id = str(event.get("task_id", ""))
                        auto_pr = event.get("auto_pr")
                        if isinstance(auto_pr, dict):
                            if auto_pr.get("url"):
                                line = f"- {label}: {task_id} PR {auto_pr.get('url','')}"
                                pr_lines.append(line)
                                counts["pr"] += 1
                            elif auto_pr.get("reason"):
                                reason = str(auto_pr.get("reason", ""))
                                pr_line = f"- {label}: {task_id} PR 阻塞: {reason}"
                                err_line = f"- {label}: {reason}"
                                pr_lines.append(pr_line)
                                error_lines.append(err_line)
                                counts["error"] += 1
                                project_summary[label]["errors"] += 1

            item_result = item.get("result")
            if isinstance(item_result, dict) and item_result.get("reason"):
                line = f"- {label}: {item_result.get('reason','')}"
                error_lines.append(line)
                counts["error"] += 1
                project_summary[label]["errors"] += 1

        if len(activity_list.controls) > 120:
            activity_list.controls = activity_list.controls[:120]

        cycle_value.value = str(len(cycles))
        project_value.value = str(len(project_summary))
        last_run_value.value = datetime.now().isoformat(timespec="seconds")
        stats_value.value = (
            f"派发={counts['dispatch']} | 活跃={counts['active']} | 自动任务={counts['auto']} | "
            f"PR事件={counts['pr']} | 异常={counts['error']}"
        )
        _set_section(dispatch_col, dispatch_lines)
        _set_section(active_col, active_lines)
        _set_section(auto_col, auto_lines)
        _set_section(pr_col, pr_lines)
        _set_section(error_col, error_lines)
        _set_section(policy_col, policy_lines, default="- 无策略信息")
        _add_history(auto_lines + pr_lines + error_lines)

        project_cards.controls.clear()
        for name, meta in sorted(project_summary.items()):
            project_cards.controls.append(
                ft.Container(
                    width=290,
                    border_radius=12,
                    padding=10,
                    bgcolor="#FFFFFF" if page.theme_mode == ft.ThemeMode.LIGHT else "#1E2230",
                    border=ft.Border.all(1, "#E0E4EA"),
                    content=ft.Column(
                        [
                            ft.Text(name, weight=ft.FontWeight.BOLD, size=14),
                            ft.Row(
                                [
                                    ft.Chip(label=ft.Text(f"派发 {meta['dispatch']}"), bgcolor="#E8F0FE"),
                                    ft.Chip(label=ft.Text(f"自动 {meta['auto']}"), bgcolor="#E6F4EA"),
                                ],
                                wrap=True,
                            ),
                            ft.Row(
                                [
                                    ft.Chip(label=ft.Text(f"活跃 {meta['active']}"), bgcolor="#FEF7E0"),
                                    ft.Chip(label=ft.Text(f"异常 {meta['errors']}"), bgcolor="#FCE8E6"),
                                ],
                                wrap=True,
                            ),
                        ],
                        spacing=6,
                    ),
                )
            )

    def _daemon_worker() -> None:
        set_busy(True, "守护模式运行中", "#0B57D0")
        try:
            interval = max(30, int(interval_field.value.strip() or "300"))
        except ValueError:
            log_queue.put("[ERROR] 守护间隔必须是 >=30 的整数")
            set_busy(False, "输入无效", "#B00020")
            return

        cycle_no = 0
        while not process_state.get("stop_requested"):
            cycle_no += 1
            status_text.value = f"执行轮次 {cycle_no}"
            command = _build_cycle_command(execute=True)
            code, stdout = _run_command_capture(command, cwd=repo_root, log_queue=log_queue, process_state=process_state)
            payload = _parse_json_payload(stdout)
            if payload:
                _extract_dashboard(payload)
            else:
                log_queue.put("[WARN] 本轮未解析到结构化 JSON 输出")

            status_text.value = f"轮次 {cycle_no} 完成" if code == 0 else f"轮次 {cycle_no} 失败（code={code}）"
            status_text.color = "#0F9D58" if code == 0 else "#B00020"
            page.update()

            for remaining in range(interval, 0, -1):
                if process_state.get("stop_requested"):
                    break
                countdown_value.value = str(remaining)
                page.update()
                time.sleep(1)

        set_busy(False, "已停止", "#C77700")

    def _single_worker(*, execute: bool) -> None:
        set_busy(True, "单次执行中", "#0B57D0")
        try:
            command = _build_cycle_command(execute=execute)
            code, stdout = _run_command_capture(command, cwd=repo_root, log_queue=log_queue, process_state=process_state)
            payload = _parse_json_payload(stdout)
            if payload:
                _extract_dashboard(payload)
            else:
                log_queue.put("[WARN] 未解析到结构化 JSON 输出")
            set_busy(False, "单次完成" if code == 0 else f"单次失败（code={code}）", "#0F9D58" if code == 0 else "#B00020")
        except ValueError:
            log_queue.put("[ERROR] 守护间隔必须是 >=30 的整数")
            set_busy(False, "输入无效", "#B00020")
        except Exception as error:  # noqa: BLE001
            log_queue.put(f"[ERROR] {error}")
            set_busy(False, "运行异常", "#B00020")

    def start_clicked(_event: ft.ControlEvent) -> None:
        if process_state["busy"]:
            return
        process_state["stop_requested"] = False
        mode = mode_dropdown.value
        if mode == "execute_daemon":
            page.run_thread(_daemon_worker)
        elif mode == "execute_once":
            page.run_thread(lambda: _single_worker(execute=True))
        else:
            page.run_thread(lambda: _single_worker(execute=False))

    def stop_clicked(_event: ft.ControlEvent) -> None:
        process_state["stop_requested"] = True
        process = process_state.get("process")
        if process is not None:
            _kill_process_tree(process)
        log_queue.put("[INFO] 已请求停止。")

    start_button = ft.ElevatedButton(
        "启动",
        on_click=start_clicked,
        icon=ft.Icons.PLAY_CIRCLE_FILL_ROUNDED,
        style=ft.ButtonStyle(bgcolor="#0B57D0", color="#FFFFFF"),
    )
    stop_button = ft.OutlinedButton("停止", on_click=stop_clicked, icon=ft.Icons.STOP_CIRCLE_OUTLINED, disabled=True)

    def metric_card(title: str, value_control: ft.Control, icon: str, color: str) -> ft.Container:
        return ft.Container(
            width=210,
            padding=12,
            border_radius=14,
            gradient=ft.LinearGradient([color, "#FFFFFF"]),
            border=ft.Border.all(1, "#E0E4EA"),
            content=ft.Column(
                [
                    ft.Row([ft.Icon(icon, size=18), ft.Text(title, size=12, weight=ft.FontWeight.W_500)]),
                    value_control,
                ],
                spacing=6,
            ),
        )

    def section_card(title: str, content: ft.Control, subtitle: str = "") -> ft.Container:
        return ft.Container(
            padding=12,
            border_radius=14,
            border=ft.Border.all(1, "#E0E4EA"),
            bgcolor="#FFFFFF" if page.theme_mode == ft.ThemeMode.LIGHT else "#1E2230",
            content=ft.Column(
                [
                    ft.Text(title, weight=ft.FontWeight.BOLD, size=15),
                    ft.Text(subtitle, size=11, color="#6B7280") if subtitle else ft.Container(height=0),
                    content,
                ],
                spacing=8,
            ),
            expand=True,
        )

    header = ft.Container(
        padding=18,
        gradient=ft.LinearGradient(["#0B57D0", "#7C4DFF"]),
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("SmartWorkmate 指挥中心", size=28, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                        ft.Text("本地 Worktree 执行 + Discord 进度线程（中文可视化）", color="#EAF0FF"),
                    ],
                    spacing=4,
                ),
                ft.Container(expand=True),
                ft.Column([ft.Text("状态", color="#EAF0FF", size=12), status_text], horizontal_alignment=ft.CrossAxisAlignment.END),
            ]
        ),
    )

    control_bar = ft.Container(
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        content=ft.Column(
            [
                ft.Row([mode_dropdown, user_field, interval_field, dark_mode_toggle], wrap=True),
                ft.Row([root_field]),
                ft.Row([opencode_global_checkbox, start_button, stop_button], wrap=True),
            ],
            spacing=8,
        ),
    )

    overview_tab = ft.Column(
        [
            ft.Row(
                [
                    metric_card("轮次", cycle_value, ft.Icons.AUTORENEW_ROUNDED, "#E8F0FE"),
                    metric_card("识别项目", project_value, ft.Icons.HUB_ROUNDED, "#E6F4EA"),
                    metric_card("下次轮询(秒)", countdown_value, ft.Icons.TIMER_ROUNDED, "#FFF4E5"),
                    metric_card("最近刷新", last_run_value, ft.Icons.UPDATE_ROUNDED, "#F3E8FF"),
                ],
                wrap=True,
            ),
            ft.Container(
                padding=10,
                border_radius=10,
                bgcolor="#EEF3FD" if page.theme_mode == ft.ThemeMode.LIGHT else "#20283A",
                content=stats_value,
            ),
            ft.Row(
                [
                    section_card("当前派发", dispatch_col, "本轮新任务派发情况"),
                    section_card("执行中的任务", active_col, "正在等待完成或验证的任务"),
                ],
            ),
            ft.Row(
                [
                    section_card("自动发现与任务生成", auto_col, "auto task 生成/跳过信息"),
                    section_card("PR 状态", pr_col, "PR 创建与阻塞信息"),
                ],
            ),
            ft.Row(
                [
                    section_card("异常与阻塞", error_col, "需要优先处理的问题"),
                    section_card("执行策略", policy_col, "后端/隔离/自动提交策略"),
                ],
            ),
        ],
        spacing=12,
    )

    project_tab = ft.Column(
        [
            ft.Text("项目视图", size=18, weight=ft.FontWeight.BOLD),
            ft.Text("每个项目的派发、活跃、自动任务、异常统计", color="#6B7280", size=12),
            project_cards,
        ],
        spacing=10,
    )

    timeline_tab = ft.Column(
        [
            ft.Text("时间线", size=18, weight=ft.FontWeight.BOLD),
            section_card("历史摘要", history_list, "最近轮次的关键信息"),
            section_card("活动流", activity_list, "派发和关键事件按时间倒序"),
        ],
        spacing=10,
    )

    logs_tab = ft.Column(
        [
            ft.Text("运行日志", size=18, weight=ft.FontWeight.BOLD),
            section_card("命令输出", log_list, "展示最近命令输出（错误高亮）"),
        ],
        spacing=10,
    )

    view_name = {"value": "overview"}
    overview_container = ft.Container(content=overview_tab)
    project_container = ft.Container(content=project_tab, visible=False)
    timeline_container = ft.Container(content=timeline_tab, visible=False)
    logs_container = ft.Container(content=logs_tab, visible=False)

    nav_overview = ft.FilledButton("总览", icon=ft.Icons.DASHBOARD_ROUNDED)
    nav_project = ft.OutlinedButton("项目", icon=ft.Icons.ACCOUNT_TREE_ROUNDED)
    nav_timeline = ft.OutlinedButton("时间线", icon=ft.Icons.TIMELINE_ROUNDED)
    nav_logs = ft.OutlinedButton("日志", icon=ft.Icons.RECEIPT_LONG_ROUNDED)

    def _apply_nav_style() -> None:
        current = view_name["value"]
        nav_overview.style = ft.ButtonStyle(bgcolor="#0B57D0", color="#FFFFFF") if current == "overview" else None
        nav_project.style = ft.ButtonStyle(bgcolor="#0B57D0", color="#FFFFFF") if current == "project" else None
        nav_timeline.style = ft.ButtonStyle(bgcolor="#0B57D0", color="#FFFFFF") if current == "timeline" else None
        nav_logs.style = ft.ButtonStyle(bgcolor="#0B57D0", color="#FFFFFF") if current == "logs" else None

    def _switch_view(name: str) -> None:
        view_name["value"] = name
        overview_container.visible = name == "overview"
        project_container.visible = name == "project"
        timeline_container.visible = name == "timeline"
        logs_container.visible = name == "logs"
        _apply_nav_style()
        page.update()

    nav_overview.on_click = lambda _e: _switch_view("overview")
    nav_project.on_click = lambda _e: _switch_view("project")
    nav_timeline.on_click = lambda _e: _switch_view("timeline")
    nav_logs.on_click = lambda _e: _switch_view("logs")
    _apply_nav_style()

    page.add(
        header,
        control_bar,
        ft.Container(
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
            content=ft.Column(
                [
                    ft.Row([nav_overview, nav_project, nav_timeline, nav_logs], wrap=True),
                    overview_container,
                    project_container,
                    timeline_container,
                    logs_container,
                ],
                spacing=10,
            ),
            expand=True,
        ),
    )


if __name__ == "__main__":
    try:
        ft.run(main)
    except ImportError as error:
        if "flet_desktop" not in str(error):
            raise
        print("[WARN] Desktop Flet runtime unavailable; falling back to browser mode.")
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550)
