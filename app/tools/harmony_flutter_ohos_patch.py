"""Flutter OHOS 原生侧截图桥接（Want 参数 → MethodChannel）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.tools.harmony_project_detect import find_entry_ability

PATCH_MARKER = "// harmony-auto: flutter ohos screenshot support"
BACKUP_DIR_NAME = ".harmony-auto-backups"
BRIDGE_REL = "entry/src/main/ets/utils/HarmonyAutoBridge.ets"
PLUGIN_REL = "entry/src/main/ets/plugins/HarmonyAutoScreenshotPlugin.ets"

BRIDGE_SOURCE = """// harmony-auto: flutter screenshot bridge
export class HarmonyAutoBridge {
  static tabIndex: number = 0;
  static screenshotMode: boolean = false;

  static applyFromParams(params: Record<string, Object> | undefined): void {
    if (!params) {
      HarmonyAutoBridge.tabIndex = 0;
      HarmonyAutoBridge.screenshotMode = false;
      return;
    }
    const tab = params['harmonyAutoTab'];
    if (typeof tab === 'number') {
      HarmonyAutoBridge.tabIndex = tab;
    } else if (typeof tab === 'string' && tab.length > 0) {
      HarmonyAutoBridge.tabIndex = Number(tab);
    } else {
      HarmonyAutoBridge.tabIndex = 0;
    }
    const mode = params['harmonyAutoScreenshot'];
    HarmonyAutoBridge.screenshotMode =
      mode === true || mode === 'true' || mode === '1';
  }
}
"""

PLUGIN_SOURCE = """// harmony-auto: flutter screenshot plugin
import { MethodCall, MethodCallHandler, MethodChannel, MethodResult } from '@ohos/flutter_ohos';
import {
  FlutterPlugin,
  FlutterPluginBinding,
} from '@ohos/flutter_ohos/src/main/ets/embedding/engine/plugins/FlutterPlugin';
import { HarmonyAutoBridge } from '../utils/HarmonyAutoBridge';

export default class HarmonyAutoScreenshotPlugin implements FlutterPlugin, MethodCallHandler {
  private channel?: MethodChannel;

  getUniqueClassName(): string {
    return 'HarmonyAutoScreenshotPlugin';
  }

  onAttachedToEngine(binding: FlutterPluginBinding): void {
    this.channel = new MethodChannel(binding.getBinaryMessenger(), 'harmony_auto/screenshot');
    this.channel.setMethodCallHandler(this);
  }

  onDetachedFromEngine(_binding: FlutterPluginBinding): void {
    this.channel?.setMethodCallHandler(null);
    this.channel = undefined;
  }

  onMethodCall(call: MethodCall, result: MethodResult): void {
    if (call.method === 'getInitialTab') {
      result.success(HarmonyAutoBridge.tabIndex);
      return;
    }
    if (call.method === 'isScreenshotMode') {
      result.success(HarmonyAutoBridge.screenshotMode);
      return;
    }
    result.notImplemented();
  }
}
"""


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _backup_file(source: Path, ohos_root: Path, name: str) -> Path:
    backup = _backup_dir(ohos_root) / name
    if not backup.is_file():
        shutil.copy2(source, backup)
    return backup


def _patch_entry_ability(entry: Path) -> bool:
    original = entry.read_text(encoding="utf-8")
    if PATCH_MARKER in original and "new HarmonyAutoScreenshotPlugin()" in original:
        return False

    patched = original

    if PATCH_MARKER not in patched:
        patched = patched.replace(
            "export default class EntryAbility extends FlutterAbility {",
            f"export default class EntryAbility extends FlutterAbility {{\n  {PATCH_MARKER}",
            1,
        )

    extra_imports = (
        "import { Want, AbilityConstant } from '@kit.AbilityKit';\n"
        "import { HarmonyAutoBridge } from '../utils/HarmonyAutoBridge';\n"
        "import HarmonyAutoScreenshotPlugin from '../plugins/HarmonyAutoScreenshotPlugin';\n"
    )
    if "HarmonyAutoBridge" not in patched:
        registrant_import = re.search(
            r"import \{ GeneratedPluginRegistrant \} from '[^']+';\n",
            patched,
        )
        if registrant_import:
            insert_at = registrant_import.end()
            patched = patched[:insert_at] + extra_imports + patched[insert_at:]
        else:
            patched = extra_imports + patched

    if "applyHarmonyAutoWant" not in patched:
        helper = """
  private applyHarmonyAutoWant(want: Want): void {
    HarmonyAutoBridge.applyFromParams(want.parameters);
  }
"""
        patched = patched.replace(
            "  configureFlutterEngine(flutterEngine: FlutterEngine) {",
            f"{helper}\n  configureFlutterEngine(flutterEngine: FlutterEngine) {{",
            1,
        )

    if "new HarmonyAutoScreenshotPlugin()" not in patched:
        patched = re.sub(
            r"(GeneratedPluginRegistrant\.registerWith\(flutterEngine\)\s*;?\s*\n)",
            r"\1    flutterEngine.getPlugins()?.add(new HarmonyAutoScreenshotPlugin());\n",
            patched,
            count=1,
        )

    if "applyHarmonyAutoWant(want)" not in patched:
        if re.search(r"onCreate\(want: Want", patched):
            patched = re.sub(
                r"(onCreate\(want: Want, launchParam: AbilityConstant\.LaunchParam\): void \{)",
                r"\1\n    this.applyHarmonyAutoWant(want);",
                patched,
                count=1,
            )
        else:
            lifecycle = (
                "\n  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {\n"
                "    super.onCreate(want, launchParam);\n"
                "    this.applyHarmonyAutoWant(want);\n"
                "  }\n\n"
                "  onNewWant(want: Want, launchParam: AbilityConstant.LaunchParam): void {\n"
                "    super.onNewWant(want, launchParam);\n"
                "    this.applyHarmonyAutoWant(want);\n"
                "  }\n"
            )
            patched = patched.replace(
                f"export default class EntryAbility extends FlutterAbility {{\n  {PATCH_MARKER}",
                f"export default class EntryAbility extends FlutterAbility {{\n  {PATCH_MARKER}{lifecycle}",
                1,
            )

        if (
            "onNewWant(want: Want" in patched
            and patched.count("applyHarmonyAutoWant(want)") < 2
        ):
            patched = re.sub(
                r"(onNewWant\(want: Want, launchParam: AbilityConstant\.LaunchParam\): void \{)",
                r"\1\n    this.applyHarmonyAutoWant(want);",
                patched,
                count=1,
            )

    entry.write_text(patched, encoding="utf-8")
    return patched != original


def ensure_flutter_ohos_screenshot_patch(ohos_root: Path) -> dict[str, object]:
    ohos_root = ohos_root.expanduser().resolve()
    bridge_path = ohos_root / BRIDGE_REL
    plugin_path = ohos_root / PLUGIN_REL
    entry_path = find_entry_ability(ohos_root)

    bridge_changed = _write_if_changed(bridge_path, BRIDGE_SOURCE)
    plugin_changed = _write_if_changed(plugin_path, PLUGIN_SOURCE)
    entry_backup = _backup_file(entry_path, ohos_root, "EntryAbility.flutter.bak")
    entry_changed = _patch_entry_ability(entry_path)

    return {
        "patched": bridge_changed or plugin_changed or entry_changed,
        "bridgePath": str(bridge_path),
        "pluginPath": str(plugin_path),
        "entryAbilityPath": str(entry_path),
        "backupPath": str(entry_backup),
    }
