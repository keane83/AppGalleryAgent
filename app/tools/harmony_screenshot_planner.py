"""分析 HarmonyOS 工程，挑选应用商店截图页面（原生 / WebView / Flutter）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.tools.harmony_flutter_planner import plan_flutter_screenshots
from app.tools.harmony_project_detect import (
    ProjectKind,
    read_app_display_name,
    read_bundle_name,
    resolve_project_paths,
)
from app.tools.harmony_webview_planner import plan_webview_screenshots


@dataclass(frozen=True)
class ScreenshotPlan:
    page: str
    slug: str
    reason: str
    title_hint: str = ""


def _sanitize_dir_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name.strip())
    return cleaned or "unknown_app"


def _load_main_pages(ohos_root: Path) -> list[str]:
    pages_json = ohos_root / "entry/src/main/resources/base/profile/main_pages.json"
    if not pages_json.is_file():
        raise FileNotFoundError(f"缺少 main_pages.json：{pages_json}")
    data = json.loads(pages_json.read_text(encoding="utf-8"))
    pages = data.get("src") or []
    if not isinstance(pages, list) or not pages:
        raise ValueError("main_pages.json 中 src 为空")
    return [str(p) for p in pages]


def _page_slug(page: str) -> str:
    return page.split("/")[-1]


def _page_ets_path(ohos_root: Path, page: str) -> Path | None:
    path = ohos_root / "entry/src/main/ets" / f"{page}.ets"
    return path if path.is_file() else None


def _extract_title_hint(ets_path: Path | None) -> str:
    if not ets_path or not ets_path.is_file():
        return ""
    text = ets_path.read_text(encoding="utf-8", errors="ignore")
    for pattern in (
        r"Text\(\s*['\"]([^'\"]{2,30})['\"]\s*\)",
        r"\.title\(\s*['\"]([^'\"]{2,30})['\"]\s*\)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _index_linked_pages(ohos_root: Path) -> list[str]:
    index_path = ohos_root / "entry/src/main/ets/pages/Index.ets"
    if not index_path.is_file():
        return []
    text = index_path.read_text(encoding="utf-8", errors="ignore")
    return re.findall(r"url:\s*['\"]pages/([A-Za-z0-9_]+)['\"]", text)


_FEATURE_SLUG_BOOST: dict[str, int] = {
    "statistics": 85,
    "achievements": 75,
    "dashboard": 70,
    "home": 65,
    "diary": 55,
    "moneycalculator": 50,
    "triggerlogger": 45,
}


def _score_page(
    page: str,
    *,
    index_links: list[str],
    ohos_root: Path,
) -> tuple[int, str]:
    slug = _page_slug(page)
    slug_key = slug.lower()
    score = 0
    reasons: list[str] = []

    if slug_key == "index":
        score += 100
        reasons.append("应用主页")
    if slug in index_links:
        score += 50
        reasons.append("主页菜单可达")
    if slug_key in _FEATURE_SLUG_BOOST:
        score += _FEATURE_SLUG_BOOST[slug_key]
        reasons.append("核心功能页")
    elif re.search(r"(stat|achiev|home|main|dashboard|record|diary|profile|setting)", slug, re.I):
        score += 30
        reasons.append("功能展示页")

    ets_path = _page_ets_path(ohos_root, page)
    if ets_path:
        size = ets_path.stat().st_size
        score += min(size // 500, 40)
        title = _extract_title_hint(ets_path)
        if title:
            reasons.append(f"标题「{title}」")

    if re.search(r"(select|picker|dialog|popup|login|splash)", slug, re.I):
        score -= 40
        reasons.append("次级/选择页，优先级较低")

    return score, "；".join(reasons) or slug


def plan_native_screenshots(ohos_root: Path, *, count: int = 3) -> dict[str, object]:
    pages = _load_main_pages(ohos_root)
    unique_target = min(count, len(pages))
    index_links = _index_linked_pages(ohos_root)

    ranked: list[tuple[int, str, str]] = []
    for page in pages:
        score, reason = _score_page(page, index_links=index_links, ohos_root=ohos_root)
        ranked.append((score, page, reason))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: list[ScreenshotPlan] = []
    used_slugs: set[str] = set()

    index_page = next((p for p in pages if _page_slug(p).lower() == "index"), None)
    if index_page:
        selected.append(
            ScreenshotPlan(
                page=index_page,
                slug=_page_slug(index_page),
                reason="应用主页",
                title_hint=_extract_title_hint(_page_ets_path(ohos_root, index_page)),
            )
        )
        used_slugs.add(_page_slug(index_page).lower())

    for _, page, reason in ranked:
        slug = _page_slug(page)
        if slug.lower() in used_slugs:
            continue
        title_hint = _extract_title_hint(_page_ets_path(ohos_root, page))
        selected.append(
            ScreenshotPlan(page=page, slug=slug, reason=reason, title_hint=title_hint)
        )
        used_slugs.add(slug.lower())
        if len(selected) >= unique_target:
            break

    return {
        "mode": "native_arkts",
        "allPages": pages,
        "targetDeliverCount": count,
        "uniqueCaptureCount": len(selected),
        "screenshots": [
            {
                "page": item.page,
                "slug": item.slug,
                "reason": item.reason,
                "titleHint": item.title_hint,
                "outputFile": f"{idx + 1:02d}_{item.slug}.png",
            }
            for idx, item in enumerate(selected)
        ],
    }


def plan_screenshots(project_root: Path | str, *, count: int = 3) -> dict[str, object]:
    resolved = resolve_project_paths(project_root)
    ohos_root = resolved["ohosRoot"]
    assert isinstance(ohos_root, Path)
    kind = resolved["projectKind"]
    assert isinstance(kind, ProjectKind)
    flutter_root = resolved.get("flutterRoot")
    if flutter_root is not None and not isinstance(flutter_root, Path):
        flutter_root = None

    bundle_name = read_bundle_name(ohos_root)
    app_name = read_app_display_name(ohos_root)

    if kind == ProjectKind.WEBVIEW_HTML:
        plan = plan_webview_screenshots(ohos_root, count=count)
    elif kind == ProjectKind.FLUTTER_OHOS:
        plan = plan_flutter_screenshots(ohos_root, flutter_root, count=count)
    else:
        plan = plan_native_screenshots(ohos_root, count=count)

    plan["projectKind"] = kind.value
    plan["bundleName"] = bundle_name
    plan["appName"] = app_name
    plan["ohosRoot"] = str(ohos_root)
    plan["inputPath"] = str(resolved["inputPath"])
    if flutter_root:
        plan["flutterRoot"] = str(flutter_root)
    return plan
