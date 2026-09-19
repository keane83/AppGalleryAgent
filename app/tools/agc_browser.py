"""AppGallery Connect 控制台浏览器自动化：启动浏览器、复用登录态。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from playwright.sync_api import Browser, BrowserContext, Frame, Locator, Page, Playwright, sync_playwright

from app.config import (
    AGC_BROWSER_HEADLESS,
    AGC_BROWSER_SLOW_MO_MS,
    AGC_BROWSER_USER_DATA_DIR,
    AGC_MYAPP_URL,
    AGC_STORAGE_STATE_PATH,
    BROWSER_ARTIFACT_DIR,
)

T = TypeVar("T")
logger = logging.getLogger("haa.agc_browser")

LOGIN_URL_HINTS = (
    "login",
    "signin",
    "passport",
    "oauth",
    "id1.cloud.huawei",
    "id.cloud.huawei",
)
CONSOLE_MARKERS = ("新建发布", "APP与元服务", "HarmonyOS", "myApp")


def ensure_artifact_dir() -> Path:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return BROWSER_ARTIFACT_DIR


def storage_state_exists() -> bool:
    return AGC_STORAGE_STATE_PATH.is_file() and AGC_STORAGE_STATE_PATH.stat().st_size > 0


def save_storage_state(context: BrowserContext) -> Path:
    """将当前浏览器上下文登录态写入 storage_state 文件。"""
    AGC_STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(AGC_STORAGE_STATE_PATH))
    logger.info("AGC 登录态已保存：%s", AGC_STORAGE_STATE_PATH.resolve())
    return AGC_STORAGE_STATE_PATH


def run_sync_in_thread(fn: Callable[..., T], *args, **kwargs) -> T:
    """在独立线程跑 Playwright Sync API，避免与 FastAPI asyncio loop 冲突。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


def save_screenshot(page: Page, name: str) -> str:
    ensure_artifact_dir()
    path = BROWSER_ARTIFACT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    return str(path.resolve())


def is_agc_console_ready(page: Page) -> bool:
    """是否已进入 AGC 控制台（非登录页）。"""
    for marker in CONSOLE_MARKERS:
        try:
            loc = page.get_by_text(marker, exact=False).first
            if loc.is_visible(timeout=1_500):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def is_agc_login_page(page: Page) -> bool:
    """是否被重定向到华为账号登录页。"""
    url = (page.url or "").lower()
    if any(hint in url for hint in LOGIN_URL_HINTS):
        return True
    try:
        login_visible = page.get_by_text(re.compile(r"华为账号|账号登录|验证码登录|扫码登录")).first
        if login_visible.is_visible(timeout=800):
            return not is_agc_console_ready(page)
    except Exception:  # noqa: BLE001
        pass
    return False


def ensure_agc_logged_in(
    page: Page,
    context: BrowserContext,
    *,
    interactive: bool = True,
    max_wait_sec: int = 300,
    refresh_storage: bool = True,
) -> None:
    """确保已进入 AGC 控制台；若登录态过期则在当前浏览器窗口等待用户登录并自动续存。"""
    page.wait_for_timeout(800)
    if is_agc_console_ready(page):
        return

    if is_agc_login_page(page) or not is_agc_console_ready(page):
        if not interactive:
            raise RuntimeError(
                "AGC 登录态已失效或未登录。请运行：python -m app.tools.agc_browser_login "
                f"（保存到 {AGC_STORAGE_STATE_PATH}）"
            )

        print(
            "\n[AGC 浏览器] 检测到登录页或登录态已过期。\n"
            "请在已打开的 Chromium 窗口中完成华为账号登录，"
            "直到看到「APP与元服务 / 新建发布 / HarmonyOS」页面。\n"
            f"脚本将自动等待（最多 {max_wait_sec} 秒）并保存新的登录态…\n"
        )

        deadline = time.time() + max_wait_sec
        while time.time() < deadline:
            if is_agc_console_ready(page):
                if refresh_storage and not AGC_BROWSER_USER_DATA_DIR:
                    save_storage_state(context)
                print("[AGC 浏览器] 登录成功，继续执行自动化。\n")
                return
            page.wait_for_timeout(1_000)

        raise RuntimeError(
            f"等待 AGC 登录超时（{max_wait_sec}s）。"
            f"请手动运行 python -m app.tools.agc_browser_login"
        )


def open_harmony_app_from_myapp_list(
    page: Page,
    app_name: str,
    bundle_id: str = "",
) -> None:
    """在 myApp → HarmonyOS 列表中按应用名称进入详情（点击名称列，非包名列）。"""
    name = (app_name or "").strip()
    bundle = (bundle_id or "").strip()
    if not name:
        raise ValueError("appName 不能为空")

    try:
        tab = page.get_by_text("HarmonyOS", exact=True).first
        if tab.is_visible():
            tab.click(timeout=3_000)
            page.wait_for_timeout(400)
    except Exception:  # noqa: BLE001
        pass

    page.wait_for_timeout(300)

    # 列表上方搜索框（Element Plus）
    search_candidates = [
        page.locator("input.el-input__inner").first,
        page.locator("input[placeholder*='应用']").first,
        page.locator("input[placeholder*='搜索']").first,
        page.locator("input[placeholder*='名称']").first,
    ]
    for search in search_candidates:
        try:
            if search.count() and search.first.is_visible():
                box = search.first
                box.click(timeout=2_000)
                box.fill("", timeout=2_000)
                box.fill(name, timeout=2_000)
                box.press("Enter")
                filtered = page.locator(".el-table__body-wrapper tbody tr").filter(has_text=name)
                try:
                    filtered.first.wait_for(state="visible", timeout=5_000)
                except Exception:  # noqa: BLE001
                    page.wait_for_timeout(400)
                break
        except Exception:  # noqa: BLE001
            continue

    row = page.locator(".el-table__body-wrapper tbody tr").filter(has_text=name)
    if bundle:
        matched = row.filter(has_text=bundle)
        if matched.count():
            row = matched

    row.first.wait_for(state="visible", timeout=15_000)

    # 名称在第一列，通常是蓝色可点击链接
    name_cell = row.first.locator("td").first
    click_candidates = [
        name_cell.locator("a").filter(has_text=name),
        name_cell.locator(".el-link").filter(has_text=name),
        name_cell.get_by_text(name, exact=False),
        name_cell,
    ]
    last_error: Exception | None = None
    for locator in click_candidates:
        try:
            target = locator.first
            target.wait_for(state="visible", timeout=3_000)
            target.click(timeout=5_000)
            wait_for_app_detail_ready(page)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    hint = f"应用名「{name}」"
    if bundle:
        hint += f"（包名 {bundle}）"
    raise RuntimeError(f"HarmonyOS 列表中未能点击进入 {hint}：{last_error}")


def wait_for_app_detail_ready(page: Page, *, timeout_ms: int = 15_000) -> None:
    """应用详情页左侧菜单就绪（不等右侧 iframe 内容）。"""
    markers = ("应用信息", "版本信息", "用户与权限", "协议服务")
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            menu = page.locator(".el-menu, aside, [class*='side-menu']").first
            if menu.count() and menu.is_visible(timeout=400):
                for marker in markers:
                    item = page.locator(".el-menu-item, .el-sub-menu__title").filter(has_text=marker)
                    if item.count():
                        return
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(150)
    page.locator(".el-menu-item").first.wait_for(state="visible", timeout=3_000)


def wait_for_app_detail_content_loaded(page: Page, *, timeout_ms: int = 20_000) -> None:
    """应用详情主内容区加载完成（侧栏 + 去掉 loading 遮罩）。"""
    wait_for_app_detail_ready(page, timeout_ms=min(timeout_ms, 10_000))
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        loading = False
        for frame in page.frames:
            try:
                mask = frame.locator(".el-loading-mask:visible, [class*='loading-mask']:visible").first
                if mask.count() and mask.is_visible(timeout=200):
                    loading = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not loading:
            return
        page.wait_for_timeout(200)


def click_sidebar_menu_item(
    page: Page,
    item_name: str,
    *,
    parent_group: str = "",
    timeout_ms: int = 2_500,
) -> None:
    """点击应用详情左侧菜单项；可选先展开 parent_group（如「服务」）。"""
    name = item_name.strip()
    if not name:
        raise ValueError("item_name 不能为空")

    if parent_group:
        group = page.locator(".el-sub-menu").filter(
            has=page.locator(".el-sub-menu__title").filter(
                has_text=re.compile(rf"^\s*{re.escape(parent_group)}\s*$")
            )
        )
        if group.count():
            sub_title = group.first.locator(".el-sub-menu__title").first
            item_probe = page.locator(".el-menu-item").filter(
                has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
            )
            try:
                if item_probe.count() and not item_probe.first.is_visible(timeout=400):
                    sub_title.click(timeout=timeout_ms)
                    page.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                try:
                    sub_title.click(timeout=timeout_ms)
                    page.wait_for_timeout(120)
                except Exception:  # noqa: BLE001
                    pass

    candidates = [
        page.locator(".el-menu-item").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")),
        page.locator(".el-menu-item").filter(has_text=name),
        page.get_by_role("menuitem", name=re.compile(re.escape(name))),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            target = locator.first
            target.scroll_into_view_if_needed(timeout=timeout_ms)
            target.wait_for(state="visible", timeout=timeout_ms)
            target.click(timeout=timeout_ms)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"未找到侧边栏「{name}」：{last_error}")


def frame_has_loading_mask(frame: Frame) -> bool:
    try:
        mask = frame.locator(".el-loading-mask:visible, [class*='loading-mask']:visible").first
        return bool(mask.count() and mask.is_visible(timeout=120))
    except Exception:  # noqa: BLE001
        return False


def click_first_visible(
    page: Page,
    locators: list[Locator],
    *,
    label: str,
    timeout_ms: int = 8_000,
) -> None:
    last_error: Exception | None = None
    for locator in locators:
        try:
            target = locator.first
            target.wait_for(state="visible", timeout=timeout_ms)
            target.click(timeout=min(timeout_ms, 5_000))
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"未找到可点击的「{label}」：{last_error}")


def wait_for_version_detail_loaded(page: Page, *, timeout_ms: int = 30_000) -> Frame:
    """点击「准备提交」后右侧 iframe 常空白 loading，需等待「隐私政策」出现。"""
    deadline = time.time() + timeout_ms / 1000
    last_loading = True
    while time.time() < deadline:
        loading = any(frame_has_loading_mask(frame) for frame in page.frames)
        last_loading = loading

        if not loading:
            for frame in page.frames:
                try:
                    label = frame.get_by_text("隐私政策", exact=False).first
                    if label.count() and label.is_visible(timeout=250):
                        return frame
                    marked = frame.locator("[data-agc-privacy-select='1']").first
                    if marked.count() and marked.is_visible(timeout=250):
                        return frame
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(250)

    hint = "仍在 loading" if last_loading else "loading 已结束但未渲染"
    raise RuntimeError(
        f"版本详情页未加载完成（{hint}，未找到「隐私政策」）；"
        f"{debug_frames_summary(page, probe_text='隐私政策')}"
    )


def debug_frames_summary(page: Page, *, probe_text: str = "") -> str:
    """列出所有 frame 及可选探测文本数量，便于排查 iframe 内元素找不到的问题。"""
    parts: list[str] = [f"main_url={page.url!r}"]
    for index, frame in enumerate(page.frames):
        try:
            extra = ""
            if probe_text:
                count = frame.get_by_text(probe_text, exact=False).count()
                extra = f" text[{probe_text!r}]={count}"
            parts.append(f"frame[{index}] url={frame.url!r}{extra}")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"frame[{index}] error={exc}")
    return "; ".join(parts)


def resolve_frame_with_visible_text(
    page: Page,
    *markers: str | re.Pattern[str],
    timeout_ms: int = 20_000,
    poll_ms: int = 400,
) -> Frame:
    """在 page 的所有 frame（含 iframe）中查找包含可见 marker 文本的 frame。"""
    if not markers:
        raise ValueError("markers 不能为空")

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            for marker in markers:
                try:
                    if isinstance(marker, str):
                        loc = frame.get_by_text(marker, exact=False).first
                    else:
                        loc = frame.locator("body").get_by_text(marker).first
                    if loc.count() and loc.is_visible(timeout=300):
                        return frame
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(poll_ms)

    raise RuntimeError(
        f"未在任意 frame 中找到可见文本 {markers!r}；{debug_frames_summary(page, probe_text='新建协议')}"
    )


def click_text_in_content_area(frame: Frame, text: str) -> None:
    """在 frame 内点击包含 text 的可见控件，优先主内容区（非左侧菜单）。"""
    clicked = frame.evaluate(
        """
        ([text]) => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) return false;
            const s = window.getComputedStyle(el);
            return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0;
          };
          const matches = [];
          const nodes = document.querySelectorAll(
            'button, a, .el-button, [role="button"], span, div'
          );
          for (const el of nodes) {
            const label = (el.innerText || el.textContent || '').trim();
            if (!label.includes(text)) continue;
            if (!visible(el)) continue;
            const r = el.getBoundingClientRect();
            if (r.left < window.innerWidth * 0.18) continue;
            matches.push(el);
          }
          matches.sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
          const target = matches.find((el) =>
            el.matches('button, a, .el-button, [role="button"]')
          ) || matches[0];
          if (!target) return false;
          target.click();
          return true;
        }
        """,
        [text],
    )
    if not clicked:
        raise RuntimeError(f"JS 兜底未能在 frame 内点击「{text}」")


def dismiss_blocking_tips(page: Page) -> None:
    """关闭「知道了 / 关闭」类阻塞弹窗。"""
    for name in ("知道了", "我知道了", "关闭"):
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=2_000)
                page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001
            pass


def dismiss_menu_guide_popup(page: Page) -> None:
    """关闭左侧「全新自定义菜单栏」等引导浮层。"""
    for label in ("知道了", "了解详情", "关闭"):
        try:
            popup = page.locator(".el-popover, .el-dialog, [class*='guide']").filter(
                has_text=re.compile(r"自定义菜单栏|菜单栏")
            )
            if popup.count():
                btn = popup.first.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=2_000)
                    page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass
    dismiss_blocking_tips(page)


def _version_info_submenu(page: Page) -> Locator:
    return page.locator(".el-sub-menu").filter(
        has=page.locator(".el-sub-menu__title").filter(has_text=re.compile(r"^版本信息$"))
    ).first


def _ready_submit_version_menu_item(page: Page) -> Locator:
    """「版本信息」下子项：可能是「x.x.x 准备提交」，也可能只有「准备提交」。"""
    version_sub = _version_info_submenu(page)
    scope = version_sub if version_sub.count() else page
    return scope.locator("li.el-menu-item").filter(
        has_text=re.compile(r"(?:\d+\.\d+\.\d+\s*)?准备提交")
    ).first


def _ensure_version_info_submenu_expanded(page: Page) -> None:
    version_sub = _version_info_submenu(page)
    if not version_sub.count():
        return
    try:
        is_open = version_sub.evaluate("el => el.classList.contains('is-open')")
    except Exception:  # noqa: BLE001
        is_open = False
    if is_open:
        return
    version_sub.locator(".el-sub-menu__title").first.click(timeout=2_000)
    page.wait_for_timeout(200)


def _is_ready_submit_version_active(page: Page) -> bool:
    item = _ready_submit_version_menu_item(page)
    if not item.count():
        return False
    try:
        return bool(item.evaluate("el => el.classList.contains('is-active')"))
    except Exception:  # noqa: BLE001
        return False


def navigate_to_version_ready_submit(page: Page) -> None:
    """展开「版本信息」后点击「准备提交」子项（可有版本号前缀）。"""
    dismiss_menu_guide_popup(page)
    _ensure_version_info_submenu_expanded(page)

    item = _ready_submit_version_menu_item(page)
    if not item.count():
        raise RuntimeError("未找到「准备提交」版本菜单项")

    item.scroll_into_view_if_needed(timeout=4_000)

    if _is_ready_submit_version_active(page):
        return

    item.click(timeout=3_000, force=True)
    page.wait_for_timeout(300)


def open_myapp_page(
    page: Page,
    context: BrowserContext | None = None,
    *,
    timeout_ms: int = 60_000,
    ensure_login: bool = True,
) -> None:
    page.goto(AGC_MYAPP_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(500)
    if ensure_login and context is not None:
        ensure_agc_logged_in(page, context)
        # 登录完成后重新打开 myApp，避免仍停留在登录回调页
        if not is_agc_console_ready(page):
            page.goto(AGC_MYAPP_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(500)
        ensure_agc_logged_in(page, context, refresh_storage=False)


@contextmanager
def launch_agc_context(
    *,
    headless: bool | None = None,
    require_login: bool = True,
    allow_interactive_login: bool = True,
) -> Iterator[tuple[Playwright, Browser | None, BrowserContext, Page]]:
    """启动 Chromium 并打开 AGC 页面，自动加载 storage_state 或持久化 Profile。"""
    use_headless = AGC_BROWSER_HEADLESS if headless is None else headless
    use_persistent = bool(AGC_BROWSER_USER_DATA_DIR)

    if (
        require_login
        and not storage_state_exists()
        and not use_persistent
        and not allow_interactive_login
    ):
        raise RuntimeError(
            "尚未保存 AGC 登录态。请先运行：python -m app.tools.agc_browser_login "
            f"（将生成 {AGC_STORAGE_STATE_PATH}）"
        )

    ensure_artifact_dir()
    playwright = sync_playwright().start()
    browser: Browser | None = None

    if use_persistent:
        profile_dir = Path(AGC_BROWSER_USER_DATA_DIR).expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=use_headless,
            slow_mo=AGC_BROWSER_SLOW_MO_MS,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
    else:
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

    page = context.pages[0] if context.pages else context.new_page()
    try:
        yield playwright, browser, context, page
    finally:
        context.close()
        if browser is not None:
            browser.close()
        playwright.stop()
