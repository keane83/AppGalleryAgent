"""AppGallery 控制台：版本信息 → 准备提交 → 内容分级 / 年龄分级问卷（全部选否）。"""

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
    dismiss_menu_guide_popup,
    launch_agc_context,
    navigate_to_version_ready_submit,
    open_harmony_app_from_myapp_list,
    open_myapp_page,
    run_sync_in_thread,
    save_screenshot,
    storage_state_exists,
    version_detail_content_visible,
    wait_for_app_detail_content_loaded,
    wait_for_version_detail_loaded,
)

logger = logging.getLogger("haa.age_rating_browser")

FrameLike = Page | Frame

_CLICK_NO_RADIO_JS = """
(el) => {
  const radio = el.closest?.('.el-radio') || el;
  radio.scrollIntoView({ block: 'nearest' });
  radio.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  radio.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
  radio.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  if (typeof radio.click === 'function') radio.click();
  const input =
    radio.querySelector('input[type=radio]') ||
    radio.querySelector('.el-radio__original');
  if (input) {
    input.checked = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }
  return !!(input && input.checked);
}
"""

_SELECT_ALL_NO_JS = """
(scope) => {
  const root = scope || document;
  let clicked = 0;

  const clickNoLabel = (label) => {
    if (!label) return false;
    const input = label.querySelector('input[type=radio], .el-radio__original');
    if (input && input.checked) return true;
    label.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    label.click?.();
    if (input) {
      input.checked = true;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return true;
  };

  for (const wrap of root.querySelectorAll('.question-wrap')) {
    const label =
      wrap.querySelector('label.single-select-radio input[value="1"]')?.closest('label.single-select-radio')
      || [...wrap.querySelectorAll('label.single-select-radio')].find(
        (el) => (el.textContent || '').trim() === '否'
      );
    if (clickNoLabel(label)) clicked += 1;
  }

  for (const group of root.querySelectorAll('.el-radio-group')) {
    for (const radio of group.querySelectorAll('.el-radio, label.el-radio')) {
      const t = ((radio.querySelector('.el-radio__label') || radio).textContent || '').trim();
      if (t !== '否') continue;
      if (clickNoLabel(radio.closest('label') || radio)) {
        clicked += 1;
        break;
      }
    }
  }
  return clicked;
}
"""


def _ensure_browser_login_ready() -> None:
    if storage_state_exists() or AGC_BROWSER_USER_DATA_DIR:
        return
    raise RuntimeError(
        "尚未保存 AGC 浏览器登录态。请先运行：python -m app.tools.agc_browser_login "
        f"（将生成 {AGC_STORAGE_STATE_PATH}），或配置 AGC_BROWSER_USE_PERSISTENT_PROFILE=true"
    )


def _iter_contexts(page: Page) -> list[FrameLike]:
    return [page, *page.frames]


def _dialog_title_pattern(title: str) -> re.Pattern[str]:
    return re.compile(re.escape(title))


def _locate_dialog_by_titles(ctx: FrameLike, *titles: str) -> Locator | None:
    for title in titles:
        pat = _dialog_title_pattern(title)
        candidates = [
            ctx.locator(".el-overlay-dialog .el-dialog").filter(
                has=ctx.locator(".el-dialog__title, .el-dialog__header").filter(has_text=pat)
            ),
            ctx.locator(".el-dialog").filter(
                has=ctx.locator(".el-dialog__title, .el-dialog__header").filter(has_text=pat)
            ),
            ctx.locator(".el-overlay-dialog .el-dialog, .el-dialog, [role='dialog']").filter(
                has_text=pat
            ),
        ]
        for candidate in candidates:
            dlg = candidate.last
            try:
                if dlg.count() and dlg.is_visible(timeout=200):
                    return dlg
            except Exception:  # noqa: BLE001
                continue
    return None


def _find_visible_dialog(
    page: Page,
    *titles: str,
    timeout_ms: int = 15_000,
) -> tuple[FrameLike, Locator]:
    if not titles:
        raise ValueError("titles 不能为空")

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        # Element Plus 弹窗常 teleport 到顶层 page，优先查 page
        for ctx in (page, *page.frames):
            try:
                dlg = _locate_dialog_by_titles(ctx, *titles)
                if dlg is not None:
                    return ctx, dlg
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(250)

    joined = " / ".join(titles)
    raise RuntimeError(
        f"未找到标题含「{joined}」的弹框；{debug_frames_summary(page, probe_text=titles[0])}"
    )


def _any_dialog_visible(page: Page, *titles: str) -> bool:
    for ctx in (page, *page.frames):
        try:
            if _locate_dialog_by_titles(ctx, *titles) is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _click_dialog_button(dlg: Locator, name: str, *, exact: bool = True) -> None:
    pattern = re.compile(rf"^{re.escape(name)}$") if exact else re.compile(re.escape(name))
    targets = [
        dlg.get_by_role("button", name=pattern),
        dlg.locator(".el-dialog__footer button").filter(has_text=pattern),
        dlg.locator("button, .el-button").filter(has_text=pattern),
    ]
    last_error: Exception | None = None
    for target in targets:
        try:
            btn = target.first
            btn.wait_for(state="visible", timeout=3_000)
            btn.scroll_into_view_if_needed(timeout=2_000)
            btn.click(timeout=3_000, force=True)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"弹框内未找到可点击的「{name}」按钮：{last_error}")


_SCROLL_AND_MARK_AGE_RATING_SETTINGS_JS = """
() => {
  document.querySelectorAll('[data-agc-age-rating-set]').forEach((el) => {
    el.removeAttribute('data-agc-age-rating-set');
  });

  const isSettings = (el) => (el.innerText || el.textContent || '').trim() === '设置';
  const controlSelector = 'button, .el-button, .el-link, a, span, div[role="button"]';

  const markSettings = (el) => {
    if (!el || !isSettings(el)) return false;
    const target = el.closest('.el-link, .el-button, button, a') || el;
    target.setAttribute('data-agc-age-rating-set', '1');
    target.scrollIntoView({ block: 'center', behavior: 'instant' });
    return true;
  };

  const findSettingsInDocument = () => {
    for (const item of document.querySelectorAll('.el-form-item')) {
      const labelText = (item.querySelector('.el-form-item__label, label')?.textContent || '');
      if (!labelText.includes('年龄分级')) continue;
      for (const el of item.querySelectorAll(controlSelector)) {
        if (markSettings(el)) return true;
      }
    }

    for (const label of document.querySelectorAll('.el-form-item__label, label, span')) {
      const t = (label.textContent || '').replace(/\\s+/g, ' ').trim();
      if (!t.includes('年龄分级') || t.length > 48) continue;
      const item = label.closest('.el-form-item');
      const scopes = [
        item?.querySelector('.el-form-item__content'),
        item,
        label.parentElement,
      ].filter(Boolean);
      for (const scope of scopes) {
        for (const el of scope.querySelectorAll(controlSelector)) {
          if (markSettings(el)) return true;
        }
      }
      let node = label;
      for (let i = 0; i < 8; i++) {
        node = node.nextElementSibling;
        if (!node) break;
        if (isSettings(node) && markSettings(node)) return true;
        for (const el of node.querySelectorAll?.(controlSelector) || []) {
          if (markSettings(el)) return true;
        }
      }
    }

    for (const head of document.querySelectorAll('h3, h4, div, section, span')) {
      const t = (head.innerText || head.textContent || '').trim();
      if (t !== '内容分级' && !t.startsWith('内容分级')) continue;
      let root = head.parentElement;
      for (let up = 0; up < 5 && root; up++) {
        const text = (root.innerText || '').replace(/\\s+/g, ' ');
        if (text.includes('年龄分级')) {
          for (const el of root.querySelectorAll(controlSelector)) {
            if (markSettings(el)) return true;
          }
        }
        root = root.parentElement;
      }
    }
    return false;
  };

  const scrollRoots = [];
  const addRoot = (el) => {
    if (el && !scrollRoots.includes(el)) scrollRoots.push(el);
  };
  addRoot(document.querySelector('.el-scrollbar__wrap'));
  addRoot(document.querySelector('.el-main'));
  for (const el of document.querySelectorAll('div, section, main')) {
    const s = window.getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
      addRoot(el);
    }
  }
  addRoot(document.scrollingElement);
  addRoot(document.documentElement);
  addRoot(document.body);

  for (const root of scrollRoots) {
    const step = Math.max(260, (root.clientHeight || window.innerHeight) * 0.5);
    const maxScroll = Math.max(root.scrollHeight - root.clientHeight, 0);
    for (let pos = 0; pos <= maxScroll + step; pos += step) {
      root.scrollTop = pos;
      if (findSettingsInDocument()) return true;
    }
  }
  return findSettingsInDocument();
}
"""


def _version_form_frames(page: Page) -> list[Frame]:
    """版本表单可能在 iframe 中，且「年龄分级」与「设置」同在可滚动容器内。"""
    frames: list[Frame] = []
    seen: set[str] = set()
    for frame in page.frames:
        key = frame.url or str(id(frame))
        if key in seen:
            continue
        seen.add(key)
        for marker in ("年龄分级", "内容分级", "隐私政策", "新版本 - 准备提交", "软件版本"):
            try:
                loc = frame.get_by_text(marker, exact=False).first
                if loc.count():
                    frames.append(frame)
                    break
            except Exception:  # noqa: BLE001
                continue
    if not frames:
        loaded = wait_for_version_detail_loaded(page, timeout_ms=5_000)
        frames.append(loaded)
    return frames


def _scroll_and_mark_age_rating_settings(frame: Frame) -> bool:
    return bool(frame.evaluate(_SCROLL_AND_MARK_AGE_RATING_SETTINGS_JS))


def _locate_age_rating_settings(frame: Frame) -> Locator | None:
    marked = frame.locator("[data-agc-age-rating-set='1']").first
    if marked.count():
        return marked

    form_item = frame.locator(".el-form-item").filter(has_text=re.compile(r"年龄分级"))
    for selector in ("button", ".el-button", ".el-link", "a", "span"):
        btn = form_item.locator(selector).filter(has_text=re.compile(r"^设置$")).first
        if btn.count():
            return btn

    xpath_btn = frame.locator(
        "xpath=//*[contains(@class,'el-form-item__label') and contains(.,'年龄分级')]"
        "/ancestor::*[contains(@class,'el-form-item')][1]"
        "//*[self::button or contains(@class,'el-button') or contains(@class,'el-link') "
        "or self::a or self::span][normalize-space()='设置']"
    ).first
    if xpath_btn.count():
        return xpath_btn

    return None


def _find_age_rating_settings_button(page: Page) -> tuple[Frame, Locator]:
    deadline = time.time() + 25
    last_summary = ""
    while time.time() < deadline:
        for frame in _version_form_frames(page):
            try:
                if _scroll_and_mark_age_rating_settings(frame):
                    btn = _locate_age_rating_settings(frame)
                    if btn is not None:
                        return frame, btn

                btn = _locate_age_rating_settings(frame)
                if btn is not None:
                    btn.scroll_into_view_if_needed(timeout=2_000)
                    return frame, btn
            except Exception:  # noqa: BLE001
                continue

        last_summary = debug_frames_summary(page, probe_text="年龄分级")
        page.wait_for_timeout(350)

    raise RuntimeError(
        f"未在版本「准备提交」页找到「年龄分级」旁的「设置」按钮；{last_summary}"
    )


def _js_click_settings(frame: Frame, settings_btn: Locator) -> None:
    handle = settings_btn.element_handle()
    if handle is None:
        raise RuntimeError("「设置」元素已消失")
    frame.evaluate(
        """
        (el) => {
          const target = el.closest('.el-link, .el-button, button, a') || el;
          target.scrollIntoView({ block: 'center', behavior: 'instant' });
          target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
          target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
          target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
          if (typeof target.click === 'function') target.click();
        }
        """,
        handle,
    )


def _click_age_rating_settings(page: Page) -> Frame:
    if not version_detail_content_visible(page):
        navigate_to_version_ready_submit(page)

    wait_for_version_detail_loaded(page, timeout_ms=30_000)
    content_frame, settings_btn = _find_age_rating_settings_button(page)
    settings_btn.scroll_into_view_if_needed(timeout=3_000)

    std_dialog_titles = ("华为年龄分级标准", "年龄分级标准")
    for attempt in range(3):
        _js_click_settings(content_frame, settings_btn)
        page.wait_for_timeout(900 if attempt == 0 else 600)
        if _any_dialog_visible(page, *std_dialog_titles, "内容分级"):
            break
        try:
            settings_btn.click(timeout=2_000, force=True)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(700)
        if _any_dialog_visible(page, *std_dialog_titles, "内容分级"):
            break
    else:
        raise RuntimeError(
            "已点击「设置」但未弹出年龄分级对话框；"
            f"{debug_frames_summary(page, probe_text='华为年龄分级标准')}"
        )
    return content_frame


def _open_content_rating_questionnaire(page: Page) -> tuple[FrameLike, Locator]:
    _click_age_rating_settings(page)

    if _any_dialog_visible(page, "内容分级"):
        return _find_visible_dialog(page, "内容分级", timeout_ms=5_000)

    _ctx, std_dialog = _find_visible_dialog(
        page,
        "华为年龄分级标准",
        "年龄分级标准",
        timeout_ms=12_000,
    )
    fill_btn = std_dialog.locator("button, .el-button, .el-link, a, span").filter(
        has_text=re.compile(r"填写问卷")
    )
    if fill_btn.count():
        try:
            fill_btn.first.scroll_into_view_if_needed(timeout=2_000)
            fill_btn.first.click(timeout=3_000, force=True)
        except Exception:  # noqa: BLE001
            _click_dialog_button(std_dialog, "填写问卷")
    else:
        _click_dialog_button(std_dialog, "填写问卷")
    page.wait_for_timeout(800)
    return _find_visible_dialog(page, "内容分级", timeout_ms=15_000)


def _dialog_body(dlg: Locator) -> Locator:
    body = dlg.locator(".el-dialog__body").first
    return body if body.count() else dlg


def _js_click_element(page: Page, locator: Locator) -> None:
    handle = locator.element_handle()
    if handle is None:
        raise RuntimeError("元素不存在，无法点击")
    page.evaluate(
        """
        (el) => {
          el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
          el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
          el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
          if (typeof el.click === 'function') el.click();
        }
        """,
        handle,
    )


def _click_no_radio(page: Page, radio: Locator) -> bool:
    try:
        radio.scroll_into_view_if_needed(timeout=2_000)
        handle = radio.element_handle()
        if handle is None:
            return False
        return bool(page.evaluate(_CLICK_NO_RADIO_JS, handle))
    except Exception:  # noqa: BLE001
        try:
            radio.click(timeout=2_000, force=True)
            return True
        except Exception:  # noqa: BLE001
            return False


def _agc_questionnaire_groups(body: Locator) -> Locator:
    root = body.locator(".agc-questionnaire__body").first
    scope = root if root.count() else body
    return scope.locator("ul.agc-question-body > li")


def _is_agc_group_expanded(group: Locator) -> bool:
    try:
        return bool(
            group.evaluate(
                """
                (li) => {
                  const up = li.querySelector('.el-icon-arrow-up');
                  return !!(up && getComputedStyle(up).display !== 'none');
                }
                """
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _expand_agc_question_group(page: Page, group: Locator) -> bool:
    """点击 AGC 问卷分类行（agc-question-group_tips / 箭头）展开题目。"""
    if _is_agc_group_expanded(group):
        return False

    group.scroll_into_view_if_needed(timeout=2_000)
    click_targets = [
        group.locator("> div").first,
        group.locator(".agc-question-group_tips").first,
        group.locator(".agc-question-group__title").first,
        group.locator(".el-icon-arrow-down").first,
    ]
    for target in click_targets:
        try:
            if target.count():
                _js_click_element(page, target)
                page.wait_for_timeout(550)
                if _is_agc_group_expanded(group):
                    return True
        except Exception:  # noqa: BLE001
            continue

    try:
        group.locator("> div").first.click(timeout=2_000, force=True)
        page.wait_for_timeout(600)
    except Exception:  # noqa: BLE001
        pass
    return True


def _wait_for_visible_question_wraps(page: Page, group: Locator, *, timeout_ms: int = 5_000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            wraps = group.locator(".question-wrap")
            for idx in range(wraps.count()):
                if wraps.nth(idx).is_visible(timeout=150):
                    return True
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(150)
    return False


def _no_radio_in_question_wrap(wrap: Locator) -> Locator:
    """AGC 问卷：否 = input[value='1'] 或文案「否」。"""
    by_value = wrap.locator('label.single-select-radio:has(input[value="1"])').first
    if by_value.count():
        return by_value
    return wrap.locator("label.single-select-radio").filter(has_text=re.compile(r"^否\s*$")).first


def _select_no_in_agc_group(page: Page, group: Locator) -> int:
    selected = 0
    wraps = group.locator(".question-wrap")
    for idx in range(wraps.count()):
        wrap = wraps.nth(idx)
        try:
            if not wrap.is_visible(timeout=400):
                continue
        except Exception:  # noqa: BLE001
            continue
        no_label = _no_radio_in_question_wrap(wrap)
        if no_label.count() and _click_no_radio(page, no_label):
            selected += 1
            page.wait_for_timeout(120)
    return selected


def _select_no_radios_in_scope(page: Page, scope: Locator) -> int:
    """兼容 AGC question-wrap 与 Element Plus el-radio-group。"""
    selected = _select_no_in_agc_group(page, scope)
    if selected:
        return selected

    groups = scope.locator(".el-radio-group")
    for idx in range(groups.count()):
        group = groups.nth(idx)
        no_radio = group.locator('label.single-select-radio:has(input[value="1"])').first
        if not no_radio.count():
            no_radio = group.get_by_role("radio", name="否")
        if not no_radio.count():
            no_radio = group.locator(".el-radio").filter(has_text=re.compile(r"^否\s*$")).first
        if no_radio.count() and _click_no_radio(page, no_radio.first):
            selected += 1
            page.wait_for_timeout(120)
    return selected


def _fill_questionnaire_all_no(page: Page, dlg: Locator) -> dict[str, int]:
    """AGC 内容分级问卷：ul.agc-question-body > li，逐项展开后选「否」。"""
    body = _dialog_body(dlg)
    groups = _agc_questionnaire_groups(body)
    group_count = groups.count()
    totals = {
        "passes": 0,
        "expanded": 0,
        "selected": 0,
        "collapse_total": group_count,
    }

    if group_count == 0:
        raise RuntimeError("内容分级弹框内未找到 ul.agc-question-body 问卷分类")

    for idx in range(group_count):
        group = groups.nth(idx)
        totals["passes"] += 1

        if _expand_agc_question_group(page, group):
            totals["expanded"] += 1

        _wait_for_visible_question_wraps(page, group, timeout_ms=5_000)
        picked = _select_no_in_agc_group(page, group)
        totals["selected"] += picked
        page.wait_for_timeout(250)

    totals["selected"] += int(body.evaluate(_SELECT_ALL_NO_JS) or 0)

    if totals["selected"] == 0:
        title = ""
        try:
            title = groups.first.locator(".agc-question-group__title").inner_text(timeout=1_000)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            "内容分级问卷中未能选中任何「否」选项"
            f"（分类数={group_count}，已展开={totals['expanded']}，首项={title!r}）"
        )

    page.wait_for_timeout(200)
    return totals


def _click_verify_and_submit(page: Page, dlg: Locator) -> None:
    _click_dialog_button(dlg, "验证")
    page.wait_for_timeout(800)

    body = _dialog_body(dlg)
    for _ in range(4):
        _select_no_radios_in_scope(page, body)
        body.evaluate(_SELECT_ALL_NO_JS)
        page.wait_for_timeout(300)

    deadline = time.time() + 90
    submit_btn: Locator | None = None
    while time.time() < deadline:
        try:
            candidate = dlg.get_by_role("button", name=re.compile(r"^提交$"))
            if not candidate.count():
                candidate = dlg.locator(".el-dialog__footer button").filter(has_text=re.compile(r"^提交$"))
            if candidate.count() and candidate.first.is_visible(timeout=400):
                if candidate.first.is_enabled():
                    submit_btn = candidate.first
                    break
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(400)

    if submit_btn is None:
        raise RuntimeError("点击「验证」后未出现可点击的「提交」按钮")

    submit_btn.scroll_into_view_if_needed(timeout=2_000)
    submit_btn.click(timeout=5_000, force=True)
    page.wait_for_timeout(800)


def _confirm_children_only_no(page: Page) -> None:
    markers = ("仅面向儿童", "面向儿童")
    deadline = time.time() + 20
    child_dialog: Locator | None = None
    while time.time() < deadline:
        for ctx in (page, *page.frames):
            try:
                dlg = _locate_dialog_by_titles(ctx, *markers)
                if dlg is not None:
                    child_dialog = dlg
                    break
            except Exception:  # noqa: BLE001
                continue
            if child_dialog is not None:
                break
        if child_dialog is not None:
            break
        page.wait_for_timeout(250)

    if child_dialog is None:
        raise RuntimeError("提交后未出现「是否仅面向儿童」确认弹框")

    body = _dialog_body(child_dialog)
    selected = _select_no_radios_in_scope(page, body)
    if not selected:
        selected = body.evaluate(_SELECT_ALL_NO_JS)
    if not selected:
        _click_dialog_button(child_dialog, "否", exact=False)
    page.wait_for_timeout(200)
    _click_dialog_button(child_dialog, "确认")
    page.wait_for_timeout(800)


def _configure_age_rating_on_page(page: Page) -> dict[str, Any]:
    _ctx, rating_dialog = _open_content_rating_questionnaire(page)
    fill_stats = _fill_questionnaire_all_no(page, rating_dialog)
    _click_verify_and_submit(page, rating_dialog)
    _confirm_children_only_no(page)
    click_version_ready_submit_save(page, timeout_ms=12_000)
    return fill_stats


def configure_age_rating(*, app_name: str, bundle_id: str = "") -> dict[str, Any]:
    """浏览器：版本信息 → 准备提交 → 年龄分级问卷（全部选否）→ 保存。"""
    from app.config import BROWSER_ARTIFACT_DIR

    _ensure_browser_login_ready()
    name = (app_name or "").strip()
    if not name:
        raise ValueError("appName 不能为空")

    stamp = int(time.time())
    with launch_agc_context(headless=False) as (_pw, _browser, context, page):
        try:
            open_myapp_page(page, context)
            dismiss_blocking_tips(page)
            open_harmony_app_from_myapp_list(page, name, bundle_id)
            wait_for_app_detail_content_loaded(page)
            dismiss_menu_guide_popup(page)
            navigate_to_version_ready_submit(page)
            fill_stats = _configure_age_rating_on_page(page)
            shot = save_screenshot(page, f"age_rating_{stamp}.png")
            logger.info("年龄分级问卷已完成: app=%s stats=%s", name, fill_stats)
            return {
                "ok": True,
                "appName": name,
                "section": "内容分级 / 年龄分级",
                "questionnaireStats": fill_stats,
                "screenshot": shot,
                "artifactDir": str(BROWSER_ARTIFACT_DIR.resolve()),
            }
        except Exception as exc:
            fail_shot = ""
            try:
                fail_shot = save_screenshot(page, f"age_rating_failed_{stamp}.png")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"{exc}；失败截图={fail_shot or '(无)'}") from exc


def configure_age_rating_sync(app_name: str, bundle_id: str = "") -> dict[str, Any]:
    return run_sync_in_thread(configure_age_rating, app_name=app_name, bundle_id=bundle_id)
