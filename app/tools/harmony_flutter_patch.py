"""为 Flutter OHOS 工程注入截图模式（dart-define 初始 Tab）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PATCH_MARKER = "// harmony-auto: flutter screenshot support"
BACKUP_DIR_NAME = ".harmony-auto-backups"


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
        "    if (!_harmonyAutoScreenshot) {\n"
        "      WidgetsBinding.instance.addPostFrameCallback((_) {\n"
        "        OaidLogger.log('1-ROOT_FRAME', 'first frame done, schedule OAID upload');\n"
        "        _scheduleOaidUploadWithDebug();\n"
        "      });\n"
        "    }",
        1,
    )


def _patch_shell_content(original: str) -> str:
    if PATCH_MARKER in original and "HARMONY_AUTO_TAB" in original:
        return original

    class_match = re.search(r"class (_\w+State) extends State<(\w+)> \{", original)
    if not class_match:
        raise ValueError("Tab 壳层文件中未找到 State 类定义")

    state_open = class_match.group(0)
    patched = original
    if "HARMONY_AUTO_TAB" not in patched:
        patched = patched.replace(
            state_open,
            f"{PATCH_MARKER}\n{state_open}\n"
            "  static const int _harmonyAutoTab = int.fromEnvironment('HARMONY_AUTO_TAB', defaultValue: 0);\n"
            "  static const bool _harmonyAutoScreenshot = "
            "bool.fromEnvironment('HARMONY_AUTO_SCREENSHOT', defaultValue: false);",
            1,
        )

    if re.search(r"int _index\s*=\s*0", patched):
        patched = re.sub(r"int _index\s*=\s*0", "int _index = _harmonyAutoTab", patched, count=1)
    elif re.search(r"int _currentIndex\s*=\s*0", patched):
        patched = re.sub(
            r"int _currentIndex\s*=\s*0",
            "int _currentIndex = _harmonyAutoTab",
            patched,
            count=1,
        )

    patched = _apply_oaid_skip(patched)
    return patched


def ensure_flutter_screenshot_patch(
    flutter_root: Path,
    *,
    patch_target: str | None = None,
) -> dict[str, str | bool]:
    flutter_root = flutter_root.expanduser().resolve()
    shell_path = find_flutter_tab_shell(flutter_root, patch_target)
    original = shell_path.read_text(encoding="utf-8")

    rel_name = shell_path.relative_to(flutter_root).as_posix().replace("/", "__")
    backup = _backup_dir(flutter_root) / f"{rel_name}.flutter_shell.bak"
    if not backup.is_file():
        shutil.copy2(shell_path, backup)

    patched = _patch_shell_content(original)
    changed = patched != original
    if changed:
        shell_path.write_text(patched, encoding="utf-8")

    return {
        "patched": changed or (PATCH_MARKER in patched),
        "shellPath": str(shell_path),
        "rootPagePath": str(shell_path),
        "backupPath": str(backup),
    }
