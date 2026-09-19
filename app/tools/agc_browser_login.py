"""一次性手动登录 AppGallery Connect，并保存 Playwright storage_state。

用法：
  python -m app.tools.agc_browser_login

浏览器打开后请完成登录；脚本会自动检测控制台页面并保存登录态。
也可在终端按 Enter 强制保存。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

from app.config import AGC_MYAPP_URL, AGC_STORAGE_STATE_PATH  # noqa: E402
from app.tools.agc_browser import (  # noqa: E402
    ensure_artifact_dir,
    is_agc_console_ready,
    save_storage_state,
)
from playwright.sync_api import sync_playwright  # noqa: E402


def _wait_enter_or_timeout(stop_event: threading.Event, timeout_sec: float) -> None:
    def _read_enter() -> None:
        try:
            input()
        except EOFError:
            pass
        stop_event.set()

    thread = threading.Thread(target=_read_enter, daemon=True)
    thread.start()
    stop_event.wait(timeout=timeout_sec)


def main() -> None:
    ensure_artifact_dir()
    AGC_STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"将打开浏览器：{AGC_MYAPP_URL}")
    print("请在浏览器中完成华为账号登录，并确认能看到「APP与元服务 / 新建发布」。")
    print("检测到控制台页面后会自动保存；也可随时回到终端按 Enter 保存。\n")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page.goto(AGC_MYAPP_URL, wait_until="domcontentloaded")

        stop = threading.Event()
        saved = False
        deadline = time.time() + 600

        while time.time() < deadline and not stop.is_set():
            if is_agc_console_ready(page):
                save_storage_state(context)
                saved = True
                print(f"\n登录态已自动保存：{AGC_STORAGE_STATE_PATH.resolve()}")
                break
            if sys.stdin.isatty():
                _wait_enter_or_timeout(stop, timeout_sec=2.0)
                if stop.is_set() and not saved:
                    save_storage_state(context)
                    saved = True
                    print(f"\n登录态已保存：{AGC_STORAGE_STATE_PATH.resolve()}")
                    break
            else:
                time.sleep(2)

        if not saved:
            print(
                "\n未检测到 AGC 控制台且未手动确认。"
                "若已登录，请重新运行本脚本并在登录完成后按 Enter。",
                file=sys.stderr,
            )
            sys.exit(1)

        browser.close()


if __name__ == "__main__":
    main()
