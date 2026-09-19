"""HarmonyOS hdc / 模拟器 / 截屏 / 安装 辅助。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

from app.config import _PROJECT_ROOT

logger = logging.getLogger("haa.hdc")

DEFAULT_DEVECO = Path("/Applications/DevEco-Studio.app/Contents")
SCREENSHOT_WIDTH = 1080
SCREENSHOT_HEIGHT = 1920
DEVICE_BOOT_TIMEOUT_SEC = 120
APP_FOREGROUND_TIMEOUT_SEC = 60
APP_FOREGROUND_POLL_SEC = 0.35
PAGE_RENDER_SETTLE_SEC = 1.2
FLUTTER_RENDER_SETTLE_SEC = 2.5
FIRST_SHOT_EXTRA_SETTLE_SEC = 1.5
FLUTTER_FIRST_EXTRA_SETTLE_SEC = FIRST_SHOT_EXTRA_SETTLE_SEC
WEBVIEW_RENDER_SETTLE_SEC = 2.0
WEBVIEW_FIRST_EXTRA_SETTLE_SEC = 4.0
WEBVIEW_ACTION_EXTRA_WAIT_SEC = 1.5

# 兼容旧引用
PAGE_RENDER_WAIT_SEC = PAGE_RENDER_SETTLE_SEC
FLUTTER_RENDER_WAIT_SEC = FLUTTER_RENDER_SETTLE_SEC
WEBVIEW_RENDER_WAIT_SEC = WEBVIEW_RENDER_SETTLE_SEC
WEBVIEW_FIRST_SCREEN_WAIT_SEC = WEBVIEW_FIRST_EXTRA_SETTLE_SEC

_HARMONY_ENV_CACHE: dict[str, str] | None = None


def _reload_harmony_env(*, force: bool = False) -> dict[str, str]:
    global _HARMONY_ENV_CACHE
    if _HARMONY_ENV_CACHE is not None and not force:
        return _HARMONY_ENV_CACHE
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    deveco = Path(
        os.getenv("DEVECO_STUDIO_PATH", str(DEFAULT_DEVECO))
    ).expanduser()
    sdk_default = deveco / "sdk/default/openharmony/toolchains/hdc"
    return {
        "hdc": os.getenv(
            "HARMONY_HDC_PATH",
            str(sdk_default if sdk_default.is_file() else shutil.which("hdc") or sdk_default),
        ),
        "hvigorw": os.getenv(
            "HARMONY_HVIGORW_PATH",
            str(deveco / "tools/hvigor/bin/hvigorw"),
        ),
        "emulator": os.getenv(
            "HARMONY_EMULATOR_PATH",
            str(deveco / "tools/emulator/Emulator"),
        ),
        "emulator_name": (os.getenv("HARMONY_EMULATOR_NAME") or "Mate 70 Pro").strip(),
    }
    _HARMONY_ENV_CACHE = env
    return env


def run_hdc(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = _reload_harmony_env()
    hdc = env["hdc"]
    if not Path(hdc).is_file() and not shutil.which(hdc):
        raise FileNotFoundError(f"hdc 未找到：{hdc}")
    cmd = [hdc, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def list_targets() -> list[str]:
    proc = run_hdc("list", "targets")
    lines = (proc.stdout or "").splitlines()
    targets = [line.strip() for line in lines if line.strip() and "Empty" not in line]
    return targets


def wait_for_device(timeout_sec: int = DEVICE_BOOT_TIMEOUT_SEC) -> str:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        targets = list_targets()
        if targets:
            return targets[0]
        time.sleep(2)
    raise TimeoutError(f"{timeout_sec}s 内未检测到 hdc 设备")


def ensure_emulator_running() -> str:
    targets = list_targets()
    if targets:
        return targets[0]

    env = _reload_harmony_env()
    emulator_bin = Path(env["emulator"])
    if not emulator_bin.is_file():
        raise FileNotFoundError(f"模拟器可执行文件不存在：{emulator_bin}")

    avd = env["emulator_name"]
    subprocess.Popen(
        [str(emulator_bin), "-avd", avd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return wait_for_device()


def force_stop_app(bundle_name: str) -> None:
    run_hdc("shell", "aa", "force-stop", bundle_name, timeout=30)


def parse_foreground_page_bundle(aa_dump: str) -> str | None:
    """从 aa dump -a 输出中解析当前前台 PAGE Ability 的包名。"""
    for block in re.split(r"\n    Mission ID #", aa_dump):
        if "ability type [PAGE]" not in block:
            continue
        if "state #FOREGROUND" not in block:
            continue
        match = re.search(r"bundle name \[([^\]]+)\]", block)
        if match:
            return match.group(1)
    return None


def get_foreground_page_bundle() -> str | None:
    proc = run_hdc("shell", "aa", "dump", "-a", timeout=20)
    return parse_foreground_page_bundle(proc.stdout or "")


def wait_for_app_foreground(
    bundle_name: str,
    *,
    timeout_sec: float = APP_FOREGROUND_TIMEOUT_SEC,
    poll_sec: float = APP_FOREGROUND_POLL_SEC,
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = get_foreground_page_bundle()
        if current == bundle_name:
            return True
        time.sleep(poll_sec)
    return False


def cold_start_and_wait(
    bundle_name: str,
    *,
    ability_name: str = "EntryAbility",
    target_page: str | None = None,
    want_params: dict[str, str | int] | None = None,
    render_settle_sec: float = PAGE_RENDER_SETTLE_SEC,
    extra_settle_sec: float = 0.0,
) -> None:
    """冷启动应用，轮询确认进入前台后再短暂等待渲染稳定。"""
    logger.info("冷启动 %s …", bundle_name)
    force_stop_app(bundle_name)
    time.sleep(0.4)
    start_ability(
        bundle_name,
        ability_name,
        target_page=target_page,
        want_params=want_params,
    )
    if not wait_for_app_foreground(bundle_name):
        current = get_foreground_page_bundle()
        raise TimeoutError(
            f"应用未进入前台（期望 {bundle_name}，当前 {current or '未知'}）"
        )
    settle = render_settle_sec + extra_settle_sec
    if settle > 0:
        logger.info("前台已确认，等待渲染 %.1fs …", settle)
        time.sleep(settle)


def start_ability(
    bundle_name: str,
    ability_name: str = "EntryAbility",
    *,
    target_page: str | None = None,
    want_params: dict[str, str | int] | None = None,
) -> None:
    args = ["shell", "aa", "start", "-b", bundle_name, "-a", ability_name]
    if target_page:
        args.extend(["--ps", "targetPage", target_page])
    if want_params:
        for key, value in want_params.items():
            args.extend(["--ps", key, str(value)])
    proc = run_hdc(*args)
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    if proc.returncode != 0 or "success" not in combined:
        raise RuntimeError(
            f"启动应用失败 (code={proc.returncode}): {proc.stdout} {proc.stderr}"
        )


def get_display_size() -> tuple[int, int]:
    proc = run_hdc("shell", "uitest", "dumpLayout", "-p", "/data/local/tmp/harmony_auto_display.json")
    if proc.returncode != 0:
        return 1080, 1920
    recv = run_hdc("file", "recv", "/data/local/tmp/harmony_auto_display.json", "/tmp/harmony_auto_display.json")
    if recv.returncode != 0:
        return 1080, 1920
    try:
        import json
        from pathlib import Path

        data = json.loads(Path("/tmp/harmony_auto_display.json").read_text(encoding="utf-8"))
        bounds = data.get("attributes", {}).get("bounds", "")
        match = re.search(r"\[0,0\]\[(\d+),(\d+)\]", bounds)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 1080, 1920


def ensure_uitest_ready() -> None:
    run_hdc("shell", "uitest", "start-daemon", "0")


def uitest_click(x: int, y: int) -> None:
    proc = run_hdc("shell", "uitest", "uiInput", "click", str(x), str(y))
    if proc.returncode != 0:
        raise RuntimeError(f"uitest click 失败: {proc.stdout} {proc.stderr}")


def tap_screen(x: int, y: int, *, repeat: int = 1) -> None:
    """uitest + input tap 双通道，提高 Flutter/WebView 命中率。"""
    for _ in range(repeat):
        uitest_click(x, y)
        run_hdc("shell", "input", "tap", str(x), str(y))
        time.sleep(0.35)


def dismiss_flutter_overlays(width: int, height: int) -> None:
    """关闭 OAID 调试弹窗等可能挡住底部 Tab 的浮层。"""
    ensure_uitest_ready()
    candidates = [
        (width // 2, int(height * 0.62)),
        (width // 2, int(height * 0.67)),
        (width // 2, int(height * 0.55)),
    ]
    for x, y in candidates:
        tap_screen(x, y, repeat=1)
        time.sleep(0.4)
    run_hdc("shell", "input", "keyEvent", "Back")
    time.sleep(0.5)


def flutter_tab_click(
    tab_index: int,
    tab_count: int,
    *,
    width: int | None = None,
    height: int | None = None,
    y_offset: int = 0,
) -> tuple[int, int]:
    if width is None or height is None:
        width, height = get_display_size()
    if tab_count < 1:
        tab_count = 1
    x = int(width * (2 * tab_index + 1) / (2 * tab_count))
    y = int(height - 100 + y_offset)
    ensure_uitest_ready()
    tap_screen(x, y, repeat=2)
    return x, y


def build_flutter_hap(
    flutter_root: Path,
    *,
    release: bool = False,
    dart_defines: dict[str, str] | None = None,
    label: str = "",
) -> Path:
    import os

    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    flutter_bin = os.getenv("FLUTTER_BIN", shutil.which("flutter") or "flutter")
    flutter_root = flutter_root.expanduser().resolve()
    if not (flutter_root / "pubspec.yaml").is_file():
        raise FileNotFoundError(f"Flutter 工程不存在：{flutter_root}")

    cmd = [flutter_bin, "build", "hap"]
    if release:
        cmd.append("--release")
    if dart_defines:
        for key, value in dart_defines.items():
            cmd.extend(["--dart-define", f"{key}={value}"])

    hint = label or "HAP"
    logger.info("开始编译 Flutter %s（dart-define=%s）…", hint, dart_defines or {})
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(flutter_root),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    logger.info("Flutter 编译完成，耗时 %.0fs", time.time() - started)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-5000:]
        raise RuntimeError(f"flutter build hap 失败 (code={proc.returncode}):\n{tail}")

    candidates = [
        flutter_root / "build/ohos/hap/entry-default-signed.hap",
        flutter_root / "build/ohos/hap/entry-default-unsigned.hap",
        flutter_root / "ohos/entry/build/default/outputs/default/entry-default-signed.hap",
        flutter_root / "ohos/entry/build/default/outputs/default/entry-default-unsigned.hap",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("flutter build hap 后未找到 entry-default-signed.hap")


def install_hap(hap_path: Path) -> None:
    hap_path = hap_path.resolve()
    if not hap_path.is_file():
        raise FileNotFoundError(f"HAP 不存在：{hap_path}")
    logger.info("安装 HAP: %s", hap_path.name)
    started = time.time()
    proc = run_hdc("install", str(hap_path), timeout=180)
    logger.info("HAP 安装完成，耗时 %.0fs", time.time() - started)
    combined = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0 and "success" not in combined.lower():
        raise RuntimeError(f"安装 HAP 失败: {combined}")


def capture_display_jpeg(
    local_jpeg: Path,
    *,
    width: int = SCREENSHOT_WIDTH,
    height: int = SCREENSHOT_HEIGHT,
) -> None:
    remote = "/data/local/tmp/harmony_auto_shot.jpeg"
    proc = run_hdc(
        "shell",
        "snapshot_display",
        "-f",
        remote,
        "-w",
        str(width),
        "-h",
        str(height),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"snapshot_display 失败: {proc.stdout} {proc.stderr}")

    local_jpeg.parent.mkdir(parents=True, exist_ok=True)
    recv = run_hdc("file", "recv", remote, str(local_jpeg))
    if recv.returncode != 0 or not local_jpeg.is_file():
        raise RuntimeError(f"拉取截图失败: {recv.stdout} {recv.stderr}")


def jpeg_to_png(jpeg_path: Path, png_path: Path) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["sips", "-s", "format", "png", str(jpeg_path), "--out", str(png_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not png_path.is_file():
        raise RuntimeError(f"JPEG 转 PNG 失败: {proc.stdout} {proc.stderr}")


def capture_screenshot_png(output_png: Path) -> Path:
    logger.info("截屏 → %s", output_png.name)
    started = time.time()
    tmp_jpeg = output_png.with_suffix(".jpeg")
    capture_display_jpeg(tmp_jpeg)
    jpeg_to_png(tmp_jpeg, output_png)
    if tmp_jpeg.is_file():
        tmp_jpeg.unlink()
    logger.info("截屏完成 %.1fs: %s", time.time() - started, output_png.name)
    return output_png


def build_entry_hap(project_root: Path | str) -> Path:
    project_root = Path(project_root).expanduser().resolve()
    env = _reload_harmony_env()
    hvigorw = Path(env["hvigorw"])
    if not hvigorw.is_file():
        raise FileNotFoundError(f"hvigorw 未找到：{hvigorw}")

    proc = subprocess.run(
        [
            str(hvigorw),
            "assembleHap",
            "-p",
            "product=default",
            "-p",
            "module=entry@default",
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    signed = project_root / "entry/build/default/outputs/default/entry-default-signed.hap"
    unsigned = project_root / "entry/build/default/outputs/default/entry-default-unsigned.hap"
    if signed.is_file():
        return signed
    if unsigned.is_file():
        return unsigned
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-4000:]
        raise RuntimeError(f"assembleHap 失败 (code={proc.returncode}):\n{tail}")
    raise FileNotFoundError("未找到 entry-default-signed.hap 或 unsigned hap")


def find_app_package(root: Path | str) -> Path:
    """在工程目录下查找最新构建的 .app 包（优先 signed）。"""
    project_root = Path(root).expanduser().resolve()
    candidates = [p for p in project_root.glob("**/*.app") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"未找到 .app 软件包：{project_root}")

    signed = [p for p in candidates if "signed" in p.name.lower()]
    pool = signed or candidates
    return max(pool, key=lambda p: p.stat().st_mtime)


def build_entry_app(project_root: Path | str, *, release: bool = True) -> Path:
    """hvigor assembleApp 编译 HarmonyOS .app 包（用于 AppGallery 上传）。"""
    project_root = Path(project_root).expanduser().resolve()
    env = _reload_harmony_env()
    hvigorw = Path(env["hvigorw"])
    if not hvigorw.is_file():
        raise FileNotFoundError(f"hvigorw 未找到：{hvigorw}")

    cmd = [
        str(hvigorw),
        "assembleApp",
        "-p",
        "product=default",
    ]
    if release:
        cmd.extend(["-p", "buildMode=release"])

    logger.info("开始编译 HarmonyOS APP 包（assembleApp, release=%s）…", release)
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    logger.info("assembleApp 完成，耗时 %.0fs", time.time() - started)

    preferred = [
        project_root / "build/outputs/default/default-default-signed.app",
        project_root / "build/outputs/default/default-default-unsigned.app",
        project_root / "build/default/outputs/default/default-default-signed.app",
    ]
    for path in preferred:
        if path.is_file():
            return path

    try:
        return find_app_package(project_root)
    except FileNotFoundError:
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr)[-4000:]
            raise RuntimeError(f"assembleApp 失败 (code={proc.returncode}):\n{tail}") from None
        raise


def build_flutter_app(
    flutter_root: Path,
    *,
    release: bool = True,
) -> Path:
    """flutter build app 编译 Flutter OHOS .app 包。"""
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    flutter_bin = os.getenv("FLUTTER_BIN", shutil.which("flutter") or "flutter")
    flutter_root = flutter_root.expanduser().resolve()
    if not (flutter_root / "pubspec.yaml").is_file():
        raise FileNotFoundError(f"Flutter 工程不存在：{flutter_root}")

    cmd = [flutter_bin, "build", "app"]
    if release:
        cmd.append("--release")

    logger.info("开始编译 Flutter APP 包（flutter build app）…")
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(flutter_root),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    logger.info("Flutter APP 编译完成，耗时 %.0fs", time.time() - started)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-5000:]
        raise RuntimeError(f"flutter build app 失败 (code={proc.returncode}):\n{tail}")

    ohos_root = flutter_root / "ohos"
    candidates = [
        flutter_root / "build/ohos/app/entry-default-signed.app",
        flutter_root / "build/ohos/app/entry-default-unsigned.app",
        ohos_root / "build/outputs/default/default-default-signed.app",
        ohos_root / "build/outputs/default/default-default-unsigned.app",
    ]
    for path in candidates:
        if path.is_file():
            return path

    search_roots = [flutter_root, ohos_root] if ohos_root.is_dir() else [flutter_root]
    for root in search_roots:
        try:
            return find_app_package(root)
        except FileNotFoundError:
            continue
    raise FileNotFoundError("flutter build app 后未找到 .app 软件包")
