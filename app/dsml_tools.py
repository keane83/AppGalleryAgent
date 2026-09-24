"""DeepSeek 等模型偶发将 tool_calls 写入正文 DSML 标记，在此解析并剥离。"""

from __future__ import annotations

import re
import uuid
from typing import Any

# 半角 | 与全角 ｜ 均可能出现（勿用 f-string 拼接 {2}，会被误解析）
_PIPE2 = r"[|｜]{2}"
DSML_CALLS_BLOCK = re.compile(
    rf"<{_PIPE2}DSML{_PIPE2}\s*calls>(.*?)</{_PIPE2}DSML{_PIPE2}\s*calls>",
    re.DOTALL | re.IGNORECASE,
)
DSML_INVOKE = re.compile(
    rf"<{_PIPE2}DSML{_PIPE2}\s*invoke\s+name=\"([^\"]+)\">(.*?)</{_PIPE2}DSML{_PIPE2}\s*invoke>",
    re.DOTALL | re.IGNORECASE,
)
DSML_PARAMETER = re.compile(
    rf"<{_PIPE2}DSML{_PIPE2}\s*parameter\s+name=\"([^\"]+)\"[^>]*>([^<]*)</{_PIPE2}DSML{_PIPE2}\s*parameter>",
    re.DOTALL | re.IGNORECASE,
)


def strip_dsml_markup(text: str) -> str:
    """从展示文本中移除 DSML 伪工具调用块。"""
    if not text or "DSML" not in text:
        return text
    cleaned = DSML_CALLS_BLOCK.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def parse_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    """从正文中解析 DSML 格式的工具调用，转为 LangChain tool_calls 结构。"""
    if not text or "DSML" not in text:
        return []

    parsed: list[dict[str, Any]] = []
    for block in DSML_CALLS_BLOCK.findall(text):
        for name, body in DSML_INVOKE.findall(block):
            args: dict[str, str] = {}
            for param_name, param_value in DSML_PARAMETER.findall(body):
                args[param_name.strip()] = param_value.strip()
            parsed.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": name.strip(),
                    "args": args,
                }
            )
    return parsed


def recover_tool_calls_from_message(content: str) -> tuple[str, list[dict[str, Any]]]:
    """若正文含 DSML 工具调用，返回剥离后的正文与解析出的 tool_calls。"""
    tool_calls = parse_dsml_tool_calls(content)
    if not tool_calls:
        return content, []
    return strip_dsml_markup(content), tool_calls
