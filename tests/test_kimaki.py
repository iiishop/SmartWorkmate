from __future__ import annotations

import ast
from pathlib import Path
import subprocess

import pytest

from smartworkmate.kimaki.kimaki import send_to_channel_subthread


def test_kimaki_module_contains_only_public_api_functions() -> None:
    module_path = Path("smartworkmate/kimaki/kimaki.py")
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    class_names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]

    assert function_names == ["send_to_channel_subthread"]
    assert class_names == []


def test_send_to_channel_subthread_reuses_existing_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        if command[:4] == ["kimaki", "project", "list", "--json"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='[{"channel_id":"147","directory":"D:\\\\workspace"}]',
                stderr="",
            )
        if command[:5] == ["kimaki", "session", "list", "--project", "D:\\workspace"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='[{"source":"kimaki","title":"build-log","threadId":"555"}]',
                stderr="",
            )
        if command[:2] == ["kimaki", "send"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="ok",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    send_to_channel_subthread("147", "build-log", "done")

    assert recorded[-1] == [
        "kimaki",
        "send",
        "--thread",
        "555",
        "--prompt",
        "done",
    ]


def test_send_to_channel_subthread_creates_thread_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        if command[:4] == ["kimaki", "project", "list", "--json"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='[{"channel_id":"147","directory":"D:\\\\workspace"}]',
                stderr="",
            )
        if command[:5] == ["kimaki", "session", "list", "--project", "D:\\workspace"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='[{"source":"kimaki","title":"other","threadId":"777"}]',
                stderr="",
            )
        if command[:2] == ["kimaki", "send"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="ok",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    send_to_channel_subthread("147", "build-log", "done")

    assert recorded[-1] == [
        "kimaki",
        "send",
        "--channel",
        "147",
        "--prompt",
        "done",
        "--name",
        "build-log",
    ]


def test_send_honors_custom_kimaki_executable_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='[{"channel_id":"147","directory":"D:\\\\workspace"}]',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("KIMAKI_EXECUTABLE", "npx -y kimaki")

    send_to_channel_subthread("147", "build-log", "done")

    assert recorded[0] == ["npx", "-y", "kimaki", "project", "list", "--json"]


def test_send_invokes_subprocess_with_utf8_text_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs_seen: dict[str, object] = {}

    def _fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        kwargs_seen.update(kwargs)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='[{"channel_id":"147","directory":"D:\\\\workspace"}]',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    send_to_channel_subthread("147", "build-log", "done")

    assert kwargs_seen["text"] is True
    assert kwargs_seen["encoding"] == "utf-8"
    assert kwargs_seen["errors"] == "replace"


def test_send_raises_runtime_error_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=2,
            stdout="",
            stderr="bad args",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="bad args"):
        send_to_channel_subthread("147", "build-log", "done")


def test_send_raises_value_error_on_empty_input() -> None:
    with pytest.raises(ValueError, match="channel_id"):
        send_to_channel_subthread("", "build-log", "done")
