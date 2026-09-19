"""LangGraph Agent 状态定义。"""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Agent 图状态：消息 + 会话级 AppGallery 鉴权缓存。"""

    app_gallery_access_token: NotRequired[str | None]
    app_gallery_client_id: NotRequired[str | None]
    app_gallery_token_expires_at: NotRequired[float | None]
