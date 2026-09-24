"""从 HarmonyOS 工程提取应用图标并导出 1024x1024 PNG（AppGallery 要求）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

APP_ICON_OUTPUT_NAME = "app_icon_1024.png"
TARGET_ICON_SIZE = 1024

_MEDIA_REF_RE = re.compile(r"\$media:([A-Za-z0-9_]+)")
_ICON_FIELD_RE = re.compile(r'"icon"\s*:\s*"\$media:([A-Za-z0-9_]+)"')
_START_WINDOW_ICON_RE = re.compile(
    r'"startWindowIcon"\s*:\s*"\$media:([A-Za-z0-9_]+)"'
)
_APP_ICON_RE = re.compile(
    r'"app"\s*:\s*\{[^}]*"icon"\s*:\s*"\$media:([A-Za-z0-9_]+)"',
    re.S,
)
_IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _media_png_candidates(ohos_root: Path, media_name: str) -> list[Path]:
    stems = {media_name, media_name.removesuffix(".png")}
    candidates: list[Path] = []
    for resources_root in (
        ohos_root / "AppScope/resources",
        ohos_root / "entry/src/main/resources",
    ):
        candidates.extend(_media_png_candidates_in_resources(resources_root, stems))
    return _dedupe_paths(candidates)


def _media_png_candidates_in_resources(
    resources_root: Path,
    media_names: set[str],
) -> list[Path]:
    candidates: list[Path] = []
    if not resources_root.is_dir():
        return candidates
    for qualifier_dir in sorted(resources_root.iterdir()):
        media_dir = qualifier_dir / "media"
        if not media_dir.is_dir():
            continue
        for stem in media_names:
            for suffix in _IMAGE_SUFFIXES:
                path = media_dir / f"{stem}{suffix}"
                if path.is_file():
                    candidates.append(path)
    return candidates


def resolve_media_png_paths(
    resources_root: Path,
    media_name: str,
    *,
    visited: set[str] | None = None,
) -> list[Path]:
    """在指定 resources 根目录下解析 $media 引用，返回需替换的 PNG 路径列表。"""
    visited = visited or set()
    name = (media_name or "").strip()
    if not name or name in visited:
        return []
    visited.add(name)

    if not resources_root.is_dir():
        return []

    resolved: list[Path] = []
    for qualifier_dir in sorted(resources_root.iterdir()):
        media_dir = qualifier_dir / "media"
        if not media_dir.is_dir():
            continue

        json_path = media_dir / f"{name}.json"
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            layered = data.get("layered-image") or data.get("layered_image")
            if isinstance(layered, dict):
                for key in ("background", "foreground"):
                    ref = str(layered.get(key) or "")
                    match = _MEDIA_REF_RE.search(ref)
                    if match:
                        resolved.extend(
                            resolve_media_png_paths(
                                resources_root,
                                match.group(1),
                                visited=visited,
                            )
                        )
            continue

        for suffix in _IMAGE_SUFFIXES:
            png_path = media_dir / f"{name}{suffix}"
            if png_path.is_file():
                resolved.append(png_path)
                break

    return _dedupe_paths(resolved)


def discover_configured_icon_targets(ohos_root: Path | str) -> list[dict[str, Any]]:
    """从 app.json5 / module.json5 的 icon 与 startWindowIcon 字段解析待替换 PNG。"""
    ohos_root = Path(ohos_root).expanduser().resolve()
    targets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def _add(paths: list[Path], *, source: str, media_ref: str) -> None:
        for path in paths:
            key = str(path.resolve())
            if key in seen_paths:
                continue
            seen_paths.add(key)
            targets.append(
                {
                    "path": path,
                    "source": source,
                    "mediaRef": media_ref,
                    "name": path.name,
                }
            )

    app_json5 = ohos_root / "AppScope/app.json5"
    if app_json5.is_file():
        text = app_json5.read_text(encoding="utf-8", errors="ignore")
        match = _APP_ICON_RE.search(text) or _ICON_FIELD_RE.search(text)
        if match:
            media_ref = match.group(1)
            app_resources = ohos_root / "AppScope/resources"
            _add(
                resolve_media_png_paths(app_resources, media_ref),
                source="AppScope/app.json5:icon",
                media_ref=f"$media:{media_ref}",
            )

    for module_json5 in sorted(ohos_root.glob("**/src/main/module.json5")):
        rel = module_json5.as_posix()
        if any(part in rel for part in ("/ohosTest/", "/test/", "/mock/")):
            continue
        resources_root = module_json5.parent / "resources"
        text = module_json5.read_text(encoding="utf-8", errors="ignore")
        for media_ref in _ICON_FIELD_RE.findall(text):
            _add(
                resolve_media_png_paths(resources_root, media_ref),
                source=f"{module_json5.relative_to(ohos_root).as_posix()}:icon",
                media_ref=f"$media:{media_ref}",
            )
        for media_ref in _START_WINDOW_ICON_RE.findall(text):
            _add(
                resolve_media_png_paths(resources_root, media_ref),
                source=f"{module_json5.relative_to(ohos_root).as_posix()}:startWindowIcon",
                media_ref=f"$media:{media_ref}",
            )

    return targets


def _read_layered_image_paths(ohos_root: Path) -> tuple[Path | None, Path | None]:
    layered_json = ohos_root / "AppScope/resources/base/media/layered_image.json"
    if not layered_json.is_file():
        return None, None

    try:
        data = json.loads(layered_json.read_text(encoding="utf-8"))
    except Exception:
        return None, None

    layered = data.get("layered-image") or data.get("layered_image") or {}
    if not isinstance(layered, dict):
        return None, None

    fg_ref = str(layered.get("foreground") or "")
    bg_ref = str(layered.get("background") or "")
    fg_match = _MEDIA_REF_RE.search(fg_ref)
    bg_match = _MEDIA_REF_RE.search(bg_ref)

    foreground = (
        _media_png_candidates(ohos_root, fg_match.group(1))[0]
        if fg_match and _media_png_candidates(ohos_root, fg_match.group(1))
        else None
    )
    background = (
        _media_png_candidates(ohos_root, bg_match.group(1))[0]
        if bg_match and _media_png_candidates(ohos_root, bg_match.group(1))
        else None
    )
    return foreground, background


def _read_icon_media_name(ohos_root: Path) -> str | None:
    for json_path in (
        ohos_root / "AppScope/app.json5",
        ohos_root / "entry/src/main/module.json5",
    ):
        if not json_path.is_file():
            continue
        text = json_path.read_text(encoding="utf-8", errors="ignore")
        match = _ICON_FIELD_RE.search(text)
        if match:
            return match.group(1)
    return None


def _discover_icon_png(ohos_root: Path) -> Path | None:
    preferred_names = (
        "app_icon.png",
        "icon.png",
        "foreground.png",
        "startIcon.png",
        "layered_image.png",
    )
    search_roots = [
        ohos_root / "AppScope/resources",
        ohos_root / "entry/src/main/resources",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for name in preferred_names:
            hits = sorted(root.glob(f"**/media/{name}"))
            if hits:
                return hits[0]

    best: Path | None = None
    best_area = 0
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.glob("**/media/*icon*.png"):
            try:
                with Image.open(path) as img:
                    w, h = img.size
            except Exception:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                best = path
    return best


def find_app_icon_sources(ohos_root: Path) -> dict[str, Any]:
    """定位工程内应用图标素材。"""
    ohos_root = ohos_root.expanduser().resolve()

    media_name = _read_icon_media_name(ohos_root)
    if media_name:
        hits = _media_png_candidates(ohos_root, media_name)
        if hits:
            return {
                "sourceType": "app_json_icon",
                "sourcePath": str(hits[0]),
                "mediaName": media_name,
            }

    foreground, background = _read_layered_image_paths(ohos_root)
    if foreground:
        return {
            "sourceType": "layered_image",
            "sourcePath": str(foreground),
            "foregroundPath": str(foreground),
            "backgroundPath": str(background) if background else "",
        }

    discovered = _discover_icon_png(ohos_root)
    if discovered:
        return {
            "sourceType": "discovered",
            "sourcePath": str(discovered),
        }

    raise FileNotFoundError(
        f"未在工程中找到应用图标（AppScope/resources 或 entry/resources/media）：{ohos_root}"
    )


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGBA")


def _compose_layered_icon(foreground: Path, background: Path | None) -> Image.Image:
    foreground_img = _load_rgba(foreground)
    if background is None or not background.is_file():
        return foreground_img

    background_img = _load_rgba(background)
    if background_img.size != foreground_img.size:
        background_img = background_img.resize(foreground_img.size, Image.Resampling.LANCZOS)
    return Image.alpha_composite(background_img, foreground_img)


def _fit_square_1024(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("图标尺寸无效")

    side = max(width, height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - width) // 2, (side - height) // 2)
    canvas.paste(image, offset, image)
    if side != TARGET_ICON_SIZE:
        canvas = canvas.resize((TARGET_ICON_SIZE, TARGET_ICON_SIZE), Image.Resampling.LANCZOS)
    return canvas


def export_app_icon_1024(
    ohos_root: Path | str,
    output_dir: Path | str,
    *,
    output_name: str = APP_ICON_OUTPUT_NAME,
) -> dict[str, Any]:
    """导出 1024x1024 PNG 应用图标到输出目录。"""
    root = Path(ohos_root).expanduser().resolve()
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / output_name

    sources = find_app_icon_sources(root)
    source_type = str(sources["sourceType"])

    if source_type == "layered_image":
        foreground = Path(str(sources["foregroundPath"]))
        background_raw = str(sources.get("backgroundPath") or "")
        background = Path(background_raw) if background_raw else None
        composed = _compose_layered_icon(foreground, background)
        icon = _fit_square_1024(composed)
    else:
        source = Path(str(sources["sourcePath"]))
        icon = _fit_square_1024(_load_rgba(source))

    rgb = icon.convert("RGB")
    rgb.save(target, format="PNG", optimize=True)

    if rgb.size != (TARGET_ICON_SIZE, TARGET_ICON_SIZE):
        raise RuntimeError(f"导出图标尺寸异常：{rgb.size}")

    return {
        "path": str(target),
        "width": TARGET_ICON_SIZE,
        "height": TARGET_ICON_SIZE,
        "format": "png",
        "sourceType": source_type,
        "sourcePath": sources.get("sourcePath", ""),
        "foregroundPath": sources.get("foregroundPath", ""),
        "backgroundPath": sources.get("backgroundPath", ""),
    }
