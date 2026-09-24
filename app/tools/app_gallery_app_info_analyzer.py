"""分析 HarmonyOS 工程，生成 AppGallery 商店文案与分类/标签。"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.config import APP_GALLERY_LISTING_ANALYSIS_MODE
from app.tool_progress import report_tool_progress
from app.tools.app_gallery_category_tags import (
    ensure_allowed_auto_tag,
    format_category_tag_options_for_categories,
    is_forbidden_auto_tag,
    prepare_tag_matching_text,
    rank_categories_by_text,
    resolve_best_category_tag,
    validate_category_tag,
)
from app.tools.harmony_llm_planner import _collect_dart_files, request_ollama_json
from app.tools.harmony_project_detect import (
    ProjectKind,
    read_app_display_name,
    read_bundle_name,
    resolve_project_paths,
)
from app.tools.harmony_webview_content import (
    WEBVIEW_HTML_MARKER,
    collect_webview_html_for_listing,
)

logger = logging.getLogger("haa.app_info_analyzer")

MAX_DESC_LEN = 500
MAX_BRIEF_LEN = 15
# 字数目标：引导 LLM 写详细，但不做机械凑字
TARGET_DESC_MIN = 280
TARGET_BRIEF_MIN = 13

_AI_BRIEF_PATTERN = re.compile(
    r"(?i)(人工智能|智能助手|AI助手|AI应用|AI工具|A\.I\.|\bAI\b)"
)
# AppGallery 简介/介绍禁止 emoji、颜文字、装饰符号
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U00002300-\U000023FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)
_GENERIC_DESC_MARKERS: tuple[str, ...] = (
    "简洁实用",
    "界面清晰",
    "操作便捷",
    "界面设计简洁清晰",
    "功能实用易上手",
    "适合日常使用",
    "体验流畅稳定",
    "欢迎下载使用",
    "帮助用户高效完成日常任务",
    "提升使用体验",
)
_CHINESE_UI_STRING = re.compile(r'["\']([^"\']{2,24}[\u4e00-\u9fff][^"\']*)["\']')
_ETS_TEXT_LABEL = re.compile(r"Text\(\s*['\"]([^'\"]{2,40})['\"]")
_STRING_JSON_VALUE = re.compile(
    r'"name"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
    re.S,
)
_PAGE_NAME_BLOCKLIST = frozenset(
    {
        "index",
        "page",
        "about",
        "aboutpage",
        "feedback",
        "feedbackpage",
        "help",
        "helpcenter",
        "helpcenterpage",
        "main",
        "home",
        "entry",
        "detail",
        "list",
        "draw",
        "drawpage",
    }
)
MAX_FILE_CHARS = 1800
MAX_CONTEXT_CHARS = 10_000
MAX_LIGHT_CONTEXT_CHARS = 2500
MAX_NATIVE_LIGHT_CONTEXT_CHARS = 8500
MAX_FLUTTER_LIGHT_CONTEXT_CHARS = 9000
MAX_SOURCE_FILES = 4
MAX_LIGHT_SOURCE_FILES = 3
MAX_FLUTTER_LIGHT_SOURCE_FILES = 6
MAX_NATIVE_LABEL_PAGES = 10
LLM_LISTING_NUM_PREDICT = 1800
LLM_LISTING_RETRY_ATTEMPTS = 3
_DART_TEXT_LABEL = re.compile(r"Text\(\s*['\"]([^'\"]{2,40})['\"]")
_STRING_PLACEHOLDER_VALUES = frozenset(
    {
        "module description",
        "description",
        "模块描述",
        "module test",
        "test",
    }
)

_SKIP_PATH_PARTS = (
    "/test/",
    "/tests/",
    "/mock/",
    "/ohosTest/",
    "/build/",
    "/oh_modules/",
    "/node_modules/",
    "/.git/",
    "/hvigor/",
    "/generated/",
    "Test.ets",
    "Mock.ets",
)


def _read_excerpt(path: Path, limit: int = MAX_FILE_CHARS) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n/* ... truncated ... */\n" + text[-half:]


def _should_skip_source(path: Path) -> bool:
    rel = path.as_posix()
    return any(part in rel for part in _SKIP_PATH_PARTS)


def _resolve_page_source(ohos_root: Path, page_route: str) -> Path | None:
    route = (page_route or "").strip().lstrip("/")
    if not route:
        return None
    stem = route.removesuffix(".ets") if route.endswith(".ets") else route
    candidates = [
        ohos_root / f"entry/src/main/ets/{stem}.ets",
        ohos_root / f"entry/src/main/ets/{route}",
        ohos_root / f"entry/src/main/ets/pages/{Path(stem).name}.ets",
    ]
    for candidate in candidates:
        if candidate.is_file() and not _should_skip_source(candidate):
            return candidate
    return None


def _collect_native_pages(ohos_root: Path) -> list[str]:
    pages_json = ohos_root / "entry/src/main/resources/base/profile/main_pages.json"
    if not pages_json.is_file():
        return []
    try:
        data = json.loads(pages_json.read_text(encoding="utf-8"))
        pages = data.get("src") or []
        return [str(p) for p in pages][:12]
    except Exception:
        return []


def _is_useful_string_value(value: str) -> bool:
    cleaned = (value or "").strip()
    if len(cleaned) < 2:
        return False
    if cleaned.lower() in _STRING_PLACEHOLDER_VALUES:
        return False
    if cleaned in _STRING_PLACEHOLDER_VALUES:
        return False
    if not re.search(r"[\u4e00-\u9fff]", cleaned):
        return False
    return True


def _collect_string_resource_files(ohos_root: Path, *, light: bool = False) -> list[Path]:
    files: list[Path] = []
    for path in sorted(ohos_root.glob("**/resources/**/element/string.json")):
        if _should_skip_source(path):
            continue
        rel = path.as_posix()
        if light and "/en_US/" in rel:
            continue
        files.append(path)
    return files


def _extract_string_json_labels(path: Path, *, limit: int = 30) -> list[str]:
    labels: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return labels
    for item in data.get("string") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if _is_useful_string_value(value) and value not in labels:
            labels.append(value)
        if len(labels) >= limit:
            break
    return labels


def _collect_flutter_marketing_dart_files(flutter_root: Path, *, limit: int = 6) -> list[Path]:
    lib_dir = flutter_root / "lib"
    if not lib_dir.is_dir():
        return []

    selected: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            return
        seen.add(key)
        selected.append(path)

    priority_globs = (
        "main.dart",
        "app.dart",
        "**/app_info.dart",
        "**/*vault*.dart",
        "**/*password*.dart",
        "**/*auth*.dart",
        "**/*generator*.dart",
        "**/*settings*.dart",
        "**/*shell*.dart",
    )
    for pattern in priority_globs:
        for path in sorted(lib_dir.glob(pattern)):
            _add(path)
            if len(selected) >= limit:
                return selected[:limit]

    for path in _collect_dart_files(flutter_root):
        _add(path)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _extract_dart_text_labels(path: Path, *, limit: int = 40) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    labels: list[str] = []
    seen: set[str] = set()
    for match in _DART_TEXT_LABEL.finditer(text):
        label = re.sub(r"\s+", "", match.group(1).strip())
        if len(label) < 2 or len(label) > 20:
            continue
        if not re.search(r"[\u4e00-\u9fff]", label):
            continue
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _collect_flutter_ui_labels(flutter_root: Path, dart_files: list[Path]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        item = re.sub(r"\s+", "", (raw or "").strip())
        if len(item) < 2 or len(item) > 20:
            return
        if not re.search(r"[\u4e00-\u9fff]", item):
            return
        if item in seen:
            return
        seen.add(item)
        labels.append(item)

    for path in dart_files:
        for item in _extract_dart_text_labels(path):
            _add(item)
    return labels[:40]


def _extract_ets_text_labels(path: Path, *, limit: int = 40) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    labels: list[str] = []
    seen: set[str] = set()
    for match in _ETS_TEXT_LABEL.finditer(text):
        label = re.sub(r"\s+", "", match.group(1).strip())
        if len(label) < 2 or len(label) > 20:
            continue
        if not re.search(r"[\u4e00-\u9fff]", label):
            continue
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _collect_native_ui_labels(ohos_root: Path, pages: list[str]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        item = re.sub(r"\s+", "", (raw or "").strip())
        if len(item) < 2 or len(item) > 20:
            return
        if not re.search(r"[\u4e00-\u9fff]", item):
            return
        if item in seen:
            return
        seen.add(item)
        labels.append(item)

    for string_json in _collect_string_resource_files(ohos_root):
        for item in _extract_string_json_labels(string_json):
            _add(item)

    for page_route in pages[:MAX_NATIVE_LABEL_PAGES]:
        source = _resolve_page_source(ohos_root, page_route)
        if source is None:
            continue
        for item in _extract_ets_text_labels(source):
            _add(item)

    return labels[:40]


def _select_native_source_files(ohos_root: Path, pages: list[str], *, limit: int) -> list[Path]:
    scored: list[tuple[int, Path]] = []
    for page_route in pages:
        source = _resolve_page_source(ohos_root, page_route)
        if source is None:
            continue
        try:
            scored.append((source.stat().st_size, source))
        except OSError:
            continue
    scored.sort(key=lambda item: item[0])
    selected: list[Path] = []
    seen: set[str] = set()
    for _, source in scored:
        key = str(source.resolve())
        if key in seen:
            continue
        seen.add(key)
        selected.append(source)
        if len(selected) >= limit:
            break
    return selected


def collect_project_marketing_context(
    project_path: str | Path,
    *,
    light: bool = False,
) -> dict[str, Any]:
    """收集用于生成商店文案的工程上下文。"""
    is_webview = False
    max_source_files = MAX_LIGHT_SOURCE_FILES if light else MAX_SOURCE_FILES
    max_context_chars = MAX_LIGHT_CONTEXT_CHARS if light else MAX_CONTEXT_CHARS
    max_file_chars = 900 if light else MAX_FILE_CHARS
    resolved = resolve_project_paths(project_path)
    ohos_root = resolved["ohosRoot"]
    assert isinstance(ohos_root, Path)
    kind = resolved["projectKind"]
    assert isinstance(kind, ProjectKind)
    is_webview = kind == ProjectKind.WEBVIEW_HTML
    is_native = kind == ProjectKind.NATIVE_ARKTS
    is_flutter = kind == ProjectKind.FLUTTER_OHOS
    if is_webview and light:
        max_context_chars = max(max_context_chars, 6500)
    if is_native and light:
        max_context_chars = max(max_context_chars, MAX_NATIVE_LIGHT_CONTEXT_CHARS)
        max_source_files = max(max_source_files, MAX_LIGHT_SOURCE_FILES)
    if is_flutter and light:
        max_context_chars = max(max_context_chars, MAX_FLUTTER_LIGHT_CONTEXT_CHARS)
        max_file_chars = max(max_file_chars, 1400)
    flutter_root = resolved.get("flutterRoot")
    if flutter_root is not None and not isinstance(flutter_root, Path):
        flutter_root = None

    app_name = read_app_display_name(ohos_root)
    bundle_name = read_bundle_name(ohos_root)
    html_feature_hints: list[str] = []
    parts: list[str] = [
        f"应用名称: {app_name}",
        f"包名: {bundle_name}",
        f"工程类型: {kind.value}",
    ]

    if is_webview:
        html_limit = 5500 if light else 8000
        webview_html, html_feature_hints = collect_webview_html_for_listing(
            ohos_root,
            limit_per_file=html_limit,
        )
        if webview_html:
            parts.append(f"{WEBVIEW_HTML_MARKER}\n{webview_html}")
        if html_feature_hints:
            parts.append("HTML 功能标签: " + "、".join(html_feature_hints[:12]))

    ui_feature_hints: list[str] = []
    pages = _collect_native_pages(ohos_root)
    if is_native:
        ui_feature_hints = _collect_native_ui_labels(ohos_root, pages)
        if ui_feature_hints:
            parts.append("界面与功能文案: " + "、".join(ui_feature_hints[:24]))

    for string_json in _collect_string_resource_files(ohos_root, light=light):
        string_limit = 800 if (light and is_webview) else (1200 if light else 3000)
        rel = string_json.relative_to(ohos_root).as_posix()
        parts.append(f"=== {rel} ===\n{_read_excerpt(string_json, string_limit)}")

    if pages and not (light and is_webview):
        parts.append("页面列表: " + ", ".join(pages))

    if is_native:
        source_files = _select_native_source_files(
            ohos_root,
            pages,
            limit=max_source_files if light else MAX_SOURCE_FILES,
        )
        if not source_files:
            for pattern in ("entry/src/main/ets/pages/*.ets", "entry/src/main/ets/*Page.ets"):
                source_files.extend(
                    path
                    for path in sorted(ohos_root.glob(pattern))
                    if not _should_skip_source(path)
                )
                if source_files:
                    break
        for source in source_files[: max_source_files if light else MAX_SOURCE_FILES]:
            rel = source.relative_to(ohos_root).as_posix()
            parts.append(f"=== {rel} ===\n{_read_excerpt(source, max_file_chars)}")
    elif not light and not is_webview:
        added_sources = 0
        for page_route in pages:
            if added_sources >= max_source_files:
                break
            source = _resolve_page_source(ohos_root, page_route)
            if source is None:
                continue
            rel = source.relative_to(ohos_root).as_posix()
            parts.append(f"=== {rel} ===\n{_read_excerpt(source, max_file_chars)}")
            added_sources += 1

    if flutter_root and flutter_root.is_dir():
        pubspec = flutter_root / "pubspec.yaml"
        if pubspec.is_file():
            parts.append(f"=== pubspec.yaml ===\n{_read_excerpt(pubspec, 600 if light else 1200)}")
        dart_limit = MAX_FLUTTER_LIGHT_SOURCE_FILES if light else 6
        dart_files = _collect_flutter_marketing_dart_files(flutter_root, limit=dart_limit)
        flutter_ui_labels = _collect_flutter_ui_labels(flutter_root, dart_files)
        if flutter_ui_labels:
            ui_feature_hints.extend(flutter_ui_labels)
            parts.append("界面与功能文案: " + "、".join(flutter_ui_labels[:24]))
        dart_chars = max_file_chars
        for path in dart_files:
            rel = path.relative_to(flutter_root).as_posix()
            parts.append(f"=== {rel} ===\n{_read_excerpt(path, dart_chars)}")

    readme = resolved["inputPath"]
    if isinstance(readme, Path):
        for name in ("README.md", "readme.md", "README.txt"):
            candidate = readme / name
            if candidate.is_file():
                parts.append(f"=== {name} ===\n{_read_excerpt(candidate, 2000)}")
                break

    context_text = "\n\n".join(parts)
    if len(context_text) > max_context_chars:
        context_text = (
            context_text[: max_context_chars // 2]
            + "\n\n/* ... truncated ... */\n\n"
            + context_text[-max_context_chars // 2 :]
        )

    return {
        "appName": app_name,
        "bundleName": bundle_name,
        "projectKind": kind.value,
        "ohosRoot": str(ohos_root),
        "contextText": context_text,
        "htmlFeatureHints": html_feature_hints,
        "uiFeatureHints": ui_feature_hints,
    }


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit]


def _truncate_smart(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "！", "？", "；", "，", " "):
        idx = cut.rfind(sep)
        if idx >= int(limit * 0.7):
            end = idx + (1 if sep != " " else 0)
            return cut[:end].strip()
    return cut


def _strip_ai_mentions(text: str) -> str:
    cleaned = _AI_BRIEF_PATTERN.sub("", text or "")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[，。、·\-—]+$", "", cleaned)
    return cleaned.strip()


def _strip_emoji(text: str) -> str:
    """移除 emoji / 颜文字 / 装饰符号（AppGallery 文案合规）。"""
    cleaned = _EMOJI_PATTERN.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_html_hints_from_context(context: dict[str, Any], *, limit: int = 12) -> list[str]:
    """从 WebView HTML 摘要中提取功能标签。"""
    stored = context.get("htmlFeatureHints")
    hints: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        item = re.sub(r"\s+", "", (raw or "").strip())
        if len(item) < 2 or len(item) > 18 or item in seen:
            return
        seen.add(item)
        hints.append(item)

    if isinstance(stored, list):
        for item in stored:
            _add(str(item))
            if len(hints) >= limit:
                return hints[:limit]

    text = str(context.get("contextText") or "")
    if WEBVIEW_HTML_MARKER not in text:
        return hints[:limit]

    segment = text.split(WEBVIEW_HTML_MARKER, 1)[1]
    segment = segment.split("\n\n===", 1)[0]

    for line in segment.splitlines():
        stripped = line.strip()
        if stripped.startswith("功能标签:") or stripped.startswith("HTML 功能标签:"):
            blob = stripped.split(":", 1)[1]
            for token in re.split(r"[、,，/|]", blob):
                _add(token)
        elif stripped.startswith("页面标题:"):
            _add(stripped.split(":", 1)[1])
        elif _is_useful_plain_hint(stripped):
            _add(stripped)
        if len(hints) >= limit:
            break

    return hints[:limit]


def _is_useful_plain_hint(text: str) -> bool:
    item = re.sub(r"\s+", "", text)
    if len(item) < 2 or len(item) > 18:
        return False
    if not re.search(r"[\u4e00-\u9fff]", item):
        return False
    if item.startswith(("HTML 文件:", "页面标题:", "功能标签:", "=== ")):
        return False
    return True


def _extract_feature_hints(context: dict[str, Any], *, limit: int = 8) -> list[str]:
    """从工程摘要中提取可用于文案的功能/界面关键词。"""
    text = str(context.get("contextText") or "")
    app_name = str(context.get("appName") or "")
    seen: set[str] = set()
    hints: list[str] = []

    def _add(raw: str) -> None:
        item = re.sub(r"\s+", "", raw.strip())
        if len(item) < 2 or len(item) > 18:
            return
        if item in seen or item == app_name:
            return
        if item.lower() in _PAGE_NAME_BLOCKLIST:
            return
        if re.fullmatch(r"[A-Za-z0-9_]+", item):
            return
        if item.startswith("是否") or item.endswith("？") or item.endswith("?"):
            return
        if item.endswith("：") and len(item) <= 5:
            return
        if item in {"姓名", "角色", "头像", "样式", "创建时间", "家庭名称", "模块描述"}:
            return
        if not _is_useful_string_value(item) and not re.search(r"[\u4e00-\u9fff]{2,}", item):
            return
        if any(skip in item for skip in ("http", "assets/", ".dart", ".ets", "ohos", "flutter")):
            return
        seen.add(item)
        hints.append(item)

    stored_ui = context.get("uiFeatureHints")
    if isinstance(stored_ui, list):
        for item in stored_ui:
            _add(str(item))
            if len(hints) >= limit:
                return hints[:limit]

    if str(context.get("projectKind") or "") == ProjectKind.WEBVIEW_HTML.value:
        for item in _extract_html_hints_from_context(context, limit=limit):
            _add(item)
        if hints:
            return hints[:limit]

    for line in text.splitlines():
        if line.startswith("界面与功能文案:"):
            blob = line.split(":", 1)[1]
            for token in re.split(r"[、,，/|]", blob):
                _add(token)
            if len(hints) >= limit:
                return hints[:limit]

    for match in _STRING_JSON_VALUE.finditer(text):
        _add(match.group(1))
        if len(hints) >= limit:
            return hints

    for match in _ETS_TEXT_LABEL.finditer(text):
        _add(match.group(1))
        if len(hints) >= limit:
            return hints

    for match in _CHINESE_UI_STRING.finditer(text):
        _add(match.group(1))
        if len(hints) >= limit:
            return hints

    for token in re.findall(r"[\u4e00-\u9fff]{2,8}", app_name):
        _add(token)

    def _hint_score(label: str) -> int:
        score = 0
        if any(
            key in label
            for key in ("添加任务", "家务库", "家庭奖励", "家务小贴士", "房间与区域", "自定义任务")
        ):
            score += 30
        if "添加" in label and any(key in label for key in ("任务", "成员", "房间", "奖励")):
            score += 20
        if any(key in label for key in ("家务", "任务", "奖励", "成员", "房间", "库", "贴士", "转盘", "抽奖")):
            score += 10
        if any(key in label for key in ("编辑", "删除", "清除", "管理", "选择添加")):
            score += 2
        if 4 <= len(label) <= 8:
            score += 4
        return score

    hints.sort(key=_hint_score, reverse=True)
    return hints[:limit]


def _count_generic_markers(text: str) -> int:
    return sum(1 for marker in _GENERIC_DESC_MARKERS if marker in (text or ""))


def _build_webview_context_desc(context: dict[str, Any], hints: list[str]) -> str:
    app_name = str(context.get("appName") or "本应用")
    html_hints = hints or _extract_html_hints_from_context(context)
    if not html_hints:
        return (
            f"{app_name}基于 WebView 在鸿蒙端呈现 H5 业务页面。"
            "请在工程中确认 rawfile HTML 是否可被读取后再生成更具体的介绍。"
        )

    primary = html_hints[0]
    feature_clause = "、".join(html_hints[:6])
    nav_clause = "、".join(html_hints[:4])
    paragraphs = [
        f"{app_name}围绕{primary}等场景提供移动端服务，"
        f"页面涵盖{feature_clause}等功能入口。",
        f"用户可在应用内通过{nav_clause}等模块完成查看、切换与日常使用，"
        f"内容组织与 H5 页面业务逻辑保持一致。",
        f"适合需要{primary}相关能力的用户安装体验。",
    ]
    return _truncate_smart("".join(paragraphs), MAX_DESC_LEN)


def _build_context_aware_desc(context: dict[str, Any]) -> str:
    """基于工程上下文生成非模板化介绍（LLM 失败或输出过泛时使用）。"""
    app_name = str(context.get("appName") or "本应用")
    hints = _extract_feature_hints(context)
    kind = str(context.get("projectKind") or "")

    if kind == ProjectKind.WEBVIEW_HTML.value:
        return _build_webview_context_desc(context, hints)

    if hints:
        feature_clause = "、".join(hints[:6])
        paragraphs = [
            f"{app_name}是一款围绕{hints[0]}等能力打造的鸿蒙应用，"
            f"提供{feature_clause}等功能，满足对应场景下的日常使用需求。",
            f"用户可在应用内通过{feature_clause}等入口完成核心操作，"
            f"功能组织与工程代码中的业务模块保持一致。",
            f"适合有{hints[0]}相关需求的用户安装体验。",
        ]
    elif kind == ProjectKind.FLUTTER_OHOS.value:
        paragraphs = [
            f"{app_name}基于 Flutter 适配鸿蒙平台，保留跨端业务逻辑与界面交互。",
            "应用围绕代码中呈现的核心功能场景设计，可在主要页面完成日常操作。",
            "安装后即可体验完整功能模块。",
        ]
    else:
        paragraphs = [
            f"{app_name}面向鸿蒙用户打造，聚焦实际使用场景中的核心需求。",
            "主要功能可在应用首页及各级页面中直接访问，操作流程清晰。",
            "安装后即可体验完整功能模块。",
        ]

    return _truncate_smart("".join(paragraphs), MAX_DESC_LEN)


def _sanitize_generic_desc(
    text: str,
    context: dict[str, Any],
    *,
    allow_fallback: bool = True,
) -> str:
    cleaned = _strip_emoji(re.sub(r"\s+", " ", (text or "").strip()))
    if not cleaned:
        return _build_context_aware_desc(context) if allow_fallback else ""

    marker_count = _count_generic_markers(cleaned)
    if marker_count >= 2 and allow_fallback:
        return _build_context_aware_desc(context)
    return cleaned


def _finalize_app_desc(
    text: str,
    *,
    context: dict[str, Any] | None = None,
    allow_fallback: bool = True,
) -> str:
    text = _sanitize_generic_desc(text, context or {}, allow_fallback=allow_fallback)
    return _truncate_smart(text, MAX_DESC_LEN)


def _finalize_brief_info(text: str, *, app_name: str = "") -> str:
    text = _strip_emoji(_strip_ai_mentions(text))
    text = _truncate_smart(text, MAX_BRIEF_LEN)
    if len(text) < TARGET_BRIEF_MIN:
        candidate = _strip_ai_mentions(app_name)
        if candidate and TARGET_BRIEF_MIN <= len(candidate) <= MAX_BRIEF_LEN:
            text = candidate
        elif candidate and len(candidate) <= MAX_BRIEF_LEN:
            text = candidate
    if len(text) < TARGET_BRIEF_MIN and text:
        suffix = "实用工具"
        text = (text + suffix)[:MAX_BRIEF_LEN]
    return _truncate_smart(_strip_ai_mentions(text), MAX_BRIEF_LEN)


def _finalize_listing_copy(
    app_desc: str,
    brief_info: str,
    *,
    app_name: str = "",
    context: dict[str, Any] | None = None,
    allow_desc_fallback: bool = True,
) -> tuple[str, str]:
    return (
        _finalize_app_desc(app_desc, context=context, allow_fallback=allow_desc_fallback),
        _finalize_brief_info(brief_info, app_name=app_name),
    )


def _build_analysis_result(
    *,
    harmony_child_type: int,
    kind_main_tag: int,
    category_name: str,
    tag_name: str,
    app_desc: str,
    brief_info: str,
    reason: str,
    source: str,
    context: dict[str, Any],
    raw_llm: dict[str, Any] | None = None,
    allow_desc_fallback: bool = True,
) -> dict[str, Any]:
    app_name = str(context.get("appName") or "")
    app_desc, brief_info = _finalize_listing_copy(
        app_desc,
        brief_info,
        app_name=app_name,
        context=context,
        allow_desc_fallback=allow_desc_fallback,
    )
    return {
        "harmonyChildType": harmony_child_type,
        "kindMainTag": kind_main_tag,
        "categoryName": category_name,
        "tagName": tag_name,
        "appDesc": app_desc,
        "briefInfo": brief_info,
        "appDescLen": len(app_desc),
        "briefInfoLen": len(brief_info),
        "reason": reason,
        "source": source,
        "context": context,
        "rawLlm": raw_llm or {},
    }


def format_analysis_summary(analysis: dict[str, Any]) -> str:
    """人类可读的分析摘要（返回给 Agent / 用户查看）。"""
    app_desc = str(analysis.get("appDesc") or "")
    brief_info = str(analysis.get("briefInfo") or "")
    return (
        "【AI 分析结果 — 提交前请核对】\n"
        f"1. 二级分类：{analysis.get('categoryName')} "
        f"(harmonyChildType={analysis.get('harmonyChildType')})\n"
        f"2. 标签：{analysis.get('tagName')} "
        f"(kindMainTag={analysis.get('kindMainTag')})\n"
        f"3. 应用介绍（{len(app_desc)}字）：{app_desc}\n"
        f"4. 一句话简介（{len(brief_info)}字）：{brief_info}\n"
        f"选型理由：{analysis.get('reason') or '无'}\n"
        f"分析来源：{analysis.get('source') or 'unknown'}"
        + (
            f"\nLLM 提示：{analysis.get('llmWarning')}"
            if analysis.get("llmWarning")
            else ""
        )
    )


def log_analysis_result(analysis: dict[str, Any]) -> None:
    """后台日志打印完整分析结果。"""
    logger.info("AppGallery 基本信息 AI 分析完成:\n%s", format_analysis_summary(analysis))
    logger.info(
        "AppGallery 分析 IDs: harmonyChildType=%s kindMainTag=%s source=%s",
        analysis.get("harmonyChildType"),
        analysis.get("kindMainTag"),
        analysis.get("source"),
    )
    raw_llm = analysis.get("rawLlm")
    if raw_llm:
        logger.info("AppGallery LLM 原始 JSON: %s", json.dumps(raw_llm, ensure_ascii=False))


def _heuristic_fallback(context: dict[str, Any]) -> dict[str, Any]:
    app_name = str(context.get("appName") or "应用")
    pages = str(context.get("contextText") or "")
    picked = _keyword_category_result(context)

    hints = _extract_feature_hints(context)
    if hints:
        brief_info = _truncate("".join(hints[:2]), MAX_BRIEF_LEN) or _truncate(app_name, MAX_BRIEF_LEN)
    elif any(keyword in app_name + pages for keyword in ("打卡", "作业", "签到", "考勤")):
        brief_info = _truncate(f"{app_name[:6]}打卡助手", MAX_BRIEF_LEN)
    else:
        brief_info = _truncate(app_name, MAX_BRIEF_LEN)

    return _build_analysis_result(
        harmony_child_type=picked["harmonyChildType"],
        kind_main_tag=picked["kindMainTag"],
        category_name=picked["categoryName"],
        tag_name=picked["tagName"],
        app_desc=_build_context_aware_desc(context),
        brief_info=brief_info,
        reason="LLM 不可用，使用工程上下文启发式兜底",
        source="heuristic",
        context=context,
    )


def _parse_llm_ids(raw: dict[str, Any]) -> tuple[int, int]:
    harmony_child_type = raw.get("harmonyChildType", raw.get("secondCategoryId"))
    kind_main_tag = raw.get("kindMainTag", raw.get("tagId"))
    if harmony_child_type is None or kind_main_tag is None:
        raise ValueError("LLM 响应缺少 harmonyChildType / kindMainTag")
    return int(harmony_child_type), int(kind_main_tag)


def _analysis_mode() -> str:
    mode = (APP_GALLERY_LISTING_ANALYSIS_MODE or "fast").strip().lower()
    if mode in {"heuristic", "fast", "llm"}:
        return mode
    logger.warning("未知 APP_GALLERY_LISTING_ANALYSIS_MODE=%s，回退 fast", mode)
    return "fast"


def _keyword_category_result(context: dict[str, Any]) -> dict[str, Any]:
    text = f"{context.get('appName', '')}\n{context.get('contextText', '')}"
    entry = resolve_best_category_tag(text)
    entry = ensure_allowed_auto_tag(int(entry["secondCategoryId"]), int(entry["tagId"]))
    return {
        "harmonyChildType": int(entry["secondCategoryId"]),
        "kindMainTag": int(entry["tagId"]),
        "categoryName": str(entry["category"]),
        "tagName": str(entry["tag"]),
        "entry": entry,
    }


def _sanitize_analysis_tags(result: dict[str, Any]) -> dict[str, Any]:
    """将分析结果中的敏感标签替换为安全项。"""
    try:
        harmony_child_type = int(result["harmonyChildType"])
        kind_main_tag = int(result["kindMainTag"])
    except (KeyError, TypeError, ValueError):
        return result

    if not is_forbidden_auto_tag(tag_id=kind_main_tag, tag_name=str(result.get("tagName") or "")):
        return result

    entry = ensure_allowed_auto_tag(harmony_child_type, kind_main_tag)
    logger.warning(
        "AppGallery 分析命中禁止自动使用的标签 kindMainTag=%s (%s)，已替换为 %s (%s)",
        kind_main_tag,
        result.get("tagName"),
        entry["tagId"],
        entry["tag"],
    )
    result = dict(result)
    result["harmonyChildType"] = int(entry["secondCategoryId"])
    result["kindMainTag"] = int(entry["tagId"])
    result["categoryName"] = str(entry["category"])
    result["tagName"] = str(entry["tag"])
    return result


def _llm_listing_payload_valid(raw: dict[str, Any]) -> bool:
    app_desc = re.sub(r"\s+", " ", str(raw.get("appDesc") or "")).strip()
    return len(app_desc) >= 20


def _build_listing_copy_prompts(
    context: dict[str, Any],
    *,
    category_name: str,
    tag_name: str,
) -> tuple[str, str]:
    """构造商店文案 LLM 的 system / user prompt。"""
    hints = _extract_feature_hints(context)
    kind = str(context.get("projectKind") or "")
    is_webview = kind == ProjectKind.WEBVIEW_HTML.value
    is_native = kind == ProjectKind.NATIVE_ARKTS.value
    hint_line = "、".join(hints[:10]) if hints else "（请从代码/strings/HTML 中自行提炼）"
    kind_rule = ""
    if is_webview:
        kind_rule = (
            "当前为 webview_html 工程：依据 HTML 业务内容写文案，"
            "必须引用 HTML 功能标签/页面标题中的至少 3 个词，"
            "不得写 WebView/浏览器/壳层/简洁实用/界面清晰 等空话。"
        )
    elif is_native:
        kind_rule = (
            "当前为 native_arkts 工程：依据 ArkTS 页面代码与 string.json、"
            "「界面与功能文案」中的中文词写文案；"
            "禁止把 Index/Page 等英文页面名当作功能名。"
        )
    elif kind == ProjectKind.FLUTTER_OHOS.value:
        kind_rule = (
            "当前为 flutter_ohos 工程：依据 Dart 代码、pubspec 与 string 资源写文案。"
        )
    system = (
        "你是华为 AppGallery 商店文案助手。"
        "根据工程摘要撰写客观、合规、与应用真实功能匹配的中文商店文案。"
        "输出严格 JSON，不要 markdown。"
        f"appDesc 建议 {TARGET_DESC_MIN}-{MAX_DESC_LEN} 字（必须≤{MAX_DESC_LEN}）。"
        f"briefInfo 建议 {TARGET_BRIEF_MIN}-{MAX_BRIEF_LEN} 字（必须≤{MAX_BRIEF_LEN}）。"
        "appDesc 必须写出 3-5 个从工程摘要中能对应上的具体功能或场景，"
        "禁止套话：简洁实用、界面清晰、操作便捷、体验流畅、欢迎下载、"
        "界面设计简洁清晰、功能实用易上手、适合日常使用 等空泛表述。"
        "briefInfo 禁止出现 AI、人工智能、智能助手 等字样。"
        "appDesc 与 briefInfo 均禁止包含 emoji 表情、颜文字、★♥→ 等装饰符号，"
        "只用纯中文（及必要英文/数字）描述功能。"
        f"{kind_rule}"
    )
    user = (
        f"应用名: {context.get('appName', '')}\n"
        f"包名: {context.get('bundleName', '')}\n"
        f"已选分类: {category_name} / {tag_name}\n"
        f"功能线索（必须尽量写入 appDesc）: {hint_line}\n\n"
        f"工程摘要:\n{context.get('contextText', '')}\n\n"
        '输出 JSON: {"appDesc":"...","briefInfo":"...","reason":"..."}'
    )
    return system, user


def _llm_generate_listing_copy(
    context: dict[str, Any],
    *,
    category_name: str,
    tag_name: str,
    num_predict: int = LLM_LISTING_NUM_PREDICT,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """仅生成 appDesc / briefInfo（分类已由关键词确定，prompt 更小）。"""
    system, user = _build_listing_copy_prompts(
        context,
        category_name=category_name,
        tag_name=tag_name,
    )
    return request_ollama_json(system, user, num_predict=num_predict, timeout=timeout)


def _request_listing_copy_llm(
    context: dict[str, Any],
    *,
    category_name: str,
    tag_name: str,
) -> dict[str, Any]:
    """带重试的文案 LLM 请求；空 appDesc 视为失败并重试。"""
    last_exc: Exception | None = None
    for attempt in range(1, LLM_LISTING_RETRY_ATTEMPTS + 1):
        num_predict = LLM_LISTING_NUM_PREDICT + (attempt - 1) * 500
        try:
            raw = _llm_generate_listing_copy(
                context,
                category_name=category_name,
                tag_name=tag_name,
                num_predict=num_predict,
            )
            if _llm_listing_payload_valid(raw):
                return raw
            raise ValueError(f"LLM 返回空或过短 appDesc: {raw!r}")
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "AppGallery 文案 LLM 第 %s/%s 次失败: %s",
                attempt,
                LLM_LISTING_RETRY_ATTEMPTS,
                exc,
            )
    raise ValueError(f"LLM 多次尝试仍无法生成有效文案: {last_exc}") from last_exc


def _llm_full_listing_analysis(context: dict[str, Any]) -> dict[str, Any]:
    """完整 LLM 分析：分类+标签+文案（旧模式，较慢）。"""
    listing_text = f"{context.get('appName', '')}\n{context.get('contextText', '')}"
    candidate_categories = rank_categories_by_text(
        prepare_tag_matching_text(listing_text),
        prepared=True,
    )
    category_table = format_category_tag_options_for_categories(candidate_categories)
    system = (
        "你是华为 AppGallery HarmonyOS 应用商店上架助手。"
        "根据工程信息，从给定分类/标签表中选择最合适的一项，并撰写中文商店文案。"
        "分类、标签、应用介绍、一句话简介必须一次性在同一条 JSON 中返回。"
        "分类与标签必须使用表中的 ID，且 harmonyChildType 与 kindMainTag 必须来自同一行。"
        "禁止选择标签「AI」(15000007) 与「浏览器」(15000001)。"
        "无明确合适标签时，使用工具/工具（harmonyChildType=10000000, kindMainTag=15000005）。"
        "webview_html 工程须根据「WebView 加载的 HTML 业务内容」判断应用方向，"
        "勿因 Index.ets 中的 WebView/浏览器 壳层代码误判为浏览器类应用。"
        "输出严格 JSON，不要 markdown。"
        f"appDesc 建议 {TARGET_DESC_MIN}-{MAX_DESC_LEN} 字（必须≤{MAX_DESC_LEN}）。"
        f"briefInfo 建议 {TARGET_BRIEF_MIN}-{MAX_BRIEF_LEN} 字（必须≤{MAX_BRIEF_LEN}）。"
        "appDesc 必须基于工程摘要写出具体功能/场景，禁止空泛套话（简洁实用、界面清晰、"
        "操作便捷、体验流畅、欢迎下载 等）。"
        "briefInfo 禁止出现 AI、人工智能、智能助手 等字样。"
        "appDesc 与 briefInfo 均禁止包含 emoji 表情、颜文字、★♥→ 等装饰符号，"
        "只用纯中文（及必要英文/数字）描述功能。"
        "文案应客观、合规，不夸大，不含敏感/违规内容。"
    )
    user = (
        f"工程信息:\n{context['contextText']}\n\n"
        f"候选分类/标签表（TSV，列：二级分类、harmonyChildType、标签、kindMainTag）:\n"
        f"{category_table}\n\n"
        "输出 JSON 示例:\n"
        '{"harmonyChildType":10000004,"kindMainTag":15000075,'
        '"categoryName":"教育","tagName":"学习",'
        '"appDesc":"...","briefInfo":"...","reason":"..."}'
    )
    return request_ollama_json(system, user, num_predict=LLM_LISTING_NUM_PREDICT, timeout=180.0)


def analyze_app_gallery_listing(project_path: str | Path) -> dict[str, Any]:
    """分析工程，返回分类/标签/介绍/一句话简介。"""
    started = time.time()
    mode = _analysis_mode()
    light = mode == "fast"
    steps: list[dict[str, str]] = []

    def _step(name: str, detail: str = "", *, status: str = "done") -> None:
        steps.append({"step": name, "detail": detail, "status": status})
        report_tool_progress(name, step=name, detail=detail, status=status)

    _step("收集工程上下文", f"分析模式={mode}", status="running")
    context = collect_project_marketing_context(project_path, light=light)
    collect_ms = int((time.time() - started) * 1000)
    _step(
        "收集工程上下文",
        f"应用={context.get('appName')}，摘要 {len(context.get('contextText') or '')} 字，耗时 {collect_ms}ms",
    )

    logger.info(
        "AppGallery 工程上下文已收集: mode=%s chars=%s collectMs=%s",
        mode,
        len(context.get("contextText") or ""),
        collect_ms,
    )

    if mode == "heuristic":
        _step("关键词启发式分析", "跳过 LLM")
        result = _sanitize_analysis_tags(_heuristic_fallback(context))
        result["source"] = "heuristic"
        result["steps"] = steps
        logger.info("AppGallery 启发式分析完成: totalMs=%s", int((time.time() - started) * 1000))
        log_analysis_result(result)
        return result

    if mode == "fast":
        _step("关键词匹配分类/标签", status="running")
        picked = _keyword_category_result(context)
        _step(
            "关键词匹配分类/标签",
            f"{picked['categoryName']} / {picked['tagName']} (kindMainTag={picked['kindMainTag']})",
        )
        llm_started = time.time()
        _step("LLM 生成商店文案", "调用本地 Ollama…", status="running")
        llm_warning = ""
        try:
            raw = _request_listing_copy_llm(
                context,
                category_name=picked["categoryName"],
                tag_name=picked["tagName"],
            )
            llm_ms = int((time.time() - llm_started) * 1000)
            _step("LLM 生成商店文案", f"完成，耗时 {llm_ms}ms")
            logger.info(
                "AppGallery fast LLM 文案完成: llmMs=%s totalMs=%s",
                llm_ms,
                int((time.time() - started) * 1000),
            )
        except Exception as exc:
            llm_warning = str(exc)
            _step("LLM 生成商店文案", f"失败，使用工程上下文兜底: {exc}")
            logger.warning("AppGallery fast 文案 LLM 失败，使用兜底: %s", exc)
            fallback = _heuristic_fallback(context)
            fallback["harmonyChildType"] = picked["harmonyChildType"]
            fallback["kindMainTag"] = picked["kindMainTag"]
            fallback["categoryName"] = picked["categoryName"]
            fallback["tagName"] = picked["tagName"]
            fallback["source"] = "keyword+heuristic"
            fallback["llmWarning"] = llm_warning
            fallback = _sanitize_analysis_tags(fallback)
            fallback["steps"] = steps
            log_analysis_result(fallback)
            return fallback

        app_desc = _sanitize_generic_desc(str(raw.get("appDesc") or ""), context)
        if not app_desc:
            llm_warning = llm_warning or "LLM 返回的 appDesc 经校验后为空，已使用工程上下文兜底"
            app_desc = _build_context_aware_desc(context)
        brief_info = _truncate(str(raw.get("briefInfo") or picked["tagName"]), MAX_BRIEF_LEN)
        if not brief_info:
            brief_info = _truncate(str(context.get("appName") or picked["tagName"]), MAX_BRIEF_LEN)

        result = _build_analysis_result(
            harmony_child_type=picked["harmonyChildType"],
            kind_main_tag=picked["kindMainTag"],
            category_name=picked["categoryName"],
            tag_name=picked["tagName"],
            app_desc=app_desc,
            brief_info=brief_info,
            reason=str(raw.get("reason") or "关键词匹配分类，LLM 生成文案"),
            source="keyword+llm" if not llm_warning else "keyword+llm+fallback",
            context=context,
            raw_llm=raw,
            allow_desc_fallback=False,
        )
        if llm_warning:
            result["llmWarning"] = llm_warning
        result = _sanitize_analysis_tags(result)
        result["steps"] = steps
        log_analysis_result(result)
        return result

    _step("LLM 全量分析", "分类+标签+文案（较慢）", status="running")
    llm_started = time.time()
    try:
        raw = _llm_full_listing_analysis(context)
        logger.info(
            "AppGallery LLM 全量分析完成: llmMs=%s totalMs=%s",
            int((time.time() - llm_started) * 1000),
            int((time.time() - started) * 1000),
        )
        _step(
            "LLM 全量分析",
            f"完成，耗时 {int((time.time() - llm_started) * 1000)}ms",
        )
        logger.info("AppGallery LLM 原始响应: %s", json.dumps(raw, ensure_ascii=False))
    except Exception as exc:
        _step("LLM 全量分析", f"失败，使用兜底: {exc}")
        logger.warning("AppGallery 文案 LLM 分析失败，使用兜底: %s", exc)
        result = _sanitize_analysis_tags(_heuristic_fallback(context))
        result["steps"] = steps
        log_analysis_result(result)
        return result

    try:
        harmony_child_type, kind_main_tag = _parse_llm_ids(raw)
        entry = ensure_allowed_auto_tag(harmony_child_type, kind_main_tag)
    except Exception as exc:
        logger.warning("LLM 分类/标签无效，使用兜底: %s | raw=%s", exc, raw)
        result = _sanitize_analysis_tags(_heuristic_fallback(context))
        result["steps"] = steps
        log_analysis_result(result)
        return result

    app_desc = _sanitize_generic_desc(str(raw.get("appDesc") or ""), context)
    if not app_desc:
        app_desc = _build_context_aware_desc(context)
    brief_info = _truncate(str(raw.get("briefInfo") or entry["tag"]), MAX_BRIEF_LEN)
    if not brief_info:
        brief_info = _truncate(str(context.get("appName") or entry["tag"]), MAX_BRIEF_LEN)

    result = _build_analysis_result(
        harmony_child_type=int(entry["secondCategoryId"]),
        kind_main_tag=int(entry["tagId"]),
        category_name=entry["category"],
        tag_name=entry["tag"],
        app_desc=app_desc,
        brief_info=brief_info,
        reason=str(raw.get("reason") or ""),
        source="llm",
        context=context,
        raw_llm=raw,
        allow_desc_fallback=False,
    )
    result = _sanitize_analysis_tags(result)
    result["steps"] = steps
    log_analysis_result(result)
    return result
