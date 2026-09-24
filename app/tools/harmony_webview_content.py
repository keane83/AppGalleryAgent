"""从 WebView 工程提取 HTML 业务文本，供商店分类/文案分析使用。"""

from __future__ import annotations

import re
from pathlib import Path

from app.tools.harmony_project_detect import find_index_ets

WEBVIEW_HTML_MARKER = "=== WebView 加载的 HTML 业务内容 ==="

_RAWFILE_HTML_RE = re.compile(r"\$rawfile\(\s*['\"]([^'\"]+\.html?)['\"]\s*\)", re.I)
_TAG_TEXT_RE = re.compile(
    r"<(title|h1|h2|h3|h4|button|label|span|p|a|li|div|strong|em)[^>]*>([^<]{2,120})</\1>",
    re.I,
)
_ATTR_TEXT_RE = re.compile(
    r'(?:placeholder|aria-label|alt|data-tab|data-page|data-title|title)=["\']([^"\']{2,40})["\']',
    re.I,
)
_CHINESE_PHRASE_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
_STRIP_BLOCK_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_GENERIC_HTML_LABELS = frozenset(
    {
        "首页",
        "我的",
        "设置",
        "返回",
        "确定",
        "取消",
        "提交",
        "加载中",
        "暂无数据",
        "点击",
        "更多",
    }
)


def discover_webview_html_paths(ohos_root: Path) -> list[Path]:
    """定位 WebView 加载的 rawfile HTML（主 HTML + 同目录其余 html）。"""
    ohos_root = ohos_root.expanduser().resolve()
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path.resolve())
        if path.is_file() and key not in seen:
            seen.add(key)
            found.append(path)

    try:
        index_ets = find_index_ets(ohos_root)
        match = _RAWFILE_HTML_RE.search(index_ets.read_text(encoding="utf-8", errors="ignore"))
        if match:
            _add(ohos_root / "entry/src/main/resources/rawfile" / match.group(1))
    except Exception:
        pass

    rawfile_dir = ohos_root / "entry/src/main/resources/rawfile"
    if rawfile_dir.is_dir():
        for path in sorted(rawfile_dir.glob("*.html")):
            _add(path)
        for path in sorted(rawfile_dir.glob("*.htm")):
            _add(path)

    return found


def _normalize_label(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "").strip())


def _is_useful_html_label(text: str) -> bool:
    item = _normalize_label(text)
    if len(item) < 2 or len(item) > 18:
        return False
    if item in _GENERIC_HTML_LABELS:
        return False
    if item.startswith("{{") or (item.isascii() and not item.isdigit()):
        return False
    if any(skip in item for skip in ("function", "var ", "const ", "http", "px", "rgb")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", item))


def extract_html_feature_labels(html_path: Path, *, limit: int = 16) -> list[str]:
    """从 HTML 提取 Tab/按钮/标题等功能标签。"""
    raw = html_path.read_text(encoding="utf-8", errors="ignore")
    labels: list[str] = []
    seen: set[str] = set()

    def _add(raw_label: str) -> None:
        item = _normalize_label(raw_label)
        if not _is_useful_html_label(item) or item in seen:
            return
        seen.add(item)
        labels.append(item)

    title_match = re.search(r"<title[^>]*>([^<]+)</title>", raw, re.I)
    if title_match:
        _add(title_match.group(1))

    meta_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        raw,
        re.I,
    )
    if meta_match:
        for token in re.split(r"[，,。；;|/]", meta_match.group(1)):
            _add(token)
            if len(labels) >= limit:
                return labels[:limit]

    for match in _ATTR_TEXT_RE.finditer(raw):
        _add(match.group(1))
        if len(labels) >= limit:
            return labels[:limit]

    body = _STRIP_BLOCK_RE.sub(" ", raw)
    for match in _TAG_TEXT_RE.finditer(body):
        _add(match.group(2))
        if len(labels) >= limit:
            return labels[:limit]

    for match in _CHINESE_PHRASE_RE.finditer(re.sub(r"<[^>]+>", " ", body)):
        _add(match.group(0))
        if len(labels) >= limit:
            break

    return labels[:limit]


def extract_html_listing_text(html_path: Path, *, limit: int = 8000) -> str:
    """从 HTML 提取可见文案与 Tab 标签，供分类/商店文案分析。"""
    raw = html_path.read_text(encoding="utf-8", errors="ignore")
    chunks: list[str] = [f"HTML 文件: {html_path.name}"]

    title_match = re.search(r"<title[^>]*>([^<]+)</title>", raw, re.I)
    page_title = title_match.group(1).strip() if title_match else ""
    if page_title:
        chunks.append(f"页面标题: {page_title}")

    feature_labels = extract_html_feature_labels(html_path)
    if feature_labels:
        chunks.append("功能标签: " + "、".join(feature_labels[:12]))

    body = _STRIP_BLOCK_RE.sub(" ", raw)
    seen_text: set[str] = set(feature_labels)
    for match in _TAG_TEXT_RE.finditer(body):
        text = re.sub(r"\s+", " ", match.group(2)).strip()
        if not _is_useful_html_label(text) or text in seen_text:
            continue
        seen_text.add(text)
        chunks.append(text)
        if len(seen_text) >= 80:
            break

    merged = "\n".join(chunks)
    if len(merged) <= limit:
        return merged
    half = limit // 2
    return merged[:half] + "\n\n/* ... truncated ... */\n\n" + merged[-half:]


def collect_webview_html_for_listing(
    ohos_root: Path,
    *,
    limit_per_file: int = 6000,
) -> tuple[str, list[str]]:
    """汇总 WebView 工程内 HTML 业务描述文本与功能标签。"""
    paths = discover_webview_html_paths(ohos_root)
    if not paths:
        return "", []

    parts: list[str] = []
    labels: list[str] = []
    seen_labels: set[str] = set()
    for html_path in paths[:3]:
        parts.append(extract_html_listing_text(html_path, limit=limit_per_file))
        for label in extract_html_feature_labels(html_path):
            if label not in seen_labels:
                seen_labels.add(label)
                labels.append(label)
    return "\n\n".join(parts), labels
