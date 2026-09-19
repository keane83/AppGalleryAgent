"""HarmonyOS 模拟器应用商店截图（3 张 1080x1920 PNG）+ 1024x1024 应用图标。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.tools import tool

from app.config import _PROJECT_ROOT
from app.tools.harmony_app_icon import export_app_icon_1024
from app.tools.harmony_entry_ability_patch import ensure_entry_ability_patch
from app.tools.harmony_flutter_patch import ensure_flutter_screenshot_patch
from app.tools.harmony_hdc import (
    FIRST_SHOT_EXTRA_SETTLE_SEC,
    FLUTTER_RENDER_SETTLE_SEC,
    PAGE_RENDER_SETTLE_SEC,
    WEBVIEW_ACTION_EXTRA_WAIT_SEC,
    WEBVIEW_FIRST_EXTRA_SETTLE_SEC,
    WEBVIEW_RENDER_SETTLE_SEC,
    build_entry_hap,
    build_flutter_hap,
    capture_screenshot_png,
    cold_start_and_wait,
    ensure_emulator_running,
    install_hap,
)

logger = logging.getLogger("haa.capture")
from app.tools.harmony_project_detect import ProjectKind, resolve_project_paths
from app.tools.harmony_screenshot_planner import _sanitize_dir_name, plan_screenshots
from app.tools.harmony_webview_patch import ensure_webview_patches

TARGET_SCREENSHOT_COUNT = 3


def _screenshot_output_dir(*, app_name: str) -> Path:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    base = Path(
        os.getenv(
            "HARMONY_SCREENSHOT_DIR",
            str(_PROJECT_ROOT / "downloads" / "screenshots"),
        )
    ).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_dir = _sanitize_dir_name(app_name)
    return base / app_dir / stamp


def _file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _pad_screenshots_to_target(
    captured: list[dict[str, str]],
    output_dir: Path,
    *,
    target_count: int = TARGET_SCREENSHOT_COUNT,
) -> list[dict[str, str]]:
    if not captured:
        raise ValueError("没有可复制的截图")

    result = list(captured)
    source_index = 0
    while len(result) < target_count:
        source = captured[source_index % len(captured)]
        source_path = Path(source["path"])
        slot = len(result) + 1
        dest = output_dir / f"{slot:02d}_{source['slug']}.png"
        shutil.copy2(source_path, dest)
        result.append(
            {
                "page": source.get("page", ""),
                "slug": source["slug"],
                "reason": f"复制补齐（来源 {source_path.name}）",
                "path": str(dest),
                "duplicated": "true",
                "sourcePath": str(source_path),
            }
        )
        source_index += 1
    return result


def _build_and_install(
    *,
    kind: ProjectKind,
    ohos_root: Path,
    flutter_root: Path | None,
    rebuild: bool,
    patch_info: dict[str, object],
    web_patch_info: dict[str, object] | None,
) -> Path | None:
    if not rebuild and not patch_info.get("patched") and not (web_patch_info or {}).get("patched"):
        signed = ohos_root / "entry/build/default/outputs/default/entry-default-signed.hap"
        if signed.is_file():
            install_hap(signed)
            return signed
        return None

    if kind == ProjectKind.FLUTTER_OHOS:
        if flutter_root is None:
            raise FileNotFoundError("Flutter 工程根目录未找到（缺少 pubspec.yaml）")
        hap_path = build_flutter_hap(flutter_root, release=False)
    else:
        hap_path = build_entry_hap(ohos_root)

    install_hap(hap_path)
    return hap_path


def _capture_native(
    *,
    bundle_name: str,
    shots: list[dict[str, object]],
    output_dir: Path,
) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []
    for idx, item in enumerate(shots):
        page = str(item["page"])
        slug = str(item["slug"])
        png_path = output_dir / str(item["outputFile"])
        logger.info("原生截图 %d/%d: %s", idx + 1, len(shots), slug)

        cold_start_and_wait(
            bundle_name,
            target_page=page,
            render_settle_sec=PAGE_RENDER_SETTLE_SEC,
            extra_settle_sec=FIRST_SHOT_EXTRA_SETTLE_SEC if idx == 0 else 0.0,
        )
        capture_screenshot_png(png_path)

        captured.append(
            {
                "page": page,
                "slug": slug,
                "reason": str(item.get("reason", "")),
                "path": str(png_path),
                "duplicated": "false",
            }
        )
    return captured


def _capture_webview(
    *,
    bundle_name: str,
    shots: list[dict[str, object]],
    output_dir: Path,
) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []
    for idx, item in enumerate(shots):
        slug = str(item["slug"])
        script_idx = int(item.get("scriptIdx", idx))
        script = str(item.get("script", ""))
        png_path = output_dir / str(item["outputFile"])

        logger.info("WebView 截图 %d/%d: %s", idx + 1, len(shots), slug)
        extra = float(item.get("waitSec") or 0)
        if idx == 0:
            extra = max(extra, WEBVIEW_FIRST_EXTRA_SETTLE_SEC)
        cold_start_and_wait(
            bundle_name,
            want_params={"scriptIdx": script_idx},
            render_settle_sec=WEBVIEW_RENDER_SETTLE_SEC,
            extra_settle_sec=extra,
        )
        if "decideBtn" in script:
            time.sleep(WEBVIEW_ACTION_EXTRA_WAIT_SEC)

        capture_screenshot_png(png_path)

        captured.append(
            {
                "page": f"webview:{slug}",
                "slug": slug,
                "reason": str(item.get("reason", "")),
                "path": str(png_path),
                "duplicated": "false",
                "script": script,
            }
        )
    return captured


def _capture_flutter(
    *,
    bundle_name: str,
    shots: list[dict[str, object]],
    output_dir: Path,
) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    for idx, item in enumerate(shots):
        tab_index = int(item.get("tabIndex", 0))
        slug = str(item["slug"])
        png_path = output_dir / str(item["outputFile"])
        logger.info(
            "Flutter 截图 %d/%d: Tab %d (%s) — 冷启动切页",
            idx + 1,
            len(shots),
            tab_index,
            slug,
        )

        cold_start_and_wait(
            bundle_name,
            want_params={
                "harmonyAutoTab": tab_index,
                "harmonyAutoScreenshot": "true",
            },
            render_settle_sec=FLUTTER_RENDER_SETTLE_SEC,
            extra_settle_sec=FIRST_SHOT_EXTRA_SETTLE_SEC if idx == 0 else 0.0,
        )
        capture_screenshot_png(png_path)

        captured.append(
            {
                "page": f"flutter:tab:{tab_index}",
                "slug": slug,
                "reason": str(item.get("reason", "")),
                "path": str(png_path),
                "duplicated": "false",
                "tabIndex": str(tab_index),
                "wantParamTab": str(tab_index),
                "md5": _file_md5(png_path),
            }
        )

    return captured


def capture_harmony_screenshots(
    project_path: str,
    *,
    rebuild: bool = True,
) -> dict[str, object]:
    resolved = resolve_project_paths(project_path)
    ohos_root = resolved["ohosRoot"]
    assert isinstance(ohos_root, Path)
    kind = resolved["projectKind"]
    assert isinstance(kind, ProjectKind)
    flutter_root = resolved.get("flutterRoot")
    if flutter_root is not None and not isinstance(flutter_root, Path):
        flutter_root = None

    prep_started = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        emulator_future = pool.submit(ensure_emulator_running)
        plan_future = pool.submit(plan_screenshots, project_path)
        device = emulator_future.result()
        logger.info("模拟器就绪，耗时 %.1fs", time.time() - prep_started)
        plan = plan_future.result()
        logger.info(
            "截图规划完成，耗时 %.1fs，来源=%s",
            time.time() - prep_started,
            plan.get("planSource", "native"),
        )

    bundle_name = str(plan["bundleName"])
    app_name = str(plan.get("appName") or bundle_name)
    shots = plan["screenshots"]
    if not isinstance(shots, list) or len(shots) < 1:
        raise ValueError("未能规划截图页面")

    patch_info: dict[str, object] = {"patched": False}
    web_patch_info: dict[str, object] | None = None

    flutter_patch_info: dict[str, object] | None = None

    patch_started = time.time()
    if kind == ProjectKind.NATIVE_ARKTS:
        patch_info = ensure_entry_ability_patch(ohos_root)
    elif kind == ProjectKind.FLUTTER_OHOS:
        if flutter_root is None:
            raise FileNotFoundError("Flutter 工程根目录未找到（缺少 pubspec.yaml）")
        patch_target = str(plan.get("patchTargetFile") or "")
        flutter_patch_info = ensure_flutter_screenshot_patch(
            flutter_root,
            ohos_root,
            patch_target=patch_target or None,
        )
        patch_info = flutter_patch_info
    elif kind == ProjectKind.WEBVIEW_HTML:
        scripts = plan.get("scriptsJson") or [str(item.get("script", "")) for item in shots]
        web_patch_info = ensure_webview_patches(ohos_root, list(scripts))
        patch_info = web_patch_info
    logger.info("工程 patch 完成，耗时 %.1fs", time.time() - patch_started)

    hap_path: Path | None = _build_and_install(
        kind=kind,
        ohos_root=ohos_root,
        flutter_root=flutter_root,
        rebuild=rebuild,
        patch_info=patch_info,
        web_patch_info=web_patch_info,
    )

    output_dir = _screenshot_output_dir(app_name=app_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    icon_info = export_app_icon_1024(ohos_root, output_dir)
    logger.info("应用图标已导出: %s", icon_info.get("path"))

    if kind == ProjectKind.WEBVIEW_HTML:
        captured = _capture_webview(bundle_name=bundle_name, shots=shots, output_dir=output_dir)
    elif kind == ProjectKind.FLUTTER_OHOS:
        assert flutter_root is not None
        captured = _capture_flutter(
            bundle_name=bundle_name,
            shots=shots,
            output_dir=output_dir,
        )
    else:
        captured = _capture_native(bundle_name=bundle_name, shots=shots, output_dir=output_dir)

    unique_count = len(captured)
    final_screenshots = (
        captured
        if unique_count >= TARGET_SCREENSHOT_COUNT
        else _pad_screenshots_to_target(captured, output_dir)
    )

    return {
        "projectPath": str(resolved["inputPath"]),
        "ohosRoot": str(ohos_root),
        "flutterRoot": str(flutter_root) if flutter_root else "",
        "projectKind": kind.value,
        "bundleName": bundle_name,
        "appName": app_name,
        "device": device,
        "outputDir": str(output_dir),
        "patch": patch_info,
        "plan": plan,
        "hapPath": str(hap_path) if hap_path else "",
        "uniqueCaptureCount": unique_count,
        "targetDeliverCount": TARGET_SCREENSHOT_COUNT,
        "screenshots": final_screenshots,
        "appIconPath": icon_info.get("path", ""),
        "appIcon": icon_info,
    }


@tool
def capture_harmony_screenshots_and_app_icon(projectPath: str) -> str:
    """获取应用商店素材：3 张 1080x1920 截图 + 1024x1024 应用图标（PNG）。

    截图：HarmonyOS 模拟器自动截取 3 张商店介绍图。
    图标：从工程 AppScope/entry 资源提取并导出 app_icon_1024.png（1024x1024 PNG）。

    自动识别工程类型：
    - native_arkts：多页 ArkTS，EntryAbility targetPage 冷启动
    - webview_html：本地 HTML + WebView，注入 JS 切 Tab/状态
    - flutter_ohos：Flutter OHOS，编译 1 次 + Want 参数运行时切 Tab 截屏

    页面不足 3 个时复制已有截图补齐。
    输出目录：{HARMONY_SCREENSHOT_DIR}/{应用名}/{时间戳}/
      - 01_*.png ~ 03_*.png  介绍截图
      - app_icon_1024.png    应用图标

    参数：
    - projectPath: 鸿蒙工程路径。原生/WebView 传 ohos 根目录（含 AppScope）；
      Flutter 推荐传 ohos 目录（如 OOTD/ootd/ohos），也兼容 Flutter 工程根目录。
    """
    try:
        result = capture_harmony_screenshots(projectPath)
    except Exception as exc:
        return f"Harmony 截图/图标失败: {exc}"

    return json.dumps(result, ensure_ascii=False)


# 兼容旧工具名
capture_harmony_app_screenshots = capture_harmony_screenshots_and_app_icon
