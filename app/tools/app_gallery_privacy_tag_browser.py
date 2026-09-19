"""AppGallery 控制台：「隐私标签信息录入」→ 是否涉及个人信息收集 = 否。

注意：这与 configure_app_gallery_privacy_agreement（隐私协议草稿/关联）无关。
定位策略与隐私政策下拉框一致：先等「隐私政策」iframe 就绪，再 JS 标记 + 轮询查找。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from playwright.sync_api import Frame, Locator, Page

from app.config import AGC_BROWSER_USER_DATA_DIR, AGC_STORAGE_STATE_PATH
from app.tools.agc_browser import (
    click_first_visible,
    debug_frames_summary,
    dismiss_blocking_tips,
    launch_agc_context,
    navigate_to_version_ready_submit,
    open_harmony_app_from_myapp_list,
    open_myapp_page,
    run_sync_in_thread,
    save_screenshot,
    storage_state_exists,
    wait_for_app_detail_content_loaded,
    wait_for_version_detail_loaded,
)

logger = logging.getLogger("haa.privacy_tag_browser")

_COLLECT_LABEL = "是否涉及个人信息收集"
_MARK_COLLECT_NO_JS = """
() => {
  document.querySelectorAll('[data-agc-privacy-collect-no]').forEach((el) => {
    el.removeAttribute('data-agc-privacy-collect-no');
  });

  for (const item of document.querySelectorAll('.el-form-item, tr, li')) {
    const labelEl = item.querySelector('.el-form-item__label, label');
    const labelText = (labelEl && labelEl.textContent) || '';
    if (!labelText.includes('是否涉及个人信息收集')) continue;
    const radios = item.querySelectorAll('.el-radio');
    for (const radio of radios) {
      const t = (radio.innerText || radio.textContent || '').trim();
      if (t === '否' || t.startsWith('否')) {
        radio.setAttribute('data-agc-privacy-collect-no', '1');
        return true;
      }
    }
  }

  const labels = [...document.querySelectorAll('*')].filter((el) => {
    const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    return t.includes('是否涉及个人信息收集') && t.length < 100;
  });
  for (const label of labels) {
    let node = label;
    for (let i = 0; i < 8 && node; i++) {
      const radios = node.querySelectorAll?.('.el-radio') || [];
      for (const radio of radios) {
        const t = (radio.innerText || radio.textContent || '').trim();
        if (t === '否' || t.startsWith('否')) {
          radio.setAttribute('data-agc-privacy-collect-no', '1');
          return true;
        }
      }
      let sib = label;
      for (let j = 0; j < 6 && sib; j++) {
        sib = sib.nextElementSibling;
        if (!sib) break;
        const radios2 = sib.matches?.('.el-radio-group')
          ? sib.querySelectorAll('.el-radio')
          : sib.querySelectorAll?.('.el-radio') || [];
        for (const radio of radios2) {
          const t = (radio.innerText || radio.textContent || '').trim();
          if (t === '否' || t.startsWith('否')) {
            radio.setAttribute('data-agc-privacy-collect-no', '1');
            return true;
          }
        }
      }
      node = node.parentElement;
    }
  }
  return false;
}
"""


def _ensure_browser_login_ready() -> None:
    if storage_state_exists() or AGC_BROWSER_USER_DATA_DIR:
        return
    raise RuntimeError(
        "尚未保存 AGC 浏览器登录态。请先运行：python -m app.tools.agc_browser_login "
        f"（将生成 {AGC_STORAGE_STATE_PATH}），或配置 AGC_BROWSER_USE_PERSISTENT_PROFILE=true"
    )


def _scroll_privacy_label_section(frame: Frame) -> None:
    frame.evaluate(
        """
        () => {
          const hit =
            document.querySelector('[data-agc-privacy-collect-no="1"]') ||
            Array.from(document.querySelectorAll('.el-form-item__label, label, h3, h4, div, section'))
              .find((el) => {
                const t = (el.innerText || el.textContent || '').trim();
                return t.includes('隐私标签信息录入') || t.includes('是否涉及个人信息收集');
              });
          if (hit) hit.scrollIntoView({ block: 'center', behavior: 'instant' });
          else window.scrollBy(0, Math.max(320, window.innerHeight * 0.45));
        }
        """
    )
    frame.page.wait_for_timeout(300)


def _mark_privacy_collect_no_radio(frame: Frame) -> bool:
    return bool(frame.evaluate(_MARK_COLLECT_NO_JS))


def _find_privacy_collect_no_radio(page: Page, *, timeout_ms: int = 20_000) -> tuple[Frame, Locator]:
    """与隐私政策下拉相同：全 frame 轮询 + JS 标记 + xpath 兜底。"""
    deadline = time.time() + timeout_ms / 1000
    content_frame: Frame | None = None
    while time.time() < deadline:
        for frame in page.frames:
            try:
                if content_frame is None:
                    try:
                        probe = frame.get_by_text("隐私政策", exact=False).first
                        if probe.count() and probe.is_visible(timeout=150):
                            content_frame = frame
                    except Exception:  # noqa: BLE001
                        pass

                _scroll_privacy_label_section(frame)

                if _mark_privacy_collect_no_radio(frame):
                    radio = frame.locator("[data-agc-privacy-collect-no='1']").first
                    if radio.count():
                        return frame, radio

                form_item = frame.locator(".el-form-item").filter(has_text=_COLLECT_LABEL)
                radio = form_item.locator(".el-radio").filter(has_text=re.compile(r"^否$")).first
                if radio.count():
                    return frame, radio

                radio = frame.locator(
                    "xpath=//*[contains(@class,'el-form-item__label') "
                    "and contains(.,'是否涉及个人信息收集')]"
                    "/ancestor::*[contains(@class,'el-form-item')][1]"
                    "//label[contains(@class,'el-radio')]["
                    ".//span[normalize-space()='否'] or normalize-space()='否']"
                ).first
                if radio.count():
                    return frame, radio

                radio = frame.locator(
                    "xpath=//*[contains(normalize-space(.), '是否涉及个人信息收集')]"
                    "/following::label[contains(@class,'el-radio')]["
                    ".//span[normalize-space()='否'] or normalize-space()='否'][1]"
                ).first
                if radio.count():
                    return frame, radio
            except Exception:  # noqa: BLE001
                continue

        if content_frame is not None:
            _scroll_privacy_label_section(content_frame)
        page.wait_for_timeout(250)

    raise RuntimeError(
        f"未找到「隐私标签信息录入 / 是否涉及个人信息收集」表单项；"
        f"{debug_frames_summary(page, probe_text=_COLLECT_LABEL)}"
    )


def _click_collect_no_radio(page: Page, frame: Frame, radio: Locator) -> None:
    radio.scroll_into_view_if_needed(timeout=3_000)
    last_error: Exception | None = None
    for target in (
        radio,
        radio.locator(".el-radio__label"),
        radio.locator("input[type='radio']"),
    ):
        try:
            el = target.first
            el.wait_for(state="attached", timeout=2_000)
            el.click(timeout=2_000, force=True)
            frame.page.wait_for_timeout(200)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    handle = radio.element_handle()
    clicked = frame.evaluate(
        """(root) => {
          if (!root) return false;
          root.click();
          const input = root.querySelector('input[type=radio]')
            || root.closest('.el-radio')?.querySelector('input[type=radio]');
          if (input) {
            input.checked = true;
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return true;
        }""",
        handle,
    )
    if not clicked:
        raise RuntimeError(f"未能选择「否」：{last_error}")


def _save_version_if_present(page: Page, frame: Frame) -> None:
    save_targets = [
        frame.get_by_role("button", name=re.compile(r"^保存$")),
        frame.locator("button.el-button--primary").filter(has_text=re.compile(r"^保存$")),
        frame.locator("button").filter(has_text=re.compile(r"^保存$")),
        page.get_by_role("button", name=re.compile(r"^保存$")),
        page.locator("button").filter(has_text=re.compile(r"^保存$")),
    ]
    click_first_visible(page, save_targets, label="保存", timeout_ms=5_000)
    page.wait_for_timeout(800)


def _set_privacy_label_no_collect(page: Page) -> None:
    wait_for_version_detail_loaded(page, timeout_ms=30_000)
    content_frame, radio = _find_privacy_collect_no_radio(page, timeout_ms=20_000)
    _click_collect_no_radio(page, content_frame, radio)
    _save_version_if_present(page, content_frame)


def set_privacy_tag_no_collect(*, app_name: str, bundle_id: str = "") -> dict[str, Any]:
    """浏览器：版本信息 → 隐私标签信息录入 → 是否涉及个人信息收集 = 否。"""
    _ensure_browser_login_ready()
    name = (app_name or "").strip()
    if not name:
        raise ValueError("appName 不能为空")

    stamp = int(time.time())
    with launch_agc_context(headless=False) as (_pw, _browser, context, page):
        open_myapp_page(page, context)
        dismiss_blocking_tips(page)
        open_harmony_app_from_myapp_list(page, name, bundle_id)
        wait_for_app_detail_content_loaded(page)
        navigate_to_version_ready_submit(page)
        _set_privacy_label_no_collect(page)
        shot = save_screenshot(page, f"privacy_label_no_collect_{stamp}.png")
        logger.info("隐私标签已设为「是否涉及个人信息收集=否」: app=%s", name)
        return {
            "ok": True,
            "appName": name,
            "collectPersonInfo": 0,
            "section": "隐私标签信息录入",
            "screenshot": shot,
        }


def set_privacy_tag_no_collect_sync(app_name: str, bundle_id: str = "") -> dict[str, Any]:
    return run_sync_in_thread(set_privacy_tag_no_collect, app_name=app_name, bundle_id=bundle_id)
