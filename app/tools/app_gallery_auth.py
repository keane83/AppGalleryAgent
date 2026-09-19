"""AppGallery Connect API 鉴权：用 client_credentials 换取 access_token。"""

from __future__ import annotations

import json
import threading
import time
from contextvars import ContextVar
from typing import Any, Mapping

import httpx
from langchain_core.tools import tool

from app.config import (
    APP_GALLERY_API_CLIENT_ID,
    APP_GALLERY_API_CLIENT_SECRET,
    APP_GALLERY_API_TOKEN_URL,
)

TOKEN_URL = APP_GALLERY_API_TOKEN_URL

# 提前刷新缓冲，避免临界过期
_TOKEN_REFRESH_SKEW_SECONDS = 120

# 进程级缓存（跨请求复用，线程安全）
_cache_lock = threading.Lock()
_token_cache: dict[str, Any] | None = None

# 单次 Agent 会话缓存（同一 request / stream 内复用）
_session_auth_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "app_gallery_session_auth",
    default=None,
)

# 调用 Connect API 时需要 Authorization + client_id 的工具
APP_GALLERY_API_TOOL_NAMES = frozenset(
    {
        "get_app_gallery_access_token",
        "create_app_gallery_app_id",
        "create_app_gallery_provision",
        "update_app_gallery_app_file_info",
        "update_app_gallery_app_basic_info",
        "update_app_gallery_build_update_app",
    }
)


def tool_needs_app_gallery_auth(tool_name: str | None) -> bool:
    return bool(tool_name and tool_name in APP_GALLERY_API_TOOL_NAMES)


def begin_session_auth() -> None:
    """新 Agent 会话开始时清空会话级 token。"""
    _session_auth_ctx.set(None)


def get_session_auth() -> dict[str, Any] | None:
    return _session_auth_ctx.get()


def set_session_auth(auth: dict[str, Any]) -> None:
    _session_auth_ctx.set(dict(auth))


def clear_access_token_cache() -> None:
    global _token_cache
    with _cache_lock:
        _token_cache = None


def _auth_valid(auth: Mapping[str, Any] | None) -> bool:
    if not auth:
        return False
    token = str(auth.get("access_token") or "").strip()
    client_id = str(auth.get("client_id") or "").strip()
    expires_at = float(auth.get("expires_at") or 0)
    return bool(token and client_id and time.time() < expires_at)


def auth_from_state(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """从 Agent state 读取有效鉴权信息。"""
    if not state:
        return None
    token = str(state.get("app_gallery_access_token") or "").strip()
    client_id = str(state.get("app_gallery_client_id") or "").strip()
    expires_at = float(state.get("app_gallery_token_expires_at") or 0)
    auth = {
        "access_token": token,
        "client_id": client_id,
        "expires_at": expires_at,
    }
    return auth if _auth_valid(auth) else None


def auth_state_patch(auth: Mapping[str, Any]) -> dict[str, Any]:
    """将鉴权信息写回 Agent state 的 patch。"""
    return {
        "app_gallery_access_token": str(auth.get("access_token") or ""),
        "app_gallery_client_id": str(auth.get("client_id") or ""),
        "app_gallery_token_expires_at": float(auth.get("expires_at") or 0),
    }


def _cache_valid(cid: str) -> bool:
    if not _token_cache:
        return False
    if _token_cache.get("client_id") != cid:
        return False
    expires_at = float(_token_cache.get("expires_at") or 0)
    return time.time() < expires_at


def fetch_access_token(
    client_id: str | None = None,
    client_secret: str | None = None,
    timeout: float = 30.0,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """调用华为 Connect API 换取 access_token（带进程内缓存）。

    文档：https://developer.huawei.com/consumer/cn/doc/app/agc-help-connect-api-obtain-server-auth-0000002271134661
    """
    global _token_cache

    cid = (client_id or APP_GALLERY_API_CLIENT_ID or "").strip()
    secret = (client_secret or APP_GALLERY_API_CLIENT_SECRET or "").strip()
    if not cid or not secret:
        raise ValueError(
            "缺少 APP_GALLERY_API_CLIENT_ID 或 APP_GALLERY_API_CLIENT_SECRET，请在 .env 中配置"
        )

    with _cache_lock:
        if not force_refresh and _cache_valid(cid):
            cached = dict(_token_cache or {})
            cached["from_cache"] = True
            cached["expires_in"] = max(0, int(float(cached.get("expires_at", 0)) - time.time()))
            return cached

    payload = {
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
        )

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"换取 Token 失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    if response.status_code != 200 or not data.get("access_token"):
        raise RuntimeError(
            f"换取 Token 失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    expires_in = int(data.get("expires_in") or 0)
    expires_at = time.time() + max(expires_in - _TOKEN_REFRESH_SKEW_SECONDS, 60)

    result = {
        "access_token": data["access_token"],
        "expires_in": expires_in,
        "expires_at": expires_at,
        "client_id": cid,
        "token_url": TOKEN_URL,
        "from_cache": False,
    }

    with _cache_lock:
        _token_cache = dict(result)

    return result


def ensure_app_gallery_auth(
    state: Mapping[str, Any] | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """确保当前会话有可用 access_token，并写入会话 context。

    优先级：Agent state → 会话 context → 进程缓存/远程换票。
    """
    if not force_refresh:
        state_auth = auth_from_state(state)
        if state_auth:
            set_session_auth(state_auth)
            return state_auth

        session_auth = get_session_auth()
        if _auth_valid(session_auth):
            return dict(session_auth)

    auth = fetch_access_token(force_refresh=force_refresh)
    set_session_auth(auth)
    return auth


def resolve_api_auth(access_token: str | None = None) -> tuple[str, str | None]:
    """解析 API 客户端鉴权：返回 (access_token, client_id|None)。"""
    if access_token:
        return access_token.strip(), None

    session_auth = get_session_auth()
    if _auth_valid(session_auth):
        return str(session_auth["access_token"]), str(session_auth["client_id"])

    auth = ensure_app_gallery_auth()
    return str(auth["access_token"]), str(auth["client_id"])


def build_api_client_headers(access_token: str, client_id: str | None = None) -> dict[str, str]:
    """API 客户端方式调用 Connect API 时的公共请求头。"""
    cid = (client_id or APP_GALLERY_API_CLIENT_ID or "").strip()
    if not cid:
        raise ValueError("缺少 APP_GALLERY_API_CLIENT_ID，请在 .env 中配置")
    if not access_token:
        raise ValueError("缺少 access_token")
    return {
        "Content-Type": "application/json",
        "client_id": cid,
        "Authorization": f"Bearer {access_token}",
    }


def mask_token(token: str, keep: int = 8) -> str:
    if not token:
        return ""
    if len(token) <= keep * 2:
        return "***"
    return f"{token[:keep]}...{token[-keep:]}"


def safe_tool_log_content(tool_name: str | None, content: object) -> object:
    """写日志时脱敏敏感工具返回值。"""
    if tool_name == "get_app_gallery_access_token" and isinstance(content, str):
        try:
            data = json.loads(content)
            if isinstance(data, dict) and data.get("access_token"):
                data = {**data, "access_token": mask_token(str(data["access_token"]))}
                return json.dumps(data, ensure_ascii=False)
        except Exception:
            return mask_token(content)
    return content


@tool
def get_app_gallery_access_token() -> str:
    """获取华为 AppGallery Connect API 的 access_token（会自动缓存复用）。

    一般不需要每次任务都调用：创建 APP ID / 申请 Profile 等工具内部会自动取 token。
    仅当用户明确要求查看/刷新 token，或鉴权失败需要强制换票时再调用。
    """
    try:
        result = ensure_app_gallery_auth()
    except Exception as exc:
        return f"获取 AppGallery access_token 失败: {exc}"

    return json.dumps(
        {
            "ok": True,
            "access_token": result["access_token"],
            "access_token_masked": mask_token(str(result["access_token"])),
            "expires_in": result.get("expires_in"),
            "from_cache": bool(result.get("from_cache")),
            "client_id": result.get("client_id"),
        },
        ensure_ascii=False,
    )
