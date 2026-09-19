"""AppGallery Connect Upload Management API：获取上传地址 → 上传 OBS → 返回 objectId。

流程参考：
- https://developer.huawei.com/consumer/cn/doc/app/agc-help-upload-api-guide-0000002271160549
- https://github.com/AngChow/harmony-agc-publish (scripts/upload_app.py)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from app.config import APP_GALLERY_API_BASE_URL
from app.tools.app_gallery_auth import build_api_client_headers, resolve_api_auth

# v2：.app 包与通用素材（需 sha256 + fileType，harmony-agc-publish 已验证）
UPLOAD_URL_PATH_V2 = "/publish/v2/upload-url/for-obs"
# v3：部分素材类型也可能走 suffix（作为 v2 失败时的兜底）
UPLOAD_URL_PATH_V3 = "/publish/v3/upload-url/for-obs"

FILE_TYPE_BY_SUFFIX = {
    "app": "APP",
    "png": "PNG",
    "jpg": "JPG",
    "jpeg": "JPG",
    "mp4": "MP4",
    "zip": "ZIP",
}

CHUNK_SIZE = 8 * 1024 * 1024


def _parse_suffix(file_path: Path) -> str:
    suffix = file_path.suffix.lstrip(".").lower()
    if not suffix:
        raise ValueError(f"无法从文件名推断后缀：{file_path}")
    return suffix


def infer_file_type(suffix: str) -> str:
    normalized = suffix.lstrip(".").lower()
    return FILE_TYPE_BY_SUFFIX.get(normalized, normalized.upper())


def compute_file_sha256(file_path: Path) -> str:
    """计算文件 SHA256（小写 hex；OBS x-amz-content-sha256 严格要求小写）。"""
    file_path = file_path.expanduser().resolve()
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _extract_url_info(data: dict[str, Any]) -> dict[str, Any]:
    """解析 AGC 响应：{ret, urlInfo: {url, method, headers, objectId}}。"""
    ret = data.get("ret")
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    if isinstance(ret, dict) and ret_code not in (0, "0"):
        raise RuntimeError(
            f"获取上传地址失败：ret={json.dumps(ret, ensure_ascii=False)}，"
            f"响应={json.dumps(data, ensure_ascii=False)}"
        )

    url_info = data.get("urlInfo")
    if not isinstance(url_info, dict):
        url_info = data

    upload_url = url_info.get("url") or url_info.get("uploadUrl") or ""
    object_id = url_info.get("objectId") or ""
    upload_headers = url_info.get("headers") or {}
    method = (url_info.get("method") or "PUT").upper()

    if not upload_url or not object_id:
        raise RuntimeError(
            f"获取上传地址失败：urlInfo 不完整，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "uploadUrl": str(upload_url),
        "objectId": str(object_id),
        "headers": {str(k): str(v) for k, v in dict(upload_headers).items()},
        "method": method,
        "raw": data,
    }


def _request_upload_url(
    *,
    path: str,
    params: dict[str, str | int],
    access_token: str,
    client_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{path}"
    headers = build_api_client_headers(access_token, client_id)

    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, headers=headers, params=params)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"获取上传地址失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"获取上传地址失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(f"获取上传地址失败：响应格式异常：{data!r}")

    return _extract_url_info(data)


def get_upload_url_for_obs(
    *,
    app_id: str,
    file_name: str,
    content_length: int,
    sha256: str,
    suffix: str,
    file_type: str | None = None,
    access_token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """调用「获取上传文件地址」接口，返回 OBS 预签名 URL 与 headers。"""
    token, client_id = resolve_api_auth(access_token)

    normalized_sha256 = sha256.strip().lower()
    if not normalized_sha256:
        raise ValueError("sha256 不能为空")

    params_v2 = {
        "appId": app_id.strip(),
        "fileName": file_name,
        "sha256": normalized_sha256,
        "contentLength": str(content_length),
        "fileType": file_type or infer_file_type(suffix),
    }

    try:
        info = _request_upload_url(
            path=UPLOAD_URL_PATH_V2,
            params=params_v2,
            access_token=token,
            client_id=client_id,
            timeout=timeout,
        )
        info["uploadApiVersion"] = "v2"
        return info
    except RuntimeError as v2_error:
        params_v3 = {
            "appId": app_id.strip(),
            "fileName": file_name,
            "contentLength": content_length,
            "suffix": suffix,
            "sha256": normalized_sha256,
        }
        try:
            info = _request_upload_url(
                path=UPLOAD_URL_PATH_V3,
                params=params_v3,
                access_token=token,
                client_id=client_id,
                timeout=timeout,
            )
            info["uploadApiVersion"] = "v3"
            return info
        except RuntimeError:
            raise v2_error from None


def upload_file_to_obs(
    *,
    upload_url: str,
    file_path: Path,
    upload_headers: dict[str, str],
    method: str = "PUT",
    timeout: float | None = None,
) -> None:
    """将文件上传到 OBS（S3 兼容 PUT，headers 必须完整使用 API 返回值）。"""
    file_path = file_path.expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"上传文件不存在：{file_path}")

    file_size = file_path.stat().st_size
    headers = {str(k): str(v) for k, v in upload_headers.items()}
    headers["Content-Length"] = str(file_size)
    if not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/octet-stream"

    request_timeout = timeout or max(120.0, file_size / (512 * 1024) + 60.0)

    with file_path.open("rb") as handle, httpx.Client(timeout=request_timeout) as client:
        response = client.request(method.upper(), upload_url, headers=headers, content=handle)

    if response.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"上传文件失败：HTTP {response.status_code}，响应={response.text[:300]}"
        )


def upload_publish_file(
    *,
    app_id: str,
    file_path: str | Path,
    file_name: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """上传单个文件到 OBS，返回 objectId（供 app-file-info 使用）。"""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")

    name = (file_name or path.name).strip()
    suffix = _parse_suffix(Path(name))
    content_length = path.stat().st_size
    sha256 = compute_file_sha256(path)

    url_info = get_upload_url_for_obs(
        app_id=app_id,
        file_name=name,
        content_length=content_length,
        sha256=sha256,
        suffix=suffix,
        access_token=access_token,
    )
    upload_file_to_obs(
        upload_url=url_info["uploadUrl"],
        file_path=path,
        upload_headers=url_info["headers"],
        method=url_info.get("method", "PUT"),
    )

    return {
        "objectId": url_info["objectId"],
        "fileName": name,
        "filePath": str(path),
        "contentLength": content_length,
        "sha256": sha256,
        "uploadApiVersion": url_info.get("uploadApiVersion", ""),
    }


def upload_publish_files(
    *,
    app_id: str,
    file_paths: list[str | Path],
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in file_paths:
        results.append(
            upload_publish_file(
                app_id=app_id,
                file_path=item,
                access_token=access_token,
            )
        )
    return results
