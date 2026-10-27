"""统一 LLM 工厂：按 .env 在本地 Ollama 与 DeepSeek（OpenAI 兼容）API 间切换。"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

from app.openai_compat_chat import OpenAICompatChat

from app.config import (
    LLM_API_BASE_URL,
    LLM_API_KEY,
    LLM_API_MODEL,
    LLM_PROVIDER,
    LLM_TOOL_API_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_REASONING,
    OLLAMA_TEMPERATURE,
    OLLAMA_TOOL_MODEL,
)

_llm_cache: BaseChatModel | None = None
_llm_signature: tuple[Any, ...] | None = None


def is_api_provider() -> bool:
    return LLM_PROVIDER == "deepseek"


def get_active_model_name(*, for_tool: bool = False) -> str:
    if is_api_provider():
        return LLM_TOOL_API_MODEL if for_tool else LLM_API_MODEL
    return OLLAMA_TOOL_MODEL if for_tool else OLLAMA_MODEL


def get_active_base_url() -> str:
    return LLM_API_BASE_URL if is_api_provider() else OLLAMA_BASE_URL


def validate_llm_config() -> str | None:
    if is_api_provider() and not LLM_API_KEY:
        return "LLM_PROVIDER=deepseek 但未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY"
    return None


def get_llm_mode_label() -> str:
    if is_api_provider():
        return "DeepSeek API"
    return "本地 Ollama"


def get_llm_public_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "provider": LLM_PROVIDER,
        "mode_label": get_llm_mode_label(),
        "model": get_active_model_name(),
        "tool_model": get_active_model_name(for_tool=True),
        "base_url": get_active_base_url(),
    }
    config_error = validate_llm_config()
    if config_error:
        info["config_error"] = config_error
    return info


def is_model_warm() -> bool:
    """API 模式无需冷启动；Ollama 模式查询是否已加载目标模型。"""
    if is_api_provider():
        return True
    try:
        response = httpx.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=2.0)
        if response.status_code != 200:
            return True
        models = response.json().get("models") or []
        for item in models:
            name = str(item.get("model") or item.get("name") or "")
            if name == OLLAMA_MODEL or name.startswith(f"{OLLAMA_MODEL}"):
                return True
        return False
    except Exception:
        return True


def initial_status_text() -> str:
    model = get_active_model_name()
    if is_api_provider():
        return f"正在连接 DeepSeek API（{model}）…"
    if is_model_warm():
        return "正在处理…"
    return f"正在加载本地模型 {model}（冷启动可能稍慢）..."


def build_chat_llm(*, for_tools: bool = False) -> BaseChatModel:
    """构建 LangChain Chat 模型（Agent 与工具内 JSON 调用共用入口）。"""
    global _llm_cache, _llm_signature

    if is_api_provider():
        config_error = validate_llm_config()
        if config_error:
            raise ValueError(config_error)
        signature = (
            "deepseek",
            LLM_API_MODEL,
            LLM_API_BASE_URL,
            OLLAMA_TEMPERATURE,
        )
        if _llm_cache is None or _llm_signature != signature:
            _llm_cache = OpenAICompatChat(
                model=LLM_API_MODEL,
                api_key=LLM_API_KEY,
                base_url=LLM_API_BASE_URL,
                temperature=OLLAMA_TEMPERATURE,
            )
            _llm_signature = signature
        return _llm_cache

    reasoning = False if for_tools else OLLAMA_REASONING
    signature = ("ollama", OLLAMA_MODEL, reasoning, OLLAMA_TEMPERATURE, OLLAMA_KEEP_ALIVE)
    if _llm_cache is None or _llm_signature != signature:
        _llm_cache = ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=OLLAMA_TEMPERATURE,
            reasoning=reasoning,
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
        _llm_signature = signature
    return _llm_cache


