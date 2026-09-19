"""分析 HarmonyOS 工程，生成 AppGallery 商店文案与分类/标签。"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.tools.app_gallery_category_tags import (
    format_category_tag_options_for_categories,
    rank_categories_by_text,
    validate_category_tag,
)
from app.tools.harmony_llm_planner import request_ollama_json
from app.tools.harmony_project_detect import (
    ProjectKind,
    read_app_display_name,
    read_bundle_name,
    resolve_project_paths,
)

logger = logging.getLogger("haa.app_info_analyzer")

MAX_DESC_LEN = 500
MAX_BRIEF_LEN = 15
MAX_FILE_CHARS = 1800
MAX_CONTEXT_CHARS = 10_000
MAX_SOURCE_FILES = 4

_SKIP_PATH_PARTS = (
    "/test/",
    "/tests/",
    "/mock/",
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


def collect_project_marketing_context(project_path: str | Path) -> dict[str, Any]:
    """收集用于生成商店文案的工程上下文。"""
    resolved = resolve_project_paths(project_path)
    ohos_root = resolved["ohosRoot"]
    assert isinstance(ohos_root, Path)
    kind = resolved["projectKind"]
    assert isinstance(kind, ProjectKind)
    flutter_root = resolved.get("flutterRoot")
    if flutter_root is not None and not isinstance(flutter_root, Path):
        flutter_root = None

    app_name = read_app_display_name(ohos_root)
    bundle_name = read_bundle_name(ohos_root)
    parts: list[str] = [
        f"应用名称: {app_name}",
        f"包名: {bundle_name}",
        f"工程类型: {kind.value}",
    ]

    string_json = ohos_root / "AppScope/resources/base/element/string.json"
    if string_json.is_file():
        parts.append(f"=== string.json ===\n{_read_excerpt(string_json, 3000)}")

    pages = _collect_native_pages(ohos_root)
    if pages:
        parts.append("页面列表: " + ", ".join(pages))

    added_sources = 0
    for page_route in pages:
        if added_sources >= MAX_SOURCE_FILES:
            break
        source = _resolve_page_source(ohos_root, page_route)
        if source is None:
            continue
        rel = source.relative_to(ohos_root).as_posix()
        parts.append(f"=== {rel} ===\n{_read_excerpt(source, MAX_FILE_CHARS)}")
        added_sources += 1

    if added_sources == 0:
        for pattern in ("entry/src/main/ets/pages/*.ets", "entry/src/main/ets/*Page.ets"):
            for path in sorted(ohos_root.glob(pattern)):
                if added_sources >= MAX_SOURCE_FILES or _should_skip_source(path):
                    continue
                rel = path.relative_to(ohos_root).as_posix()
                parts.append(f"=== {rel} ===\n{_read_excerpt(path, MAX_FILE_CHARS)}")
                added_sources += 1

    if flutter_root and flutter_root.is_dir():
        pubspec = flutter_root / "pubspec.yaml"
        if pubspec.is_file():
            parts.append(f"=== pubspec.yaml ===\n{_read_excerpt(pubspec, 1200)}")
        main_dart = flutter_root / "lib/main.dart"
        if main_dart.is_file():
            parts.append(f"=== lib/main.dart ===\n{_read_excerpt(main_dart, MAX_FILE_CHARS)}")

    readme = resolved["inputPath"]
    if isinstance(readme, Path):
        for name in ("README.md", "readme.md", "README.txt"):
            candidate = readme / name
            if candidate.is_file():
                parts.append(f"=== {name} ===\n{_read_excerpt(candidate, 2000)}")
                break

    context_text = "\n\n".join(parts)
    if len(context_text) > MAX_CONTEXT_CHARS:
        context_text = context_text[: MAX_CONTEXT_CHARS // 2] + "\n\n/* ... truncated ... */\n\n" + context_text[-MAX_CONTEXT_CHARS // 2 :]

    return {
        "appName": app_name,
        "bundleName": bundle_name,
        "projectKind": kind.value,
        "ohosRoot": str(ohos_root),
        "contextText": context_text,
    }


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit]


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
) -> dict[str, Any]:
    return {
        "harmonyChildType": harmony_child_type,
        "kindMainTag": kind_main_tag,
        "categoryName": category_name,
        "tagName": tag_name,
        "appDesc": app_desc,
        "briefInfo": brief_info,
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

    if any(keyword in app_name + pages for keyword in ("词汇", "单词", "成语", "语言", "翻译", "英文", "学习")):
        entry = validate_category_tag(10000004, 15000075)
        harmony_child_type, kind_main_tag = 10000004, 15000075
    elif any(keyword in app_name + pages for keyword in ("记账", "理财", "股票", "基金")):
        entry = validate_category_tag(10000003, 15000048)
        harmony_child_type, kind_main_tag = 10000003, 15000048
    elif any(keyword in app_name + pages for keyword in ("健康", "运动", "计步", "养生")):
        entry = validate_category_tag(10000012, 15000018)
        harmony_child_type, kind_main_tag = 10000012, 15000018
    else:
        entry = validate_category_tag(10000000, 15000005)
        harmony_child_type, kind_main_tag = 10000000, 15000005

    return _build_analysis_result(
        harmony_child_type=harmony_child_type,
        kind_main_tag=kind_main_tag,
        category_name=entry["category"],
        tag_name=entry["tag"],
        app_desc=_truncate(
            f"{app_name}是一款简洁实用的鸿蒙应用，界面清晰、操作便捷，"
            f"帮助用户高效完成日常任务，提升使用体验。",
            MAX_DESC_LEN,
        ),
        brief_info=_truncate(app_name, MAX_BRIEF_LEN),
        reason="LLM 不可用，使用关键词启发式兜底",
        source="heuristic",
        context=context,
    )


def _parse_llm_ids(raw: dict[str, Any]) -> tuple[int, int]:
    harmony_child_type = raw.get("harmonyChildType", raw.get("secondCategoryId"))
    kind_main_tag = raw.get("kindMainTag", raw.get("tagId"))
    if harmony_child_type is None or kind_main_tag is None:
        raise ValueError("LLM 响应缺少 harmonyChildType / kindMainTag")
    return int(harmony_child_type), int(kind_main_tag)


def analyze_app_gallery_listing(project_path: str | Path) -> dict[str, Any]:
    """用 LLM 分析工程，返回分类/标签/介绍/一句话简介（单次 LLM 调用）。"""
    started = time.time()
    context = collect_project_marketing_context(project_path)
    collect_ms = int((time.time() - started) * 1000)

    candidate_categories = rank_categories_by_text(
        f"{context.get('appName', '')}\n{context.get('contextText', '')}"
    )
    category_table = format_category_tag_options_for_categories(candidate_categories)
    logger.info(
        "AppGallery 工程上下文已收集: chars=%s files~=%s candidateCategories=%s collectMs=%s",
        len(context.get("contextText") or ""),
        min(MAX_SOURCE_FILES, len(candidate_categories) + 2),
        candidate_categories,
        collect_ms,
    )

    system = (
        "你是华为 AppGallery HarmonyOS 应用商店上架助手。"
        "根据工程信息，从给定分类/标签表中选择最合适的一项，并撰写中文商店文案。"
        "分类、标签、应用介绍、一句话简介必须一次性在同一条 JSON 中返回。"
        "分类与标签必须使用表中的 ID，且 harmonyChildType 与 kindMainTag 必须来自同一行。"
        "输出严格 JSON，不要 markdown。"
        f"appDesc 不超过 {MAX_DESC_LEN} 字，briefInfo 不超过 {MAX_BRIEF_LEN} 字。"
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

    llm_started = time.time()
    try:
        raw = request_ollama_json(system, user)
        logger.info(
            "AppGallery LLM 分析完成: llmMs=%s totalMs=%s",
            int((time.time() - llm_started) * 1000),
            int((time.time() - started) * 1000),
        )
        logger.info("AppGallery LLM 原始响应: %s", json.dumps(raw, ensure_ascii=False))
    except Exception as exc:
        logger.warning("AppGallery 文案 LLM 分析失败，使用兜底: %s", exc)
        result = _heuristic_fallback(context)
        log_analysis_result(result)
        return result

    try:
        harmony_child_type, kind_main_tag = _parse_llm_ids(raw)
        entry = validate_category_tag(harmony_child_type, kind_main_tag)
    except Exception as exc:
        logger.warning("LLM 分类/标签无效，使用兜底: %s | raw=%s", exc, raw)
        result = _heuristic_fallback(context)
        log_analysis_result(result)
        return result

    app_desc = _truncate(str(raw.get("appDesc") or ""), MAX_DESC_LEN)
    brief_info = _truncate(str(raw.get("briefInfo") or entry["tag"]), MAX_BRIEF_LEN)
    if not app_desc:
        app_desc = _heuristic_fallback(context)["appDesc"]
    if not brief_info:
        brief_info = _truncate(str(context.get("appName") or entry["tag"]), MAX_BRIEF_LEN)

    result = _build_analysis_result(
        harmony_child_type=harmony_child_type,
        kind_main_tag=kind_main_tag,
        category_name=entry["category"],
        tag_name=entry["tag"],
        app_desc=app_desc,
        brief_info=brief_info,
        reason=str(raw.get("reason") or ""),
        source="llm",
        context=context,
        raw_llm=raw,
    )
    log_analysis_result(result)
    return result
