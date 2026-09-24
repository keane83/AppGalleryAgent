"""AppGallery Connect：编译 .app 包并上传到 AGC。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from app.config import APP_GALLERY_API_BASE_URL
from app.tool_progress import report_tool_progress
from app.tools.app_gallery_auth import build_api_client_headers, resolve_api_auth
from app.tools.app_gallery_upload import upload_publish_file
from app.tools.harmony_hdc import build_entry_app, build_flutter_app
from app.tools.harmony_project_detect import ProjectKind, resolve_project_paths

UPDATE_APP_PACKAGE_INFO_PATH = "/publish/v3/app-package-info"

logger = logging.getLogger("haa.app_package_update")


def build_project_app_package(
    project_path: str | Path,
    *,
    release: bool = True,
) -> dict[str, Any]:
    """识别工程类型并编译 .app 包。"""
    resolved = resolve_project_paths(project_path)
    kind = resolved["projectKind"]
    ohos_root = resolved["ohosRoot"]
    flutter_root = resolved["flutterRoot"]

    if kind == ProjectKind.FLUTTER_OHOS:
        if flutter_root is None:
            raise FileNotFoundError(f"Flutter 工程根目录未找到：{project_path}")
        app_path = build_flutter_app(flutter_root, release=release)
        build_root = flutter_root
    else:
        if ohos_root is None:
            raise FileNotFoundError(f"HarmonyOS 工程根目录未找到：{project_path}")
        app_path = build_entry_app(ohos_root, release=release)
        build_root = ohos_root

    stat = app_path.stat()
    return {
        "appPath": str(app_path.resolve()),
        "fileName": app_path.name,
        "contentLength": stat.st_size,
        "projectKind": str(kind.value if hasattr(kind, "value") else kind),
        "buildRoot": str(build_root.resolve()),
    }


def update_app_package_info(
    app_id: str,
    *,
    object_id: str,
    file_name: str,
    release_type: int = 1,
    access_token: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """PUT /publish/v3/app-package-info — 登记已上传的 .app 软件包。

    releasePhase 固定为 0（AGC 要求；勿与 releaseType=1 混淆）。
    """
    release_phase = 0
    aid = (app_id or "").strip()
    oid = (object_id or "").strip()
    name = (file_name or "").strip()
    if not aid:
        raise ValueError("appId 不能为空")
    if not oid:
        raise ValueError("objectId 不能为空")
    if not name:
        raise ValueError("fileName 不能为空")

    token, client_id = resolve_api_auth(access_token)
    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{UPDATE_APP_PACKAGE_INFO_PATH}"
    headers = build_api_client_headers(token, client_id)
    params: dict[str, str | int] = {
        "appId": aid,
        "releaseType": release_type,
        "releasePhase": release_phase,
    }
    body = {"objectId": oid, "fileName": name}

    logger.info(
        "更新 AppGallery 软件包信息: appId=%s fileName=%s objectId=%s",
        aid,
        name,
        oid,
    )

    with httpx.Client(timeout=timeout) as client:
        response = client.put(url, headers=headers, params=params, json=body)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"更新应用软件包信息失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    if response.status_code != 200 or (isinstance(ret, dict) and ret_code not in (0, "0")):
        raise RuntimeError(
            f"更新应用软件包信息失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "appId": aid,
        "fileName": name,
        "objectId": oid,
        "packageId": data.get("packageId") if isinstance(data, dict) else None,
        "releaseType": release_type,
        "releasePhase": release_phase,
        "requestBody": body,
        "raw": data,
    }


def build_and_upload_app_package(
    *,
    app_id: str,
    project_path: str,
    release: bool = True,
    release_type: int = 1,
    access_token: str | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, str]] = []

    def _step(name: str, detail: str = "", *, status: str = "done") -> None:
        steps.append({"step": name, "detail": detail, "status": status})
        report_tool_progress(name, step=name, detail=detail, status=status)

    resolved = resolve_project_paths(project_path)
    kind = resolved["projectKind"]
    _step("识别工程类型", f"{kind.value} · {project_path}")
    _step("编译 .app 包", f"release={release}（可能较久）", status="running")
    build_info = build_project_app_package(project_path, release=release)
    app_path = build_info["appPath"]
    size_mb = int(build_info.get("contentLength") or 0) / (1024 * 1024)
    _step(
        "编译 .app 包",
        f"{build_info.get('fileName')} · {size_mb:.1f} MB",
    )

    _step("获取 OBS 地址并上传 .app", f"约 {size_mb:.1f} MB，请稍候", status="running")
    upload_info = upload_publish_file(
        app_id=app_id,
        file_path=app_path,
        access_token=access_token,
    )
    _step(
        "获取 OBS 地址并上传 .app",
        f"{upload_info.get('fileName')} · API {upload_info.get('uploadApiVersion', 'v2')}",
    )

    _step("登记软件包信息", "PUT /publish/v3/app-package-info", status="running")
    package_info = update_app_package_info(
        app_id=app_id,
        object_id=str(upload_info["objectId"]),
        file_name=str(upload_info["fileName"]),
        release_type=release_type,
        access_token=access_token,
    )
    _step(
        "登记软件包信息",
        f"packageId={package_info.get('packageId')}；releasePhase=0",
    )

    return {
        "appId": app_id,
        "projectPath": project_path,
        "build": build_info,
        "upload": upload_info,
        "packageInfo": package_info,
        "steps": steps,
    }


@tool
def update_app_gallery_build_update_app(
    projectPath: str,
    appId: str,
    release: bool = True,
    releaseType: int = 1,
) -> str:
    """编译 HarmonyOS 工程并上传 .app 软件包到 AppGallery Connect。

    流程：
    1. 识别工程类型并编译 .app（原生/WebView：`hvigorw assembleApp`；Flutter：`flutter build app`）
    2. GET /publish/v2/upload-url/for-obs 获取 OBS 上传地址（fileType=APP）
    3. PUT 上传 .app 到 OBS
    4. PUT /publish/v3/app-package-info 登记软件包（releasePhase 固定为 0）

    参数：
    - projectPath: 鸿蒙工程路径（ohos 根目录或 Flutter 工程根目录）
    - appId: AppGallery 应用 ID
    - releaseType: 默认 1（全网）；releasePhase 不由参数控制，始终为 0

    上传完成后 AGC 需约 2 分钟解析软件包，再提交审核。
    工程须已配置发布签名（可先调用 configure_harmony_app_signing）。
    """
    try:
        result = build_and_upload_app_package(
            app_id=appId,
            project_path=projectPath,
            release=release,
            release_type=releaseType,
        )
    except Exception as exc:
        logger.exception("编译并上传 APP 包失败")
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    build = result.get("build") or {}
    upload = result.get("upload") or {}
    package = result.get("packageInfo") or {}
    payload = {
        "ok": True,
        "appId": appId,
        "projectPath": projectPath,
        "projectKind": build.get("projectKind"),
        "appPath": build.get("appPath"),
        "fileName": upload.get("fileName"),
        "contentLength": upload.get("contentLength"),
        "sha256": upload.get("sha256"),
        "objectId": upload.get("objectId"),
        "packageId": package.get("packageId"),
        "uploadApiVersion": upload.get("uploadApiVersion"),
        "releasePhase": package.get("releasePhase", 0),
        "steps": result.get("steps"),
    }
    return json.dumps(payload, ensure_ascii=False)
