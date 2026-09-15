"""使用本地 Ollama 分析 Flutter / WebView 工程，规划截图方案。"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TEMPERATURE, _PROJECT_ROOT

logger = logging.getLogger("haa.llm_planner")

MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 48000


def _llm_enabled() -> bool:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    return os.getenv("HARMONY_LLM_PLANNER", "true").lower() in {"1", "true", "yes", "on"}


def _read_excerpt(path: Path, limit: int = MAX_FILE_CHARS) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n/* ... truncated ... */\n" + text[-limit // 2 :]


def _collect_dart_files(flutter_root: Path) -> list[Path]:
    lib_dir = flutter_root / "lib"
    if not lib_dir.is_dir():
        return []
    files: list[Path] = []
    for name in ("main.dart",):
        p = lib_dir / name
        if p.is_file():
            files.append(p)
    for pattern in ("**/*shell*.dart", "**/*root*.dart", "**/*nav*.dart", "**/main.dart"):
        for p in lib_dir.glob(pattern):
            if p.is_file() and p not in files:
                files.append(p)
    for p in sorted(lib_dir.rglob("*.dart")):
        if p not in files and len(files) < 14:
            text = p.read_text(encoding="utf-8", errors="ignore")
            if any(k in text for k in ("IndexedStack", "BottomNavigationBar", "_NavItem", "currentIndex", "GoRouter", "routes:")):
                files.append(p)
    return files[:16]


def collect_flutter_context(flutter_root: Path) -> str:
    parts: list[str] = []
    total = 0
    for path in _collect_dart_files(flutter_root):
        rel = path.relative_to(flutter_root).as_posix()
        chunk = f"=== {rel} ===\n{_read_excerpt(path, 8000)}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)


def collect_webview_context(ohos_root: Path, html_path: Path, index_ets: Path) -> str:
    html_text = _read_excerpt(html_path, MAX_FILE_CHARS)
    index_text = _read_excerpt(index_ets, 4000)
    return (
        f"=== Index.ets ===\n{index_text}\n\n"
        f"=== {html_path.name} ===\n{html_text}\n"
    )


def _ollama_json(system: str, user: str) -> dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": min(OLLAMA_TEMPERATURE, 0.3)},
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = (data.get("message") or {}).get("content") or "{}"
    return json.loads(content)


def _slugify(label: str, fallback: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", label.strip()).strip("_")
    return slug or fallback


def plan_flutter_with_llm(flutter_root: Path, *, count: int = 3) -> dict[str, Any] | None:
    if not _llm_enabled():
        return None
    context = collect_flutter_context(flutter_root)
    if not context.strip():
        return None

    system = (
        "你是 HarmonyOS Flutter 应用商店截图规划器。"
        "根据 Dart 源码分析 Tab/页面结构，输出严格 JSON（不要 markdown）。"
        "Flutter OHOS 只有单页宿主，截图需通过 HARMONY_AUTO_TAB dart-define 指定 Tab 索引。"
        "patchTargetFile 必须是含 int _index  Tab 状态字段的 StatefulWidget 文件（相对 Flutter 根目录）。"
        f"screenshots 数组长度应为 {count}，每项含 tabIndex(0-based)、slug、label、reason。"
    )
    user = (
        f"Flutter 工程根目录: {flutter_root}\n"
        f"需要 {count} 张不同截图。优先选核心功能 Tab，避免弹窗/登录/空白页。\n\n"
        f"{context}\n\n"
        "输出 JSON 格式:\n"
        '{"patchTargetFile":"lib/...dart","tabCount":3,'
        '"screenshots":[{"tabIndex":0,"slug":"home","label":"首页","reason":"..."}]}'
    )
    try:
        raw = _ollama_json(system, user)
    except Exception as exc:
        logger.warning("Flutter LLM 规划失败: %s", exc)
        return None

    shots = raw.get("screenshots") or []
    if not isinstance(shots, list) or not shots:
        return None

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(shots[:count]):
        if not isinstance(item, dict):
            continue
        tab_index = int(item.get("tabIndex", idx))
        label = str(item.get("label") or f"tab_{tab_index}")
        slug = _slugify(str(item.get("slug") or label), f"tab_{tab_index}")
        normalized.append(
            {
                "slug": slug,
                "tabIndex": tab_index,
                "tabLabel": label,
                "reason": str(item.get("reason") or f"LLM 推荐 Tab {label}"),
                "outputFile": f"{idx + 1:02d}_{slug}.png",
            }
        )

    if not normalized:
        return None

    tab_count = int(raw.get("tabCount") or len(normalized))
    patch_file = str(raw.get("patchTargetFile") or "").strip()

    return {
        "mode": "flutter_ohos",
        "planSource": "llm",
        "flutterRoot": str(flutter_root),
        "patchTargetFile": patch_file,
        "tabCount": tab_count,
        "tabLabels": [s["tabLabel"] for s in normalized],
        "targetDeliverCount": count,
        "uniqueCaptureCount": len(normalized),
        "screenshots": normalized,
    }


def plan_webview_with_llm(
    ohos_root: Path,
    html_path: Path,
    index_ets: Path,
    *,
    count: int = 3,
) -> dict[str, Any] | None:
    if not _llm_enabled():
        return None
    context = collect_webview_context(ohos_root, html_path, index_ets)
    system = (
        "你是 HarmonyOS WebView 本地 HTML 应用商店截图规划器。"
        "WebView 通过 runJavaScript 切页后截图。输出严格 JSON。"
        "script 必须是可在 WebView 内直接执行的 JS（优先 element.click()，避免依赖闭包内函数）。"
        "第一个截图若对应默认页，script 可为空字符串，waitSec 建议 9。"
        f"screenshots 长度应为 {count}。"
    )
    user = (
        f"OHOS 根目录: {ohos_root}\nHTML: {html_path.name}\n需要 {count} 张不同截图。\n\n"
        f"{context}\n\n"
        "输出 JSON:\n"
        '{"screenshots":[{"slug":"create","label":"创建","script":"document.getElementById(\'tabCreate\')?.click();",'
        '"reason":"...","waitSec":5}]}'
    )
    try:
        raw = _ollama_json(system, user)
    except Exception as exc:
        logger.warning("WebView LLM 规划失败: %s", exc)
        return None

    shots = raw.get("screenshots") or []
    if not isinstance(shots, list) or not shots:
        return None

    normalized: list[dict[str, Any]] = []
    scripts_json: list[str] = []
    for idx, item in enumerate(shots[:count]):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("slug") or f"shot_{idx}")
        slug = _slugify(str(item.get("slug") or label), f"shot_{idx}")
        script = str(item.get("script") or "")
        scripts_json.append(script)
        normalized.append(
            {
                "slug": slug,
                "label": label,
                "script": script,
                "scriptIdx": idx,
                "waitSec": float(item.get("waitSec") or (9.0 if idx == 0 else 5.5)),
                "reason": str(item.get("reason") or f"LLM 推荐「{label}」"),
                "outputFile": f"{idx + 1:02d}_{slug}.png",
            }
        )

    if not normalized:
        return None

    return {
        "mode": "webview_html",
        "planSource": "llm",
        "htmlFile": html_path.name,
        "htmlPath": str(html_path),
        "targetDeliverCount": count,
        "uniqueCaptureCount": len(normalized),
        "screenshots": normalized,
        "scriptsJson": scripts_json,
    }
