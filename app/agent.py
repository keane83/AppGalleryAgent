from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent_state import AgentState
from app.config import (
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_REASONING,
    OLLAMA_TEMPERATURE,
    SYSTEM_PROMPT,
)
from app.logging_config import begin_request, finish_request, log_event
from app.thinking import collect_agent_output, extract_message_parts
from app.tools import TOOLS, safe_tool_log_content
from app.tools.app_gallery_auth import (
    auth_from_state,
    auth_state_patch,
    begin_session_auth,
    ensure_app_gallery_auth,
    tool_needs_app_gallery_auth,
)

TOOLS_NODE = ToolNode(TOOLS)

_llm_call_count = 0
_llm: ChatOllama | None = None
_llm_signature: tuple[str, bool, float, str] | None = None


def _build_llm() -> ChatOllama:
    """按当前配置复用 ChatOllama；模型/思考开关变化时重建。"""
    global _llm, _llm_signature
    signature = (OLLAMA_MODEL, OLLAMA_REASONING, OLLAMA_TEMPERATURE, OLLAMA_KEEP_ALIVE)
    if _llm is None or _llm_signature != signature:
        _llm = ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=OLLAMA_TEMPERATURE,
            reasoning=OLLAMA_REASONING,
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
        _llm_signature = signature
    return _llm


def _is_model_warm() -> bool:
    """查询 Ollama 当前是否已加载目标模型。"""
    try:
        import httpx

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


def _message_summary(message: BaseMessage) -> str:
    role = message.__class__.__name__
    if isinstance(message, AIMessage):
        if message.tool_calls:
            names = ", ".join(call["name"] for call in message.tool_calls)
            return f"{role}(tool_calls=[{names}])"
        return f"{role}(content={message.content!r})"
    if isinstance(message, ToolMessage):
        return f"{role}(name={message.name}, content={message.content!r})"
    return f"{role}(content={getattr(message, 'content', '')!r})"


def _request_id(config) -> str:
    if config is None:
        return "-"
    configurable = config.get("configurable", {}) if hasattr(config, "get") else getattr(config, "configurable", {})
    return configurable.get("request_id", "-")


def _pending_tool_names(state: AgentState) -> list[str]:
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return [str(call["name"]) for call in last_message.tool_calls]
    return []


def _prepare_app_gallery_auth(state: AgentState) -> dict[str, Any]:
    pending = _pending_tool_names(state)
    if not any(tool_needs_app_gallery_auth(name) for name in pending):
        return {}
    auth = ensure_app_gallery_auth(state)
    return auth_state_patch(auth)


def _call_model(state: AgentState, config) -> dict[str, list[BaseMessage]]:
    global _llm_call_count
    _llm_call_count += 1
    call_no = _llm_call_count
    request_id = _request_id(config)

    messages = state["messages"]
    log_event(
        request_id,
        "进入 agent 节点",
        llm_call=call_no,
        message_count=len(messages),
        last_message=_message_summary(messages[-1]),
    )
    log_event(
        request_id,
        "调用 LLM",
        llm_call=call_no,
        model=OLLAMA_MODEL,
        tools=[tool_item.name for tool_item in TOOLS],
    )

    llm = _build_llm().bind_tools(TOOLS)
    response = llm.invoke(messages)

    if response.tool_calls:
        for tool_call in response.tool_calls:
            log_event(
                request_id,
                "LLM 决定调用工具",
                llm_call=call_no,
                tool=tool_call["name"],
                args=tool_call.get("args"),
            )
    else:
        thinking, content = extract_message_parts(response)
        log_event(
            request_id,
            "LLM 返回最终文本",
            llm_call=call_no,
            thinking_len=len(thinking),
            content=content,
        )

    return {"messages": [response]}


def _call_tools(state: AgentState, config) -> dict[str, Any]:
    request_id = _request_id(config)
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            log_event(
                request_id,
                "执行工具",
                tool=tool_call["name"],
                args=tool_call.get("args"),
            )

    had_auth = bool(auth_from_state(state))
    auth_patch = _prepare_app_gallery_auth(state)
    if auth_patch:
        log_event(
            request_id,
            "AppGallery 鉴权已就绪",
            client_id=auth_patch.get("app_gallery_client_id"),
            reused_session_auth=had_auth,
        )

    result = TOOLS_NODE.invoke(state)

    for message in result.get("messages", []):
        if isinstance(message, ToolMessage):
            log_event(
                request_id,
                "工具返回结果",
                tool=message.name,
                content=safe_tool_log_content(message.name, message.content),
            )

    if auth_patch:
        result.update(auth_patch)
    return result


def _route_after_agent(state: AgentState, config):
    request_id = _request_id(config)
    route = tools_condition(state)
    destination = "tools" if route == "tools" else "END"
    log_event(request_id, "条件路由", destination=destination)
    return route


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("agent", _call_model)
    graph.add_node("tools", _call_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


AGENT = build_agent()


def to_langchain_messages(raw_messages: list[dict[str, str]]) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    for item in raw_messages:
        role = item.get("role", "")
        content = item.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def _latest_user_message(raw_messages: list[dict[str, str]]) -> str:
    for item in reversed(raw_messages):
        if item.get("role") == "user":
            return item.get("content", "")
    return ""


def _invoke_agent(raw_messages: list[dict[str, str]], mode: str) -> tuple[dict[str, list[BaseMessage]], str]:
    global _llm_call_count
    _llm_call_count = 0

    user_message = _latest_user_message(raw_messages)
    request_id = begin_request(mode=mode, user_message=user_message)
    begin_session_auth()
    log_event(
        request_id,
        "LangGraph 开始执行",
        node_order="START -> agent -> (tools -> agent)* -> END",
    )

    result = AGENT.invoke(
        {"messages": to_langchain_messages(raw_messages)},
        config={"configurable": {"request_id": request_id}},
    )

    final_message = result["messages"][-1]
    log_event(
        request_id,
        "LangGraph 执行完成",
        llm_calls=_llm_call_count,
        final=_message_summary(final_message),
    )
    return result, request_id


def stream_agent(raw_messages: list[dict[str, str]]):
    """真实流式：LLM 生成时就把 thinking/content 逐块推给前端。"""
    global _llm_call_count
    _llm_call_count = 0

    user_message = _latest_user_message(raw_messages)
    request_id = begin_request(mode="stream", user_message=user_message)
    begin_session_auth()
    messages = to_langchain_messages(raw_messages)
    session_auth_state: dict[str, Any] = {}
    log_event(
        request_id,
        "LangGraph 流式执行",
        node_order="agent(stream) -> (tools -> agent(stream))* -> END",
    )

    try:
        max_rounds = 8
        for round_no in range(1, max_rounds + 1):
            _llm_call_count += 1
            call_no = _llm_call_count
            log_event(
                request_id,
                "进入 agent 节点",
                llm_call=call_no,
                round=round_no,
                message_count=len(messages),
                last_message=_message_summary(messages[-1]),
            )
            log_event(
                request_id,
                "调用 LLM stream",
                llm_call=call_no,
                model=OLLAMA_MODEL,
                tools=[tool_item.name for tool_item in TOOLS],
            )

            if round_no == 1:
                if _is_model_warm():
                    yield {"type": "status", "text": "正在处理…"}
                else:
                    yield {
                        "type": "status",
                        "text": f"正在加载本地模型 {OLLAMA_MODEL}（冷启动可能稍慢）...",
                    }

            llm = _build_llm().bind_tools(TOOLS)
            accumulated: AIMessage | None = None

            for chunk in llm.stream(messages):
                accumulated = chunk if accumulated is None else accumulated + chunk

                thinking_token = chunk.additional_kwargs.get("reasoning_content")
                if thinking_token:
                    yield {"type": "thinking", "token": thinking_token}

                if chunk.content:
                    yield {"type": "content", "token": chunk.content}

            if accumulated is None:
                raise RuntimeError("LLM 未返回任何内容")

            if accumulated.tool_calls:
                for tool_call in accumulated.tool_calls:
                    log_event(
                        request_id,
                        "LLM 决定调用工具",
                        llm_call=call_no,
                        tool=tool_call["name"],
                        args=tool_call.get("args"),
                    )

                tool_names = ", ".join(call["name"] for call in accumulated.tool_calls)
                yield {"type": "status", "text": f"正在调用工具：{tool_names}"}

                messages.append(accumulated)
                tools_by_name = {tool_item.name: tool_item for tool_item in TOOLS}
                for tool_call in accumulated.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call.get("args") or {}
                    log_event(
                        request_id,
                        "执行工具",
                        tool=tool_name,
                        args=tool_args,
                    )
                    if tool_needs_app_gallery_auth(tool_name):
                        auth = ensure_app_gallery_auth(session_auth_state)
                        session_auth_state.update(auth_state_patch(auth))
                        log_event(
                            request_id,
                            "AppGallery 鉴权已就绪",
                            client_id=session_auth_state.get("app_gallery_client_id"),
                        )
                    tool_result = tools_by_name[tool_name].invoke(tool_args)
                    log_event(
                        request_id,
                        "工具返回结果",
                        tool=tool_name,
                        content=safe_tool_log_content(tool_name, tool_result),
                    )
                    messages.append(
                        ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
                continue

            thinking, content = extract_message_parts(accumulated)
            log_event(
                request_id,
                "LLM 返回最终文本",
                llm_call=call_no,
                thinking_len=len(thinking),
                content=content,
            )
            log_event(request_id, "LangGraph 流式执行完成", llm_calls=_llm_call_count)
            return

        raise RuntimeError(f"Agent 超过最大轮次 {max_rounds}")
    finally:
        finish_request(request_id)


def run_agent(raw_messages: list[dict[str, str]]) -> dict[str, str]:
    result, request_id = _invoke_agent(raw_messages, mode="chat")
    try:
        thinking, content = collect_agent_output(result["messages"])
        return {"thinking": thinking, "reply": content}
    finally:
        finish_request(request_id)


def get_agent_info() -> dict[str, Any]:
    return {
        "model": OLLAMA_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "tools": [tool_item.name for tool_item in TOOLS],
    }
