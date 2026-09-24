"""替换 HarmonyOS 工程内配置项指向的应用图标资源。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from PIL import Image

from app.tools.harmony_app_icon import discover_configured_icon_targets
from app.tools.harmony_project_detect import resolve_project_paths

logger = logging.getLogger("haa.replace_icon")

BACKUP_DIR_NAME = ".harmony-auto-backups"
_ICON_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _backup_file(source: Path, project_root: Path) -> Path:
    rel = source.relative_to(project_root).as_posix().replace("/", "__")
    destination = _backup_dir(project_root) / f"{rel}.bak"
    shutil.copy2(source, destination)
    return destination


def _load_icon_rgba(icon_path: Path) -> Image.Image:
    suffix = icon_path.suffix.lower()
    if suffix and suffix not in _ICON_IMAGE_SUFFIXES:
        raise ValueError(
            f"不支持的图标格式：{icon_path}（支持 {_ICON_IMAGE_SUFFIXES}）"
        )
    try:
        with Image.open(icon_path) as img:
            return img.convert("RGBA")
    except Exception as exc:
        raise ValueError(f"无法读取图标文件：{icon_path}（{exc}）") from exc


def _cover_resize(source: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = source.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("源图标尺寸无效")
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = source.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _is_splash_like_size(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    ratio = width / height
    return ratio > 1.8 or ratio < 0.55


def _render_icon_for_target(
    source: Image.Image,
    target_path: Path,
    *,
    is_start_window_icon: bool,
) -> tuple[Image.Image, str | None]:
    """按目标文件现有尺寸导出 PNG（保持各密度目录原有大小）。"""
    try:
        with Image.open(target_path) as existing:
            width, height = existing.size
    except Exception:
        width = height = 0

    if width <= 0 or height <= 0:
        width = height = max(source.size)

    note: str | None = None
    if is_start_window_icon and _is_splash_like_size(width, height):
        note = (
            f"startWindowIcon 目标为启动页尺寸 {width}x{height}，"
            "已按居中裁剪适配"
        )
        return _cover_resize(source, width, height), note

    if source.size == (width, height):
        return source.copy(), note

    return source.resize((width, height), Image.Resampling.LANCZOS), note


def replace_project_app_icons(
    project_path: str | Path,
    icon_path: str | Path,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """按 app.json5 / module.json5 配置替换 icon 与 startWindowIcon 指向的图片。"""
    resolved = resolve_project_paths(project_path)
    input_path = resolved["inputPath"]
    ohos_root = resolved["ohosRoot"]
    assert isinstance(input_path, Path)
    assert isinstance(ohos_root, Path)

    source_icon = Path(icon_path).expanduser().resolve()
    if not source_icon.is_file():
        raise FileNotFoundError(f"新图标文件不存在：{source_icon}")

    configured_targets = discover_configured_icon_targets(ohos_root)
    if not configured_targets:
        raise FileNotFoundError(
            "未从 app.json5 / module.json5 解析到 icon 或 startWindowIcon 资源。"
            f"ohosRoot={ohos_root}"
        )

    try:
        base_icon = _load_icon_rgba(source_icon)
    except Exception as exc:
        raise ValueError(f"无法加载新图标：{exc}") from exc

    replaced: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    warnings: list[str] = []

    for target_info in configured_targets:
        target = target_info["path"]
        assert isinstance(target, Path)
        source_field = str(target_info.get("source") or "")
        media_ref = str(target_info.get("mediaRef") or "")
        is_start_window = source_field.endswith(":startWindowIcon")

        try:
            backup_path = ""
            if backup:
                backup_path = str(_backup_file(target, ohos_root))

            rendered, note = _render_icon_for_target(
                base_icon,
                target,
                is_start_window_icon=is_start_window,
            )
            if note and note not in warnings:
                warnings.append(note)

            width, height = rendered.size
            if target.name == "background.png":
                rendered.convert("RGB").save(target, format="PNG", optimize=True)
            else:
                rendered.save(target, format="PNG", optimize=True)

            item = {
                "path": str(target),
                "name": target.name,
                "width": width,
                "height": height,
                "source": source_field,
                "mediaRef": media_ref,
                "backupPath": backup_path,
            }
            if note:
                item["note"] = note
            replaced.append(item)
            logger.info("已替换图标: %s (%sx%s) <= %s", target, width, height, source_field)
        except Exception as exc:
            logger.warning("替换图标失败: %s <= %s | %s", target, source_field, exc)
            failed.append(
                {
                    "path": str(target),
                    "name": target.name,
                    "source": source_field,
                    "mediaRef": media_ref,
                    "error": str(exc),
                }
            )

    by_name: dict[str, int] = {}
    for item in replaced:
        by_name[item["name"]] = by_name.get(item["name"], 0) + 1

    resolved_refs = _dedupe_strings(
        str(item.get("source") or "") for item in configured_targets
    )

    return {
        "ok": len(replaced) > 0,
        "projectPath": str(input_path),
        "iconPath": str(source_icon),
        "ohosRoot": str(ohos_root),
        "projectKind": str(
            resolved["projectKind"].value
            if hasattr(resolved["projectKind"], "value")
            else resolved["projectKind"]
        ),
        "resolvedFrom": resolved_refs,
        "replacedCount": len(replaced),
        "failedCount": len(failed),
        "replacedByName": by_name,
        "replacedFiles": replaced,
        "failedFiles": failed,
        "warnings": warnings,
        "error": (
            "全部替换失败"
            if not replaced and failed
            else ""
        ),
    }


def _dedupe_strings(items: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


@tool
def replace_harmony_app_icon(projectPath: str, iconPath: str) -> str:
    """替换鸿蒙工程内配置项指向的应用图标资源（app 换 icon）。

    解析以下配置并替换对应 PNG（若为 layered_image.json 则替换其中 background/foreground）：
    - AppScope/app.json5 → app.icon
    - entry/.../module.json5 → abilities[].icon
    - entry/.../module.json5 → abilities[].startWindowIcon

    单个文件替换失败不会中断，会继续处理其余目标；返回 replacedFiles 与 failedFiles。
    原文件会备份到 ohos 工程根目录 `.harmony-auto-backups/`。

    参数：
    - projectPath: 鸿蒙工程路径（ohos 根目录或 Flutter 工程根目录）
    - iconPath: 新图标 PNG 路径（建议 1024x1024 或更大正方形）

    全流程上架：仅当用户消息里明确给出了 iconPath 时才调用；未提供则跳过。
    调用时机：configure_harmony_app_signing 之后、capture_harmony_screenshots_and_app_icon 之前。
    """
    try:
        result = replace_project_app_icons(projectPath, iconPath)
    except Exception as exc:
        logger.exception("替换工程图标失败")
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    if not result.get("ok"):
        payload = dict(result)
        if not payload.get("error"):
            payload["error"] = "未能成功替换任何图标文件"
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)
