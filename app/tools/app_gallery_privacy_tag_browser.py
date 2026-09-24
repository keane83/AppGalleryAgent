"""AppGallery 控制台：隐私标签 → 是否涉及个人信息收集 = 否。

DOM 锚点：.privacy-label-info-container（见 AGC 版本页实际结构）。
与 configure_app_gallery_privacy_agreement（隐私协议）无关。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from playwright.sync_api import Frame, Locator, Page

from app.config import AGC_BROWSER_USER_DATA_DIR, AGC_STORAGE_STATE_PATH
from app.tools.agc_browser import (
    click_version_ready_submit_save,
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
_CONTAINER_SELECTOR = ".privacy-label-info-container"
_MARK_ATTR = "data-agc-privacy-collect-no"

# 在 .privacy-label-info-container 内定位「否」单选框并打标
_MARK_COLLECT_NO_JS = """
() => {
  document.querySelectorAll('[data-agc-privacy-collect-no]').forEach((el) => {
    el.removeAttribute('data-agc-privacy-collect-no');
  });

  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const radioText = (radio) => {
    const span = radio.querySelector('.el-radio__label');
    return norm(span ? span.textContent : radio.textContent);
  };

  const container = document.querySelector('.privacy-label-info-container');
  if (!container) return { ok: false, reason: 'no_container' };

  const items = container.querySelectorAll('.el-form-item');
  for (const item of items) {
    const labelEl = item.querySelector('.el-form-item__label');
    const labelText = norm(labelEl && labelEl.textContent);
    if (!labelText.includes('是否涉及个人信息收集')) continue;
    if (labelText.includes('隐私声明') || labelText.includes('隐私政策')) continue;

    for (const radio of item.querySelectorAll('label.el-radio, .el-radio')) {
      const t = radioText(radio);
      if (t !== '否' && !t.startsWith('否')) continue;
      radio.setAttribute('data-agc-privacy-collect-no', '1');
      const input = radio.querySelector('input.el-radio__original, input[type=radio]');
      return {
        ok: true,
        already: !!(input && input.checked),
        value: input ? input.value : null,
      };
    }
    return { ok: false, reason: 'no_no_radio' };
  }
  return { ok: false, reason: 'no_form_item' };
}
"""

# 校验容器内「否」是否已选中（value=false 或 label 文本为否）
_VERIFY_COLLECT_NO_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const container = document.querySelector('.privacy-label-info-container');
  if (!container) return false;

  const items = container.querySelectorAll('.el-form-item');
  for (const item of items) {
    const labelEl = item.querySelector('.el-form-item__label');
    const labelText = norm(labelEl && labelEl.textContent);
    if (!labelText.includes('是否涉及个人信息收集')) continue;

    const byValue = item.querySelector('input.el-radio__original[value="false"], input[type=radio][value="false"]');
    if (byValue && byValue.checked) return true;

    for (const radio of item.querySelectorAll('label.el-radio.is-checked, .el-radio.is-checked')) {
      const t = norm(radio.querySelector('.el-radio__label')?.textContent || radio.textContent);
      if (t === '否' || t.startsWith('否')) return true;
    }
  }
  return false;
}
"""

_CLICK_COLLECT_NO_JS = """
() => {
  const marked = document.querySelector('[data-agc-privacy-collect-no="1"]');
  if (!marked) return false;
  marked.scrollIntoView({ block: 'center', behavior: 'instant' });

  const clickRadio = (el) => {
    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    if (typeof el.click === 'function') el.click();
  };

  clickRadio(marked);
  const input = marked.querySelector('input.el-radio__original, input[type=radio]');
  if (input) {
    input.checked = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }
  return !!(input && input.checked);
}
"""


def _ensure_browser_login_ready() -> None:
    if storage_state_exists() or AGC_BROWSER_USER_DATA_DIR:
        return
    raise RuntimeError(
        "尚未保存 AGC 浏览器登录态。请先运行：python -m app.tools.agc_browser_login "
        f"（将生成 {AGC_STORAGE_STATE_PATH}），或配置 AGC_BROWSER_USE_PERSISTENT_PROFILE=true"
    )


def _scroll_privacy_label_container(frame: Frame) -> None:
    frame.evaluate(
        """
        () => {
          const hit =
            document.querySelector('.privacy-label-info-container')
            || document.querySelector('[data-agc-privacy-collect-no="1"]')
            || Array.from(document.querySelectorAll('.el-form-item__label'))
              .find((el) => (el.textContent || '').includes('是否涉及个人信息收集'));
          if (hit) hit.scrollIntoView({ block: 'center', behavior: 'instant' });
        }
        """
    )
    frame.page.wait_for_timeout(200)


def _frame_has_privacy_container(frame: Frame) -> bool:
    try:
        loc = frame.locator(_CONTAINER_SELECTOR).first
        return bool(loc.count()) and loc.is_visible(timeout=200)
    except Exception:  # noqa: BLE001
        return False


def _verify_collect_no_selected(frame: Frame) -> bool:
    return bool(frame.evaluate(_VERIFY_COLLECT_NO_JS))


def _try_mark_collect_no(frame: Frame) -> dict[str, Any]:
    result = frame.evaluate(_MARK_COLLECT_NO_JS)
    return result if isinstance(result, dict) else {"ok": False, reason: "bad_result"}


def _find_privacy_collect_no_radio(
    page: Page,
    *,
    timeout_ms: int = 8_000,
) -> tuple[Frame, Locator | None, bool]:
    """返回 (frame, radio_locator|None, already_selected)。"""
    deadline = time.time() + timeout_ms / 1000
    container_frame: Frame | None = None

    while time.time() < deadline:
        for frame in page.frames:
            try:
                if container_frame is None and _frame_has_privacy_container(frame):
                    container_frame = frame

                _scroll_privacy_label_container(frame)

                if _verify_collect_no_selected(frame):
                    logger.info("隐私标签「否」已是选中状态，跳过点击")
                    return frame, None, True

                mark_result = _try_mark_collect_no(frame)
                if mark_result.get("ok"):
                    if mark_result.get("already"):
                        logger.info("隐私标签「否」标记时已为选中，跳过点击")
                        return frame, None, True
                    radio = frame.locator(f"[{_MARK_ATTR}='1']").first
                    if radio.count():
                        return frame, radio, False

                # Playwright 兜底：容器内表单项
                container = frame.locator(_CONTAINER_SELECTOR)
                if container.count():
                    form_item = container.locator(".el-form-item").filter(
                        has=frame.locator(".el-form-item__label").filter(
                            has_text=re.compile(r"是否涉及个人信息收集")
                        )
                    )
                    radio = form_item.locator("label.el-radio").filter(
                        has=frame.locator(".el-radio__label", has_text=re.compile(r"^否$"))
                    ).first
                    if not radio.count():
                        radio = form_item.locator(
                            'input.el-radio__original[value="false"]'
                        ).locator("xpath=ancestor::label[contains(@class,'el-radio')][1]")
                    if radio.count():
                        return frame, radio, False
            except Exception:  # noqa: BLE001
                continue

        page.wait_for_timeout(150)

    raise RuntimeError(
        f"未找到 .privacy-label-info-container 或「{_COLLECT_LABEL}」单选框；"
        f"{debug_frames_summary(page, probe_text=_COLLECT_LABEL)}"
    )


def _click_collect_no_radio(frame: Frame, radio: Locator | None) -> None:
    if radio is None:
        return

    radio.scroll_into_view_if_needed(timeout=3_000)
    last_error: Exception | None = None

    for target in (
        radio,
        radio.locator(".el-radio__label"),
        radio.locator("input.el-radio__original, input[type='radio']"),
    ):
        try:
            el = target.first
            el.wait_for(state="visible", timeout=2_000)
            el.click(timeout=2_000)
            frame.page.wait_for_timeout(150)
            if _verify_collect_no_selected(frame):
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    if frame.evaluate(_CLICK_COLLECT_NO_JS) and _verify_collect_no_selected(frame):
        return

    handle = radio.element_handle()
    clicked = frame.evaluate(
        """(root) => {
          if (!root) return false;
          root.click();
          const input = root.querySelector('input.el-radio__original, input[type=radio]');
          if (input) {
            input.checked = true;
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return !!(input && input.checked);
        }""",
        handle,
    )
    if not clicked or not _verify_collect_no_selected(frame):
        raise RuntimeError(f"未能选择「{_COLLECT_LABEL}=否」：{last_error}")


def _save_version_if_present(page: Page) -> None:
    click_version_ready_submit_save(page, timeout_ms=10_000)


def _set_privacy_label_no_collect(page: Page) -> None:
    wait_for_version_detail_loaded(page, timeout_ms=20_000)
    content_frame, radio, already = _find_privacy_collect_no_radio(page, timeout_ms=8_000)
    if not already:
        _click_collect_no_radio(content_frame, radio)
    if not _verify_collect_no_selected(content_frame):
        raise RuntimeError(f"「{_COLLECT_LABEL}」未确认为「否」")
    _save_version_if_present(page)


def set_privacy_tag_no_collect(*, app_name: str, bundle_id: str = "") -> dict[str, Any]:
    """浏览器：版本信息 → 隐私标签 → 是否涉及个人信息收集 = 否。"""
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
            "section": "privacy-label-info-container",
            "screenshot": shot,
        }


def set_privacy_tag_no_collect_sync(app_name: str, bundle_id: str = "") -> dict[str, Any]:
    return run_sync_in_thread(set_privacy_tag_no_collect, app_name=app_name, bundle_id=bundle_id)
