"""WebView + 本地 HTML 工程的截图规划（启发式 + LLM）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.tools.harmony_llm_planner import plan_webview_with_llm
from app.tools.harmony_project_detect import find_index_ets


def _extract_rawfile_html(index_ets: Path) -> str:
    text = index_ets.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"\$rawfile\(\s*['\"]([^'\"]+\.html?)['\"]\s*\)", text, re.I)
    if not match:
        raise ValueError(f"Index.ets 中未找到 rawfile HTML：{index_ets}")
    return match.group(1)


def _parse_html_tabs(html_text: str) -> list[dict[str, str]]:
    tabs: list[dict[str, str]] = []
    seen: set[str] = set()

    for tab_id, tab_key in re.findall(
        r"(tab\w+)\.addEventListener\(\s*'click'\s*,\s*\(\)\s*=>\s*switchTab\(\s*['\"]([^'\"]+)['\"]\s*\)",
        html_text,
    ):
        if tab_key in seen:
            continue
        seen.add(tab_key)
        label = tab_key
        button_match = re.search(rf'id="{re.escape(tab_id)}"[\s\S]*?</button>', html_text)
        if button_match:
            spans = re.findall(r"<span[^>]*>\s*([^<]+?)\s*</span>", button_match.group(0))
            text_spans = [
                s.strip()
                for s in spans
                if s.strip() and (not s.strip().isascii() or len(s.strip()) > 1)
            ]
            if text_spans:
                label = text_spans[-1]
        script = "" if len(tabs) == 0 else f"document.getElementById('{tab_id}')?.click();"
        tabs.append(
            {
                "slug": tab_key,
                "label": label,
                "script": script,
                "reason": f"HTML Tab「{label}」",
                "tabId": tab_id,
                "waitSec": 9.0 if len(tabs) == 0 else 5.5,
            }
        )

    if tabs:
        return tabs

    page_ids = re.findall(r'<div[^>]*class="[^"]*page[^"]*"[^>]*id="([^"]+)"', html_text, re.I)
    for page_id in page_ids:
        slug = page_id.replace("Page", "").lower()
        tabs.append(
            {
                "slug": slug,
                "label": page_id,
                "script": f"document.getElementById('{page_id}')?.classList.add('active')",
                "reason": f"HTML 页面 #{page_id}",
                "waitSec": 9.0 if len(tabs) == 0 else 5.5,
            }
        )
    return tabs


def _default_action_scripts(html_text: str) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if re.search(r"id=[\"']decideBtn[\"']", html_text):
        actions.append(
            {
                "slug": "result_popup",
                "label": "结果弹窗",
                "script": (
                    "document.getElementById('tabCreate')?.click();"
                    "setTimeout(function(){document.getElementById('decideBtn')?.click();},600);"
                ),
                "reason": "触发决策结果弹窗",
                "waitSec": 6.0,
            }
        )
    return actions


def _heuristic_plan(ohos_root: Path, *, count: int = 3) -> dict[str, object]:
    index_ets = find_index_ets(ohos_root)
    html_name = _extract_rawfile_html(index_ets)
    html_path = ohos_root / "entry/src/main/resources/rawfile" / html_name
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML 资源不存在：{html_path}")

    html_text = html_path.read_text(encoding="utf-8", errors="ignore")
    tabs = _parse_html_tabs(html_text)
    scripts: list[dict[str, str]] = list(tabs)

    if len(scripts) < count:
        for action in _default_action_scripts(html_text):
            if all(item["slug"] != action["slug"] for item in scripts):
                scripts.append(action)
            if len(scripts) >= count:
                break

    unique_target = min(count, max(len(scripts), 1))
    selected = scripts[:unique_target]

    return {
        "mode": "webview_html",
        "planSource": "heuristic",
        "htmlFile": html_name,
        "htmlPath": str(html_path),
        "targetDeliverCount": count,
        "uniqueCaptureCount": len(selected),
        "screenshots": [
            {
                "slug": item["slug"],
                "label": item.get("label", item["slug"]),
                "script": item["script"],
                "scriptIdx": idx,
                "waitSec": float(item.get("waitSec", 5.5 if idx else 9.0)),
                "reason": item.get("reason", ""),
                "outputFile": f"{idx + 1:02d}_{item['slug']}.png",
            }
            for idx, item in enumerate(selected)
        ],
        "scriptsJson": [item["script"] for item in selected],
    }


def _needs_llm(heuristic: dict[str, object], *, count: int = 3) -> bool:
    shots = heuristic.get("screenshots") or []
    unique_slugs = {s.get("slug") for s in shots if isinstance(s, dict)}
    return len(unique_slugs) < min(2, count) or len(shots) < count


def plan_webview_screenshots(ohos_root: Path, *, count: int = 3) -> dict[str, object]:
    heuristic = _heuristic_plan(ohos_root, count=count)

    if not _needs_llm(heuristic, count=count):
        return heuristic

    llm_plan = plan_webview_with_llm(
        ohos_root,
        Path(str(heuristic["htmlPath"])),
        find_index_ets(ohos_root),
        count=count,
    )
    if llm_plan:
        return llm_plan

    return heuristic


def write_webview_scripts_file(ohos_root: Path, scripts: list[str]) -> Path:
    rawfile_dir = ohos_root / "entry/src/main/resources/rawfile"
    rawfile_dir.mkdir(parents=True, exist_ok=True)
    target = rawfile_dir / "harmony_auto_screenshot_scripts.json"
    target.write_text(json.dumps(scripts, ensure_ascii=False), encoding="utf-8")
    return target
