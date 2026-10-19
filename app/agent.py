import json
import queue
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent_state import AgentState
from app.config import SYSTEM_PROMPT
from app.llm_factory import (
    build_chat_llm,
    get_active_model_name,
    get_llm_public_info,
    initial_status_text,
)
from app.logging_config import begin_request, finish_request, log_event
from app.tool_progress import tool_progress_context
from app.tool_labels import (
    TOOL_STEP_LABELS,
    sanitize_thinking_for_user,
    user_tool_label,
    user_tool_status_text,
)
from app.dsml_tools import recover_tool_calls_from_message, strip_dsml_markup
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


_FAKE_TOOL_RESULT_MARKERS: tuple[str, ...] = (
    "objectid:",
    "packageid:",
    "编译并上传完成",
    "编译上传完成",
    "软件包：",
    "软件包:",
    "-signed.app",
    "releasephase",
)

_PREMATURE_COMPLETION_MARKERS: tuple[str, ...] = (
    "已完成",
    "已经完成",
    "执行完成",
    "配置完成",
    "已成功",
    "成功完成",
    "工具执行完成",
    "工具已完成",
    "完成了",
    "完成。",
    "done",
    "successfully",
)

FULL_FLOW_TOOL_SEQUENCE: tuple[str, ...] = (
    "create_app_gallery_app_id",
    "create_app_gallery_provision",
    # AGC 官网改版后不再需要控制台「新建发布」。恢复时取消下一行注释，并重新注册工具。
    # "create_app_gallery_publish_app",
    "configure_harmony_app_signing",
    "capture_harmony_screenshots_and_app_icon",
    "update_app_gallery_app_file_info",
    "update_app_gallery_app_basic_info",
    "configure_app_gallery_privacy_agreement",
    "configure_app_gallery_age_rating",
    "update_app_gallery_build_update_app",
)


def _latest_user_message(raw_messages: list[dict[str, str]]) -> str:
    for item in reversed(raw_messages):
        if str(item.get("role") or "") == "user":
            return str(item.get("content") or "")
    return ""


def _tool_mentioned_in_message(user_message: str, tool_name: str) -> bool:
    """判断用户消息是否提及某上架步骤工具。"""
    text = (user_message or "").strip()
    lower = text.lower()
    if tool_name == "create_app_gallery_app_id":
        return any(
            key in lower
            for key in (
                "创建 appid",
                "创建appid",
                "创建 app id",
                "app id",
                "appid",
                "第1步",
                "第 1 步",
                "create_app_gallery_app_id",
            )
        )
    if tool_name == "create_app_gallery_provision":
        return any(
            key in lower
            for key in (
                "申请 profile",
                "申请profile",
                "生成profile",
                "生成 profile",
                "下载 profile",
                "下载profile",
                "profile",
                "第2步",
                "第 2 步",
                "create_app_gallery_provision",
            )
        )
    # AGC 官网改版后第 3 步已停用。恢复全流程时取消注释，并重新注册工具。
    # if tool_name == "create_app_gallery_publish_app":
    #     return any(
    #         key in text
    #         for key in (
    #             "新建发布",
    #             "创建发布",
    #             "发布应用",
    #             "控制台发布",
    #             "第3步",
    #             "第 3 步",
    #             "create_app_gallery_publish",
    #         )
    #     )
    if tool_name == "configure_harmony_app_signing":
        return any(
            key in text
            for key in ("配置签名", "第4步", "第 4 步", "configure_harmony")
        )
    if tool_name == "replace_harmony_app_icon":
        if _extract_icon_path(text):
            return True
        return any(key in lower for key in ("换icon", "换 icon", "replace_harmony_app_icon")) and any(
            ext in lower for ext in (".png", ".jpg", ".jpeg", ".webp")
        )
    if tool_name == "capture_harmony_screenshots_and_app_icon":
        return any(key in text for key in ("截图", "第5步", "第 5 步", "capture_harmony"))
    if tool_name == "update_app_gallery_app_file_info":
        return any(key in text for key in ("上传图标", "上传截图", "第6步", "第 6 步", "app_file_info"))
    if tool_name == "update_app_gallery_app_basic_info":
        return any(key in text for key in ("更新基本信息", "应用介绍", "第7步", "第 7 步", "app_basic_info"))
    if tool_name == "configure_app_gallery_privacy_agreement":
        return any(
            key in text
            for key in ("隐私协议", "隐私政策", "第8步", "第 8 步", "configure_app_gallery_privacy")
        )
    if tool_name == "configure_app_gallery_age_rating":
        return any(
            key in text
            for key in ("年龄分级", "内容分级", "第9步", "第 9 步", "configure_app_gallery_age")
        )
    if tool_name == "update_app_gallery_build_update_app":
        return any(
            key in text
            for key in (
                "编译并上传",
                "重新编译",
                "编译上传",
                "第10步",
                "第 10 步",
                "update_app_gallery_build_update_app",
                "build_update_app",
            )
        ) or ("编译" in text and "上传" in text)
    return False


def _infer_chain_tools(user_message: str) -> list[str]:
    """按推荐顺序推断用户消息中涉及的多个步骤（不含全流程关键字）。"""
    if _is_full_flow_request(user_message):
        return []
    chain: list[str] = []
    for tool_name in FULL_FLOW_TOOL_SEQUENCE:
        if _tool_mentioned_in_message(user_message, tool_name):
            chain.append(tool_name)
    if _tool_mentioned_in_message(user_message, "replace_harmony_app_icon"):
        if "replace_harmony_app_icon" not in chain:
            insert_at = 0
            if "configure_harmony_app_signing" in chain:
                insert_at = chain.index("configure_harmony_app_signing") + 1
            chain.insert(insert_at, "replace_harmony_app_icon")
    return chain


def _active_tool_sequence(user_message: str) -> list[str]:
    """当前请求应遵循的工具顺序（全流程或多步链）。"""
    if _is_full_flow_request(user_message):
        return _full_flow_sequence(user_message)
    chain = _infer_chain_tools(user_message)
    if len(chain) > 1:
        return chain
    return []


def _next_sequence_tool(tools_executed: set[str], sequence: list[str]) -> str | None:
    for name in sequence:
        if name not in tools_executed:
            return name
    return None


def _infer_expected_tools(user_message: str) -> list[str]:
    """从用户消息推断应调用的工具。"""
    sequence = _active_tool_sequence(user_message)
    if sequence:
        return sequence
    chain = _infer_chain_tools(user_message)
    if len(chain) == 1:
        return chain
    return []


def _looks_like_fake_tool_result(content: str) -> bool:
    blob = (content or "").lower()
    return any(marker in blob for marker in _FAKE_TOOL_RESULT_MARKERS)


def _looks_like_premature_completion(content: str) -> bool:
    blob = (content or "").lower()
    if _looks_like_fake_tool_result(content):
        return True
    return any(marker in blob for marker in _PREMATURE_COMPLETION_MARKERS)


def _should_force_tool_retry(
    user_message: str,
    message: AIMessage,
    *,
    tools_executed: set[str],
    messages: list[BaseMessage] | None = None,
) -> tuple[bool, list[str]]:
    """模型未发起 tool_calls 但用户明确要执行工具时，应重试。"""
    if message.tool_calls:
        return False, []

    # 上一步工具已失败：不要强推后续步骤
    if messages:
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage) and "禁止调用任何其他工具" in str(msg.content or ""):
                return False, []
            if isinstance(msg, ToolMessage):
                if _tool_result_failed(msg.content):
                    return False, []
                break

    expected = _infer_expected_tools(user_message)
    if not expected:
        return False, []

    missing = [name for name in expected if name not in tools_executed]
    if not missing:
        return False, []

    # 链式/全流程：只强制「下一个」缺失工具，且要求前序都已成功执行过
    sequence = _active_tool_sequence(user_message)
    if sequence:
        next_tool = _next_sequence_tool(tools_executed, sequence)
        if not next_tool:
            return False, []
        missing = [next_tool]

    _, content = extract_message_parts(message)
    wants_real_tool = any(
        key in user_message
        for key in ("真执行", "真正执行", "实际执行", "调用工具", "执行工具", "要执行")
    )
    if wants_real_tool or _looks_like_fake_tool_result(content) or not content.strip():
        return True, missing
    return True, missing


def _tool_retry_message(missing_tools: list[str], *, next_tool: str | None = None) -> str:
    target = next_tool or (missing_tools[0] if missing_tools else "")
    return (
        f"【系统】你刚才没有通过 API tool_calls 调用工具。"
        f"禁止在正文输出 DSML/XML 等伪工具调用标记。"
        f"请立即只调用下一个工具：{target}。"
        f"禁止编造 objectId、packageId、编译产物或「已完成」类结论；"
        f"必须等 ToolMessage 返回后再总结。"
    )


def _is_system_hint_message(content: str) -> bool:
    return str(content or "").strip().startswith("【系统")


def _user_message_from_state(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = str(msg.content or "")
            if _is_system_hint_message(content):
                continue
            return content
    return ""


def _tools_executed_in_messages(messages: list[BaseMessage]) -> set[str]:
    executed: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name:
            executed.add(msg.name)
    return executed


def _is_full_flow_request(user_message: str) -> bool:
    return any(
        key in user_message
        for key in (
            "完整上架",
            "全流程",
            "整个全流程",
            "一键上架",
            "从头到尾",
            "按推荐顺序",
            "按顺序执行",
        )
    )


def _extract_labeled_value(user_message: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*(\S+)", user_message)
        if match:
            return match.group(1).strip()
    return None


def _extract_icon_path(user_message: str) -> str | None:
    labeled = _extract_labeled_value(
        user_message,
        "icon路径",
        "iconPath",
        "icon path",
        "Icon路径",
        "图标路径",
        "新图标",
    )
    if labeled and re.search(r"\.(png|jpe?g|webp)$", labeled, re.I):
        return labeled

    inline = re.search(
        r"(?:icon|图标)[^\n]{0,40}?(/[^\s\"']+\.(?:png|jpe?g|webp))",
        user_message,
        re.I,
    )
    if inline:
        return inline.group(1).strip()
    return None


def _extract_project_path(user_message: str) -> str | None:
    return _extract_labeled_value(
        user_message,
        "工程路径",
        "projectPath",
        "项目路径",
        "工程目录",
    )


def _is_single_step_request(user_message: str) -> bool:
    """最新用户消息是否为单步/补做（非全流程）。"""
    if _is_full_flow_request(user_message):
        return False
    if _infer_expected_tools(user_message):
        return True
    hints = (
        "重新执行",
        "重做",
        "补做",
        "再做",
        "只做",
        "单步",
        "这一步",
        "这步",
        "执行下",
        "再执行",
        "第1步",
        "第2步",
        "第3步",
        "第4步",
        "第5步",
        "第6步",
        "第7步",
        "第8步",
        "第9步",
        "第10步",
        "第 1 步",
        "第 2 步",
        "第 3 步",
        "第 4 步",
        "第 5 步",
        "第 6 步",
        "第 7 步",
        "第 8 步",
        "第 9 步",
        "第 10 步",
    )
    return any(key in user_message for key in hints)


def _user_wants_icon_replace(user_message: str) -> bool:
    return _extract_icon_path(user_message) is not None


def _full_flow_sequence(user_message: str) -> list[str]:
    sequence = list(FULL_FLOW_TOOL_SEQUENCE)
    if _user_wants_icon_replace(user_message):
        insert_at = sequence.index("configure_harmony_app_signing") + 1
        sequence.insert(insert_at, "replace_harmony_app_icon")
    return sequence


def _next_full_flow_tool(tools_executed: set[str], user_message: str) -> str | None:
    for name in _full_flow_sequence(user_message):
        if name not in tools_executed:
            return name
    return None


def _round_wants_tool_calls(
    messages: list[BaseMessage],
    user_message: str,
    *,
    tools_executed: set[str],
) -> bool:
    """当前轮是否应绑定工具并允许继续 tool_calls。"""
    last = messages[-1]
    if isinstance(last, HumanMessage):
        content = str(last.content or "")
        if _is_system_hint_message(content):
            if "禁止调用任何其他工具" in content:
                return False
            if content.startswith(("【系统-全流程】", "【系统-多步任务】")):
                return True
            return False
        return True
    if isinstance(last, ToolMessage):
        sequence = _active_tool_sequence(user_message)
        if sequence:
            return _next_sequence_tool(tools_executed, sequence) is not None
        if _is_single_step_request(user_message):
            expected = _infer_expected_tools(user_message)
            return any(name not in tools_executed for name in expected)
        return False
    return True


def _full_flow_tool_args_hint(tool_name: str, user_message: str) -> str:
    if tool_name == "replace_harmony_app_icon":
        icon_path = _extract_icon_path(user_message)
        project_path = _extract_project_path(user_message)
        parts: list[str] = []
        if project_path:
            parts.append(f'projectPath="{project_path}"')
        if icon_path:
            parts.append(f'iconPath="{icon_path}"')
        if parts:
            return f"({', '.join(parts)})"
        return "(projectPath 与 iconPath 从用户消息提取)"
    return ""


def _tool_result_failed(tool_result: Any) -> bool:
    """工具返回是否表示失败（ok=false 或明显 error）。"""
    if tool_result is None:
        return True
    if isinstance(tool_result, dict):
        if tool_result.get("ok") is False:
            return True
        if tool_result.get("error") and not tool_result.get("ok"):
            return True
        return False
    text = str(tool_result).strip()
    if not text:
        return True
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if isinstance(data, dict):
        if data.get("ok") is False:
            return True
        if data.get("error") and not data.get("ok"):
            return True
    return False


def _post_tool_system_hint(
    user_message: str,
    *,
    tools_executed: set[str],
    last_tool_name: str | None = None,
    last_tool_result: Any = None,
) -> str | None:
    """工具执行后注入系统提示，约束下一步行为。"""
    if last_tool_name and _tool_result_failed(last_tool_result):
        return (
            f"【系统】上一步「{user_tool_label(last_tool_name)}」失败，请根据 ToolMessage 说明原因与修复建议，"
            "禁止调用任何其他工具，等待用户处理后再继续。"
        )

    sequence = _active_tool_sequence(user_message)
    if sequence:
        next_tool = _next_sequence_tool(tools_executed, sequence)
        if next_tool:
            args_hint = _full_flow_tool_args_hint(next_tool, user_message)
            extra = ""
            if next_tool == "replace_harmony_app_icon":
                extra = "必须在 capture_harmony_screenshots_and_app_icon 之前执行。"
            label = "【系统-全流程】" if _is_full_flow_request(user_message) else "【系统-多步任务】"
            full_flow_extra = (
                "不得跳过中间步骤；第8步 privacy 与第9步 age_rating 不可省略。"
                if _is_full_flow_request(user_message)
                else "按用户要求的顺序逐步执行，不要跳步。"
            )
            return (
                f"{label}上一步工具已返回。请立即通过 API tool_calls 调用：{next_tool}{args_hint}。"
                f"禁止在正文写 DSML/XML 伪工具调用。{extra}{full_flow_extra}"
                "其余参数从 ToolMessage 与历史消息提取。"
            )
        return (
            "【系统】用户要求的步骤已全部完成。"
            "请根据各步 ToolMessage 做结构化总结，禁止调用任何其他工具。"
        )
    if _is_single_step_request(user_message):
        expected = _infer_expected_tools(user_message)
        if expected and all(name in tools_executed for name in expected):
            return (
                "【系统】用户仅要求完成指定单步，该步已完成。"
                "请根据 ToolMessage 做结构化总结，禁止调用任何其他工具。"
            )
    return None


def _build_agent_llm(*, wants_tool_calls: bool) -> BaseChatModel:
    llm = build_chat_llm(for_tools=wants_tool_calls)
    if wants_tool_calls:
        return llm.bind_tools(TOOLS)
    return llm


def _recover_message_tool_calls(message: AIMessage) -> AIMessage:
    """若模型把工具调用写进 DSML 正文，解析为真实 tool_calls。"""
    if message.tool_calls:
        return message
    thinking, content = extract_message_parts(message)
    blob = "\n".join(part for part in (thinking, content) if part)
    _, tool_calls = recover_tool_calls_from_message(blob)
    if not tool_calls:
        return message
    return AIMessage(
        content=strip_dsml_markup(content),
        tool_calls=tool_calls,
        additional_kwargs=dict(message.additional_kwargs),
    )


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


def _invoke_tool_with_progress(
    tool_name: str,
    tool_args: dict[str, Any],
    tools_by_name: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """在后台线程执行工具；yield 进度事件，结束时 return 工具结果。"""
    progress_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    result_holder: list[Any] = []
    error_holder: list[BaseException] = []

    def _on_progress(event: dict[str, Any]) -> None:
        progress_queue.put(event)

    def _run() -> None:
        try:
            with tool_progress_context(tool_name, _on_progress):
                result_holder.append(tools_by_name[tool_name].invoke(tool_args))
        except BaseException as exc:
            error_holder.append(exc)

    worker = threading.Thread(target=_run, name=f"tool-{tool_name}", daemon=True)
    worker.start()

    while worker.is_alive():
        while True:
            try:
                yield progress_queue.get_nowait()
            except queue.Empty:
                break
        time.sleep(0.08)

    while True:
        try:
            yield progress_queue.get_nowait()
        except queue.Empty:
            break

    if error_holder:
        raise error_holder[0]
    if not result_holder:
        raise RuntimeError(f"工具 {tool_name} 未返回结果")
    return result_holder[0]


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
        model=get_active_model_name(),
        tools=[tool_item.name for tool_item in TOOLS],
    )

    working_messages = list(messages)
    user_message = _user_message_from_state(working_messages)
    tools_executed = _tools_executed_in_messages(working_messages)
    wants_tool_calls = _round_wants_tool_calls(
        working_messages,
        user_message,
        tools_executed=tools_executed,
    )
    llm = _build_agent_llm(wants_tool_calls=wants_tool_calls)
    response: AIMessage | None = None

    for attempt in range(1, 4):
        response = _recover_message_tool_calls(llm.invoke(working_messages))
        if response.tool_calls:
            for tool_call in response.tool_calls:
                log_event(
                    request_id,
                    "LLM 决定调用工具",
                    llm_call=call_no,
                    tool=tool_call["name"],
                    args=tool_call.get("args"),
                )
            return {"messages": [response]}

        should_retry, missing = _should_force_tool_retry(
            user_message,
            response,
            tools_executed=tools_executed,
            messages=working_messages,
        )
        if not should_retry or attempt >= 3:
            break

        thinking, content = extract_message_parts(response)
        log_event(
            request_id,
            "LLM 未调用工具，注入纠正并重试",
            llm_call=call_no,
            attempt=attempt,
            missing_tools=missing,
            thinking_len=len(thinking),
            content=content,
        )
        sequence = _active_tool_sequence(user_message)
        next_tool = _next_sequence_tool(tools_executed, sequence) if sequence else None
        working_messages = working_messages + [
            response,
            HumanMessage(content=_tool_retry_message(missing, next_tool=next_tool)),
        ]

    assert response is not None
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
    tools_executed: set[str] = set()
    log_event(
        request_id,
        "LangGraph 流式执行",
        node_order="agent(stream) -> (tools -> agent(stream))* -> END",
    )

    try:
        max_rounds = 16
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
                model=get_active_model_name(),
                tools=[tool_item.name for tool_item in TOOLS],
            )

            if round_no == 1:
                yield {"type": "status", "text": initial_status_text()}

            wants_tool_calls = _round_wants_tool_calls(
                messages,
                user_message,
                tools_executed=tools_executed,
            )
            buffer_content = wants_tool_calls
            llm = _build_agent_llm(wants_tool_calls=wants_tool_calls)
            accumulated: AIMessage | None = None
            content_buffer: list[str] = []

            for chunk in llm.stream(messages):
                accumulated = chunk if accumulated is None else accumulated + chunk

                thinking_token = chunk.additional_kwargs.get("reasoning_content")
                if thinking_token:
                    sanitized = sanitize_thinking_for_user(thinking_token)
                    if sanitized:
                        yield {"type": "thinking", "token": sanitized}

                if chunk.content:
                    if buffer_content:
                        content_buffer.append(chunk.content)
                    else:
                        yield {"type": "content", "token": chunk.content}

            if accumulated is None:
                raise RuntimeError("LLM 未返回任何内容")

            accumulated = _recover_message_tool_calls(accumulated)

            if accumulated.tool_calls:
                if len(accumulated.tool_calls) == 1:
                    yield {
                        "type": "status",
                        "text": user_tool_status_text(accumulated.tool_calls[0]["name"]),
                    }
                else:
                    labels = "、".join(
                        user_tool_label(call["name"]) for call in accumulated.tool_calls
                    )
                    yield {"type": "status", "text": f"正在执行：{labels}"}

                for tool_call in accumulated.tool_calls:
                    log_event(
                        request_id,
                        "LLM 决定调用工具",
                        llm_call=call_no,
                        tool=tool_call["name"],
                        args=tool_call.get("args"),
                    )

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
                    yield {
                        "type": "tool_session",
                        "action": "start",
                        "tool": tool_name,
                        "toolLabel": user_tool_label(tool_name),
                    }
                    tool_runner = _invoke_tool_with_progress(
                        tool_name, tool_args, tools_by_name
                    )
                    tool_result = None
                    try:
                        while True:
                            yield next(tool_runner)
                    except StopIteration as stop:
                        tool_result = stop.value
                    yield {
                        "type": "tool_session",
                        "action": "end",
                        "tool": tool_name,
                    }
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
                    tools_executed.add(tool_name)
                    hint = _post_tool_system_hint(
                        user_message,
                        tools_executed=tools_executed,
                        last_tool_name=tool_name,
                        last_tool_result=tool_result,
                    )
                    if hint:
                        messages.append(HumanMessage(content=hint))
                    if _tool_result_failed(tool_result):
                        # 失败则停止本轮后续工具与全流程推进
                        break
                continue

            should_retry, missing = _should_force_tool_retry(
                user_message,
                accumulated,
                tools_executed=tools_executed,
                messages=messages,
            )
            if should_retry and round_no < max_rounds:
                thinking, content = extract_message_parts(accumulated)
                log_event(
                    request_id,
                    "LLM 未调用工具，注入纠正并重试",
                    llm_call=call_no,
                    round=round_no,
                    missing_tools=missing,
                    thinking_len=len(thinking),
                    content=content,
                )
                messages.append(accumulated)
                sequence = _active_tool_sequence(user_message)
                next_tool = _next_sequence_tool(tools_executed, sequence) if sequence else None
                messages.append(
                    HumanMessage(content=_tool_retry_message(missing, next_tool=next_tool))
                )
                yield {"type": "status", "text": "模型未实际调用工具，正在重试…"}
                continue

            if buffer_content:
                thinking, content = extract_message_parts(accumulated)
                content = sanitize_thinking_for_user(strip_dsml_markup(content))
                plan_blob = thinking + content
                if content and not _looks_like_premature_completion(plan_blob):
                    yield {"type": "thinking", "token": "\n" + content.strip() + "\n\n"}
                elif content:
                    yield {"type": "content", "token": content}
            else:
                for content_token in content_buffer:
                    yield {"type": "content", "token": content_token}

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
        return {
            "thinking": sanitize_thinking_for_user(thinking),
            "reply": content,
        }
    finally:
        finish_request(request_id)


def get_agent_info() -> dict[str, Any]:
    info = get_llm_public_info()
    info["tools"] = [tool_item.name for tool_item in TOOLS]
    return info
