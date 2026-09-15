"""识别 HarmonyOS 工程类型并解析 ohos / flutter 根目录。"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


class ProjectKind(str, Enum):
    NATIVE_ARKTS = "native_arkts"
    WEBVIEW_HTML = "webview_html"
    FLUTTER_OHOS = "flutter_ohos"


def resolve_project_paths(project_path: Path | str) -> dict[str, Path | ProjectKind | None]:
    """解析用户传入路径，返回 ohos_root、flutter_root、project_kind。"""
    raw = Path(project_path).expanduser().resolve()
    if not raw.is_dir():
        raise NotADirectoryError(f"工程目录不存在：{raw}")

    ohos_root: Path | None = None
    flutter_root: Path | None = None

    if (raw / "AppScope" / "app.json5").is_file():
        ohos_root = raw
    elif (raw / "ohos" / "AppScope" / "app.json5").is_file():
        ohos_root = raw / "ohos"
        flutter_root = raw
    else:
        for candidate in raw.glob("**/AppScope/app.json5"):
            ohos_root = candidate.parent.parent
            break

    if ohos_root is None:
        raise FileNotFoundError(f"未找到 AppScope/app.json5：{raw}")

    if (ohos_root.parent / "pubspec.yaml").is_file():
        flutter_root = ohos_root.parent
    elif flutter_root is None:
        for pubspec in raw.rglob("pubspec.yaml"):
            if (pubspec.parent / "ohos" / "AppScope" / "app.json5").is_file():
                flutter_root = pubspec.parent
                ohos_root = pubspec.parent / "ohos"
                break

    kind = detect_project_kind(ohos_root)
    return {
        "inputPath": raw,
        "ohosRoot": ohos_root,
        "flutterRoot": flutter_root,
        "projectKind": kind,
    }


def detect_project_kind(ohos_root: Path) -> ProjectKind:
    entry_ability = find_entry_ability(ohos_root)
    ability_text = entry_ability.read_text(encoding="utf-8", errors="ignore")
    if "FlutterAbility" in ability_text or "@ohos/flutter_ohos" in ability_text:
        return ProjectKind.FLUTTER_OHOS

    index_ets = ohos_root / "entry/src/main/ets/pages/Index.ets"
    if index_ets.is_file():
        index_text = index_ets.read_text(encoding="utf-8", errors="ignore")
        if "$rawfile(" in index_text and ".html" in index_text.lower():
            return ProjectKind.WEBVIEW_HTML
        if "FlutterPage" in index_text:
            return ProjectKind.FLUTTER_OHOS

    return ProjectKind.NATIVE_ARKTS


def find_entry_ability(ohos_root: Path) -> Path:
    candidates = list(ohos_root.glob("entry/src/main/ets/**/EntryAbility.ets"))
    if not candidates:
        raise FileNotFoundError(f"未找到 EntryAbility.ets：{ohos_root}")
    for path in candidates:
        if "entryability" in str(path).lower():
            return path
    return candidates[0]


def find_index_ets(ohos_root: Path) -> Path:
    path = ohos_root / "entry/src/main/ets/pages/Index.ets"
    if not path.is_file():
        raise FileNotFoundError(f"未找到 Index.ets：{path}")
    return path


def read_bundle_name(ohos_root: Path) -> str:
    app_json = ohos_root / "AppScope/app.json5"
    text = app_json.read_text(encoding="utf-8")
    match = re.search(r'"bundleName"\s*:\s*"([^"]+)"', text)
    if not match:
        raise ValueError(f"无法解析 bundleName：{app_json}")
    return match.group(1)


def read_app_display_name(ohos_root: Path) -> str:
    import json

    string_json = ohos_root / "AppScope/resources/base/element/string.json"
    if string_json.is_file():
        data = json.loads(string_json.read_text(encoding="utf-8"))
        for item in data.get("string", []):
            if item.get("name") == "app_name" and item.get("value"):
                return str(item["value"]).strip()
    bundle = read_bundle_name(ohos_root)
    return bundle.split(".")[-1] or bundle
