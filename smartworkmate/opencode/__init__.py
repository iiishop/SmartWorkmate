"""OpenCode integration helpers."""

from .models import OpenCodeProject, OpenCodeSession
from .opencode import (
    block_task,
    find_task_path_by_task_id,
    get_task_status_by_task_id,
    read_task_acceptance,
    read_task_design,
    read_task_requirements,
    scan_task_markdown_documents,
    update_task_status,
    list_projects,
    list_project_sessions,
)

__all__ = [
    "OpenCodeProject",
    "OpenCodeSession",
    # task-id driven task workflow
    "block_task",
    "find_task_path_by_task_id",
    "get_task_status_by_task_id",
    "read_task_requirements",
    "read_task_design",
    "read_task_acceptance",
    "update_task_status",
    # discovery/index helpers
    "scan_task_markdown_documents",
    # opencode project/session discovery
    "list_projects",
    "list_project_sessions",
]
