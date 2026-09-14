"""AppGallery Connect 控制台：基于 packageName（bundle id）新建发布应用。

流程：打开 myApp → 点击「新建发布」→ 下拉选择包名 → 等待自动填充 → 点击「确认」。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from langchain_core.tools import tool
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.config import AGC_MYAPP_URL, BROWSER_ARTIFACT_DIR
from app.tools.agc_browser import (
    launch_agc_context,
    open_myapp_page,
    run_sync_in_thread,
    save_screenshot,
)

AUTO_FILL_WAIT_MS = 1500


def _looks_like_bundle_id(package_name: str) -> bool:
    # 至少三段：com.xxx.yyy
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+){2,}", package_name))


def _dismiss_blocking_tips(page: Page) -> None:
    """关掉可能挡住操作的引导气泡。"""
    for name in ("知道了", "我知道了", "关闭"):
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=2_000)
                page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001
            pass


def _click_new_publish(page: Page) -> None:
    # 页面右上角蓝色「新建发布」
    candidates = [
        page.get_by_role("button", name=re.compile(r"新建发布")),
        page.locator("button:has-text('新建发布')"),
        page.get_by_text("新建发布", exact=True),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            target = locator.first
            target.wait_for(state="visible", timeout=10_000)
            target.click(timeout=5_000)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"未找到「新建发布」按钮：{last_error}")


def _modal(page: Page):
    """新建发布浮层（Element Plus dialog）。"""
    return page.locator(".el-overlay-dialog[aria-label*='发布'], [role='dialog']").last


def _wait_modal(page: Page) -> None:
    dialog = _modal(page)
    dialog.wait_for(state="visible", timeout=15_000)
    dialog.get_by_text("请选择已创建的应用包名").first.wait_for(
        state="visible", timeout=10_000
    )


def _open_package_dropdown(page: Page) -> None:
    """在浮层内点开「应用包名」下拉（第一个 el-select）。"""
    dialog = _modal(page)
    select = dialog.locator(".el-select").first
    select.wait_for(state="visible", timeout=8_000)
    # 点选择器本体，比点占位符文字更稳
    select.click(timeout=5_000)


def _select_package_name(page: Page, package_name: str) -> None:
    """打开应用包名下拉并选中目标 bundle id。

    下拉项文案形如「com.xxx.yyy -- 应用名」，按包名前缀匹配即可。
    """
    dialog = _modal(page)
    _open_package_dropdown(page)

    # 包名下拉可搜索：往当前 select 的 input 里填，缩短长列表定位
    try:
        search = dialog.locator(".el-select").first.locator("input.el-select__input")
        if search.count() and search.first.is_visible():
            search.first.fill(package_name, timeout=1_500)
            page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass

    # 选项挂在 body 下的 el-select__popper；文案含「package -- name」
    option = page.locator(".el-select__popper:visible li").filter(
        has_text=re.compile(rf"^{re.escape(package_name)}(\s|$)")
    ).first
    try:
        option.wait_for(state="visible", timeout=6_000)
        option.click(timeout=3_000)
        return
    except Exception:
        # 兜底：包含包名即可（仍排除「新增」这类无关项）
        fallback = page.locator(".el-select__popper:visible li").filter(
            has_text=package_name
        ).first
        fallback.wait_for(state="visible", timeout=4_000)
        text = (fallback.inner_text(timeout=1_000) or "").strip()
        if text == "新增" or not text.startswith(package_name):
            raise RuntimeError(
                f"下拉菜单中未找到包名「{package_name}」（可见项类似：{text!r}）。"
                "请确认该 APP ID 已创建且尚未发布。"
            )
        fallback.click(timeout=3_000)


def _wait_autofill(page: Page) -> None:
    """等应用名称被自动填上；最多等 AUTO_FILL_WAIT_MS。"""
    deadline = time.time() + AUTO_FILL_WAIT_MS / 1000
    dialog = _modal(page)
    name_input = dialog.locator(
        "input[placeholder*='应用名称'], input[placeholder*='新应用名称']"
    ).first
    while time.time() < deadline:
        try:
            if name_input.count():
                value = name_input.input_value(timeout=300)
            else:
                value = dialog.locator("input").nth(1).input_value(timeout=300)
            if value and value.strip() and "请填写" not in value:
                return
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(120)
    page.wait_for_timeout(400)


def _click_confirm(page: Page) -> None:
    dialog = _modal(page)
    candidates = [
        dialog.get_by_role("button", name=re.compile(r"^\s*确认\s*$")),
        dialog.locator("button:has-text('确认')"),
        dialog.get_by_text("确认", exact=True),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            target = locator.last
            target.wait_for(state="visible", timeout=8_000)
            target.click(timeout=5_000)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"未找到「确认」按钮：{last_error}")


def create_publish_app_by_package_name(package_name: str) -> dict[str, Any]:
    name = (package_name or "").strip()
    if not name:
        raise ValueError("packageName（bundle id）不能为空")
    if not _looks_like_bundle_id(name):
        raise ValueError(
            f"packageName 格式看起来不正确：{name}（期望类似 com.example.app）"
        )

    screenshot_before = ""
    screenshot_after = ""
    with launch_agc_context(headless=False) as (_pw, _browser, _context, page):
        try:
            open_myapp_page(page)
            _dismiss_blocking_tips(page)
            # 确保在 HarmonyOS 页签
            try:
                harmony_tab = page.get_by_text("HarmonyOS", exact=True).first
                if harmony_tab.is_visible():
                    harmony_tab.click(timeout=3_000)
                    page.wait_for_timeout(400)
            except Exception:  # noqa: BLE001
                pass

            _click_new_publish(page)
            _wait_modal(page)
            screenshot_before = save_screenshot(page, f"publish_modal_before_{int(time.time())}.png")

            _select_package_name(page, name)
            _wait_autofill(page)
            screenshot_after = save_screenshot(page, f"publish_modal_filled_{int(time.time())}.png")

            _click_confirm(page)
            page.wait_for_timeout(800)
            final_shot = save_screenshot(page, f"publish_confirm_after_{int(time.time())}.png")

            return {
                "ok": True,
                "packageName": name,
                "myAppUrl": AGC_MYAPP_URL,
                "screenshotBefore": screenshot_before,
                "screenshotFilled": screenshot_after,
                "screenshotAfter": final_shot,
                "artifactDir": str(BROWSER_ARTIFACT_DIR.resolve()),
            }
        except Exception as exc:
            fail_shot = ""
            try:
                fail_shot = save_screenshot(page, f"publish_failed_{int(time.time())}.png")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"{exc}；失败截图={fail_shot or '(无)'}"
            ) from exc



@tool
def create_app_gallery_publish_app(packageName: str) -> str:
    """在 AppGallery Connect 网页控制台「新建发布」一个 HarmonyOS 应用。

    因官方无对应 API，本工具使用 Playwright 打开
    https://developer.huawei.com/consumer/cn/service/josp/agc/index.html#/myApp ，
    点击「新建发布」，在浮层「应用包名」下拉中选择 packageName（bundle id），
    等待应用名称等自动填充后点击「确认」。

    参数：
    - packageName: 应用包名 / bundle id，例如 com.keane.ppa
      （通常来自 create_app_gallery_app_id，或用户已在后台创建好的 APP ID 包名）

    使用前需先运行：python -m app.tools.agc_browser_login 保存登录态。
    """
    try:
        # FastAPI/uvicorn 线程里已有 asyncio loop，Playwright Sync 必须放到独立线程
        result = run_sync_in_thread(create_publish_app_by_package_name, packageName)
    except PlaywrightTimeoutError as exc:
        return f"新建发布失败（页面超时）: {exc}"
    except Exception as exc:
        return f"新建发布失败: {exc}"

    return json.dumps(result, ensure_ascii=False)
