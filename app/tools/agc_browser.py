"""AppGallery Connect 控制台浏览器自动化：启动浏览器、复用登录态。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from app.config import (
    AGC_BROWSER_HEADLESS,
    AGC_BROWSER_SLOW_MO_MS,
    AGC_MYAPP_URL,
    AGC_STORAGE_STATE_PATH,
    BROWSER_ARTIFACT_DIR,
)

T = TypeVar("T")


def ensure_artifact_dir() -> Path:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return BROWSER_ARTIFACT_DIR


def storage_state_exists() -> bool:
    return AGC_STORAGE_STATE_PATH.is_file() and AGC_STORAGE_STATE_PATH.stat().st_size > 0


def run_sync_in_thread(fn: Callable[..., T], *args, **kwargs) -> T:
    """在独立线程跑 Playwright Sync API，避免与 FastAPI asyncio loop 冲突。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


def save_screenshot(page: Page, name: str) -> str:
    ensure_artifact_dir()
    path = BROWSER_ARTIFACT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    return str(path.resolve())


@contextmanager
def launch_agc_context(
    *,
    headless: bool | None = None,
    require_login: bool = True,
) -> Iterator[tuple[Playwright, Browser, BrowserContext, Page]]:
    """启动 Chromium 并打开 AGC 页面，自动加载 storage_state。"""
    use_headless = AGC_BROWSER_HEADLESS if headless is None else headless
    if require_login and not storage_state_exists():
        raise RuntimeError(
            "尚未保存 AGC 登录态。请先运行：python -m app.tools.agc_browser_login "
            f"（将生成 {AGC_STORAGE_STATE_PATH}）"
        )

    ensure_artifact_dir()
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=use_headless,
        slow_mo=AGC_BROWSER_SLOW_MO_MS,
    )
    context_kwargs: dict = {
        "viewport": {"width": 1440, "height": 900},
        "locale": "zh-CN",
    }
    if storage_state_exists():
        context_kwargs["storage_state"] = str(AGC_STORAGE_STATE_PATH)

    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    try:
        yield playwright, browser, context, page
    finally:
        context.close()
        browser.close()
        playwright.stop()


def open_myapp_page(page: Page, timeout_ms: int = 60_000) -> None:
    page.goto(AGC_MYAPP_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(500)
