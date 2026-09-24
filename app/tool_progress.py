"""工具执行进度回调（供 stream_agent 实时推送到前端）。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

ProgressCallback = Callable[[dict[str, Any]], None]

_progress_cb: ContextVar[ProgressCallback | None] = ContextVar("tool_progress_cb", default=None)
_current_tool: ContextVar[str | None] = ContextVar("current_tool_name", default=None)


def report_tool_progress(
    message: str,
    *,
    step: str | None = None,
    detail: str | None = None,
    status: str = "running",
) -> None:
    """上报单步进度；无回调时静默忽略。"""
    cb = _progress_cb.get()
    if cb is None:
        return
    cb(
        {
            "type": "tool_progress",
            "tool": _current_tool.get() or "",
            "step": step or "",
            "text": message,
            "detail": detail or "",
            "status": status,
        }
    )


@contextmanager
def tool_progress_context(
    tool_name: str,
    callback: ProgressCallback | None,
) -> Iterator[None]:
    token_tool = _current_tool.set(tool_name)
    token_cb = _progress_cb.set(callback)
    try:
        yield
    finally:
        _current_tool.reset(token_tool)
        _progress_cb.reset(token_cb)
