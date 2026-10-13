"""AppGallery Connect 控制台：创建隐私协议并在版本信息中关联。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from langchain_core.tools import tool
from playwright.sync_api import Frame, Locator, Page, TimeoutError as PlaywrightTimeoutError

FrameLike = Page | Frame

from app.config import (
    AGC_MYAPP_URL,
    APP_GALLERY_DEFAULT_CONTACT_PHONE,
    BROWSER_ARTIFACT_DIR,
)
from app.tools.agc_browser import (
    click_first_visible,
    click_version_ready_submit_save,
    click_sidebar_menu_item,
    click_text_in_content_area,
    debug_frames_summary,
    dismiss_blocking_tips,
    dismiss_menu_guide_popup,
    launch_agc_context,
    navigate_to_version_ready_submit,
    open_harmony_app_from_myapp_list,
    open_myapp_page,
    resolve_frame_with_visible_text,
    run_sync_in_thread,
    save_screenshot,
    wait_for_app_detail_content_loaded,
    wait_for_app_detail_ready,
    wait_for_version_detail_loaded,
)


def _looks_like_bundle_id(package_name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+){2,}", package_name))


def _fill_first_visible(
    page: Page,
    locators: list[Locator],
    value: str,
    *,
    label: str,
    timeout_ms: int = 8_000,
) -> None:
    last_error: Exception | None = None
    for locator in locators:
        try:
            target = locator.first
            target.wait_for(state="visible", timeout=timeout_ms)
            target.fill(value, timeout=min(timeout_ms, 5_000))
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"未找到可填写的「{label}」输入框：{last_error}")


def _find_visible_select_popper(page: Page, ctx: FrameLike) -> Locator:
    """Element Plus 下拉 popper 可能 teleport 到同 frame 或顶层 document。"""
    for search in (ctx, page, *page.frames):
        try:
            popper = search.locator(".el-select__popper:visible, .el-popper:visible").last
            if popper.count() and popper.is_visible(timeout=250):
                return popper
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("未找到可见的下拉选项面板")


def _select_el_option(
    ctx: FrameLike,
    trigger: Locator,
    option_text: str,
    *,
    exact: bool = False,
    page: Page | None = None,
    timeout_ms: int = 4_000,
) -> None:
    trigger.wait_for(state="visible", timeout=timeout_ms)
    try:
        current = trigger.locator(".el-select__selected-item").first
        if current.count():
            selected = current.inner_text(timeout=500).strip()
            if selected == option_text or (not exact and option_text in selected):
                return
    except Exception:  # noqa: BLE001
        pass
    try:
        trigger.locator(".el-select__wrapper, .el-input__wrapper").first.click(timeout=2_000, force=True)
    except Exception:  # noqa: BLE001
        trigger.click(timeout=2_000, force=True)
    if page is not None:
        page.wait_for_timeout(150)
    elif isinstance(ctx, Page):
        ctx.wait_for_timeout(150)

    popper = _find_visible_select_popper(page, ctx) if page is not None else ctx.locator(
        ".el-select__popper:visible, .el-popper:visible"
    ).last
    popper.wait_for(state="visible", timeout=timeout_ms)
    if exact:
        option = popper.locator("li.el-select-dropdown__item, li").filter(
            has_text=re.compile(rf"^{re.escape(option_text)}$")
        )
    else:
        option = popper.locator("li.el-select-dropdown__item, li").filter(has_text=option_text)
    option.first.wait_for(state="visible", timeout=timeout_ms)
    option.first.click(timeout=2_000, force=True)
    if page is not None:
        page.wait_for_timeout(120)
    elif isinstance(ctx, Page):
        ctx.wait_for_timeout(120)


def _resolve_agreement_frame(page: Page, *, timeout_ms: int = 8_000) -> Frame:
    """协议服务主内容可能在 iframe 中，需在全部 frame 内查找。"""
    return resolve_frame_with_visible_text(
        page,
        "新建协议",
        "暂无数据",
        timeout_ms=timeout_ms,
    )


def _new_agreement_button(frame: Frame) -> Locator:
    """协议服务右侧主面板右上角「新建协议」按钮。"""
    candidates = [
        frame.get_by_role("button", name=re.compile(r"新建协议")),
        frame.locator("button.el-button--primary, .el-button--primary").filter(has_text="新建协议"),
        frame.locator("button, a, .el-button, [role='button']").filter(
            has_text=re.compile(r"^\s*新建协议\s*$")
        ),
        frame.locator("button, a, .el-button, [role='button']").filter(has_text="新建协议"),
        frame.get_by_text("新建协议", exact=True),
    ]
    for locator in candidates:
        try:
            target = locator.first
            if target.count() and target.is_visible(timeout=500):
                return target
        except Exception:  # noqa: BLE001
            continue
    return frame.get_by_text("新建协议", exact=False).last


def _wait_agreement_service_ready(
    page: Page,
    *,
    content_frame: Frame | None = None,
    timeout_ms: int = 6_000,
) -> tuple[Frame, Locator]:
    """等待协议服务页加载完成，返回 (content_frame, 「新建协议」按钮)。"""
    frame = content_frame or _resolve_agreement_frame(page, timeout_ms=timeout_ms)
    btn = _new_agreement_button(frame)
    try:
        btn.wait_for(state="visible", timeout=timeout_ms)
        return frame, btn
    except Exception as exc:  # noqa: BLE001
        frame = _resolve_agreement_frame(page, timeout_ms=timeout_ms)
        btn = _new_agreement_button(frame)
        btn.wait_for(state="visible", timeout=timeout_ms)
        return frame, btn


def _click_new_agreement(page: Page, frame: Frame, btn: Locator) -> None:
    try:
        btn.scroll_into_view_if_needed(timeout=3_000)
        btn.click(timeout=5_000)
    except Exception:
        click_text_in_content_area(frame, "新建协议")


def _find_new_agreement_dialog(page: Page) -> tuple[Frame, Locator]:
    """「新建协议」弹窗（Element Plus el-dialog，可能在任意 frame）。"""
    deadline = time.time() + 6
    while time.time() < deadline:
        for frame in page.frames:
            titled = frame.locator(".el-dialog").filter(
                has=frame.locator(".el-dialog__title, .el-dialog__header").filter(
                    has_text=re.compile(r"新建协议")
                )
            )
            fallback = frame.locator(".el-dialog").filter(has_text=re.compile(r"协议类型"))
            for dialog in (titled, fallback):
                if not dialog.count():
                    continue
                target = dialog.last
                try:
                    if target.is_visible(timeout=80):
                        return frame, target
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(50)
    raise RuntimeError(
        f"未找到「新建协议」弹窗；{debug_frames_summary(page, probe_text='协议类型')}"
    )


def _set_input_value_js(frame: Frame, element: str, value: str) -> bool:
    """通过 JS 设置 input/textarea/contenteditable 的值。"""
    return bool(
        frame.evaluate(
            """
            ([element, value]) => {
              const setValue = (el, val) => {
                if (el.isContentEditable) {
                  el.textContent = val;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  return;
                }
                el.focus();
                el.value = val;
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              };
              if (element === 'active') {
                const el = document.activeElement;
                if (el && (el.matches('input, textarea, [contenteditable="true"]'))) {
                  setValue(el, value);
                  return true;
                }
                return false;
              }
              return false;
            }
            """,
            [element, value],
        )
    )


def _fill_el_form_field(frame: Frame, dialog: Locator, label: str, value: str) -> None:
    """按 Element Plus 表单 label 填写输入框；弹窗已可见时优先 JS，避免慢等选择器。"""
    filled = frame.evaluate(
        """
        ([label, value]) => {
          const setValue = (el, val) => {
            if (el.isContentEditable) {
              el.textContent = val;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              return;
            }
            el.focus();
            el.value = val;
            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          };
          const dialogs = document.querySelectorAll('.el-dialog');
          for (const dialog of dialogs) {
            const style = window.getComputedStyle(dialog);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            for (const item of dialog.querySelectorAll('.el-form-item')) {
              const lab = item.querySelector('.el-form-item__label, label');
              if (!lab || !(lab.textContent || '').includes(label)) continue;
              const input = item.querySelector(
                'input.el-input__inner, textarea.el-textarea__inner, input[type="text"], textarea'
              );
              if (!input) continue;
              setValue(input, value);
              return true;
            }
          }
          return false;
        }
        """,
        [label, value],
    )
    if filled:
        return

    form_item = dialog.locator(".el-form-item").filter(has_text=label)
    target = form_item.locator("input.el-input__inner, input:not([type='hidden'])").first
    target.wait_for(state="visible", timeout=1_500)
    target.fill(value, timeout=1_500)


def _find_draft_editor_frame(page: Page, app_name: str, *, timeout_ms: int = 8_000) -> Frame:
    """协议草稿编辑器 iframe（含首段「用于…的应用」与「生成协议」按钮）。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            try:
                intro = frame.locator("*").filter(
                    has_text=re.compile(r"为您提供的，用于")
                ).filter(has_text=re.compile(r"的应用"))
                gen_btn = frame.get_by_role("button", name=re.compile(r"生成协议"))
                if intro.count() and gen_btn.count():
                    if intro.first.is_visible(timeout=150) and gen_btn.first.is_visible(timeout=150):
                        return frame
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(80)
    return resolve_frame_with_visible_text(
        page, "生成协议", "草稿", "产品简介", timeout_ms=timeout_ms
    )


def _fill_template_field_js(frame: Frame, placeholder: str, value: str) -> bool:
    return bool(
        frame.evaluate(
            """
            ([placeholder, value]) => {
              const visible = (el) => {
                const r = el.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden';
              };
              const setValue = (el, val) => {
                if (el.isContentEditable) {
                  el.textContent = val;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  return;
                }
                el.focus();
                el.value = val;
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              };
              const collect = (root, out) => {
                root.querySelectorAll('input, textarea, [contenteditable="true"], .el-input__inner').forEach((el) => out.push(el));
                root.querySelectorAll('*').forEach((el) => {
                  if (el.shadowRoot) collect(el.shadowRoot, out);
                });
              };
              const nodes = [];
              collect(document, nodes);

              for (const el of nodes) {
                const ph = el.getAttribute('placeholder') || el.dataset?.placeholder || el.getAttribute('aria-label') || '';
                if (!ph.includes(placeholder)) continue;
                if (!visible(el)) continue;
                setValue(el, value);
                return true;
              }

              for (const block of document.querySelectorAll('p, div, section, article, span, li')) {
                const text = block.textContent || '';
                if (!text.includes('用于') || !text.includes('的应用')) continue;
                for (const el of block.querySelectorAll('input, textarea, [contenteditable="true"], .el-input__inner')) {
                  if (!visible(el)) continue;
                  if (placeholder === '产品简介') {
                    setValue(el, value);
                    return true;
                  }
                }
              }

              for (const wrap of document.querySelectorAll('.el-input, .el-textarea, [class*="input"]')) {
                const text = (wrap.textContent || '').trim();
                if (!text.includes(placeholder)) continue;
                const input = wrap.querySelector('input, textarea, [contenteditable="true"]');
                if (input && visible(input)) {
                  setValue(input, value);
                  return true;
                }
              }
              return false;
            }
            """,
            [placeholder, value],
        )
    )


def _fill_product_intro(page: Page, app_name: str) -> None:
    """填写首段内联「产品简介」——可能是 input placeholder 或可见假 placeholder 文本。"""
    frame = _find_draft_editor_frame(page, app_name)
    intro = frame.locator("*").filter(has_text=re.compile(r"为您提供的，用于")).filter(
        has_text=re.compile(r"的应用")
    ).first

    playwright_candidates = [
        intro.locator("input.el-input__inner").first,
        intro.locator("input, textarea, [contenteditable='true']").first,
        intro.locator(".el-input input").first,
        frame.locator(".el-input").filter(has_text=re.compile(r"^产品简介$")).locator("input").first,
        frame.get_by_placeholder("产品简介"),
        frame.get_by_placeholder(re.compile(r"产品简介")),
    ]
    for locator in playwright_candidates:
        try:
            if not locator.count():
                continue
            target = locator.first
            if not target.is_visible(timeout=400):
                continue
            target.click(timeout=1_000)
            target.fill(app_name, timeout=1_500)
            return
        except Exception:  # noqa: BLE001
            continue

    for search_frame in page.frames:
        if _fill_template_field_js(search_frame, "产品简介", app_name):
            return

    try:
        tag = intro.get_by_text("产品简介", exact=True).first
        tag.click(timeout=1_500)
        page.keyboard.press("Meta+A")
        page.keyboard.type(app_name, delay=20)
        if _set_input_value_js(frame, "active", app_name):
            return
    except Exception:  # noqa: BLE001
        pass

    raise RuntimeError(
        f"未找到可填写的「产品简介」输入框；{debug_frames_summary(page, probe_text='产品简介')}"
    )


def _fill_placeholder_in_frame(
    page: Page,
    frame: Frame,
    value: str,
    *,
    placeholder: str,
    label: str = "",
) -> None:
    """填写 iframe 内联 placeholder 输入框（协议草稿模板）。"""
    field_label = label or placeholder
    if _fill_template_field_js(frame, placeholder, value):
        return

    candidates = [
        frame.get_by_placeholder(placeholder),
        frame.locator(f"input[placeholder*='{placeholder}']"),
        frame.locator(f"textarea[placeholder*='{placeholder}']"),
        frame.locator(f"input.el-input__inner[placeholder*='{placeholder}']"),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            target = locator.first
            target.wait_for(state="visible", timeout=1_000)
            target.fill(value, timeout=1_500)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    for search_frame in page.frames:
        if _fill_template_field_js(search_frame, placeholder, value):
            return

    raise RuntimeError(f"未找到可填写的「{field_label}」输入框：{last_error}")


def _open_agreement_service(page: Page) -> Frame:
    dismiss_menu_guide_popup(page)
    wait_for_app_detail_ready(page, timeout_ms=6_000)
    click_sidebar_menu_item(page, "协议服务", parent_group="服务", timeout_ms=2_500)
    return _resolve_agreement_frame(page, timeout_ms=6_000)


def _create_new_agreement(
    page: Page,
    app_name: str,
    *,
    content_frame: Frame | None = None,
) -> Frame:
    frame, btn = _wait_agreement_service_ready(page, content_frame=content_frame, timeout_ms=5_000)
    _click_new_agreement(page, frame, btn)

    dialog_frame, dialog = _find_new_agreement_dialog(page)
    _fill_el_form_field(dialog_frame, dialog, "协议名称", app_name)

    click_first_visible(
        page,
        [
            dialog.get_by_role("button", name=re.compile(r"下一步")),
            dialog.locator("button.el-button--primary").filter(has_text="下一步"),
            dialog.locator("button").filter(has_text="下一步"),
        ],
        label="下一步",
        timeout_ms=4_000,
    )
    return _find_draft_editor_frame(page, app_name, timeout_ms=8_000)


def _resolve_draft_frame_for_section9(page: Page, frame: Frame) -> Frame:
    for candidate in page.frames:
        try:
            if candidate.locator(
                "xpath=//*[normalize-space(.)='增加商业联系方式'"
                " and not(ancestor::*[contains(@class,'el-select')])]"
            ).count():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    return frame


def _dismiss_open_poppers(page: Page, frame: Frame) -> None:
    """关闭可能遮挡点击的 Element Plus 下拉层。"""
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
    frame.evaluate(
        """
        () => {
          document.querySelectorAll('.el-select__popper, .el-popper.is-pure, .el-popper[aria-hidden="false"]').forEach((el) => {
            el.remove();
          });
        }
        """
    )
    page.wait_for_timeout(120)


def _section9_add_contact_link(frame: Frame) -> Locator:
    """第 9 条「增加商业联系方式」链接（排除 el-select 内部误匹配）。"""
    return frame.locator(
        "xpath=//*[normalize-space(.)='增加商业联系方式'"
        " and not(ancestor::*[contains(@class,'el-select')])"
        " and not(ancestor::*[contains(@class,'el-select-dropdown')])]"
    ).last


def _section9_contact_area(frame: Frame, add_link: Locator) -> Locator:
    scoped = add_link.locator(
        "xpath=ancestor::*[contains(normalize-space(.), '如何联系我们')][1]"
    )
    if scoped.count():
        return scoped.first
    return frame.locator("*").filter(has_text=re.compile(r"如何联系我们")).last


def _resolve_section9_contact_fields(
    frame: Frame,
    add_link: Locator,
) -> tuple[Locator | None, Locator | None, Locator | None]:
    """定位「增加商业联系方式」上方一行的下拉与电话输入框（DOM 常非直接 sibling）。"""
    handle = add_link.element_handle()
    if handle is None:
        return None, None, None

    marked = frame.evaluate(
        """
        (linkEl) => {
          document.querySelectorAll('[data-agc-contact-select],[data-agc-contact-phone],[data-agc-contact-row]').forEach((el) => {
            el.removeAttribute('data-agc-contact-select');
            el.removeAttribute('data-agc-contact-phone');
            el.removeAttribute('data-agc-contact-row');
          });

          const linkText = '增加商业联系方式';
          let link = linkEl;
          if ((link.innerText || link.textContent || '').trim() !== linkText) {
            link = link.closest('a, span, button, label, p') || linkEl;
          }
          const linkRect = link.getBoundingClientRect();
          const section = [...document.querySelectorAll('*')].find((el) => {
            const t = el.textContent || '';
            return t.includes('如何联系我们') && t.includes('增加商业联系方式');
          });
          const scope = section || document.body;

          let bestSelect = null;
          let bestDist = Infinity;
          for (const sel of scope.querySelectorAll('.el-select')) {
            const r = sel.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) continue;
            if (r.bottom > linkRect.top + 12) continue;
            const dist = linkRect.top - r.bottom;
            if (dist >= -4 && dist < bestDist) {
              bestDist = dist;
              bestSelect = sel;
            }
          }

          if (!bestSelect) {
            let cur = link;
            while (cur && !bestSelect) {
              let prev = cur.previousElementSibling;
              while (prev) {
                const sel = prev.matches?.('.el-select') ? prev : prev.querySelector?.('.el-select');
                if (sel) {
                  bestSelect = sel;
                  break;
                }
                prev = prev.previousElementSibling;
              }
              cur = cur.parentElement;
            }
          }
          if (!bestSelect) return false;

          bestSelect.setAttribute('data-agc-contact-select', '1');
          const selectRect = bestSelect.getBoundingClientRect();
          let phoneInput = null;
          for (const inp of scope.querySelectorAll('input.el-input__inner, input[type="text"]')) {
            const ph = inp.getAttribute('placeholder') || '';
            if (ph.includes('自定义内容')) continue;
            const r = inp.getBoundingClientRect();
            if (r.width < 1) continue;
            if (Math.abs(r.top - selectRect.top) > 24) continue;
            if (r.left > selectRect.right - 24) {
              phoneInput = inp;
              break;
            }
          }
          if (phoneInput) phoneInput.setAttribute('data-agc-contact-phone', '1');

          const row = bestSelect.closest('div[class*="row"], div[class*="contact"], div[class*="item"]')
            || bestSelect.parentElement;
          if (row) row.setAttribute('data-agc-contact-row', '1');
          return true;
        }
        """,
        handle,
    )
    if not marked:
        return None, None, None

    contact_select = frame.locator("[data-agc-contact-select='1']").first
    contact_row = frame.locator("[data-agc-contact-row='1']").first
    phone_input = frame.locator("[data-agc-contact-phone='1']").first
    try:
        if not contact_select.is_visible(timeout=500):
            return None, None, None
    except Exception:  # noqa: BLE001
        return None, None, None
    return contact_select, contact_row, phone_input


def _js_click_section9_add_contact(frame: Frame) -> bool:
    return bool(
        frame.evaluate(
            """
            () => {
              const text = '增加商业联系方式';
              const inSelect = (el) =>
                !!el.closest('.el-select, .el-select-dropdown, .el-popper');
              const candidates = [];
              for (const el of document.querySelectorAll('a, span, button, label, p')) {
                if (inSelect(el)) continue;
                const label = (el.innerText || el.textContent || '').trim();
                if (label !== text) continue;
                candidates.push(el);
              }
              candidates.sort(
                (a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top
              );
              const target = candidates[0];
              if (!target) return false;
              target.scrollIntoView({ block: 'center' });
              target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
              return true;
            }
            """
        )
    )


def _prepare_section9_contact_row(
    page: Page,
    frame: Frame,
) -> tuple[Frame, Locator, Locator, Locator, Locator | None]:
    """点击「增加商业联系方式」，等待紧挨上方一行出现（下拉 + 输入框）。"""
    frame = _resolve_draft_frame_for_section9(page, frame)
    _dismiss_open_poppers(page, frame)

    frame.evaluate(
        """
        () => {
          window.scrollTo(0, document.body.scrollHeight);
          const marker = [...document.querySelectorAll('*')].find((el) => {
            const t = el.textContent || '';
            return t.includes('如何联系我们') && t.includes('行使您的相关权利');
          });
          if (marker) marker.scrollIntoView({ block: 'center' });
        }
        """
    )
    page.wait_for_timeout(150)

    add_link = _section9_add_contact_link(frame)
    if not add_link.count():
        raise RuntimeError("未找到「增加商业联系方式」链接")

    if not _js_click_section9_add_contact(frame):
        raise RuntimeError("未能点击「增加商业联系方式」")

    contact_select = None
    contact_row = None
    phone_input = None
    deadline = time.time() + 10
    while time.time() < deadline:
        add_link = _section9_add_contact_link(frame)
        contact_select, contact_row, phone_input = _resolve_section9_contact_fields(frame, add_link)
        if contact_select is not None:
            break
        page.wait_for_timeout(150)

    if contact_select is None:
        raise RuntimeError("点击「增加商业联系方式」后，上方未出现联系方式输入行")

    if not contact_row.count():
        contact_row = contact_select.locator(
            "xpath=ancestor::div[contains(@class,'el-select') or contains(@class,'row') or contains(@class,'item')][1]"
        )
    return frame, add_link, contact_row, contact_select, phone_input


def _select_popper_is_open(page: Page, frame: Frame) -> Locator | None:
    try:
        return _find_visible_select_popper(page, frame)
    except Exception:  # noqa: BLE001
        return None


def _open_el_select_dropdown(page: Page, frame: Frame, contact_select: Locator) -> Locator:
    """Element Plus 下拉：首次点击常仅 focus，需再点箭头/包装器才展开选项。"""
    contact_select.scroll_into_view_if_needed(timeout=3_000)

    open_targets = [
        contact_select.locator(".el-select__caret").first,
        contact_select.locator(".el-input__suffix").first,
        contact_select.locator(".el-select__suffix").first,
        contact_select.locator(".el-select__wrapper").first,
        contact_select.locator(".el-select__selection").first,
        contact_select.first,
    ]

    for _ in range(5):
        popper = _select_popper_is_open(page, frame)
        if popper is not None:
            return popper

        for target in open_targets:
            try:
                if not target.count() or not target.is_visible(timeout=250):
                    continue
                target.click(force=True, timeout=1_500)
                page.wait_for_timeout(280)
                popper = _select_popper_is_open(page, frame)
                if popper is not None:
                    return popper
            except Exception:  # noqa: BLE001
                continue

        handle = contact_select.element_handle()
        if handle is not None:
            frame.evaluate(
                """
                (selectEl) => {
                  const caret = selectEl.querySelector(
                    '.el-select__caret, .el-input__suffix, .el-select__suffix'
                  );
                  const wrap = selectEl.querySelector('.el-select__wrapper') || selectEl;
                  const target = caret || wrap;
                  target.scrollIntoView({ block: 'center' });
                  for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
                  }
                }
                """,
                handle,
            )
            page.wait_for_timeout(280)

        try:
            wrapper = contact_select.locator(".el-select__wrapper").first
            if wrapper.count():
                wrapper.focus(timeout=1_000)
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(250)
                page.keyboard.press("Space")
                page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            pass

    popper = _select_popper_is_open(page, frame)
    if popper is not None:
        return popper
    raise RuntimeError("未能展开联系方式下拉选项列表")


def _click_select_option(popper: Locator, option_text: str, *, page: Page) -> None:
    option = popper.locator("li.el-select-dropdown__item, li").filter(
        has_text=re.compile(rf"^{re.escape(option_text)}$")
    )
    option.first.wait_for(state="visible", timeout=3_000)
    option.first.click(force=True, timeout=2_000)
    page.wait_for_timeout(150)


def _select_phone_in_contact_row(
    page: Page,
    frame: Frame,
    contact_select: Locator,
) -> None:
    """在联系方式行下拉里选择「电话」。"""
    try:
        selected = contact_select.locator(".el-select__selected-item").first
        if selected.count() and "电话" in selected.inner_text(timeout=500):
            return
    except Exception:  # noqa: BLE001
        pass

    popper = _open_el_select_dropdown(page, frame, contact_select)
    try:
        _click_select_option(popper, "电话", page=page)
        return
    except Exception:  # noqa: BLE001
        pass

    picked = frame.evaluate(
        """
        () => {
          for (const popper of document.querySelectorAll('.el-select__popper, .el-popper')) {
            const style = window.getComputedStyle(popper);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            for (const li of popper.querySelectorAll('li.el-select-dropdown__item, li')) {
              const text = (li.textContent || '').replace(/\\s+/g, '');
              if (text === '电话') {
                li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                li.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                return true;
              }
            }
          }
          return false;
        }
        """
    )
    if not picked:
        raise RuntimeError("未能选择联系方式「电话」")

    try:
        selected = contact_select.locator(".el-select__selected-item").first
        if selected.count() and "电话" in selected.inner_text(timeout=1_000):
            return
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("未能选择联系方式「电话」")


def _fill_section9_business_contact(page: Page, frame: Frame, contact_phone: str) -> None:
    """第 9 条：点「增加商业联系方式」→ 上方下拉选「电话」→ 右侧填号码。"""
    frame, add_link, contact_row, contact_select, phone_input = _prepare_section9_contact_row(page, frame)

    _select_phone_in_contact_row(page, frame, contact_select)

    phone_candidates: list[Locator] = []
    if phone_input is not None and phone_input.count():
        phone_candidates.append(phone_input)
    phone_candidates.extend(
        [
            frame.locator("[data-agc-contact-phone='1']").first,
            contact_row.locator("input.el-input__inner").first,
            contact_row.locator("input[type='text']").first,
            contact_select.locator(
                "xpath=../following-sibling::*//input[contains(@class,'el-input__inner')][1]"
            ),
            add_link.locator(
                "xpath=preceding::*[.//div[contains(@class,'el-select')]][1]"
                "//input[contains(@class,'el-input__inner')][not(contains(@placeholder,'自定义'))][1]"
            ),
        ]
    )
    filled = False
    for locator in phone_candidates:
        try:
            phone_input = locator.first
            if not phone_input.count() or not phone_input.is_visible(timeout=500):
                continue
            phone_input.click(timeout=1_000)
            phone_input.fill(contact_phone, timeout=2_000)
            filled = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not filled:
        raise RuntimeError(f"未找到第 9 条商业联系电话输入框")


def _select_section_71_promise_option(frame: Frame) -> None:
    """7.1 单选：第一项是「固定存储期限」，第二项才是「我们承诺…」。"""
    section = frame.locator("*").filter(
        has_text=re.compile(r"7\.1|信息存储地点")
    ).filter(has_text=re.compile(r"固定存储期限|我们承诺")).last

    playwright_candidates = [
        section.locator(".el-radio").filter(has_text=re.compile(r"我们承诺")).first,
        section.locator("label.el-radio").filter(has_text=re.compile(r"我们承诺")).first,
        section.locator(".el-radio-group .el-radio, .el-radio-group label").nth(1),
        frame.locator(".el-radio").filter(has_text=re.compile(r"我们承诺")).first,
        frame.locator("label").filter(has_text=re.compile(r"我们承诺")).filter(
            has_text=re.compile(r"最短时间")
        ).first,
    ]
    for locator in playwright_candidates:
        try:
            if not locator.count():
                continue
            target = locator.first
            target.scroll_into_view_if_needed(timeout=2_000)
            target.click(timeout=3_000)
            return
        except Exception:  # noqa: BLE001
            continue

    clicked = frame.evaluate(
        """
        () => {
          const blocks = document.querySelectorAll('p, div, section, li, form, article');
          for (const block of blocks) {
            const text = block.textContent || '';
            if (!text.includes('7.1') && !text.includes('信息存储地点')) continue;
            if (!text.includes('我们承诺') && !text.includes('固定存储期限')) continue;

            const promise = [...block.querySelectorAll('.el-radio, label.el-radio, label')].find(
              (el) => (el.textContent || '').includes('我们承诺')
            );
            if (promise) {
              promise.click();
              const input = promise.querySelector('input[type="radio"]');
              if (input) {
                input.checked = true;
                input.dispatchEvent(new Event('change', { bubbles: true }));
              }
              return true;
            }

            const group = block.querySelector('.el-radio-group');
            if (group) {
              const items = group.querySelectorAll('.el-radio, label');
              if (items.length >= 2) {
                items[1].click();
                const input = items[1].querySelector('input[type="radio"]');
                if (input) {
                  input.checked = true;
                  input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return true;
              }
            }
          }
          return false;
        }
        """
    )
    if clicked:
        return
    raise RuntimeError("未找到 7.1「我们承诺」单选项（应为第二项）")


def _fill_agreement_draft(
    page: Page,
    app_name: str,
    contact_phone: str,
    *,
    content_frame: Frame | None = None,
) -> None:
    frame = content_frame or _find_draft_editor_frame(page, app_name)

    # 产品简介：首段内联字段，DOM 结构特殊，单独处理
    _fill_product_intro(page, app_name)
    frame = _find_draft_editor_frame(page, app_name, timeout_ms=3_000)

    # 1.1 条款勾选
    section = frame.locator("*").filter(has_text=re.compile(r"我们如何收集和使用您的个人信息")).first
    checkbox_candidates = [
        section.locator("label").filter(has_text=re.compile(r"1\.1")).locator("input[type='checkbox']"),
        section.get_by_text("1.1", exact=False).locator("xpath=ancestor::label[1]//input[@type='checkbox']"),
        frame.locator("label").filter(has_text=re.compile(r"^1\.1")).locator("input[type='checkbox']"),
    ]
    checked = False
    for locator in checkbox_candidates:
        try:
            box = locator.first
            if box.count():
                box.check(timeout=3_000)
                checked = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not checked:
        raise RuntimeError("未找到「1.1」条款复选框")

    page.wait_for_timeout(400)

    # 1.1 子项
    _fill_placeholder_in_frame(
        page,
        frame,
        app_name,
        placeholder="请输入应用内服务模式设置页面路径",
        label="应用内服务模式设置页面路径",
    )

    selects = frame.locator(".el-select").filter(
        has=frame.locator("input[placeholder='请选择'], .el-select__placeholder:has-text('请选择')")
    )
    # 1.1 下「不收集个人信息」
    try:
        _select_el_option(frame, selects.first, "不收集个人信息")
    except Exception:
        _select_el_option(
            frame,
            frame.locator(".el-select").filter(has_text="请选择").first,
            "不收集个人信息",
        )

    product_inputs = frame.get_by_placeholder(re.compile(r"请输入产品功能"))
    product_inputs.nth(0).fill(app_name, timeout=5_000)
    product_inputs.nth(1).fill("其他", timeout=5_000)

    _select_section_71_promise_option(frame)

    # 7.2 服务器所在国家
    _fill_placeholder_in_frame(
        page,
        frame,
        "中国境内",
        placeholder="服务器所在国家",
        label="服务器所在国家",
    )

    # 第 9 条：商业联系方式
    _fill_section9_business_contact(page, frame, contact_phone)

    # 生成协议
    click_first_visible(
        page,
        [
            frame.get_by_role("button", name=re.compile(r"生成协议")),
            frame.locator("button:has-text('生成协议')"),
        ],
        label="生成协议",
    )
    page.wait_for_timeout(500)

    success_dialog = None
    for ctx in (frame, page):
        candidate = ctx.locator(".el-overlay-dialog, .el-dialog, [role='dialog']").filter(
            has_text=re.compile(r"提交成功")
        )
        try:
            candidate.first.wait_for(state="visible", timeout=8_000)
            success_dialog = candidate
            break
        except Exception:  # noqa: BLE001
            continue
    if success_dialog is None:
        raise RuntimeError("点击「生成协议」后未出现「提交成功」弹框")

    click_first_visible(
        page,
        [
            success_dialog.get_by_role("button", name=re.compile(r"确认")),
            success_dialog.locator("button:has-text('确认')"),
            frame.get_by_role("button", name=re.compile(r"^确认$")),
        ],
        label="提交成功-确认",
    )
    page.wait_for_timeout(1000)


def _mark_privacy_policy_select(frame: Frame) -> bool:
    return bool(
        frame.evaluate(
            """
            () => {
              document.querySelectorAll('[data-agc-privacy-select]').forEach((el) => {
                el.removeAttribute('data-agc-privacy-select');
              });
              for (const item of document.querySelectorAll('.el-form-item, tr, li, div')) {
                const label = item.querySelector('label, .el-form-item__label, span');
                const labelText = (label && label.textContent) || '';
                if (!labelText.includes('隐私政策')) continue;
                const sel = item.querySelector('.el-select');
                if (sel) {
                  sel.setAttribute('data-agc-privacy-select', '1');
                  return true;
                }
              }
              const labels = [...document.querySelectorAll('*')].filter((el) => {
                const t = (el.textContent || '').trim();
                return t === '隐私政策' || /^隐私政策[:：]?$/.test(t);
              });
              for (const label of labels) {
                let node = label;
                for (let i = 0; i < 6 && node; i++) {
                  const sel = node.querySelector?.('.el-select')
                    || node.parentElement?.querySelector('.el-select');
                  if (sel) {
                    sel.setAttribute('data-agc-privacy-select', '1');
                    return true;
                  }
                  const following = [];
                  let sib = label;
                  while (sib && following.length < 4) {
                    sib = sib.nextElementSibling;
                    if (sib) following.push(sib);
                  }
                  for (const s of following) {
                    const sel2 = s.matches?.('.el-select') ? s : s.querySelector?.('.el-select');
                    if (sel2) {
                      sel2.setAttribute('data-agc-privacy-select', '1');
                      return true;
                    }
                  }
                  node = node.parentElement;
                }
              }
              return false;
            }
            """
        )
    )


def _find_privacy_policy_select(page: Page, *, timeout_ms: int = 15_000) -> tuple[Frame, Locator]:
    """1.8：「隐私政策」旁的「请选择」下拉框。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            try:
                if _mark_privacy_policy_select(frame):
                    sel = frame.locator("[data-agc-privacy-select='1']").first
                    if sel.count() and sel.is_visible(timeout=400):
                        return frame, sel

                form_item = frame.locator(".el-form-item").filter(has_text="隐私政策")
                sel = form_item.locator(".el-select").first
                if sel.count() and sel.is_visible(timeout=400):
                    return frame, sel

                sel = frame.locator(
                    "xpath=//*[contains(normalize-space(.), '隐私政策')]"
                    "/following::div[contains(@class,'el-select')][1]"
                ).first
                if sel.count() and sel.is_visible(timeout=400):
                    return frame, sel
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(250)

    raise RuntimeError(
        f"未找到「隐私政策」旁的「请选择」下拉框；{debug_frames_summary(page, probe_text='隐私政策')}"
    )


def _select_privacy_policy_option(
    page: Page,
    frame: Frame,
    privacy_select: Locator,
    app_name: str,
) -> None:
    popper = _open_el_select_dropdown(page, frame, privacy_select)
    try:
        _click_select_option(popper, app_name, page=page)
        return
    except Exception:  # noqa: BLE001
        pass
    option = popper.locator("li.el-select-dropdown__item, li").filter(has_text=app_name)
    option.first.click(force=True, timeout=2_000)


def _bind_privacy_policy_in_version(page: Page, app_name: str) -> None:
    navigate_to_version_ready_submit(page)
    wait_for_version_detail_loaded(page, timeout_ms=30_000)
    content_frame, privacy_select = _find_privacy_policy_select(page, timeout_ms=15_000)

    _select_privacy_policy_option(page, content_frame, privacy_select, app_name)

    click_version_ready_submit_save(page, timeout_ms=10_000)


def configure_privacy_agreement(
    *,
    app_name: str,
    package_name: str = "",
    contact_phone: str | None = None,
) -> dict[str, Any]:
    name = (app_name or "").strip()
    bundle_id = (package_name or "").strip()
    phone = (contact_phone or APP_GALLERY_DEFAULT_CONTACT_PHONE).strip()

    if not name:
        raise ValueError("appName 不能为空")
    if bundle_id and not _looks_like_bundle_id(bundle_id):
        raise ValueError(f"packageName 格式不正确：{bundle_id}")

    shots: dict[str, str] = {}
    stamp = int(time.time())

    with launch_agc_context(headless=False) as (_pw, _browser, context, page):
        try:
            open_myapp_page(page, context)
            dismiss_blocking_tips(page)
            shots["myApp"] = save_screenshot(page, f"privacy_myapp_{stamp}.png")

            open_harmony_app_from_myapp_list(page, name, bundle_id)
            wait_for_app_detail_content_loaded(page)
            shots["appDetail"] = save_screenshot(page, f"privacy_app_{stamp}.png")

            agreement_frame = _open_agreement_service(page)
            shots["agreementService"] = save_screenshot(page, f"privacy_service_{stamp}.png")

            draft_frame = _create_new_agreement(page, name, content_frame=agreement_frame)
            shots["newAgreement"] = save_screenshot(page, f"privacy_new_{stamp}.png")

            _fill_agreement_draft(page, name, phone, content_frame=draft_frame)
            shots["draftFilled"] = save_screenshot(page, f"privacy_draft_{stamp}.png")

            _bind_privacy_policy_in_version(page, name)
            shots["versionSaved"] = save_screenshot(page, f"privacy_version_{stamp}.png")

            return {
                "ok": True,
                "packageName": bundle_id,
                "appName": name,
                "contactPhone": phone,
                "myAppUrl": AGC_MYAPP_URL,
                "stepsCompleted": [
                    "create_privacy_agreement",
                    "bind_privacy_policy_in_version",
                ],
                "screenshots": shots,
                "artifactDir": str(BROWSER_ARTIFACT_DIR.resolve()),
            }
        except Exception as exc:
            fail_shot = ""
            try:
                fail_shot = save_screenshot(page, f"privacy_failed_{stamp}.png")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"{exc}；失败截图={fail_shot or '(无)'}") from exc


@tool
def configure_app_gallery_privacy_agreement(
    appName: str,
    packageName: str = "",
    contactPhone: str = "",
) -> str:
    """在 AppGallery Connect 控制台创建隐私协议，并在版本信息中关联。

    因官方无完整 API，使用 Playwright 浏览器自动化完成：

    1. 打开 myApp → 进入 HarmonyOS 应用（按应用名称点击进入）→「协议服务」→「新建协议」
    2. 填写协议草稿（产品简介、1.1 不收集个人信息、7.1/7.2、商业联系电话等）并「生成协议」
    3. 进入「版本信息」→ 准备提交版本 → 隐私政策选择该协议 →「保存草稿」

    参数：
    - appName: 应用名称（必填；用于列表搜索、协议名称、隐私政策选项）
    - packageName: 应用包名 / bundle id（可选；myApp 列表存在同名应用时用于消歧）
    - contactPhone: 商业联系电话，默认读取 .env 的 APP_GALLERY_DEFAULT_CONTACT_PHONE

    使用前需先运行：python -m app.tools.agc_browser_login 保存登录态。
    """
    try:
        result = run_sync_in_thread(
            configure_privacy_agreement,
            app_name=appName,
            package_name=packageName,
            contact_phone=contactPhone.strip() or None,
        )
    except PlaywrightTimeoutError as exc:
        return f"配置隐私协议失败（页面超时）: {exc}"
    except Exception as exc:
        return f"配置隐私协议失败: {exc}"

    return json.dumps(result, ensure_ascii=False)
