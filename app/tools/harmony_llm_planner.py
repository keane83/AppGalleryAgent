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

from app.config import (
    LLM_API_BASE_URL,
    LLM_API_KEY,
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    OLLAMA_TOOL_MODEL,
    _PROJECT_ROOT,
)
from app.llm_factory import get_active_model_name, is_api_provider, validate_llm_config

logger = logging.getLogger("haa.llm_planner")

MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 48000


def _llm_enabled() -> bool:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    return os.getenv("HARMONY_LLM_PLANNER", "false").lower() in {"1", "true", "yes", "on"}


def _webview_llm_auto_enabled() -> bool:
    """WebView 启发式规划不足时，是否自动用 LLM 分析 HTML（默认开启）。"""
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    return os.getenv("HARMONY_WEBVIEW_LLM_AUTO", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


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


def _extract_webview_html_for_llm(html_path: Path, *, limit: int = 28000) -> str:
    """提取 HTML 中与导航/切页相关的片段，避免纯 CSS 占满上下文。"""
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    chunks: list[str] = []

    for pattern in (
        r"<nav[\s\S]*?</nav>",
        r'<div[^>]*class="[^"]*bottom-nav[^"]*"[\s\S]*?</div>',
        r'<button[^>]*data-tab=[^>]*>[\s\S]*?</button>',
        r"<script[\s\S]*?</script>",
    ):
        for match in re.finditer(pattern, text, re.I):
            chunk = match.group(0).strip()
            if chunk and chunk not in chunks:
                chunks.append(chunk)

    if not chunks:
        return _read_excerpt(html_path, limit)

    merged = "\n\n/* --- extracted nav/script --- */\n\n".join(chunks)
    if len(merged) <= limit:
        return merged
    half = limit // 2
    return merged[:half] + "\n\n/* ... truncated ... */\n\n" + merged[-half:]


def collect_webview_context(ohos_root: Path, html_path: Path, index_ets: Path) -> str:
    html_text = _extract_webview_html_for_llm(html_path)
    index_text = _read_excerpt(index_ets, 4000)
    return (
        f"=== Index.ets ===\n{index_text}\n\n"
        f"=== {html_path.name} (nav/script excerpts) ===\n{html_text}\n"
    )


def request_llm_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    num_predict: int | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """按 LLM_PROVIDER 调用 Ollama 或 DeepSeek API，并解析 JSON 响应。"""
    if is_api_provider():
        return _openai_compatible_json(
            system,
            user,
            model=model,
            num_predict=num_predict,
            timeout=timeout,
        )
    return _ollama_json(
        system,
        user,
        model=model,
        num_predict=num_predict,
        timeout=timeout,
    )


def request_ollama_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    num_predict: int | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """兼容旧名；实际走 request_llm_json。"""
    return request_llm_json(
        system,
        user,
        model=model,
        num_predict=num_predict,
        timeout=timeout,
    )


def _normalize_api_base_url(base_url: str) -> str:
    """DeepSeek 等兼容接口需要 …/v1/chat/completions。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return "https://api.deepseek.com/v1"
    if base.endswith("/v1"):
        return base
    if "deepseek.com" in base and "/v1" not in base:
        return f"{base}/v1"
    return base


def _parse_json_object(content: str, *, source: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError(f"{source} 返回空 content")

    # 允许偶发 markdown 包裹
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError(f"{source} 返回非 JSON 内容：{text[:240]}") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source} 返回非 JSON 内容：{text[:240]}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{source} JSON 根节点必须是 object：{type(parsed).__name__}")
    return parsed


def _openai_compatible_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    num_predict: int | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    config_error = validate_llm_config()
    if config_error:
        raise ValueError(config_error)

    model_name = model or get_active_model_name(for_tool=True)
    # deepseek-flash 等可能把大量 token 用在 reasoning，content 易被 length 截断
    token_budget = max(int(num_predict or 1600), 1600)
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{_normalize_api_base_url(LLM_API_BASE_URL)}/chat/completions"
    last_error: Exception | None = None

    for attempt, budget in enumerate((token_budget, token_budget * 2, max(token_budget * 3, 4096)), start=1):
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": min(OLLAMA_TEMPERATURE, 0.3),
            "stream": False,
            "max_tokens": budget,
            # deepseek-flash/pro 默认 thinking=on，推理 token 会计入 max_tokens，
            # 易出现 finish_reason=length 且 content 为空。规划 JSON 必须关闭。
            "thinking": {"type": "disabled"},
        }
        logger.info(
            "工具内 LLM JSON 调用：provider=api model=%s max_tokens=%s attempt=%s",
            model_name,
            budget,
            attempt,
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"API 返回空 choices：{json.dumps(data, ensure_ascii=False)[:240]}")

        choice = choices[0]
        finish_reason = str(choice.get("finish_reason") or "")
        message = choice.get("message") or {}
        content = str(message.get("content") or "").strip()
        reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        ).strip()
        usage = data.get("usage") or {}

        if content:
            try:
                return _parse_json_object(content, source="API")
            except ValueError as exc:
                last_error = exc
                # 截断导致 JSON 不完整时加大预算重试
                if finish_reason == "length" and attempt < 3:
                    logger.warning(
                        "API JSON 不完整（finish_reason=length），加大 max_tokens 重试；usage=%s",
                        usage,
                    )
                    continue
                raise

        # content 空：尝试从 reasoning 抠 JSON
        if reasoning:
            try:
                return _parse_json_object(reasoning, source="API(reasoning)")
            except ValueError:
                pass

        last_error = ValueError(
            "API 返回空 content"
            f"（finish_reason={finish_reason}, reasoning_chars={len(reasoning)}, usage={usage}）"
        )
        if finish_reason == "length" and attempt < 3:
            logger.warning("%s；加大 max_tokens 重试", last_error)
            continue
        raise last_error

    assert last_error is not None
    raise last_error


def _ollama_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    num_predict: int | None = None,
    timeout: float = 180.0,
    think: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": min(OLLAMA_TEMPERATURE, 0.3)}
    if num_predict is not None:
        options["num_predict"] = num_predict

    model_name = model or OLLAMA_TOOL_MODEL or OLLAMA_MODEL
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        # 规划必须拿 content JSON；thinking 开启时部分模型会把结果塞进 thinking、content 为空
        "think": think,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": options,
    }
    logger.info("工具内 LLM JSON 调用：provider=ollama model=%s think=%s", model_name, think)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    message = data.get("message") or {}
    content = str(message.get("content") or "").strip()
    thinking = str(message.get("thinking") or "").strip()
    if not content and thinking:
        # 兜底：少数 reasoning 模型仍把 JSON 写进 thinking
        try:
            return _parse_json_object(thinking, source="Ollama(thinking)")
        except ValueError:
            pass
    if not content:
        done_reason = data.get("done_reason")
        raise ValueError(
            "Ollama 返回空 content"
            f"（done_reason={done_reason}, thinking_chars={len(thinking)}）。"
            "截图规划请关闭 OLLAMA_REASONING，或改用 LLM_PROVIDER=deepseek。"
        )
    return _parse_json_object(content, source="Ollama")


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
        "Flutter OHOS 通过冷启动 Want 参数 harmonyAutoTab 运行时切 Tab。"
        "patchTargetFile 必须是含 int _index Tab 状态字段的 StatefulWidget 文件（相对 Flutter 根目录）。"
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
        raw = request_llm_json(system, user, num_predict=2048, timeout=120.0)
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
    auto_fallback: bool = False,
) -> dict[str, Any] | None:
    if auto_fallback:
        if not _webview_llm_auto_enabled():
            return None
    elif not _llm_enabled():
        return None

    context = collect_webview_context(ohos_root, html_path, index_ets)
    system = (
        "你是 HarmonyOS WebView 本地 HTML 应用商店截图规划器。"
        "根据 HTML/JS 规划商店介绍截图。只输出紧凑 JSON，不要 markdown，不要解释。"
        "script 必须是可在 WebView 内直接执行的 JS：优先 querySelector + click()；"
        "支持 data-tab 导航；避免调用 HTML 内闭包函数。"
        "第一个截图若已是默认页，script 可为空字符串。"
        f"screenshots 恰好 {count} 项；每项字段仅 slug/label/script/reason/waitSec。"
    )
    user = (
        f"OHOS: {ohos_root}\nHTML: {html_path.name}\n需要 {count} 张不同截图。\n\n"
        f"{context}\n\n"
        '输出示例:{"screenshots":[{"slug":"home","label":"首页","script":"",'
        '"reason":"默认首页","waitSec":9},'
        '{"slug":"shops","label":"店铺","script":"document.querySelector(\'.nav-item[data-tab=\\"shops\\"]\')?.click();",'
        '"reason":"底部导航","waitSec":5.5}]}'
    )
    try:
        provider = "api" if is_api_provider() else "ollama"
        logger.info(
            "WebView 截图规划：调用 LLM 分析 HTML（provider=%s auto_fallback=%s）",
            provider,
            auto_fallback,
        )
        raw = request_llm_json(system, user, num_predict=2048, timeout=120.0)
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
