"""一次性手动登录 AppGallery Connect，并保存 Playwright storage_state。

用法：
  python -m app.tools.agc_browser_login

浏览器打开后请完成登录；回到终端按 Enter，脚本会保存登录态供后续自动化复用。
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

from app.config import AGC_MYAPP_URL, AGC_STORAGE_STATE_PATH  # noqa: E402
from app.tools.agc_browser import ensure_artifact_dir  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> None:
    ensure_artifact_dir()
    AGC_STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"将打开浏览器：{AGC_MYAPP_URL}")
    print("请在浏览器中完成华为账号登录，并确认能看到「APP与元服务 / 新建发布」。")
    print("完成后回到此终端按 Enter 保存登录态…")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page.goto(AGC_MYAPP_URL, wait_until="domcontentloaded")
        input()
        context.storage_state(path=str(AGC_STORAGE_STATE_PATH))
        browser.close()

    print(f"登录态已保存：{AGC_STORAGE_STATE_PATH.resolve()}")


if __name__ == "__main__":
    main()
