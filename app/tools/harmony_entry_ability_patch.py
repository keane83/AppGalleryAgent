"""为 EntryAbility 注入 targetPage 启动参数支持（截图导航）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PATCH_MARKER = "// harmony-auto: targetPage launch support"
BACKUP_DIR_NAME = ".harmony-auto-backups"


def _backup_dir(project_root: Path) -> Path:
    directory = project_root / BACKUP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def find_entry_ability(project_root: Path) -> Path:
    candidates = list(project_root.glob("entry/src/main/ets/**/EntryAbility.ets"))
    if not candidates:
        raise FileNotFoundError(f"未找到 EntryAbility.ets：{project_root}")
    if len(candidates) > 1:
        for path in candidates:
            if "entryability" in str(path).lower():
                return path
    return candidates[0]


def is_patched(content: str) -> bool:
    return PATCH_MARKER in content and "resolveLaunchPage" in content


def _strip_bad_launch_want(content: str) -> str:
    content = re.sub(
        r"\n\s*private launchWant: Want \| null = null;\n",
        "\n",
        content,
    )
    content = re.sub(
        r"\n\s*this\.launchWant = want;\n",
        "\n",
        content,
    )
    return content


def apply_target_page_patch(source: Path, project_root: Path) -> tuple[str, Path | None]:
    """返回 (patched_content, backup_path_or_none)。"""
    original = source.read_text(encoding="utf-8")
    original = _strip_bad_launch_want(original)

    if is_patched(original):
        source.write_text(original, encoding="utf-8")
        return original, None

    backup = _backup_dir(project_root) / "EntryAbility.ets.bak"
    if not backup.is_file():
        shutil.copy2(source, backup)
    elif source.read_text(encoding="utf-8") != backup.read_text(encoding="utf-8"):
        shutil.copy2(source, backup)

    patched = original

    if "super.onCreate(want, launchParam)" not in patched:
        patched = re.sub(
            r"(onCreate\(want: Want, launchParam: AbilityConstant\.LaunchParam\): void \{)",
            r"\1\n    super.onCreate(want, launchParam);",
            patched,
            count=1,
        )

    if "resolveLaunchPage" not in patched:
        helper = """
  private resolveLaunchPage(): string {
    const page = this.launchWant?.parameters?.targetPage;
    if (typeof page === 'string' && page.startsWith('pages/')) {
      return page;
    }
    return 'pages/Index';
  }
"""
        patched = patched.replace(
            "  onWindowStageCreate(windowStage: window.WindowStage): void {",
            f"{helper}\n  onWindowStageCreate(windowStage: window.WindowStage): void {{",
            1,
        )

    patched = re.sub(
        r"windowStage\.loadContent\(\s*['\"]pages/Index['\"]",
        "windowStage.loadContent(this.resolveLaunchPage()",
        patched,
        count=1,
    )
    patched = re.sub(
        r"windowStage\.loadContent\(\s*this\.resolveLaunchPage\(\)\s*,",
        "windowStage.loadContent(this.resolveLaunchPage(),",
        patched,
        count=1,
    )

    if PATCH_MARKER not in patched:
        patched = patched.replace(
            "const DOMAIN = 0x0000;",
            f"const DOMAIN = 0x0000;\n{PATCH_MARKER}",
            1,
        )

    return patched, backup


def ensure_entry_ability_patch(project_root: Path | str) -> dict[str, str | bool]:
    project_root = Path(project_root).expanduser().resolve()
    source = find_entry_ability(project_root)
    before = source.read_text(encoding="utf-8")
    patched, backup = apply_target_page_patch(source, project_root)
    changed = patched != before
    if changed:
        source.write_text(patched, encoding="utf-8")
    return {
        "entryAbilityPath": str(source),
        "patched": changed or is_patched(patched),
        "backupPath": str(backup) if backup else str(_backup_dir(project_root) / "EntryAbility.ets.bak"),
    }
