from __future__ import annotations

from typing import Any

try:
    from runtime_api import get_runtime_api
except Exception:  # pragma: no cover - import path compatibility
    from victor_os.runtime_api import get_runtime_api  # type: ignore


def get_config():
    return get_runtime_api().config()


def session_context(*, channel: str, external_user_id: str):
    return get_runtime_api().session_context(channel=channel, external_user_id=external_user_id)


def emit_event(
    *,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    channel: str = "system",
    session_id: str = "",
    task_id: str = "",
    risk_score: float = 0.0,
):
    return get_runtime_api().emit_event(
        event_type=event_type,
        actor=actor,
        payload=payload or {},
        channel=channel,
        session_id=session_id,
        task_id=task_id,
        risk_score=risk_score,
    )


def write_memory(
    *,
    text: str,
    memory_type: str = "project_fact",
    scope: str = "global",
    agent_name: str | None = None,
    source: str = "runtime_api",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
):
    return get_runtime_api().write_memory(
        text=text,
        memory_type=memory_type,
        scope=scope,
        agent_name=agent_name,
        source=source,
        tags=tags,
        metadata=metadata,
    )


def read_memory(*, query: str, top_k: int = 10, scope_filter: str | None = None, agent_filter: str | None = None):
    return get_runtime_api().read_memory(
        query=query,
        top_k=top_k,
        scope_filter=scope_filter,
        agent_filter=agent_filter,
    )


def invoke_tool(*, tool_name: str, inputs: dict[str, Any] | None = None, actor: str = "skill"):
    return get_runtime_api().invoke_tool(tool_name=tool_name, inputs=inputs, actor=actor)


def system_metrics():
    return get_runtime_api().system_metrics()
