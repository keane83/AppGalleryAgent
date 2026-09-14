import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

THINKING_TAG_PATTERN = re.compile(
    r"(?:<(?:think|redacted_thinking)>)\s*([\s\S]*?)\s*(?:</(?:think|redacted_thinking)>)",
    re.IGNORECASE,
)


def extract_message_parts(message: BaseMessage) -> tuple[str, str]:
    """从 AIMessage 中分离思考内容与正式回答。"""
    if not isinstance(message, AIMessage):
        return "", ""

    thinking = str(message.additional_kwargs.get("reasoning_content") or "").strip()
    content = _normalize_content(message.content)

    if not thinking:
        thinking, content = _split_thinking_tags(content)

    return thinking, content


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()
    return str(content).strip()


def _split_thinking_tags(content: str) -> tuple[str, str]:
    matches = THINKING_TAG_PATTERN.findall(content)
    if not matches:
        return "", content

    thinking = "\n\n".join(part.strip() for part in matches if part.strip())
    answer = THINKING_TAG_PATTERN.sub("", content).strip()
    return thinking, answer


def collect_agent_output(messages: list[BaseMessage]) -> tuple[str, str]:
    """汇总一次 Agent 执行中的全部思考与最终回答。"""
    thinking_parts: list[str] = []
    final_content = ""

    for message in messages:
        if not isinstance(message, AIMessage):
            continue

        thinking, content = extract_message_parts(message)
        if thinking:
            thinking_parts.append(thinking)
        if content and not message.tool_calls:
            final_content = content

    combined_thinking = "\n\n---\n\n".join(thinking_parts)
    return combined_thinking, final_content or "（模型没有返回文本内容）"
