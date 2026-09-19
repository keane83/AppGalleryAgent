"""AppGallery Connect：创建 APP ID（HarmonyOS 应用）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import tool

from app.config import APP_GALLERY_API_BASE_URL, APP_GALLERY_API_PROJECT_ID
from app.tools.app_gallery_auth import build_api_client_headers, resolve_api_auth

# 文档：https://developer.huawei.com/consumer/cn/doc/app/agc-help-provision-api-create-appid-0000002526543939
# 接口：POST /publish/v3/app?projectId=...
CREATE_APP_ID_PATH = "/publish/v3/app"
LOOKUP_APP_ID_PATH = "/publish/v2/appid-list"
PARENT_TYPE_APP = 13


def _is_package_name_used_error(ret: dict[str, Any] | None) -> bool:
    if not isinstance(ret, dict):
        return False
    msg = str(ret.get("msg") or "").lower()
    code = ret.get("code")
    return code in (204144746, "204144746") or "pkg's name has been used" in msg or "包名" in msg


def lookup_app_id_by_package(
    package_name: str,
    *,
    access_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """按包名查询已有 APP ID（包名已注册时用于全流程重跑）。"""
    package = (package_name or "").strip()
    if not package:
        raise ValueError("packageName 不能为空")

    token, client_id = resolve_api_auth(access_token)
    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{LOOKUP_APP_ID_PATH}"
    headers = build_api_client_headers(token, client_id)

    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, headers=headers, params={"packageName": package})

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"查询 APP ID 失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    if response.status_code != 200 or (isinstance(ret, dict) and ret_code not in (0, "0")):
        raise RuntimeError(
            f"查询 APP ID 失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    appids = data.get("appids") if isinstance(data, dict) else None
    if not isinstance(appids, list) or not appids:
        raise RuntimeError(f"包名 {package} 未找到已注册的 APP ID")

    first = appids[0] if isinstance(appids[0], dict) else {}
    app_id = str(first.get("value") or "").strip()
    app_name = str(first.get("key") or "").strip()
    if not app_id:
        raise RuntimeError(
            f"查询 APP ID 失败：响应格式异常，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "appId": app_id,
        "appName": app_name,
        "packageName": package,
        "existing": True,
        "raw": data,
    }


def create_app_id(
    app_name: str,
    package_name: str,
    *,
    project_id: str | None = None,
    parent_type: int = PARENT_TYPE_APP,
    access_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """创建 APP ID，成功时返回包含 appId 的结果。"""
    name = (app_name or "").strip()
    package = (package_name or "").strip()
    pid = (project_id or APP_GALLERY_API_PROJECT_ID or "").strip()

    if not name:
        raise ValueError("appName 不能为空")
    if not package:
        raise ValueError("packageName 不能为空")
    if not pid:
        raise ValueError("缺少 APP_GALLERY_API_PROJECT_ID，请在 .env 中配置")

    token, client_id = resolve_api_auth(access_token)

    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{CREATE_APP_ID_PATH}"
    headers = build_api_client_headers(token, client_id)
    payload = {
        "appName": name,
        "packageName": package,
        "parentType": parent_type,
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            url,
            params={"projectId": pid},
            headers=headers,
            json=payload,
        )

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"创建 APP ID 失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    app_id = data.get("appId") if isinstance(data, dict) else None

    if response.status_code != 200:
        raise RuntimeError(
            f"创建 APP ID 失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )
    if isinstance(ret, dict) and ret_code not in (0, "0"):
        if _is_package_name_used_error(ret):
            existing = lookup_app_id_by_package(package, access_token=token)
            return {
                "appId": existing["appId"],
                "appName": existing.get("appName") or name,
                "packageName": package,
                "parentType": parent_type,
                "projectId": pid,
                "existing": True,
                "raw": existing.get("raw"),
            }
        raise RuntimeError(
            f"创建 APP ID 失败：ret={json.dumps(ret, ensure_ascii=False)}，响应={json.dumps(data, ensure_ascii=False)}"
        )
    if not app_id:
        raise RuntimeError(
            f"创建 APP ID 失败：响应中无 appId，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "appId": str(app_id),
        "appName": name,
        "packageName": package,
        "parentType": parent_type,
        "projectId": pid,
        "raw": data,
    }


@tool
def create_app_gallery_app_id(appName: str, packageName: str) -> str:
    """在华为 AppGallery Connect 创建 APP ID（HarmonyOS 应用）。

    使用 API 客户端方式鉴权（自动换取 access_token），parentType 固定为 13（应用）。
    需要参数：
    - appName: 应用名称
    - packageName: 应用包名 / bundle id

    成功后返回 appId。当用户要求创建 APP ID、注册应用包名时必须调用本工具。
    """
    try:
        result = create_app_id(app_name=appName, package_name=packageName)
    except Exception as exc:
        return f"创建 APP ID 失败: {exc}"

    payload: dict[str, Any] = {
        "ok": True,
        "appId": result["appId"],
        "appName": result["appName"],
        "packageName": result["packageName"],
        "parentType": result["parentType"],
        "projectId": result["projectId"],
    }
    if result.get("existing"):
        payload["existing"] = True
        payload["note"] = "包名已存在，已复用已有 appId（全流程重跑时正常）"
    return json.dumps(payload, ensure_ascii=False)
