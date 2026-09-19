"""Flutter OHOS 工程截图规划（启发式 + LLM）。"""

from __future__ import annotations

import re
from pathlib import Path

from app.tools.harmony_flutter_patch import find_flutter_tab_shell
from app.tools.harmony_llm_planner import plan_flutter_with_llm


def _find_dart_root(flutter_root: Path | None, ohos_root: Path) -> Path:
    if flutter_root and (flutter_root / "pubspec.yaml").is_file():
        return flutter_root
    parent = ohos_root.parent
    if (parent / "pubspec.yaml").is_file():
        return parent
    raise FileNotFoundError("未找到 Flutter pubspec.yaml")


def _labels_from_dart_text(text: str) -> list[str]:
    items_block = re.search(r"items:\s*const\s*\[(.*?)\]\s*,", text, re.S)
    if items_block and "BottomNavigationBarItem" in text:
        found = re.findall(r"label:\s*['\"]([^'\"]+)['\"]", items_block.group(1))
        if found:
            return found

    nav_items = re.findall(
        r"_NavItem\s*\([\s\S]*?label:\s*['\"]([^'\"]+)['\"]",
        text,
    )
    if nav_items:
        return nav_items

    floating_items = re.findall(
        r"FloatingTabItem\s*\([\s\S]*?label:\s*['\"]([^'\"]+)['\"]",
        text,
    )
    if floating_items:
        return floating_items

    if "BottomNavigationBarItem" in text:
        found = re.findall(
            r"BottomNavigationBarItem[\s\S]*?label:\s*['\"]([^'\"]+)['\"]",
            text,
        )
        if found:
            return found
    return []


def _parse_nav_labels(flutter_root: Path) -> list[str]:
    lib_dir = flutter_root / "lib"
    if not lib_dir.is_dir():
        return []

    candidates: list[Path] = []
    for pattern in (
        "**/*shell*.dart",
        "**/*root*.dart",
        "**/*nav*.dart",
        "**/root_page.dart",
        "**/app_shell.dart",
    ):
        for path in lib_dir.glob(pattern):
            if path.is_file() and path not in candidates:
                candidates.append(path)

    for dart_file in candidates:
        labels = _labels_from_dart_text(dart_file.read_text(encoding="utf-8", errors="ignore"))
        if labels:
            return labels

    for dart_file in lib_dir.rglob("*.dart"):
        if dart_file in candidates:
            continue
        text = dart_file.read_text(encoding="utf-8", errors="ignore")
        if not any(k in text for k in ("BottomNavigationBar", "_NavItem", "bottomNavigationBar")):
            continue
        labels = _labels_from_dart_text(text)
        if labels:
            return labels

    return []


def _infer_tab_count(flutter_root: Path, labels: list[str]) -> int:
    if labels:
        return len(labels)
    try:
        shell = find_flutter_tab_shell(flutter_root)
        text = shell.read_text(encoding="utf-8", errors="ignore")
        stack = re.search(r"IndexedStack\s*\([\s\S]*?children:\s*(?:const\s*)?\[(.*?)\]", text, re.S)
        if stack:
            children = re.findall(r"\w+\(", stack.group(1))
            if children:
                return len(children)
    except FileNotFoundError:
        pass
    return 3


def _pick_tab_indices(tab_count: int, *, count: int = 3) -> list[int]:
    if tab_count <= 0:
        return [0]
    if tab_count == 1:
        return [0]
    if tab_count == 2:
        return [0, 1]
    indices = [0, 1, tab_count - 1]
    return indices[: min(count, tab_count)]


def _heuristic_plan(dart_root: Path, *, count: int = 3) -> dict[str, object]:
    labels = _parse_nav_labels(dart_root)
    tab_count = _infer_tab_count(dart_root, labels)
    indices = _pick_tab_indices(tab_count, count=count)

    patch_target = ""
    try:
        patch_target = find_flutter_tab_shell(dart_root).relative_to(dart_root).as_posix()
    except FileNotFoundError:
        pass

    screenshots = []
    for seq, tab_index in enumerate(indices):
        label = labels[tab_index] if tab_index < len(labels) else f"tab_{tab_index}"
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", label).strip("_") or f"tab_{tab_index}"
        screenshots.append(
            {
                "slug": slug,
                "tabIndex": tab_index,
                "tabLabel": label,
                "reason": f"Flutter Tab「{label}」",
                "outputFile": f"{seq + 1:02d}_{slug}.png",
            }
        )

    return {
        "mode": "flutter_ohos",
        "planSource": "heuristic",
        "flutterRoot": str(dart_root),
        "patchTargetFile": patch_target,
        "tabCount": tab_count,
        "tabLabels": labels,
        "targetDeliverCount": count,
        "uniqueCaptureCount": len(screenshots),
        "screenshots": screenshots,
    }


def _needs_llm(plan: dict[str, object]) -> bool:
    labels = plan.get("tabLabels") or []
    patch = str(plan.get("patchTargetFile") or "")
    tab_count = int(plan.get("tabCount") or 0)
    shots = plan.get("screenshots") or []
    if not patch:
        return True
    if not labels:
        return True
    if tab_count != len(labels) and labels:
        return True
    if not shots:
        return True
    indices = [int(s.get("tabIndex", 0)) for s in shots if isinstance(s, dict)]
    if indices and max(indices) >= tab_count:
        return True
    return False


def plan_flutter_screenshots(
    ohos_root: Path,
    flutter_root: Path | None,
    *,
    count: int = 3,
) -> dict[str, object]:
    dart_root = _find_dart_root(flutter_root, ohos_root)
    heuristic = _heuristic_plan(dart_root, count=count)

    if not _needs_llm(heuristic):
        return heuristic

    llm_plan = plan_flutter_with_llm(dart_root, count=count)
    if llm_plan:
        if not llm_plan.get("patchTargetFile"):
            llm_plan["patchTargetFile"] = heuristic.get("patchTargetFile", "")
        return llm_plan

    return heuristic
