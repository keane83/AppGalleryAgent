"""为 WebView 工程注入截图脚本执行支持。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.tools.harmony_project_detect import find_entry_ability, find_index_ets

PATCH_MARKER = "// harmony-auto: webview screenshot support"
BACKUP_DIR_NAME = ".harmony-auto-backups"
BRIDGE_REL = "entry/src/main/ets/utils/HarmonyAutoBridge.ets"


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _backup_file(source: Path, project_root: Path, name: str) -> Path:
    backup = _backup_dir(project_root) / name
    if not backup.is_file() or source.read_text(encoding="utf-8") != backup.read_text(encoding="utf-8"):
        shutil.copy2(source, backup)
    return backup


def _ensure_bridge_file(ohos_root: Path) -> Path:
    bridge = ohos_root / BRIDGE_REL
    bridge.parent.mkdir(parents=True, exist_ok=True)
    content = """// harmony-auto: webview screenshot bridge
export class HarmonyAutoBridge {
  static scriptIdx: number = -1;
}
"""
    if not bridge.is_file() or bridge.read_text(encoding="utf-8") != content:
        bridge.write_text(content, encoding="utf-8")
        return bridge
    return bridge


def ensure_webview_patches(ohos_root: Path, scripts: list[str]) -> dict[str, object]:
    from app.tools.harmony_webview_planner import write_webview_scripts_file

    scripts_path = write_webview_scripts_file(ohos_root, scripts)
    bridge_path = _ensure_bridge_file(ohos_root)
    entry = find_entry_ability(ohos_root)
    index = find_index_ets(ohos_root)

    entry_backup = _backup_file(entry, ohos_root, "EntryAbility.webview.bak")
    index_backup = _backup_file(index, ohos_root, "Index.webview.bak")

    entry_changed = _patch_entry_ability(entry)
    index_changed = _patch_index_ets(index)

    return {
        "patched": entry_changed or index_changed,
        "scriptsPath": str(scripts_path),
        "bridgePath": str(bridge_path),
        "entryAbilityPath": str(entry),
        "indexPath": str(index),
        "backups": [str(entry_backup), str(index_backup)],
    }


def _patch_entry_ability(source: Path) -> bool:
    original = source.read_text(encoding="utf-8")
    if PATCH_MARKER in original and "HarmonyAutoBridge" in original:
        return False

    patched = original
    if "HarmonyAutoBridge" not in patched:
        patched = patched.replace(
            "import { window } from '@kit.ArkUI';",
            "import { window } from '@kit.ArkUI';\nimport { HarmonyAutoBridge } from '../utils/HarmonyAutoBridge';",
            1,
        )

    if "applyHarmonyAutoScriptIdx" not in patched:
        helper = """
  private applyHarmonyAutoScriptIdx(want: Want): void {
    const idx = want.parameters?.scriptIdx;
    if (typeof idx === 'number') {
      HarmonyAutoBridge.scriptIdx = idx;
    } else if (typeof idx === 'string' && idx !== '') {
      HarmonyAutoBridge.scriptIdx = Number(idx);
    } else {
      HarmonyAutoBridge.scriptIdx = -1;
    }
  }
"""
        patched = re.sub(
            r"(onCreate\(want: Want, launchParam: AbilityConstant\.LaunchParam\): void \{)",
            r"\1\n    this.applyHarmonyAutoScriptIdx(want);",
            patched,
            count=1,
        )
        patched = patched.replace(
            "  onWindowStageCreate(windowStage: window.WindowStage): void {",
            f"{helper}\n  onWindowStageCreate(windowStage: window.WindowStage): void {{",
            1,
        )

    if PATCH_MARKER not in patched:
        patched = patched.replace(
            "const DOMAIN = 0x0000;",
            f"const DOMAIN = 0x0000;\n{PATCH_MARKER}",
            1,
        )

    source.write_text(patched, encoding="utf-8")
    return patched != original


def _patch_index_ets(source: Path) -> bool:
    original = source.read_text(encoding="utf-8")
    if PATCH_MARKER in original and "runHarmonyAutoScreenshotScript" in original:
        return False

    patched = original
    if "HarmonyAutoBridge" not in patched:
        patched = "import { HarmonyAutoBridge } from '../utils/HarmonyAutoBridge';\n" + patched
    if "import util from '@ohos.util';" not in patched:
        patched = "import util from '@ohos.util';\n" + patched

    helper = """
  private runHarmonyAutoScreenshotScript(): void {
    const idx = HarmonyAutoBridge.scriptIdx;
    if (idx < 0) {
      return;
    }
    try {
      const rm = getContext(this).resourceManager;
      const raw = rm.getRawFileContentSync('harmony_auto_screenshot_scripts.json');
      const decoder = util.TextDecoder.create('utf-8', { ignoreBOM: true });
      const scripts = JSON.parse(decoder.decodeToString(new Uint8Array(raw))) as string[];
      const script = scripts[idx];
      if (script && script.length > 0) {
        setTimeout(() => {
          this.webController.runJavaScript(script);
        }, 1200);
      }
    } catch (err) {
      console.error('harmony-auto screenshot script failed: ' + JSON.stringify(err));
    }
  }
"""
    if "runHarmonyAutoScreenshotScript" not in patched:
        patched = patched.replace(
            "  build() {",
            f"{helper}\n  build() {{",
            1,
        )

    if ".onPageEnd(" not in patched:
        patched = re.sub(
            r"(Web\(\{ src: \$rawfile\([^)]+\), controller: this\.webController \}\))",
            r"\1\n        .onPageEnd(() => { this.runHarmonyAutoScreenshotScript(); })",
            patched,
            count=1,
        )

    if PATCH_MARKER not in patched:
        patched = patched.replace(
            "@Entry",
            f"{PATCH_MARKER}\n@Entry",
            1,
        )

    source.write_text(patched, encoding="utf-8")
    return patched != original
