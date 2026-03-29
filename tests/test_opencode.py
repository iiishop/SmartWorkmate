from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from smartworkmate.opencode import (
    list_project_sessions,
    list_projects,
    scan_task_markdown_documents,
)
import smartworkmate.opencode.tools as opencode_tools


def test_list_projects_reads_all_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='[{"id":"p1","name":"SmartWorkmate","worktree":"D:\\\\workspace\\\\projects\\\\SmartWorkmate","time_updated":"2026-03-27T00:00:00Z"}]',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("OPENCODE_EXECUTABLE", "opencode")

    projects = list_projects()

    assert recorded[0][1:3] == [
        "db",
        "SELECT id, name, worktree, time_updated FROM project WHERE worktree IS NOT NULL AND worktree != '/' ORDER BY time_updated DESC;",
    ]
    assert len(projects) == 1
    assert projects[0].id == "p1"
    assert projects[0].name == "SmartWorkmate"
    assert projects[0].worktree == "D:\\workspace\\projects\\SmartWorkmate"


def test_list_project_sessions_filters_by_project_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[1:] == [
            "session",
            "list",
            "--format",
            "json",
            "--max-count",
            "300",
        ]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "["
                '{"id":"s1","directory":"D:\\\\workspace\\\\projects\\\\SmartWorkmate","title":"run-a"},'
                '{"id":"s2","directory":"D:\\\\workspace\\\\projects\\\\Other","title":"run-b"}'
                "]"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("OPENCODE_EXECUTABLE", "opencode")

    sessions = list_project_sessions("D:/workspace/projects/SmartWorkmate")

    assert len(sessions) == 1
    assert sessions[0].id == "s1"
    assert sessions[0].directory == "D:\\workspace\\projects\\SmartWorkmate"


def test_opencode_respects_custom_executable_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="[]",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("OPENCODE_EXECUTABLE", "npx -y opencode")

    list_projects()

    assert recorded[0][:2] == ["npx", "-y"]


def test_opencode_raises_runtime_error_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="failed",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("OPENCODE_EXECUTABLE", "opencode")

    with pytest.raises(RuntimeError, match="failed"):
        list_projects()


def test_list_project_sessions_requires_non_empty_project() -> None:
    with pytest.raises(ValueError, match="project_worktree"):
        list_project_sessions("   ")


def test_default_executable_prefers_opencode_cmd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCODE_EXECUTABLE", raising=False)
    monkeypatch.setattr(
        opencode_tools.shutil,
        "which",
        lambda name: "C:/bin/opencode.cmd" if name == "opencode.cmd" else None,
    )

    resolved = opencode_tools._resolve_opencode_executable()

    assert resolved == ["C:/bin/opencode.cmd"]


def test_default_executable_falls_back_to_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_EXECUTABLE", raising=False)
    monkeypatch.setattr(
        opencode_tools.shutil,
        "which",
        lambda name: "C:/Program Files/nodejs/npx.cmd" if name == "npx.cmd" else None,
    )
    monkeypatch.setattr(opencode_tools.Path, "exists", lambda self: False)

    resolved = opencode_tools._resolve_opencode_executable()

    assert resolved == ["C:/Program Files/nodejs/npx.cmd", "-y", "opencode"]


def test_default_executable_falls_back_to_appdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCODE_EXECUTABLE", raising=False)
    monkeypatch.setenv("APPDATA", r"C:\Users\iiishop\AppData\Roaming")
    monkeypatch.setattr(opencode_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        opencode_tools.Path,
        "exists",
        lambda self: str(self).endswith(r"npm\opencode.cmd"),
    )

    resolved = opencode_tools._resolve_opencode_executable()

    assert resolved == [r"C:\Users\iiishop\AppData\Roaming\npm\opencode.cmd"]


def test_scan_task_markdown_documents_collects_only_fin_marked_files(
    tmp_path: Path,
) -> None:
    root = tmp_path
    docs_tasks = root / "docs" / "tasks"
    (docs_tasks / "LRisk").mkdir(parents=True)
    (docs_tasks / "HRisk").mkdir(parents=True)

    (docs_tasks / "A.md").write_text("line\n--FIN--", encoding="utf-8")
    (docs_tasks / "B.md").write_text("line\n--NOT-FIN--", encoding="utf-8")
    (docs_tasks / "notes.txt").write_text("line\n--FIN--", encoding="utf-8")
    (docs_tasks / "LRisk" / "C.md").write_text("x\n--FIN--", encoding="utf-8")
    (docs_tasks / "HRisk" / "D.md").write_text("x\n--FIN--", encoding="utf-8")
    (docs_tasks / "misc" / "E.md").parent.mkdir(parents=True, exist_ok=True)
    (docs_tasks / "misc" / "E.md").write_text("x\n--FIN--", encoding="utf-8")

    files = scan_task_markdown_documents(str(root))

    rel = [Path(item).relative_to(root).as_posix() for item in files]
    assert rel == [
        "docs/tasks/A.md",
        "docs/tasks/HRisk/D.md",
        "docs/tasks/LRisk/C.md",
    ]


def test_scan_task_markdown_documents_rejects_trailing_content_after_fin(
    tmp_path: Path,
) -> None:
    docs_tasks = tmp_path / "docs" / "tasks"
    docs_tasks.mkdir(parents=True)
    (docs_tasks / "A.md").write_text("line\n--FIN--\n", encoding="utf-8")
    (docs_tasks / "B.md").write_text("line\n--FIN--\nextra", encoding="utf-8")

    files = scan_task_markdown_documents(str(tmp_path))

    rel = [Path(item).relative_to(tmp_path).as_posix() for item in files]
    assert rel == ["docs/tasks/A.md"]
