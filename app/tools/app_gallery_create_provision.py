"""AppGallery Connect：申请 Profile，并下载到本地。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from langchain_core.tools import tool

from app.config import (
    APP_GALLERY_API_BASE_URL,
    APP_GALLERY_API_CERT_ID,
    PROVISION_DOWNLOAD_DIR,
)
from app.tools.app_gallery_auth import build_api_client_headers, fetch_access_token

# 文档：https://developer.huawei.com/consumer/cn/doc/app/agc-help-provision-api-apply-provision-0000002271000689
# 接口：POST /publish/v3/provision
APPLY_PROVISION_PATH = "/publish/v3/provision"
DEFAULT_PROVISION_TYPE = 2  # 发布


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip(), flags=re.UNICODE)
    return cleaned.strip("._") or "provision"


def _filename_from_url(url: str, fallback: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name
    if name and name.lower().endswith(".p7b"):
        return name
    return f"{fallback}.p7b"


def download_provision_file(
    download_url: str,
    *,
    provision_name: str,
    app_id: str,
    download_dir: Path | None = None,
    timeout: float = 60.0,
) -> Path:
    """下载 provisionDownloadUrl 到本地，返回绝对路径。"""
    target_dir = Path(download_dir or PROVISION_DOWNLOAD_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)

    fallback = f"{_safe_filename(provision_name)}_{_safe_filename(app_id)}"
    filename = _filename_from_url(download_url, fallback)
    target_path = target_dir / filename

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(download_url)
        if response.status_code != 200:
            raise RuntimeError(
                f"下载 Profile 失败：HTTP {response.status_code}，url={download_url[:120]}"
            )
        content = response.content
        if not content:
            raise RuntimeError("下载 Profile 失败：文件内容为空")

    target_path.write_bytes(content)
    return target_path.resolve()


def apply_provision(
    provision_name: str,
    app_id: str,
    *,
    provision_type: int = DEFAULT_PROVISION_TYPE,
    cert_id: str | None = None,
    access_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """申请 Profile，成功时返回 provisionInfo（含 provisionDownloadUrl）。"""
    name = (provision_name or "").strip()
    aid = (app_id or "").strip()
    cid = (cert_id or APP_GALLERY_API_CERT_ID or "").strip()

    if not name:
        raise ValueError("provisionName 不能为空")
    if not aid:
        raise ValueError("appId 不能为空")
    if not cid:
        raise ValueError("缺少 APP_GALLERY_API_CERT_ID，请在 .env 中配置")

    if access_token:
        token = access_token.strip()
        client_id = None
    else:
        token_info = fetch_access_token()
        token = str(token_info["access_token"])
        client_id = str(token_info["client_id"])

    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{APPLY_PROVISION_PATH}"
    headers = build_api_client_headers(token, client_id)
    payload = {
        "provisionName": name,
        "provisionType": provision_type,
        "certId": cid,
        "appId": aid,
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)

    try:
        data = response.json()
    except Exception as exc:
        body = response.text[:300] if response.text else "(空响应)"
        raise RuntimeError(
            f"申请 Profile 失败：HTTP {response.status_code}，响应非 JSON：{body}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    provision_info = data.get("provisionInfo") if isinstance(data, dict) else None
    if not isinstance(provision_info, dict):
        provision_info = {}

    download_url = provision_info.get("provisionDownloadUrl") or data.get("provisionDownloadUrl")

    if response.status_code != 200:
        raise RuntimeError(
            f"申请 Profile 失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )
    if isinstance(ret, dict) and ret_code not in (0, "0"):
        raise RuntimeError(
            f"申请 Profile 失败：ret={json.dumps(ret, ensure_ascii=False)}，响应={json.dumps(data, ensure_ascii=False)}"
        )
    if not download_url:
        raise RuntimeError(
            f"申请 Profile 失败：响应中无 provisionDownloadUrl，响应={json.dumps(data, ensure_ascii=False)}"
        )

    local_path = download_provision_file(
        str(download_url),
        provision_name=name,
        app_id=aid,
    )

    return {
        "provisionId": provision_info.get("id"),
        "provisionName": name,
        "provisionType": provision_type,
        "appId": aid,
        "certId": cid,
        "provisionDownloadUrl": str(download_url),
        "localPath": str(local_path),
        "raw": data,
    }


@tool
def create_app_gallery_provision(provisionName: str, appId: str) -> str:
    """在华为 AppGallery Connect 申请应用 Profile（默认发布类型 provisionType=2）。

    使用 API 客户端方式鉴权（自动换取 access_token），certId 读取 .env 的 APP_GALLERY_API_CERT_ID。
    需要参数：
    - provisionName: Profile 名称
    - appId: 应用 ID（通常来自 create_app_gallery_app_id 的返回）

    成功后会下载 provisionDownloadUrl 到本地，并返回 localPath。
    当用户要求创建/申请 Profile、下载 .p7b 时必须调用本工具。
    """
    try:
        result = apply_provision(provision_name=provisionName, app_id=appId)
    except Exception as exc:
        return f"申请 Profile 失败: {exc}"

    return json.dumps(
        {
            "ok": True,
            "provisionId": result.get("provisionId"),
            "provisionName": result["provisionName"],
            "provisionType": result["provisionType"],
            "appId": result["appId"],
            "certId": result["certId"],
            "provisionDownloadUrl": result["provisionDownloadUrl"],
            "localPath": result["localPath"],
        },
        ensure_ascii=False,
    )
