"""为 HarmonyOS 工程写入 Signing Config（bundleName + build-profile.json5）。"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import tool

from app.config import _PROJECT_ROOT
from app.tools.harmony_project_detect import resolve_project_paths
from app.tools.harmony_signing_crypto import encrypt_signing_password, is_encrypted_password

BUNDLE_NAME_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+){2,}$"
)
BACKUP_DIR_NAME = ".harmony-auto-backups"


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _backup_file(source: Path, project_root: Path) -> Path:
    """备份到工程根目录 .harmony-auto-backups/，避免 resources 内 .bak 触发资源冲突。"""
    rel = source.relative_to(project_root).as_posix().replace("/", "__")
    destination = _backup_dir(project_root) / f"{rel}.bak"
    shutil.copy2(source, destination)
    return destination


def _cleanup_stray_resource_backups(project_root: Path) -> list[str]:
    """清理历史误放在 resources 下的 .bak（hvigor 会当成重复资源）。"""
    removed: list[str] = []
    for pattern in ("**/resources/**/*.bak", "**/resources/**/*.json.bak"):
        for path in project_root.glob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    return removed


def _reload_signing_env() -> dict[str, str]:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    material_dir = Path(
        os.getenv(
            "HARMONY_SIGNING_MATERIAL_DIR",
            str(_PROJECT_ROOT / "AppAutoBAT"),
        )
    ).resolve()
    return {
        "material_dir": str(material_dir),
        "store_file": os.getenv("HARMONY_SIGNING_STORE_FILE", "appbat123A.p12"),
        "cert_file": os.getenv("HARMONY_SIGNING_CERT_FILE", "appbat123A.cer"),
        "store_password": (os.getenv("HARMONY_SIGNING_STORE_PASSWORD") or "").strip(),
        "key_alias": (os.getenv("HARMONY_SIGNING_KEY_ALIAS") or "appbat123A").strip(),
        "key_password": (os.getenv("HARMONY_SIGNING_KEY_PASSWORD") or "").strip(),
        "sign_alg": os.getenv("HARMONY_SIGNING_SIGN_ALG", "SHA256withECDSA"),
    }


def _resolve_material_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"签名材料不存在：{resolved}")
    return resolved


def _load_signing_material(*, profile_path: str) -> dict[str, str]:
    env = _reload_signing_env()
    material_dir = Path(env["material_dir"])
    store_file = _resolve_material_path(material_dir / env["store_file"])
    cert_file = _resolve_material_path(material_dir / env["cert_file"])
    profile_file = _resolve_material_path(Path(profile_path))

    store_password = env["store_password"]
    key_alias = env["key_alias"]
    key_password = env["key_password"]
    missing = [
        name
        for name, value in (
            ("HARMONY_SIGNING_STORE_PASSWORD", store_password),
            ("HARMONY_SIGNING_KEY_ALIAS", key_alias),
            ("HARMONY_SIGNING_KEY_PASSWORD", key_password),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"请在 .env 中配置：{', '.join(missing)}")

    return {
        "storeFile": str(store_file),
        "storePassword": encrypt_signing_password(material_dir, store_password),
        "keyAlias": key_alias,
        "keyPassword": encrypt_signing_password(material_dir, key_password),
        "signAlg": env["sign_alg"],
        "profile": str(profile_file),
        "certpath": str(cert_file),
    }


def _find_entry_string_json(project_root: Path) -> Path:
    """定位 entry 模块 string.json（默认 entry/src/main/resources/base/element/string.json）。"""
    candidates = [project_root / "entry" / "src" / "main" / "resources" / "base" / "element" / "string.json"]
    try:
        profile_text = (project_root / "build-profile.json5").read_text(encoding="utf-8")
        for match in re.finditer(r'"srcPath"\s*:\s*"([^"]+)"', profile_text):
            src = match.group(1).strip().lstrip("./")
            if src:
                candidates.append(
                    project_root / src / "src" / "main" / "resources" / "base" / "element" / "string.json"
                )
    except OSError:
        pass

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path

    raise ValueError(
        "缺少 entry/src/main/resources/base/element/string.json。"
        "Flutter OHOS 工程请传 Flutter 根目录（含 pubspec.yaml 与 ohos/）或 ohos 子目录，"
        "不要只传不含 AppScope 的上层目录。"
    )


def _validate_project_root(project_path: Path) -> tuple[Path, Path, Path, Path]:
    root = project_path.expanduser().resolve()
    build_profile = root / "build-profile.json5"
    app_json5 = root / "AppScope" / "app.json5"
    app_name_json = root / "AppScope" / "resources" / "base" / "element" / "string.json"
    entry_name_json = _find_entry_string_json(root)
    if not root.is_dir():
        raise ValueError(f"工程目录不存在：{root}")
    if not build_profile.is_file():
        raise ValueError(f"缺少 build-profile.json5：{build_profile}")
    if not app_json5.is_file():
        raise ValueError(f"缺少 AppScope/app.json5：{app_json5}")
    if not app_name_json.is_file():
        raise ValueError(f"缺少 AppScope/resources/base/element/string.json：{app_name_json}")
    return build_profile, app_json5, app_name_json, entry_name_json


def _replace_bundle_name(content: str, bundle_name: str) -> str:
    pattern = re.compile(r'("bundleName"\s*:\s*")[^"]*(")')
    if not pattern.search(content):
        raise ValueError("AppScope/app.json5 中未找到 bundleName 字段")
    return pattern.sub(rf"\1{bundle_name}\2", content, count=1)


def _replace_named_strings(content: str, named_values: dict[str, str]) -> str:
    data = json.loads(content)
    strings = data.get("string")
    if not isinstance(strings, list):
        raise ValueError("string.json 格式不正确：缺少 string 数组")

    for key, value in named_values.items():
        updated = False
        for item in strings:
            if isinstance(item, dict) and item.get("name") == key:
                item["value"] = value
                updated = True
                break
        if not updated:
            strings.append({"name": key, "value": value})

    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _render_signing_configs(material: dict[str, str]) -> str:
    return (
        '"signingConfigs": [\n'
        "      {\n"
        '        "name": "default",\n'
        '        "type": "HarmonyOS",\n'
        '        "material": {\n'
        f'          "storeFile": "{material["storeFile"]}",\n'
        f'          "storePassword": "{material["storePassword"]}",\n'
        f'          "keyAlias": "{material["keyAlias"]}",\n'
        f'          "keyPassword": "{material["keyPassword"]}",\n'
        f'          "signAlg": "{material["signAlg"]}",\n'
        f'          "profile": "{material["profile"]}",\n'
        f'          "certpath": "{material["certpath"]}"\n'
        "        }\n"
        "      }\n"
        "    ]"
    )


def _replace_signing_configs(content: str, material: dict[str, str]) -> str:
    key = '"signingConfigs"'
    start = content.find(key)
    if start < 0:
        raise ValueError("build-profile.json5 中未找到 signingConfigs 字段")

    bracket_start = content.find("[", start)
    if bracket_start < 0:
        raise ValueError("build-profile.json5 中 signingConfigs 格式不正确")

    depth = 0
    end = -1
    for idx in range(bracket_start, len(content)):
        ch = content[idx]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = idx
                break
    if end < 0:
        raise ValueError("build-profile.json5 中 signingConfigs 数组未闭合")

    replacement = _render_signing_configs(material)
    return content[:start] + replacement + content[end + 1 :]


def _ensure_product_signing_config(content: str) -> str:
    if re.search(r'"products"\s*:\s*\[[\s\S]*?"signingConfig"\s*:', content):
        return content
    pattern = re.compile(
        r'("products"\s*:\s*\[\s*\{\s*"name"\s*:\s*"default"\s*,)'
    )
    if not pattern.search(content):
        raise ValueError("build-profile.json5 中未找到 default product")
    return pattern.sub(r'\1\n        "signingConfig": "default",', content, count=1)


def configure_harmony_signing(
    project_path: str,
    bundle_name: str,
    profile_path: str,
    app_name: str,
) -> dict[str, Any]:
    """写入 HarmonyOS 工程签名配置。"""
    bundle = (bundle_name or "").strip()
    name = (app_name or "").strip()
    if not bundle:
        raise ValueError("bundleName 不能为空")
    if not name:
        raise ValueError("appName 不能为空")
    if not BUNDLE_NAME_RE.fullmatch(bundle):
        raise ValueError(
            f"bundleName 格式不正确：{bundle}（期望类似 com.example.app）"
        )

    resolved = resolve_project_paths(project_path)
    ohos_root = resolved["ohosRoot"]
    assert isinstance(ohos_root, Path)
    input_path = resolved["inputPath"]
    assert isinstance(input_path, Path)
    project_kind = resolved["projectKind"]
    flutter_root = resolved.get("flutterRoot")
    if flutter_root is not None and not isinstance(flutter_root, Path):
        flutter_root = None

    project_root = ohos_root
    build_profile_path, app_json5_path, app_name_json_path, entry_name_json_path = (
        _validate_project_root(project_root)
    )
    material = _load_signing_material(profile_path=profile_path)
    removed_stray_backups = _cleanup_stray_resource_backups(project_root)

    app_backup = _backup_file(app_json5_path, project_root)
    build_backup = _backup_file(build_profile_path, project_root)
    name_backup = _backup_file(app_name_json_path, project_root)
    entry_name_backup = _backup_file(entry_name_json_path, project_root)

    try:
        app_text = app_json5_path.read_text(encoding="utf-8")
        build_text = build_profile_path.read_text(encoding="utf-8")
        name_text = app_name_json_path.read_text(encoding="utf-8")
        entry_name_text = entry_name_json_path.read_text(encoding="utf-8")

        new_app_text = _replace_bundle_name(app_text, bundle)
        new_name_text = _replace_named_strings(name_text, {"app_name": name})
        new_entry_name_text = _replace_named_strings(
            entry_name_text, {"EntryAbility_label": name}
        )
        new_build_text = _replace_signing_configs(build_text, material)
        new_build_text = _ensure_product_signing_config(new_build_text)

        app_json5_path.write_text(new_app_text, encoding="utf-8")
        app_name_json_path.write_text(new_name_text, encoding="utf-8")
        entry_name_json_path.write_text(new_entry_name_text, encoding="utf-8")
        build_profile_path.write_text(new_build_text, encoding="utf-8")
    except Exception:
        shutil.copy2(app_backup, app_json5_path)
        shutil.copy2(build_backup, build_profile_path)
        shutil.copy2(name_backup, app_name_json_path)
        shutil.copy2(entry_name_backup, entry_name_json_path)
        raise

    env = _reload_signing_env()
    return {
        "ok": True,
        "projectPath": str(input_path),
        "ohosRoot": str(ohos_root),
        "flutterRoot": str(flutter_root) if flutter_root else "",
        "projectKind": str(project_kind.value if hasattr(project_kind, "value") else project_kind),
        "bundleName": bundle,
        "appName": name,
        "updatedFiles": [
            str(app_json5_path),
            str(app_name_json_path),
            str(entry_name_json_path),
            str(build_profile_path),
        ],
        "signingConfig": {
            "name": "default",
            "type": "HarmonyOS",
            "material": {
                **material,
                "storePassword": (
                    "encrypted"
                    if is_encrypted_password(material["storePassword"])
                    else material["storePassword"]
                ),
                "keyPassword": (
                    "encrypted"
                    if is_encrypted_password(material["keyPassword"])
                    else material["keyPassword"]
                ),
            },
        },
        "passwordSource": "env",
        "materialDir": env["material_dir"],
        "backups": [
            str(app_backup),
            str(name_backup),
            str(entry_name_backup),
            str(build_backup),
        ],
        "removedStrayBackups": removed_stray_backups,
    }


@tool
def configure_harmony_app_signing(
    projectPath: str,
    bundleName: str,
    profilePath: str,
    appName: str,
) -> str:
    """为 HarmonyOS 工程配置 Signing Config。

    会修改：
    - AppScope/app.json5 的 bundleName
    - AppScope/resources/base/element/string.json 的 app_name（桌面显示名）
    - entry/src/main/resources/base/element/string.json 的 EntryAbility_label
    - build-profile.json5 的 signingConfigs（default / HarmonyOS）

    固定签名材料从 .env 读取（AppAutoBAT 下的 p12/cer 及密码、keyAlias）。
    storePassword / keyPassword 会从 .env 明文自动加密写入 build-profile。

    参数：
    - projectPath: 工程路径。原生/WebView 传 ohos 根（含 AppScope）；
      Flutter OHOS 可传 Flutter 根目录（含 pubspec.yaml 与 ohos/）或 ohos 子目录
    - bundleName: 应用包名 / bundle id
    - profilePath: 本次使用的 .p7b 路径
    - appName: 应用显示名称
    """
    try:
        result = configure_harmony_signing(
            projectPath, bundleName, profilePath, appName
        )
    except Exception as exc:
        return f"配置 Harmony 签名失败: {exc}"

    return json.dumps(result, ensure_ascii=False)
