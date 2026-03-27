from __future__ import annotations

from .models import KimakiSendResult
from .tools import (
    build_send_optional_args,
    find_thread_id_in_sessions,
    list_project_sessions,
    require_text,
    resolve_project_directory,
    run_send,
)


def send_to_channel_subthread(
    channel_id: str,
    subthread_name: str,
    prompt: str,
    *,
    user: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    notify_only: bool = False,
    send_at: str | None = None,
    wait: bool = False,
) -> KimakiSendResult:
    require_text("channel_id", channel_id)
    require_text("subthread_name", subthread_name)
    require_text("prompt", prompt)

    thread_id: str | None = None
    project_directory = resolve_project_directory(channel_id)
    if project_directory is not None:
        sessions = list_project_sessions(project_directory)
        thread_id = find_thread_id_in_sessions(sessions, subthread_name)

    if thread_id:
        args = ["send", "--thread", thread_id, "--prompt", prompt]
        args.extend(
            build_send_optional_args(
                user=user,
                agent=agent,
                model=model,
                notify_only=notify_only,
                send_at=send_at,
                wait=wait,
            )
        )
        return run_send(args)

    args = ["send", "--channel", channel_id, "--prompt", prompt]
    args.extend(
        build_send_optional_args(
            name=subthread_name,
            user=user,
            agent=agent,
            model=model,
            notify_only=notify_only,
            send_at=send_at,
            wait=wait,
        )
    )
    return run_send(args)
