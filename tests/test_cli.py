from __future__ import annotations

from pathlib import Path

import pytest

from smartworkmate.acceptance_spec.cli import (
    _derive_output_base_name,
    _derive_task_id,
    _resolve_source_paths,
)


def test_cli_resolves_positional_task_path() -> None:
    kind, path = _resolve_source_paths(
        positional_task_md=Path("docs/tasks/TSK-2026-001.md"),
        task_md=None,
        asl_file=None,
    )
    assert kind == "task_md"
    assert path.name == "TSK-2026-001.md"


def test_cli_rejects_multiple_sources() -> None:
    with pytest.raises(ValueError):
        _resolve_source_paths(
            positional_task_md=Path("docs/tasks/TSK-2026-001.md"),
            task_md=None,
            asl_file=Path("spec.asl"),
        )


def test_cli_derives_output_base_name_from_task_file() -> None:
    task_path = Path("docs/tasks/TSK-2026-777-demo.md")
    assert (
        _derive_output_base_name(source_kind="task_md", source_path=task_path)
        == "TSK-2026-777-demo"
    )
    assert (
        _derive_task_id(source_kind="task_md", source_path=task_path)
        == "TSK-2026-777-demo"
    )
