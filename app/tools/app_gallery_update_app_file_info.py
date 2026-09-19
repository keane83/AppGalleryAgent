"""AppGallery Connect：更新应用图标 + 应用介绍截图。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from app.config import APP_GALLERY_API_BASE_URL
from app.tools.app_gallery_auth import build_api_client_headers, resolve_api_auth
from app.tools.app_gallery_upload import upload_publish_file, upload_publish_files

UPDATE_APP_FILE_INFO_PATH = "/publish/v3/app-file-info"
TARGET_SCREENSHOT_COUNT = 3

DEFAULT_DEVICE_TYPE = 4
DEFAULT_LANG = "zh-CN"
DEFAULT_SHOW_TYPE = 0


def _parse_object_ids(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("objectId 列表 JSON 必须是字符串数组")
        return [str(item).strip() for item in data if str(item).strip()]
    return [part.strip() for part in re.split(r"[\s,]+", text) if part.strip()]


def build_lang_file_info(
    *,
    object_ids: list[str],
    lang: str = DEFAULT_LANG,
    device_type: int = DEFAULT_DEVICE_TYPE,
    show_type: int = DEFAULT_SHOW_TYPE,
) -> list[dict[str, Any]]:
    if not object_ids:
        raise ValueError("object_ids 不能为空")
    return [
        {
            "lang": lang,
            "fileInfoList": [
                {
                    "deviceType": device_type,
                    "objectIdList": object_ids,
                    "showType": show_type,
                }
            ],
        }
    ]


def _collect_screenshot_files(directory: Path, *, count: int = TARGET_SCREENSHOT_COUNT) -> list[Path]:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"截图目录不存在：{directory}")

    files = sorted(directory.glob("*.png"))
    if len(files) < count:
        files = sorted(directory.glob("**/*.png"))
    if len(files) < count:
        raise FileNotFoundError(
            f"截图目录至少需要 {count} 张 PNG，当前仅 {len(files)} 张：{directory}"
        )
    return files[:count]


def _resolve_icon_path(icon_path: str) -> Path:
    path = Path(icon_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"应用图标不存在：{path}")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError(f"应用图标需为 PNG/JPG：{path}")
    return path


def update_app_file_info(
    app_id: str,
    body: dict[str, Any],
    *,
    release_type: int = 1,
    release_phase: int = 0,
    access_token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    aid = (app_id or "").strip()
    if not aid:
        raise ValueError("appId 不能为空")
    if not body:
        raise ValueError("请求 body 不能为空")

    token, client_id = resolve_api_auth(access_token)

    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{UPDATE_APP_FILE_INFO_PATH}"
    headers = build_api_client_headers(token, client_id)
    params: dict[str, str | int] = {
        "appId": aid,
        "releaseType": release_type,
        "releasePhase": release_phase,
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.put(url, headers=headers, params=params, json=body)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"更新应用文件信息失败：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    if response.status_code != 200 or (isinstance(ret, dict) and ret_code not in (0, "0")):
        raise RuntimeError(
            f"更新应用文件信息失败：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "appId": aid,
        "releaseType": release_type,
        "releasePhase": release_phase,
        "requestBody": body,
        "raw": data,
    }


def update_app_icon_and_screenshots(
    *,
    app_id: str,
    app_icon_path: str | None = None,
    app_icon_object_id: str | None = None,
    screenshot_dir: str | None = None,
    screenshot_object_ids: list[str] | None = None,
    lang: str = DEFAULT_LANG,
    device_type: int = DEFAULT_DEVICE_TYPE,
    show_type: int = DEFAULT_SHOW_TYPE,
    release_type: int = 1,
    release_phase: int = 0,
    access_token: str | None = None,
) -> dict[str, Any]:
    """上传并更新 appIconList + screenShotList（仅这两项）。"""
    body: dict[str, Any] = {}
    uploaded_icon: dict[str, Any] | None = None
    uploaded_shots: list[dict[str, Any]] = []

    icon_id = (app_icon_object_id or "").strip()
    if app_icon_path:
        uploaded_icon = upload_publish_file(
            app_id=app_id,
            file_path=_resolve_icon_path(app_icon_path),
            access_token=access_token,
        )
        icon_id = str(uploaded_icon["objectId"])

    shot_ids = list(screenshot_object_ids or [])
    if screenshot_dir:
        png_files = _collect_screenshot_files(Path(screenshot_dir))
        uploaded_shots = upload_publish_files(
            app_id=app_id,
            file_paths=png_files,
            access_token=access_token,
        )
        shot_ids = [item["objectId"] for item in uploaded_shots]

    if icon_id:
        body["appIconList"] = build_lang_file_info(
            object_ids=[icon_id],
            lang=lang,
            device_type=device_type,
            show_type=show_type,
        )
    if shot_ids:
        if len(shot_ids) != TARGET_SCREENSHOT_COUNT:
            raise ValueError(
                f"应用介绍截图需要 {TARGET_SCREENSHOT_COUNT} 张，当前 {len(shot_ids)} 张"
            )
        body["screenShotList"] = build_lang_file_info(
            object_ids=shot_ids,
            lang=lang,
            device_type=device_type,
            show_type=show_type,
        )

    if not body:
        raise ValueError("请至少提供应用图标（appIconPath/appIconObjectId）或截图（screenshotDir/screenshotObjectIds）")

    result = update_app_file_info(
        app_id=app_id,
        body=body,
        release_type=release_type,
        release_phase=release_phase,
        access_token=access_token,
    )
    result["appIconObjectId"] = icon_id
    result["screenshotObjectIds"] = shot_ids
    result["uploadedIcon"] = uploaded_icon
    result["uploadedScreenshots"] = uploaded_shots
    return result


@tool
def update_app_gallery_app_file_info(
    appId: str,
    appIconPath: str = "",
    appIconObjectId: str = "",
    screenshotDir: str = "",
    screenshotObjectIds: str = "",
    lang: str = "zh-CN",
    deviceType: int = 4,
    showType: int = 0,
    releaseType: int = 1,
    releasePhase: int = 0,
) -> str:
    """更新华为 AppGallery 应用图标 + 3 张应用介绍截图。

    接口：PUT /publish/v3/app-file-info?appId=...
    仅更新 appIconList 与 screenShotList，不上传视频/证书等其他素材。

    典型用法（截图 + 图标一起更新）：
    - appId: 应用 ID
    - appIconPath: 本地应用图标 PNG/JPG 路径（须与包内图标一致）
    - screenshotDir: 本地截图目录（须含 3 张 PNG，如 capture_harmony_screenshots_and_app_icon 输出目录）

    若文件已上传过，可改传 objectId：
    - appIconObjectId: 图标 objectId
    - screenshotObjectIds: 3 个截图 objectId（逗号分隔或 JSON 数组）

    其他：lang 默认 zh-CN；deviceType 4=手机；showType 0=竖屏。
    """
    try:
        result = update_app_icon_and_screenshots(
            app_id=appId,
            app_icon_path=appIconPath.strip() or None,
            app_icon_object_id=appIconObjectId.strip() or None,
            screenshot_dir=screenshotDir.strip() or None,
            screenshot_object_ids=_parse_object_ids(screenshotObjectIds) or None,
            lang=lang,
            device_type=deviceType,
            show_type=showType,
            release_type=releaseType,
            release_phase=releasePhase,
        )
    except Exception as exc:
        return f"更新应用文件信息失败: {exc}"

    return json.dumps(
        {
            "ok": True,
            "appId": result.get("appId"),
            "appIconObjectId": result.get("appIconObjectId", ""),
            "screenshotObjectIds": result.get("screenshotObjectIds", []),
            "uploadedIcon": bool(result.get("uploadedIcon")),
            "uploadedScreenshotCount": len(result.get("uploadedScreenshots") or []),
        },
        ensure_ascii=False,
    )
