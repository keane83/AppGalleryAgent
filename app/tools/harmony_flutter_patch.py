"""为 Flutter 工程注入截图模式（MethodChannel 运行时 Tab）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.tools.harmony_flutter_ohos_patch import ensure_flutter_ohos_screenshot_patch

PATCH_MARKER = "// harmony-auto: flutter screenshot support"
BACKUP_DIR_NAME = ".harmony-auto-backups"
HELPER_REL = "lib/harmony_auto_screenshot.dart"

HELPER_SOURCE = """// harmony-auto: flutter screenshot helper
import 'package:flutter/services.dart';

class HarmonyAutoScreenshot {
  HarmonyAutoScreenshot._();

  static const MethodChannel _channel = MethodChannel('harmony_auto/screenshot');

  static int initialTab = 0;
  static bool screenshotMode = false;
  static bool _bootstrapped = false;

  static Future<void> bootstrap() async {
    if (_bootstrapped) {
      return;
    }
    _bootstrapped = true;
    try {
      final tab = await _channel.invokeMethod<int>('getInitialTab');
      final mode = await _channel.invokeMethod<bool>('isScreenshotMode');
      if (tab != null && tab >= 0) {
        initialTab = tab;
      }
      screenshotMode = mode ?? false;
    } catch (_) {
      initialTab = 0;
      screenshotMode = false;
    }
  }
}
"""


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def find_flutter_tab_shell(flutter_root: Path, hint: str | None = None) -> Path:
    flutter_root = flutter_root.expanduser().resolve()
    if hint:
        candidate = Path(hint)
        if not candidate.is_absolute():
            candidate = flutter_root / hint
        if candidate.is_file():
            return candidate

    for preferred in (
        "lib/src/app/root_page.dart",
        "lib/screens/app_shell.dart",
        "lib/screens/main_shell.dart",
        "lib/app_shell.dart",
    ):
        path = flutter_root / preferred
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "int _index" in text or "int _currentIndex" in text:
                return path

    scored: list[tuple[int, Path]] = []
    for dart in flutter_root.glob("lib/**/*.dart"):
        text = dart.read_text(encoding="utf-8", errors="ignore")
        score = 0
        if re.search(r"int _index\s*=\s*0", text):
            score += 12
        if re.search(r"int _currentIndex\s*=\s*0", text):
            score += 12
        if re.search(r"int _index\s*=\s*HarmonyAutoScreenshot\.initialTab", text):
            score += 20
        if "IndexedStack" in text:
            score += 8
        if "bottomNavigationBar" in text:
            score += 8
        if "BottomNavigationBar" in text:
            score += 8
        if "currentIndex:" in text or "currentIndex =" in text:
            score += 4
        if score >= 12:
            scored.append((score, dart))

    if not scored:
        legacy = list(flutter_root.glob("lib/**/root_page.dart"))
        if legacy:
            return legacy[0]
        raise FileNotFoundError(f"未找到 Flutter Tab 壳层文件（含 _index）：{flutter_root}")

    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return scored[0][1]


def find_main_dart(flutter_root: Path) -> Path:
    main_dart = flutter_root / "lib/main.dart"
    if main_dart.is_file():
        return main_dart
    candidates = list(flutter_root.glob("lib/**/main.dart"))
    if not candidates:
        raise FileNotFoundError(f"未找到 lib/main.dart：{flutter_root}")
    return candidates[0]


def _read_package_name(flutter_root: Path) -> str:
    pubspec = flutter_root / "pubspec.yaml"
    if not pubspec.is_file():
        raise FileNotFoundError(f"缺少 pubspec.yaml：{flutter_root}")
    match = re.search(r"^name:\s*(\S+)", pubspec.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError(f"pubspec.yaml 缺少 name 字段：{pubspec}")
    return match.group(1)


def _harmony_import_line(flutter_root: Path) -> str:
    package_name = _read_package_name(flutter_root)
    return f"import 'package:{package_name}/harmony_auto_screenshot.dart';\n"


def _normalize_harmony_imports(content: str, import_line: str) -> str:
    """统一为 package import，避免 lib 子目录相对路径找不到 helper。"""
    content = re.sub(
        r"import\s+'(?:\.\./)*harmony_auto_screenshot\.dart';\n",
        "",
        content,
    )
    if import_line.strip() in content:
        return content
    match = re.search(r"(import 'package:flutter/[^']+';\n)", content)
    if match:
        return content.replace(match.group(1), match.group(1) + import_line, 1)
    return import_line + content


def _ensure_helper(flutter_root: Path) -> tuple[Path, bool]:
    helper_path = flutter_root / HELPER_REL
    changed = not helper_path.is_file() or helper_path.read_text(encoding="utf-8") != HELPER_SOURCE
    if changed:
        helper_path.parent.mkdir(parents=True, exist_ok=True)
        helper_path.write_text(HELPER_SOURCE, encoding="utf-8")
    return helper_path, changed


def _patch_main_dart(main_path: Path, flutter_root: Path) -> bool:
    original = main_path.read_text(encoding="utf-8")
    import_line = _harmony_import_line(flutter_root)
    patched = _normalize_harmony_imports(original, import_line)
    if "HarmonyAutoScreenshot.bootstrap" in patched:
        changed = patched != original
        if changed:
            main_path.write_text(patched, encoding="utf-8")
        return changed

    main_async = re.search(
        r"(?:Future<void>\s+main|void\s+main)\(\)\s*async\s*\{",
        patched,
    )
    main_sync = re.search(r"void\s+main\(\)\s*\{", patched)
    if main_async:
        main_header = main_async.group(0)
    elif main_sync:
        main_header = main_sync.group(0)
        async_header = main_header.replace("{", " async {", 1)
        patched = patched.replace(main_header, async_header, 1)
        main_header = async_header
    else:
        raise ValueError(f"无法识别 main 函数：{main_path}")

    bootstrap_call = "  await HarmonyAutoScreenshot.bootstrap();\n"
    if "WidgetsFlutterBinding.ensureInitialized" in patched:
        patched = patched.replace(
            "WidgetsFlutterBinding.ensureInitialized();",
            "WidgetsFlutterBinding.ensureInitialized();\n" + bootstrap_call.rstrip("\n"),
            1,
        )
    elif bootstrap_call.strip() not in patched:
        patched = patched.replace(
            main_header,
            main_header + "\n  WidgetsFlutterBinding.ensureInitialized();\n  await HarmonyAutoScreenshot.bootstrap();",
            1,
        )

    if PATCH_MARKER not in patched:
        patched = re.sub(
            r"((?:Future<void>\s+main|void\s+main)\(\)\s*async\s*\{)",
            rf"\1\n{PATCH_MARKER}",
            patched,
            count=1,
        )

    changed = patched != original
    if changed:
        backup = _backup_dir(flutter_root) / f"{main_path.relative_to(flutter_root).as_posix().replace('/', '__')}.main.bak"
        if not backup.is_file():
            shutil.copy2(main_path, backup)
        main_path.write_text(patched, encoding="utf-8")
    return changed


def _strip_legacy_compile_time_constants(patched: str) -> str:
    patched = re.sub(
        r"\n\s*static const int _harmonyAutoTab = int\.fromEnvironment\('HARMONY_AUTO_TAB', defaultValue: 0\);\n",
        "\n",
        patched,
    )
    patched = re.sub(
        r"\n\s*static const bool _harmonyAutoScreenshot = "
        r"bool\.fromEnvironment\('HARMONY_AUTO_SCREENSHOT', defaultValue: false\);\n",
        "\n",
        patched,
    )
    patched = patched.replace("int _index = _harmonyAutoTab", "int _index = HarmonyAutoScreenshot.initialTab")
    patched = patched.replace(
        "int _currentIndex = _harmonyAutoTab",
        "int _currentIndex = HarmonyAutoScreenshot.initialTab",
    )
    patched = patched.replace("if (!_harmonyAutoScreenshot)", "if (!HarmonyAutoScreenshot.screenshotMode)")
    return patched


def _apply_oaid_skip(patched: str) -> str:
    block = (
        "    WidgetsBinding.instance.addPostFrameCallback((_) {\n"
        "      OaidLogger.log('1-ROOT_FRAME', 'first frame done, schedule OAID upload');\n"
        "      _scheduleOaidUploadWithDebug();\n"
        "    });"
    )
    if block not in patched:
        return patched
    return patched.replace(
        block,
        "    if (!HarmonyAutoScreenshot.screenshotMode) {\n"
        "      WidgetsBinding.instance.addPostFrameCallback((_) {\n"
        "        OaidLogger.log('1-ROOT_FRAME', 'first frame done, schedule OAID upload');\n"
        "      _scheduleOaidUploadWithDebug();\n"
        "      });\n"
        "    }",
        1,
    )


def _patch_shell_content(original: str, flutter_root: Path) -> str:
    import_line = _harmony_import_line(flutter_root)
    patched = _normalize_harmony_imports(original, import_line)
    patched = _strip_legacy_compile_time_constants(patched)

    if (
        PATCH_MARKER in patched
        and "HarmonyAutoScreenshot.initialTab" in patched
        and import_line.strip() in patched
    ):
        return patched

    class_match = re.search(
        r"class (_\w+State) extends State<(\w+)>(?:\s+with\s+[\w,\s]+)?\s*\{",
        patched,
    )
    if not class_match:
        raise ValueError("Tab 壳层文件中未找到 State 类定义")

    state_open = class_match.group(0)
    if PATCH_MARKER not in patched:
        patched = patched.replace(
            state_open,
            f"{PATCH_MARKER}\n{state_open}",
            1,
        )

    if re.search(r"int _index\s*=\s*0", patched):
        patched = re.sub(
            r"int _index\s*=\s*0",
            "int _index = HarmonyAutoScreenshot.initialTab",
            patched,
            count=1,
        )
    elif re.search(r"int _currentIndex\s*=\s*0", patched):
        patched = re.sub(
            r"int _currentIndex\s*=\s*0",
            "int _currentIndex = HarmonyAutoScreenshot.initialTab",
            patched,
            count=1,
        )

    patched = _apply_oaid_skip(patched)
    return patched


def _find_app_entry_dart(flutter_root: Path) -> Path | None:
    for rel in ("lib/app.dart", "lib/src/app/app.dart"):
        path = flutter_root / rel
        if path.is_file():
            return path
    return None


def _patch_auth_gate_for_screenshots(app_path: Path, flutter_root: Path) -> bool:
    """截图模式跳过登录门禁（auth.isUnlocked ? MainShell : AuthScreen）。"""
    original = app_path.read_text(encoding="utf-8")
    if "HarmonyAutoScreenshot.screenshotMode" in original and "screenshotMode ?" in original:
        return False

    import_line = _harmony_import_line(flutter_root)
    patched = _normalize_harmony_imports(original, import_line)

    auth_home = re.search(
        r"home:\s*(\w+)\.isUnlocked\s*\?\s*(const\s+\w+\([^)]*\))\s*:\s*(const\s+\w+\([^)]*\))",
        patched,
    )
    if not auth_home:
        return False

    auth_var, unlocked_home, locked_home = auth_home.groups()
    replacement = (
        f"home: HarmonyAutoScreenshot.screenshotMode ? {unlocked_home} "
        f": ({auth_var}.isUnlocked ? {unlocked_home} : {locked_home})"
    )
    patched = patched[: auth_home.start()] + replacement + patched[auth_home.end() :]

    if PATCH_MARKER not in patched:
        patched = patched.replace(
            "class ",
            f"{PATCH_MARKER}\nclass ",
            1,
        )

    changed = patched != original
    if changed:
        backup = _backup_dir(flutter_root) / f"{app_path.relative_to(flutter_root).as_posix().replace('/', '__')}.app.bak"
        if not backup.is_file():
            shutil.copy2(app_path, backup)
        app_path.write_text(patched, encoding="utf-8")
    return changed


def ensure_flutter_screenshot_patch(
    flutter_root: Path,
    ohos_root: Path,
    *,
    patch_target: str | None = None,
) -> dict[str, object]:
    flutter_root = flutter_root.expanduser().resolve()
    ohos_root = ohos_root.expanduser().resolve()

    helper_path, helper_changed = _ensure_helper(flutter_root)
    main_path = find_main_dart(flutter_root)
    main_changed = _patch_main_dart(main_path, flutter_root)

    shell_path = find_flutter_tab_shell(flutter_root, patch_target)
    original = shell_path.read_text(encoding="utf-8")
    rel_name = shell_path.relative_to(flutter_root).as_posix().replace("/", "__")
    backup = _backup_dir(flutter_root) / f"{rel_name}.flutter_shell.bak"
    if not backup.is_file():
        shutil.copy2(shell_path, backup)

    patched_shell = _patch_shell_content(original, flutter_root)
    shell_changed = patched_shell != original
    if shell_changed:
        shell_path.write_text(patched_shell, encoding="utf-8")

    app_changed = False
    app_path = _find_app_entry_dart(flutter_root)
    if app_path is not None:
        app_changed = _patch_auth_gate_for_screenshots(app_path, flutter_root)

    ohos_info = ensure_flutter_ohos_screenshot_patch(ohos_root)

    return {
        "patched": helper_changed or main_changed or shell_changed or app_changed or ohos_info.get("patched"),
        "mode": "runtime_method_channel",
        "helperPath": str(helper_path),
        "mainPath": str(main_path),
        "shellPath": str(shell_path),
        "rootPagePath": str(shell_path),
        "appPath": str(app_path) if app_path else "",
        "backupPath": str(backup),
        "ohos": ohos_info,
    }
