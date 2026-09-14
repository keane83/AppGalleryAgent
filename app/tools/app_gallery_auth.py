"""AppGallery Connect API 鉴权：用 client_credentials 换取 access_token。"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

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

_cache_lock = threading.Lock()
_token_cache: dict[str, Any] | None = None


def clear_access_token_cache() -> None:
    global _token_cache
    with _cache_lock:
        _token_cache = None


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

    同一次 Agent 服务生命周期内会复用未过期的 token，避免每个工具都重新换票。
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
        result = fetch_access_token()
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
