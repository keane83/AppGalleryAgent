"""HarmonyOS hdc / 模拟器 / 截屏 / 安装 辅助。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.config import _PROJECT_ROOT

load_dotenv(_PROJECT_ROOT / ".env", override=True)

logger = logging.getLogger("haa.hdc")

DEFAULT_DEVECO = Path("/Applications/DevEco-Studio.app/Contents")
SCREENSHOT_WIDTH = 1080
SCREENSHOT_HEIGHT = 1920
DEVICE_BOOT_TIMEOUT_SEC = 180
APP_FOREGROUND_TIMEOUT_SEC = 60
APP_FOREGROUND_POLL_SEC = 0.35
PAGE_RENDER_SETTLE_SEC = float(os.getenv("HARMONY_PAGE_RENDER_WAIT_SEC", "2.5"))
FLUTTER_RENDER_SETTLE_SEC = float(os.getenv("HARMONY_FLUTTER_RENDER_WAIT_SEC", "6.0"))
FIRST_SHOT_EXTRA_SETTLE_SEC = float(os.getenv("HARMONY_FIRST_SHOT_EXTRA_WAIT_SEC", "3.0"))
FLUTTER_FIRST_EXTRA_SETTLE_SEC = FIRST_SHOT_EXTRA_SETTLE_SEC
FLUTTER_SPLASH_RETRY_MAX = int(os.getenv("HARMONY_FLUTTER_SPLASH_RETRY_MAX", "8"))
FLUTTER_SPLASH_RETRY_DELAY_SEC = float(os.getenv("HARMONY_FLUTTER_SPLASH_RETRY_DELAY_SEC", "1.2"))
FLUTTER_SPLASH_SIMILARITY = float(os.getenv("HARMONY_FLUTTER_SPLASH_SIMILARITY", "0.93"))
WEBVIEW_RENDER_SETTLE_SEC = float(os.getenv("HARMONY_WEBVIEW_RENDER_WAIT_SEC", "3.0"))
WEBVIEW_FIRST_EXTRA_SETTLE_SEC = float(os.getenv("HARMONY_WEBVIEW_FIRST_EXTRA_WAIT_SEC", "4.0"))
WEBVIEW_ACTION_EXTRA_WAIT_SEC = float(os.getenv("HARMONY_WEBVIEW_ACTION_EXTRA_WAIT_SEC", "1.5"))
WEBVIEW_POST_SCRIPT_WAIT_SEC = float(os.getenv("HARMONY_WEBVIEW_POST_SCRIPT_WAIT_SEC", "2.5"))
SCREENSHOT_BLANK_RETRY_DELAY_SEC = float(os.getenv("HARMONY_SCREENSHOT_BLANK_RETRY_DELAY_SEC", "2.5"))
SCREENSHOT_BLANK_RETRY_MAX = int(os.getenv("HARMONY_SCREENSHOT_BLANK_RETRY_MAX", "3"))
SCREENSHOT_USE_UITEST = os.getenv("HARMONY_SCREENSHOT_USE_UITEST", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SCREENSHOT_FAIL_ON_INVALID = os.getenv("HARMONY_SCREENSHOT_FAIL_ON_INVALID", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SCREENSHOT_BLACK_RATIO_THRESHOLD = float(os.getenv("HARMONY_SCREENSHOT_BLACK_RATIO", "0.85"))

# 兼容旧引用
PAGE_RENDER_WAIT_SEC = PAGE_RENDER_SETTLE_SEC
FLUTTER_RENDER_WAIT_SEC = FLUTTER_RENDER_SETTLE_SEC
WEBVIEW_RENDER_WAIT_SEC = WEBVIEW_RENDER_SETTLE_SEC
WEBVIEW_FIRST_SCREEN_WAIT_SEC = WEBVIEW_FIRST_EXTRA_SETTLE_SEC

_HARMONY_ENV_CACHE: dict[str, str] | None = None
_HDC_SELECTED_TARGET: str | None = None


def _hdc_emulator_addrs() -> list[str]:
    raw = (os.getenv("HARMONY_HDC_EMULATOR_ADDR") or "127.0.0.1:5555").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def _reload_harmony_env(*, force: bool = False) -> dict[str, str]:
    global _HARMONY_ENV_CACHE
    if _HARMONY_ENV_CACHE is not None and not force:
        return _HARMONY_ENV_CACHE
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    deveco = Path(
        os.getenv("DEVECO_STUDIO_PATH", str(DEFAULT_DEVECO))
    ).expanduser()
    sdk_default = deveco / "sdk/default/openharmony/toolchains/hdc"
    env = {
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
        "boot_timeout": os.getenv("HARMONY_EMULATOR_BOOT_TIMEOUT_SEC", "180").strip(),
    }
    _HARMONY_ENV_CACHE = env
    return env


def _hdc_executable() -> str:
    env = _reload_harmony_env()
    hdc = env["hdc"]
    if not Path(hdc).is_file() and not shutil.which(hdc):
        raise FileNotFoundError(f"hdc 未找到：{hdc}")
    return hdc


def _run_hdc_raw(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """不附带 -t connect-key（用于 list targets / tconn / start -r）。"""
    cmd = [_hdc_executable(), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _list_targets_raw() -> list[str]:
    proc = _run_hdc_raw("list", "targets", timeout=30)
    lines = (proc.stdout or "").splitlines()
    return [line.strip() for line in lines if line.strip() and "Empty" not in line]


def _try_tconn_emulator() -> bool:
    """DevEco 手动启动的模拟器常需 tconn 后 hdc 才能 list 到设备。"""
    for addr in _hdc_emulator_addrs():
        proc = _run_hdc_raw("tconn", addr, timeout=45)
        hint = (proc.stdout or proc.stderr or "").strip()
        if hint:
            logger.info("hdc tconn %s: %s", addr, hint[:200])
        if _list_targets_raw():
            return True
    return False


def _hdc_output_needs_connect_key(text: str) -> bool:
    lowered = text.lower()
    return "connect-key" in lowered or "need connect" in lowered


def _select_hdc_target(*, force_refresh: bool = False) -> str | None:
    global _HDC_SELECTED_TARGET
    if not force_refresh and _HDC_SELECTED_TARGET:
        if _HDC_SELECTED_TARGET in _list_targets_raw():
            return _HDC_SELECTED_TARGET

    env_target = (os.getenv("HARMONY_HDC_TARGET") or "").strip()
    targets = _list_targets_raw()
    if not targets:
        _try_tconn_emulator()
        targets = _list_targets_raw()
    if not targets:
        _HDC_SELECTED_TARGET = None
        return None

    if env_target and env_target in targets:
        chosen = env_target
    else:
        chosen = targets[0]
        if len(targets) > 1:
            logger.warning("检测到多个 hdc 设备，使用 %s（全部：%s）", chosen, targets)

    _HDC_SELECTED_TARGET = chosen
    return chosen


def ensure_hdc_device() -> str:
    """确保 hdc 已选中可用 connect-key（模拟器/真机）。"""
    target = _select_hdc_target(force_refresh=True)
    if target:
        return target
    addrs = ", ".join(_hdc_emulator_addrs())
    raise RuntimeError(
        "hdc 未连接任何设备（list targets 为空）。"
        "若已在 DevEco 里启动模拟器，请在终端执行："
        f"hdc tconn {addrs.split(',')[0].strip()}，"
        "然后 hdc list targets 应出现 127.0.0.1:5555。"
        "也可在 .env 设置 HARMONY_HDC_EMULATOR_ADDR 或 HARMONY_HDC_TARGET。"
    )


def run_hdc(
    *args: str,
    timeout: int = 120,
    _allow_retry: bool = True,
) -> subprocess.CompletedProcess[str]:
    global _HDC_SELECTED_TARGET
    _select_hdc_target()
    hdc = _hdc_executable()
    cmd = [hdc]
    if _HDC_SELECTED_TARGET:
        cmd.extend(["-t", _HDC_SELECTED_TARGET])
    cmd.extend(args)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if _allow_retry and _hdc_output_needs_connect_key(combined):
        logger.warning("hdc 报 connect-key，尝试 tconn 并重试命令…")
        _HDC_SELECTED_TARGET = None
        _try_tconn_emulator()
        _select_hdc_target(force_refresh=True)
        if _HDC_SELECTED_TARGET:
            retry_cmd = [hdc, "-t", _HDC_SELECTED_TARGET, *args]
            proc = subprocess.run(
                retry_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
    return proc


def list_targets() -> list[str]:
    targets = _list_targets_raw()
    if targets:
        _select_hdc_target()
    return targets


def _restart_hdc_server() -> None:
    global _HDC_SELECTED_TARGET
    proc = _run_hdc_raw("start", "-r", timeout=15)
    _HDC_SELECTED_TARGET = None
    if proc.returncode != 0:
        logger.warning("hdc start -r 失败: %s", (proc.stderr or proc.stdout or "").strip())
    _try_tconn_emulator()
    _select_hdc_target(force_refresh=True)


def _list_emulator_instances() -> list[dict[str, Any]]:
    env = _reload_harmony_env()
    emulator_bin = Path(env["emulator"])
    if not emulator_bin.is_file():
        return []
    proc = subprocess.run(
        [str(emulator_bin), "-list", "-details"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("Emulator -list -details 失败: %s", (proc.stderr or proc.stdout or "").strip())
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        logger.warning("Emulator -list -details 返回非 JSON")
        return []
    return data if isinstance(data, list) else []


def _find_emulator_instance(name: str) -> dict[str, Any] | None:
    for item in _list_emulator_instances():
        if str(item.get("name") or "").strip() == name.strip():
            return item
    return None


def _is_emulator_running(name: str) -> bool:
    item = _find_emulator_instance(name)
    return bool(item and str(item.get("isRunning") or "").lower() == "true")


def _accept_emulator_license(emulator_bin: Path) -> None:
    proc = subprocess.run(
        [str(emulator_bin), "-license", "accept"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("Emulator 协议自动接受失败: %s", (proc.stderr or proc.stdout or "").strip()[:300])


def _start_emulator(emulator_bin: Path, name: str) -> None:
    _accept_emulator_license(emulator_bin)
    logger.info("正在启动 HarmonyOS 模拟器：%s", name)
    proc = subprocess.Popen(
        [str(emulator_bin), "-start", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    time.sleep(1.0)
    if proc.poll() is not None and proc.stderr is not None:
        err = proc.stderr.read().decode("utf-8", errors="ignore").strip()
        if err:
            raise RuntimeError(f"模拟器启动失败：{err[:500]}")


def _resolve_boot_timeout() -> int:
    env = _reload_harmony_env()
    try:
        return max(60, int(env.get("boot_timeout") or DEVICE_BOOT_TIMEOUT_SEC))
    except ValueError:
        return DEVICE_BOOT_TIMEOUT_SEC


def wait_for_device(
    timeout_sec: int | None = None,
    *,
    emulator_name: str | None = None,
) -> str:
    timeout = timeout_sec or _resolve_boot_timeout()
    deadline = time.time() + timeout
    poll = 0
    while time.time() < deadline:
        targets = list_targets()
        if targets:
            logger.info("hdc 已连接设备：%s", targets[0])
            return targets[0]

        poll += 1
        if poll % 5 == 0:
            _restart_hdc_server()
            if emulator_name and _is_emulator_running(emulator_name):
                logger.info("模拟器 %s 进程已运行，尝试 hdc tconn…", emulator_name)
                _try_tconn_emulator()
            else:
                logger.info("等待模拟器启动并建立 hdc 连接…")

        time.sleep(2)

    hint = ""
    if emulator_name:
        item = _find_emulator_instance(emulator_name)
        running = str((item or {}).get("isRunning") or "false")
        hint = f" 模拟器「{emulator_name}」isRunning={running}。"
    raise TimeoutError(
        f"{timeout}s 内未检测到 hdc 设备。{hint}"
        "可在 DevEco Device Manager 手动启动模拟器后重试；"
        "或确认 .env 中 HARMONY_EMULATOR_NAME 与 Emulator -list 输出一致。"
    )


def ensure_emulator_running() -> str:
    """确保 hdc 可用：已有设备则复用，否则自动启动 .env 配置的模拟器。"""
    targets = _list_targets_raw()
    if not targets:
        _try_tconn_emulator()
        targets = _list_targets_raw()
    if targets:
        target = _select_hdc_target(force_refresh=True) or targets[0]
        logger.info("复用已连接 hdc 设备：%s", target)
        return target

    env = _reload_harmony_env()
    emulator_bin = Path(env["emulator"])
    if not emulator_bin.is_file():
        raise FileNotFoundError(f"模拟器可执行文件不存在：{emulator_bin}")

    avd = env["emulator_name"]
    instance = _find_emulator_instance(avd)
    if instance is None:
        available = [str(item.get("name") or "") for item in _list_emulator_instances()]
        available = [name for name in available if name]
        raise RuntimeError(
            f"未找到名为「{avd}」的模拟器实例。"
            f"可用实例：{', '.join(available) or '无'}。"
            "请在 .env 设置 HARMONY_EMULATOR_NAME 为本机 Emulator -list 中的名称。"
        )

    device_type = str(instance.get("deviceType") or "")
    if device_type and device_type != "phone":
        logger.warning("模拟器 %s 类型为 %s（工程一般为 phone）", avd, device_type)

    if not _is_emulator_running(avd):
        _start_emulator(emulator_bin, avd)
    else:
        logger.info("模拟器 %s 已在运行，尝试 hdc tconn 并等待连接", avd)
        _try_tconn_emulator()
        if not _list_targets_raw():
            _restart_hdc_server()

    return wait_for_device(emulator_name=avd)


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
    ensure_hdc_device()
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


def _flutter_bin() -> str:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    return os.getenv("FLUTTER_BIN", shutil.which("flutter") or "flutter")


def _flutter_should_skip_pub(flutter_root: Path) -> bool:
    """依赖已解析时跳过 pub get，避免每次 build 多等数十秒。

    FLUTTER_BUILD_SKIP_PUB=0 强制每次跑 pub；未设置时若已有 package_config 则跳过。
    """
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    raw = (os.getenv("FLUTTER_BUILD_SKIP_PUB") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return (flutter_root / ".dart_tool/package_config.json").is_file()


def _run_flutter_build(
    cmd: list[str],
    *,
    cwd: Path,
    label: str,
    timeout: int = 900,
    progress_interval_sec: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """跑 Flutter 编译，并周期性上报进度（避免 UI 长时间停在同一状态）。"""
    from app.tool_progress import report_tool_progress

    mode = "release" if "--release" in cmd else ("debug" if "--debug" in cmd else "default")
    logger.info("开始编译 Flutter %s（mode=%s）…", label, mode)
    report_tool_progress(
        f"编译 Flutter {label}",
        step=f"编译 Flutter {label}",
        detail=f"mode={mode}，首次/冷启动可能较久…",
        status="running",
    )
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    last_report = started
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                chunks.append(line)
                stripped = line.strip()
                if stripped and (
                    "error" in stripped.lower()
                    or stripped.startswith(">")
                    or "Building" in stripped
                    or "Running" in stripped
                    or "✓" in stripped
                    or "√" in stripped
                ):
                    logger.info("[flutter %s] %s", label, stripped[:240])
            now = time.time()
            if now - started > timeout:
                proc.kill()
                raise TimeoutError(f"flutter 编译 {label} 超时（{timeout}s）")
            if now - last_report >= progress_interval_sec:
                elapsed = int(now - started)
                report_tool_progress(
                    f"编译 Flutter {label}",
                    step=f"编译 Flutter {label}",
                    detail=f"mode={mode}，已耗时 {elapsed}s…",
                    status="running",
                )
                last_report = now
        returncode = proc.wait(timeout=5)
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise

    output = "".join(chunks)
    result = subprocess.CompletedProcess(cmd, returncode, output, "")
    logger.info("Flutter %s 编译完成，耗时 %.0fs", label, time.time() - started)
    return result


def build_flutter_hap(
    flutter_root: Path,
    *,
    release: bool = False,
    dart_defines: dict[str, str] | None = None,
    label: str = "",
) -> Path:
    flutter_root = flutter_root.expanduser().resolve()
    if not (flutter_root / "pubspec.yaml").is_file():
        raise FileNotFoundError(f"Flutter 工程不存在：{flutter_root}")

    # 关键：flutter build hap 默认就是 --release（含 Dart AOT），截图场景必须显式 --debug。
    cmd = [_flutter_bin(), "build", "hap", "--release" if release else "--debug"]
    if _flutter_should_skip_pub(flutter_root):
        cmd.append("--no-pub")
    if dart_defines:
        for key, value in dart_defines.items():
            cmd.extend(["--dart-define", f"{key}={value}"])

    hint = label or "HAP"
    proc = _run_flutter_build(cmd, cwd=flutter_root, label=hint, timeout=900)
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-5000:]
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
    ensure_hdc_device()
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


def _analyze_screenshot_image(path: Path) -> dict[str, float | str | bool]:
    """粗采样统计截图质量（白屏 / 黑屏 / 低对比度）。"""
    empty: dict[str, float | str | bool] = {
        "black_ratio": 1.0,
        "white_ratio": 0.0,
        "max_stddev": 0.0,
        "invalid": True,
        "reason": "missing_or_tiny",
    }
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return {**empty, "reason": "no_pillow"}

    if not path.is_file() or path.stat().st_size < 1024:
        return empty

    img = Image.open(path).convert("RGB")
    sample = img.resize((180, 320))
    try:
        raw = sample.get_flattened_data()
        if raw and isinstance(raw[0], (tuple, list)):
            pixels = [tuple(p[:3]) for p in raw]
        else:
            pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
    except (AttributeError, TypeError, IndexError):
        pixels = list(sample.getdata())
    total = len(pixels)
    whiteish = sum(1 for r, g, b in pixels if r > 250 and g > 250 and b > 250)
    blackish = sum(1 for r, g, b in pixels if r < 15 and g < 15 and b < 15)
    white_ratio = whiteish / total
    black_ratio = blackish / total
    stat = ImageStat.Stat(sample)
    max_stddev = max(stat.stddev)

    reason = ""
    invalid = False
    if black_ratio >= SCREENSHOT_BLACK_RATIO_THRESHOLD:
        invalid = True
        reason = "mostly_black"
    elif white_ratio > 0.88:
        invalid = True
        reason = "mostly_white"
    elif max_stddev < 5.0:
        invalid = True
        reason = "low_contrast"

    return {
        "black_ratio": black_ratio,
        "white_ratio": white_ratio,
        "max_stddev": max_stddev,
        "invalid": invalid,
        "reason": reason,
    }


def _is_mostly_blank_image(path: Path) -> bool:
    """检测截图是否无效（白屏、黑屏或低对比度未渲染）。"""
    stats = _analyze_screenshot_image(path)
    return bool(stats.get("invalid"))


def _invalid_screenshot_message(path: Path, stats: dict[str, float | str | bool]) -> str:
    reason = str(stats.get("reason") or "invalid")
    black_ratio = float(stats.get("black_ratio") or 0.0)
    if reason == "mostly_black":
        return (
            f"截屏为纯黑（黑像素约 {black_ratio:.0%}）：模拟器内 snapshot/uitest 未读到真实画面，"
            "但你在 Emulator 窗口里可能仍能看到 App。"
            "请完全退出并冷启动模拟器（必要时 Wipe Data），执行 `hdc start -r` 后重试；"
            "或在真机上用 hdc 连接再截图。"
        )
    if reason == "mostly_white":
        return "截屏接近纯白，页面可能尚未渲染完成，可加大 HARMONY_WEBVIEW_RENDER_WAIT_SEC 后重试。"
    if reason == "low_contrast":
        return "截屏对比度过低（像空白/启动页），请延长渲染等待或检查是否停在 Splash。"
    return f"截屏无效（{reason}）：{path.name}"


def _recover_snapshot_pipeline() -> None:
    """黑屏/空白时尝试恢复 hdc 与 uitest 截屏通道。"""
    logger.warning("尝试恢复模拟器截屏通道（hdc 重启 + 唤醒 + uitest）…")
    _restart_hdc_server()
    time.sleep(1.2)
    run_hdc("shell", "power-shell", "wakeup", timeout=15)
    ensure_uitest_ready()
    time.sleep(1.5)


def _resize_png_to_store_size(png_path: Path) -> None:
    """商店截图目标 1080×1920。"""
    proc = subprocess.run(
        [
            "sips",
            "-z",
            str(SCREENSHOT_HEIGHT),
            str(SCREENSHOT_WIDTH),
            str(png_path),
            "--out",
            str(png_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"缩放截图失败: {proc.stdout} {proc.stderr}")


def capture_display_png_uitest(local_png: Path) -> bool:
    """uitest screenCap（优先于 snapshot_display，部分系统镜像更稳）。"""
    remote = "/data/local/tmp/harmony_auto_uitest.png"
    ensure_uitest_ready()
    proc = run_hdc("shell", "uitest", "screenCap", "-p", remote, timeout=60)
    combined = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0 and "ScreenCap saved" not in combined:
        logger.warning("uitest screenCap 失败: %s", combined.strip()[:240])
        return False

    local_png.parent.mkdir(parents=True, exist_ok=True)
    recv = run_hdc("file", "recv", remote, str(local_png), timeout=120)
    if recv.returncode != 0 or not local_png.is_file():
        logger.warning("拉取 uitest 截图失败: %s", (recv.stdout or recv.stderr or "").strip()[:240])
        return False
    return True


def _image_similarity(a: Path, b: Path) -> float:
    """粗粒度相似度 0~1（downsample + 像素差），用于识别品牌启动图。"""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return 0.0
    if not a.is_file() or not b.is_file():
        return 0.0
    size = (90, 160)
    img_a = Image.open(a).convert("RGB").resize(size)
    img_b = Image.open(b).convert("RGB").resize(size)
    diff = ImageChops.difference(img_a, img_b)
    stat = ImageStat.Stat(diff)
    # mean RGB 差越大越不像；255 归一
    mean_diff = sum(stat.mean) / (3.0 * 255.0)
    return max(0.0, min(1.0, 1.0 - mean_diff))


def _looks_like_flutter_splash(path: Path, splash_refs: list[Path] | None) -> bool:
    if not path.is_file():
        return True
    if _is_mostly_blank_image(path):
        return True
    for ref in splash_refs or []:
        if _image_similarity(path, ref) >= FLUTTER_SPLASH_SIMILARITY:
            return True
    return False


def _capture_screenshot_png_once(output_png: Path) -> None:
    """单次截屏：uitest PNG 优先，失败则 snapshot_display + 转 PNG。"""
    output_png.parent.mkdir(parents=True, exist_ok=True)
    tmp_full = output_png.with_name(output_png.stem + "_full.png")

    if SCREENSHOT_USE_UITEST and capture_display_png_uitest(tmp_full):
        shutil.copy2(tmp_full, output_png)
        if tmp_full.is_file() and tmp_full != output_png:
            tmp_full.unlink()
        _resize_png_to_store_size(output_png)
        return

    tmp_jpeg = output_png.with_suffix(".jpeg")
    capture_display_jpeg(tmp_jpeg)
    jpeg_to_png(tmp_jpeg, output_png)
    if tmp_jpeg.is_file():
        tmp_jpeg.unlink()
    if tmp_full.is_file():
        tmp_full.unlink()


def capture_screenshot_png(output_png: Path) -> Path:
    logger.info("截屏 → %s", output_png.name)
    started = time.time()
    _capture_screenshot_png_once(output_png)
    stats = _analyze_screenshot_image(output_png)
    logger.info(
        "截屏完成 %.1fs: %s（black=%.0f%% white=%.0f%% invalid=%s）",
        time.time() - started,
        output_png.name,
        float(stats.get("black_ratio", 0)) * 100,
        float(stats.get("white_ratio", 0)) * 100,
        stats.get("invalid"),
    )
    return output_png


def capture_screenshot_png_with_retry(output_png: Path) -> Path:
    """截屏；无效（黑/白/低对比）时恢复 hdc 并延长等待后重试。"""
    delay = SCREENSHOT_BLANK_RETRY_DELAY_SEC
    attempts = max(1, SCREENSHOT_BLANK_RETRY_MAX)
    last_stats: dict[str, float | str | bool] = {}

    for attempt in range(1, attempts + 1):
        capture_screenshot_png(output_png)
        last_stats = _analyze_screenshot_image(output_png)
        if not last_stats.get("invalid"):
            if attempt > 1:
                logger.info("重试截屏成功（第 %d 次）: %s", attempt, output_png.name)
            return output_png

        reason = str(last_stats.get("reason") or "")
        logger.warning(
            "截屏无效（%s，黑=%.0f%% 白=%.0f%%）(%d/%d): %s",
            reason,
            float(last_stats.get("black_ratio", 0)) * 100,
            float(last_stats.get("white_ratio", 0)) * 100,
            attempt,
            attempts,
            output_png.name,
        )

        if attempt >= attempts:
            msg = _invalid_screenshot_message(output_png, last_stats)
            if SCREENSHOT_FAIL_ON_INVALID:
                raise RuntimeError(msg)
            logger.warning("截屏仍无效，已用尽 %d 次重试: %s", attempts, msg)
            return output_png

        if reason == "mostly_black":
            _recover_snapshot_pipeline()
            delay = max(delay, 3.0)
        time.sleep(delay)
        delay = min(delay * 1.5, 8.0)
    return output_png


def capture_flutter_screenshot_png(
    output_png: Path,
    *,
    splash_refs: list[Path] | None = None,
) -> Path:
    """Flutter 专用：避开品牌启动图 / 加载中空白，轮询到主界面再落盘。"""
    delay = FLUTTER_SPLASH_RETRY_DELAY_SEC
    attempts = max(1, FLUTTER_SPLASH_RETRY_MAX)
    last_md5 = ""
    stable_hits = 0
    for attempt in range(1, attempts + 1):
        capture_screenshot_png(output_png)
        digest = ""
        try:
            digest = output_png.read_bytes()[:4096].hex()  # cheap stability probe
            # full md5 for stability
            import hashlib

            digest = hashlib.md5(output_png.read_bytes()).hexdigest()
        except OSError:
            digest = ""

        splash_like = _looks_like_flutter_splash(output_png, splash_refs)
        if not splash_like:
            if digest and digest == last_md5:
                stable_hits += 1
            else:
                stable_hits = 1
                last_md5 = digest
            # 非启动图且连续两帧稳定，或至少已非启动图
            if stable_hits >= 2 or attempt >= 2:
                if attempt > 1:
                    logger.info(
                        "Flutter 主界面截屏就绪（第 %d 次）: %s",
                        attempt,
                        output_png.name,
                    )
                return output_png
            time.sleep(0.45)
            continue

        if attempt >= attempts:
            logger.warning(
                "Flutter 截屏仍像启动图/加载页，已用尽 %d 次: %s",
                attempts,
                output_png.name,
            )
            return output_png
        logger.warning(
            "检测到 Flutter 启动图/加载页，%.1fs 后重试 (%d/%d): %s",
            delay,
            attempt + 1,
            attempts,
            output_png.name,
        )
        time.sleep(delay)
        delay = min(delay * 1.25, 4.0)
        last_md5 = digest
        stable_hits = 0
    return output_png


def build_entry_hap(project_root: Path | str) -> Path:
    """在 ohos 工程根目录执行 hvigor assembleHap（Flutter 工程同样走这条，由 flutter-hvigor-plugin 编译 Dart）。"""
    from app.tool_progress import report_tool_progress

    project_root = Path(project_root).expanduser().resolve()
    env = _reload_harmony_env()
    hvigorw = Path(env["hvigorw"])
    if not hvigorw.is_file():
        raise FileNotFoundError(f"hvigorw 未找到：{hvigorw}")

    cmd = [
        str(hvigorw),
        "assembleHap",
        "-p",
        "product=default",
        "-p",
        "module=entry@default",
    ]
    logger.info("开始 hvigor assembleHap: %s", project_root)
    report_tool_progress(
        "编译 HAP",
        step="编译 HAP",
        detail=f"hvigor assembleHap @ {project_root.name}",
        status="running",
    )
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    last_report = started
    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if line:
            chunks.append(line)
        now = time.time()
        if now - started > 600:
            proc.kill()
            raise TimeoutError("assembleHap 超时（600s）")
        if now - last_report >= 15:
            report_tool_progress(
                "编译 HAP",
                step="编译 HAP",
                detail=f"hvigor 已运行 {int(now - started)}s…",
                status="running",
            )
            last_report = now
    returncode = proc.wait(timeout=5)
    output = "".join(chunks)
    logger.info("assembleHap 完成，耗时 %.0fs", time.time() - started)

    signed = project_root / "entry/build/default/outputs/default/entry-default-signed.hap"
    unsigned = project_root / "entry/build/default/outputs/default/entry-default-unsigned.hap"
    if signed.is_file():
        return signed
    if unsigned.is_file():
        return unsigned
    if returncode != 0:
        raise RuntimeError(f"assembleHap 失败 (code={returncode}):\n{output[-4000:]}")
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
    flutter_root = flutter_root.expanduser().resolve()
    if not (flutter_root / "pubspec.yaml").is_file():
        raise FileNotFoundError(f"Flutter 工程不存在：{flutter_root}")

    # 上架包必须显式 mode；默认 release。勿省略，避免与 hap 一样踩「默认 release」歧义。
    cmd = [_flutter_bin(), "build", "app", "--release" if release else "--debug"]
    if _flutter_should_skip_pub(flutter_root):
        cmd.append("--no-pub")

    proc = _run_flutter_build(cmd, cwd=flutter_root, label="APP", timeout=900)
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-5000:]
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
