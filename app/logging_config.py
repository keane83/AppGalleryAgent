import logging
import os
import sys
import uuid
from typing import Any

_steps: dict[str, int] = {}


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger("haa")
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(handler)


def begin_request(mode: str, user_message: str) -> str:
    request_id = uuid.uuid4().hex[:8]
    _steps[request_id] = 0
    log_event(request_id, "请求开始", mode=mode, user=_truncate(user_message))
    return request_id


def finish_request(request_id: str) -> None:
    _steps.pop(request_id, None)


def log_event(request_id: str, event: str, **fields: Any) -> None:
    step = _steps.get(request_id, 0) + 1
    _steps[request_id] = step

    detail = " | ".join(f"{key}={_truncate(value)}" for key, value in fields.items())
    message = f"[req={request_id} step={step:02d}] {event}"
    if detail:
        message = f"{message} | {detail}"

    logging.getLogger("haa.agent").info(message)


def _truncate(value: object, limit: int = 160) -> str:
    text = str(value).replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
