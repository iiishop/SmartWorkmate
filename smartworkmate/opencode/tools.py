from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


def run_json_command(args: list[str]) -> Any:
    completed = run_opencode(args)
    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "opencode command failed"
        )
        raise RuntimeError(message)
    payload = _extract_json_payload(completed.stdout or "")
    return json.loads(payload)


def run_opencode(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = _resolve_opencode_executable()
    return subprocess.run(
        [*executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def normalize_directory(value: str) -> str:
    return os.path.normcase(os.path.normpath(value.strip().replace("/", "\\")))


def _resolve_opencode_executable() -> list[str]:
    raw = os.environ.get("OPENCODE_EXECUTABLE", "").strip()
    if raw:
        parts = shlex.split(raw)
        if not parts:
            raise ValueError("OPENCODE_EXECUTABLE must not be empty")
        return parts

    discovered = _discover_default_opencode_executable()
    if discovered:
        return [discovered]

    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        return [npx, "-y", "opencode"]

    return ["opencode"]


def _discover_default_opencode_executable() -> str:
    direct = shutil.which("opencode")
    if direct:
        return direct

    cmd = shutil.which("opencode.cmd")
    if cmd:
        return cmd

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidate = Path(appdata) / "npm" / "opencode.cmd"
        if candidate.exists():
            return str(candidate)

    return ""


def _extract_json_payload(stdout: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        return stdout[index : index + end]
    raise RuntimeError("opencode output did not contain JSON payload")
