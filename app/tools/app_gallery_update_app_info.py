"""AppGallery Connect：分析工程并更新应用基本信息。"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langchain_core.tools import tool

from app.config import (
    APP_GALLERY_API_BASE_URL,
    APP_GALLERY_DEFAULT_AICG_TYPE,
    APP_GALLERY_DEFAULT_APP_TYPE,
    APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO,
    APP_GALLERY_DEFAULT_CONTACT_EMAIL,
    APP_GALLERY_DEFAULT_CONTACT_NAME,
    APP_GALLERY_DEFAULT_CONTACT_PHONE,
)
from app.tool_progress import report_tool_progress
from app.tools.app_gallery_app_info_analyzer import (
    analyze_app_gallery_listing,
    format_analysis_summary,
    log_analysis_result,
)
from app.tools.app_gallery_auth import build_api_client_headers, resolve_api_auth

# 文档：
# - https://developer.huawei.com/consumer/cn/doc/app/agc-help-publish-api-appinfo-update-0000002236201246
# - https://developer.huawei.com/consumer/cn/doc/app/agc-help-publish-api-language-info-update-0000002271160569
UPDATE_APP_INFO_PATH = "/publish/v3/app-info"
UPDATE_APP_LANGUAGE_INFO_PATH = "/publish/v3/app-language-info"
DEFAULT_LANG = "zh-CN"

logger = logging.getLogger("haa.app_info_update")


def resolve_registered_dcl_type(app_type: int) -> int:
    """将配置中的 APP 类型映射为 Huawei registeredDclType。

    官方取值：0=服务器在中国大陆，1=服务器不在中国大陆，2=单机 APP。
    历史配置里 ``1`` 表示「单机」，需映射为 ``2``。
    """
    if app_type == 1:
        return 2
    return app_type


def format_custom_service_phone(phone: str) -> str:
    """客服电话格式，与官方示例一致：0086-1xxxxxxxxxx。"""
    raw = phone.strip()
    if raw.startswith("0086-"):
        return raw
    if raw.startswith("+86"):
        return f"0086-{raw[3:].lstrip('-')}"
    if raw.startswith("86") and len(raw) > 11:
        return f"0086-{raw[2:]}"
    return f"0086-{raw}"


def build_app_info_update_body(
    analysis: dict[str, Any],
    *,
    lang: str = DEFAULT_LANG,
    collect_person_info: int = APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO,
    aicg_type: int = APP_GALLERY_DEFAULT_AICG_TYPE,
    app_type: int = APP_GALLERY_DEFAULT_APP_TYPE,
    contact_name: str = APP_GALLERY_DEFAULT_CONTACT_NAME,
    contact_phone: str = APP_GALLERY_DEFAULT_CONTACT_PHONE,
    contact_email: str = APP_GALLERY_DEFAULT_CONTACT_EMAIL,
) -> dict[str, Any]:
    """构造 PUT /publish/v3/app-info 请求体（对齐官方示例字段）。"""
    phone = contact_phone.strip()
    email = contact_email.strip()
    body: dict[str, Any] = {
        "defaultLang": lang,
        "harmonyChildType": int(analysis["harmonyChildType"]),
        "kindMainTag": int(analysis["kindMainTag"]),
        "registeredDclType": resolve_registered_dcl_type(int(app_type)),
        "appNetType": 0,
        "deviceTypes": [{"deviceType": 4, "appAdapters": ""}],
        "isAiGenerate": 1 if int(aicg_type) else 0,
        "aiTypes": [] if not int(aicg_type) else [],
        "appReviewName": contact_name.strip(),
        "appReviewPhoneInfo": {"account": phone},
        "appReviewEmailInfo": {"account": email},
        "customInfo": {
            "phone": format_custom_service_phone(phone),
            "email": email,
        },
    }
    # 「是否涉及个人信息收集」属于版本页「隐私标签信息录入」，app-info API 无法设置。
    return body


def build_app_language_update_body(
    analysis: dict[str, Any],
    *,
    lang: str = DEFAULT_LANG,
) -> dict[str, Any]:
    """构造 PUT /publish/v3/app-language-info 请求体（应用介绍、一句话简介）。"""
    return {
        "lang": lang,
        "appDesc": str(analysis["appDesc"]),
        "briefInfo": str(analysis["briefInfo"]),
    }


def _put_app_gallery(
    path: str,
    *,
    app_id: str,
    body: dict[str, Any],
    lang: str = DEFAULT_LANG,
    release_type: int = 1,
    release_phase: int = 0,
    access_token: str | None = None,
    timeout: float = 60.0,
    include_lang_param: bool = True,
) -> dict[str, Any]:
    aid = (app_id or "").strip()
    if not aid:
        raise ValueError("appId 不能为空")
    if not body:
        raise ValueError("请求 body 不能为空")

    token, client_id = resolve_api_auth(access_token)
    url = f"{APP_GALLERY_API_BASE_URL.rstrip('/')}{path}"
    headers = build_api_client_headers(token, client_id)
    params: dict[str, str | int] = {
        "appId": aid,
        "releaseType": release_type,
        "releasePhase": release_phase,
    }
    if include_lang_param:
        params["lang"] = lang

    logger.info("AppGallery PUT %s appId=%s body=%s", path, aid, json.dumps(body, ensure_ascii=False))

    with httpx.Client(timeout=timeout) as client:
        response = client.put(url, headers=headers, params=params, json=body)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"AppGallery 请求失败（{path}）：HTTP {response.status_code}，响应非 JSON：{response.text[:300]}"
        ) from exc

    ret = data.get("ret") if isinstance(data, dict) else None
    ret_code = ret.get("code") if isinstance(ret, dict) else None
    if response.status_code != 200 or (isinstance(ret, dict) and ret_code not in (0, "0")):
        raise RuntimeError(
            f"AppGallery 请求失败（{path}）：HTTP {response.status_code}，响应={json.dumps(data, ensure_ascii=False)}"
        )

    return {
        "path": path,
        "appId": aid,
        "lang": lang,
        "releaseType": release_type,
        "releasePhase": release_phase,
        "requestBody": body,
        "raw": data,
    }


def update_app_info(
    app_id: str,
    body: dict[str, Any],
    *,
    lang: str = DEFAULT_LANG,
    release_type: int = 1,
    release_phase: int = 0,
    access_token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return _put_app_gallery(
        UPDATE_APP_INFO_PATH,
        app_id=app_id,
        body=body,
        lang=lang,
        release_type=release_type,
        release_phase=release_phase,
        access_token=access_token,
        timeout=timeout,
        include_lang_param=True,
    )


def update_app_language_info(
    app_id: str,
    body: dict[str, Any],
    *,
    release_type: int = 1,
    release_phase: int = 0,
    access_token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    lang = str(body.get("lang") or DEFAULT_LANG)
    return _put_app_gallery(
        UPDATE_APP_LANGUAGE_INFO_PATH,
        app_id=app_id,
        body=body,
        lang=lang,
        release_type=release_type,
        release_phase=release_phase,
        access_token=access_token,
        timeout=timeout,
        include_lang_param=False,
    )


def analyze_and_update_app_basic_info(
    *,
    app_id: str,
    project_path: str,
    lang: str = DEFAULT_LANG,
    collect_person_info: int = APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO,
    aicg_type: int = APP_GALLERY_DEFAULT_AICG_TYPE,
    app_type: int = APP_GALLERY_DEFAULT_APP_TYPE,
    contact_name: str = APP_GALLERY_DEFAULT_CONTACT_NAME,
    contact_phone: str = APP_GALLERY_DEFAULT_CONTACT_PHONE,
    contact_email: str = APP_GALLERY_DEFAULT_CONTACT_EMAIL,
    release_type: int = 1,
    release_phase: int = 0,
    preview_only: bool = False,
    access_token: str | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, str]] = []

    def _step(name: str, detail: str = "", *, status: str = "done") -> None:
        steps.append({"step": name, "detail": detail, "status": status})
        report_tool_progress(name, step=name, detail=detail, status=status)

    _step("分析工程并生成商店文案", status="running")
    analysis = analyze_app_gallery_listing(project_path)
    analysis_steps = analysis.get("steps") or []
    if isinstance(analysis_steps, list):
        steps.extend(analysis_steps)
    summary = format_analysis_summary(analysis)
    _step(
        "分析工程并生成商店文案",
        f"{analysis.get('categoryName')}/{analysis.get('tagName')}；"
        f"介绍 {analysis.get('appDescLen', len(str(analysis.get('appDesc') or '')))} 字；"
        f"简介 {analysis.get('briefInfoLen', len(str(analysis.get('briefInfo') or '')))} 字",
    )
    app_info_body = build_app_info_update_body(
        analysis,
        lang=lang,
        collect_person_info=collect_person_info,
        aicg_type=aicg_type,
        app_type=app_type,
        contact_name=contact_name,
        contact_phone=contact_phone,
        contact_email=contact_email,
    )
    language_body = build_app_language_update_body(analysis, lang=lang)

    if preview_only:
        return {
            "previewOnly": True,
            "analysis": analysis,
            "summary": summary,
            "collectPersonInfo": collect_person_info,
            "appInfoRequestBody": app_info_body,
            "languageInfoRequestBody": language_body,
            "steps": steps,
        }

    logger.info(
        "提交 AppGallery 基本信息: appId=%s harmonyChildType=%s kindMainTag=%s",
        app_id,
        app_info_body.get("harmonyChildType"),
        app_info_body.get("kindMainTag"),
    )

    _step("提交 app-info（分类/标签/负责人）", status="running")
    app_info_result = update_app_info(
        app_id=app_id,
        body=app_info_body,
        lang=lang,
        release_type=release_type,
        release_phase=release_phase,
        access_token=access_token,
    )
    _step("提交 app-info（分类/标签/负责人）", "已提交到商店")
    _step("提交 language-info（介绍/简介）", status="running")
    language_result = update_app_language_info(
        app_id=app_id,
        body=language_body,
        release_type=release_type,
        release_phase=release_phase,
        access_token=access_token,
    )
    _step("提交 language-info（介绍/简介）", "文案已更新")

    privacy_label_result: dict[str, Any] | None = None
    if int(collect_person_info) == 0:
        app_name = str((analysis.get("context") or {}).get("appName") or "").strip()
        bundle_name = str((analysis.get("context") or {}).get("bundleName") or "").strip()
        if app_name:
            try:
                _step("浏览器设置隐私标签", "是否涉及个人信息收集=否", status="running")
                from app.tools.app_gallery_privacy_tag_browser import set_privacy_tag_no_collect_sync

                privacy_label_result = set_privacy_tag_no_collect_sync(app_name, bundle_name)
                _step("浏览器设置隐私标签", "完成")
            except Exception as exc:
                _step("浏览器设置隐私标签", f"失败: {exc}")
                logger.warning("浏览器设置隐私标签「是否涉及个人信息收集=否」失败: %s", exc)
                privacy_label_result = {"ok": False, "error": str(exc)}

    return {
        "appId": app_id,
        "lang": lang,
        "releaseType": release_type,
        "releasePhase": release_phase,
        "analysis": analysis,
        "summary": summary,
        "collectPersonInfo": collect_person_info,
        "appInfoResult": app_info_result,
        "languageInfoResult": language_result,
        "privacyLabelBrowserResult": privacy_label_result,
        "steps": steps,
    }


def _tool_response(result: dict[str, Any], *, preview_only: bool) -> str:
    analysis = result.get("analysis") or {}
    payload: dict[str, Any] = {
        "ok": not preview_only or result.get("previewOnly"),
        "previewOnly": preview_only,
        "appId": result.get("appId"),
        "summary": result.get("summary") or format_analysis_summary(analysis),
        "harmonyChildType": analysis.get("harmonyChildType"),
        "kindMainTag": analysis.get("kindMainTag"),
        "categoryName": analysis.get("categoryName"),
        "tagName": analysis.get("tagName"),
        "appDesc": analysis.get("appDesc"),
        "briefInfo": analysis.get("briefInfo"),
        "analysisSource": analysis.get("source"),
        "reason": analysis.get("reason"),
        "appDescLen": analysis.get("appDescLen", len(str(analysis.get("appDesc") or ""))),
        "briefInfoLen": analysis.get("briefInfoLen", len(str(analysis.get("briefInfo") or ""))),
        "collectPersonInfo": result.get("collectPersonInfo"),
        "steps": result.get("steps") or analysis.get("steps"),
    }
    if preview_only:
        payload["appInfoRequestBody"] = result.get("appInfoRequestBody")
        payload["languageInfoRequestBody"] = result.get("languageInfoRequestBody")
    elif result.get("appId"):
        payload["submitted"] = True
        payload["apis"] = ["PUT /publish/v3/app-info", "PUT /publish/v3/app-language-info"]
        if result.get("privacyLabelBrowserResult") is not None:
            payload["privacyLabelBrowserResult"] = result.get("privacyLabelBrowserResult")
    return json.dumps(payload, ensure_ascii=False)


@tool
def update_app_gallery_app_basic_info(
    appId: str,
    projectPath: str,
    lang: str = "zh-CN",
    contactName: str = "",
    contactPhone: str = "",
    contactEmail: str = "",
    collectPersonInfo: int = -1,
    aicgType: int = -1,
    appType: int = -1,
    releaseType: int = 1,
    releasePhase: int = 0,
    previewOnly: bool = False,
) -> str:
    """分析鸿蒙工程并更新 AppGallery 应用基本信息。

    本工具不涉及隐私协议（请用 configure_app_gallery_privacy_agreement）。

    API 步骤：
    1. PUT /publish/v3/app-info — 分类、APP 类型、AI 声明、负责人等
    2. PUT /publish/v3/app-language-info — 应用介绍 appDesc、一句话简介 briefInfo

    collectPersonInfo=0（默认）时，API 完成后还会用浏览器自动化：
    进入「版本信息 → 准备提交 → 隐私标签信息录入」，将「是否涉及个人信息收集」选为否。
    浏览器步骤依赖已保存的 AGC 登录态（先运行 python -m app.tools.agc_browser_login）。

    AI 自动分析 projectPath（默认 fast：关键词定分类 + 小 prompt 生成文案；见 APP_GALLERY_LISTING_ANALYSIS_MODE），生成并展示：
    1. 二级分类 harmonyChildType + 名称
    2. 标签 kindMainTag + 名称
    3. 应用介绍 appDesc（500 字以内，禁止 emoji/颜文字/装饰符号）
    4. 一句话简介 briefInfo（15 字以内，禁止 emoji/颜文字/装饰符号）

    返回 JSON 中的 summary 字段为人类可读摘要；后台日志也会打印同样内容。

    previewOnly=true 时仅分析预览，不调用 AGC 接口。

    默认还会提交：隐私标签个人信息收集=否（仅浏览器）、AI合成=否、APP类型=单机(registeredDclType=2)、负责人刘健。
    releaseType 必须为 1（全网），勿使用 2。
    """
    collect_pi = (
        APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO if collectPersonInfo < 0 else collectPersonInfo
    )
    aicg = APP_GALLERY_DEFAULT_AICG_TYPE if aicgType < 0 else aicgType
    app_t = APP_GALLERY_DEFAULT_APP_TYPE if appType < 0 else appType
    contact_name = contactName.strip() or APP_GALLERY_DEFAULT_CONTACT_NAME
    contact_phone = contactPhone.strip() or APP_GALLERY_DEFAULT_CONTACT_PHONE
    contact_email = contactEmail.strip() or APP_GALLERY_DEFAULT_CONTACT_EMAIL

    try:
        result = analyze_and_update_app_basic_info(
            app_id=appId,
            project_path=projectPath,
            lang=lang,
            collect_person_info=collect_pi,
            aicg_type=aicg,
            app_type=app_t,
            contact_name=contact_name,
            contact_phone=contact_phone,
            contact_email=contact_email,
            release_type=releaseType,
            release_phase=releasePhase,
            preview_only=previewOnly,
            access_token=None,
        )
    except Exception as exc:
        logger.exception("更新应用基本信息失败")
        return f"更新应用基本信息失败: {exc}"

    if previewOnly:
        log_analysis_result(result.get("analysis") or {})
        return _tool_response(result, preview_only=True)

    return _tool_response(result, preview_only=False)
