"""OpenAI 兼容 Chat API（DeepSeek 等），仅依赖 httpx + langchain-core。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field


def _tool_specs(tools: list[BaseTool]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tool in tools:
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": tool.args_schema.model_json_schema()
                    if tool.args_schema is not None
                    else {"type": "object", "properties": {}},
                },
            }
        )
    return specs


def _to_api_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            converted.append({"role": "system", "content": str(message.content or "")})
        elif isinstance(message, HumanMessage):
            converted.append({"role": "user", "content": str(message.content or "")})
        elif isinstance(message, AIMessage):
            item: dict[str, Any] = {
                "role": "assistant",
                "content": message.content if message.content else None,
            }
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            converted.append(item)
        elif isinstance(message, ToolMessage):
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": str(message.content or ""),
                }
            )
    return converted


def _parse_tool_calls(raw_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not raw_calls:
        return []
    parsed: list[dict[str, Any]] = []
    for call in raw_calls:
        function = call.get("function") or {}
        args_raw = function.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"raw": args_raw}
        if not isinstance(args, dict):
            args = {"value": args}
        parsed.append(
            {
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": function.get("name") or "",
                "args": args,
            }
        )
    return parsed


class OpenAICompatChat(BaseChatModel):
    """DeepSeek / OpenAI 兼容 HTTP Chat Completions。"""

    model: str
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.2
    bound_tools: list[BaseTool] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "openai-compat-http"

    def bind_tools(self, tools: list[BaseTool], **kwargs: Any) -> OpenAICompatChat:
        return self.model_copy(update={"bound_tools": list(tools)})

    def _build_payload(self, messages: list[BaseMessage], *, stream: bool) -> dict[str, Any]:
        base = self.base_url.rstrip("/")
        if "deepseek.com" in base and not base.endswith("/v1"):
            base = f"{base}/v1"
        # 供 _post_chat / _stream 使用
        self._resolved_base_url = base

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _to_api_messages(messages),
            "temperature": self.temperature,
            "stream": stream,
            # flash/pro 默认 thinking=on；工具调用轮次关闭，避免 token 被推理占满
            "thinking": {"type": "disabled" if self.bound_tools else "enabled"},
        }
        if self.bound_tools:
            payload["tools"] = _tool_specs(self.bound_tools)
            payload["tool_choice"] = "auto"
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _post_chat(self, payload: dict[str, Any], *, timeout: float = 180.0) -> dict[str, Any]:
        base = getattr(self, "_resolved_base_url", None) or self.base_url.rstrip("/")
        if "deepseek.com" in base and not base.endswith("/v1"):
            base = f"{base}/v1"
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def _message_from_response(self, data: dict[str, Any]) -> AIMessage:
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"API 返回空 choices：{json.dumps(data, ensure_ascii=False)[:240]}")
        message = choices[0].get("message") or {}
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        content = message.get("content")
        return AIMessage(content=content or "", tool_calls=tool_calls)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        data = self._post_chat(self._build_payload(messages, stream=False))
        ai_message = self._message_from_response(data)
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        payload = self._build_payload(messages, stream=True)
        base = getattr(self, "_resolved_base_url", None) or self.base_url.rstrip("/")
        if "deepseek.com" in base and not base.endswith("/v1"):
            base = f"{base}/v1"
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        tool_calls_acc: dict[int, dict[str, Any]] = {}
        content_parts: list[str] = []

        with httpx.Client(timeout=180.0) as client:
            with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        break
                    chunk = json.loads(data_text)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}

                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        yield ChatGenerationChunk(message=AIMessageChunk(content=delta["content"]))

                    for tool_delta in delta.get("tool_calls") or []:
                        index = int(tool_delta.get("index") or 0)
                        acc = tool_calls_acc.setdefault(
                            index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        if tool_delta.get("id"):
                            acc["id"] = tool_delta["id"]
                        function = tool_delta.get("function") or {}
                        if function.get("name"):
                            acc["name"] = function["name"]
                        if function.get("arguments"):
                            acc["arguments"] += function["arguments"]

        if tool_calls_acc:
            raw_calls = [
                {
                    "id": item["id"] or f"call_{uuid.uuid4().hex[:12]}",
                    "function": {"name": item["name"], "arguments": item["arguments"] or "{}"},
                }
                for _, item in sorted(tool_calls_acc.items())
            ]
            tool_calls = _parse_tool_calls(raw_calls)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=tool_calls)
            )
