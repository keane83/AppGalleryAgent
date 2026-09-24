"""工具对用户展示的中文名称（不含 API / 函数名）。"""

from __future__ import annotations

import re

TOOL_STEP_LABELS: dict[str, str] = {
    "create_app_gallery_app_id": "步骤 1",
    "create_app_gallery_provision": "步骤 2",
    "create_app_gallery_publish_app": "步骤 3",
    "configure_harmony_app_signing": "步骤 4",
    "replace_harmony_app_icon": "步骤 4b",
    "capture_harmony_screenshots_and_app_icon": "步骤 5",
    "update_app_gallery_app_file_info": "步骤 6",
    "update_app_gallery_app_basic_info": "步骤 7",
    "configure_app_gallery_privacy_agreement": "步骤 8",
    "configure_app_gallery_age_rating": "步骤 9",
    "update_app_gallery_build_update_app": "步骤 10",
    "get_app_gallery_access_token": "辅助",
}

TOOL_USER_LABELS: dict[str, str] = {
    "create_app_gallery_app_id": "创建 APP ID",
    "create_app_gallery_provision": "申请 Profile 并下载",
    "create_app_gallery_publish_app": "控制台新建发布",
    "configure_harmony_app_signing": "配置工程签名",
    "replace_harmony_app_icon": "替换应用图标",
    "capture_harmony_screenshots_and_app_icon": "截取商店截图与图标",
    "update_app_gallery_app_file_info": "上传图标与截图",
    "update_app_gallery_app_basic_info": "更新商店基本信息",
    "configure_app_gallery_privacy_agreement": "创建并关联隐私协议",
    "configure_app_gallery_age_rating": "填写年龄分级",
    "update_app_gallery_build_update_app": "编译并上传安装包",
    "get_app_gallery_access_token": "刷新鉴权",
}

_API_PATH_PATTERN = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w./-]+|\b/publish/v3/[\w-]+",
    re.IGNORECASE,
)
_TOOL_CALL_LINE_PATTERN = re.compile(
    r"^\s*(?:"
    r"【即将执行工具】|【步骤\s*\d+.*已完成】|"
    r"参数：|•\s*步骤\s*\d+|"
    r"\{.*\"(?:appId|packageName|projectPath)\".*\}"
    r")\s*$",
    re.MULTILINE,
)
_TOOL_NAME_PATTERN = re.compile(
    r"\b("
    r"create_app_gallery_\w+|configure_app_gallery_\w+|configure_harmony_\w+|"
    r"update_app_gallery_\w+|capture_harmony_\w+|replace_harmony_\w+|get_app_gallery_\w+"
    r")\b"
)


def user_tool_label(tool_name: str) -> str:
    name = (tool_name or "").strip()
    if not name:
        return "执行任务"
    step = TOOL_STEP_LABELS.get(name, "")
    label = TOOL_USER_LABELS.get(name, "处理中")
    return f"{step} · {label}" if step else label


def user_tool_status_text(tool_name: str, *, prefix: str = "正在执行") -> str:
    return f"{prefix}：{user_tool_label(tool_name)}"


def _replace_tool_name(match: re.Match[str]) -> str:
    return TOOL_USER_LABELS.get(match.group(1), "相关步骤")


def _should_drop_thinking_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("【即将执行工具】", "参数：")):
        return True
    if stripped.startswith("•") and "步骤" in stripped:
        return True
    if "已完成】" in stripped and stripped.startswith("【步骤"):
        return True
    if stripped.startswith("{") and stripped.endswith("}"):
        return True
    if _API_PATH_PATTERN.search(stripped):
        return True
    return bool(_TOOL_CALL_LINE_PATTERN.match(stripped))


def sanitize_thinking_for_user(text: str) -> str:
    """从思考区文案中移除 API 路径、工具函数名等内部细节。"""
    if not text:
        return text

    kept: list[str] = []
    for line in text.splitlines():
        if _should_drop_thinking_line(line):
            continue
        cleaned_line = _API_PATH_PATTERN.sub("", line)
        cleaned_line = _TOOL_NAME_PATTERN.sub(_replace_tool_name, cleaned_line)
        cleaned_line = re.sub(r"`[^`]*`", "", cleaned_line).rstrip()
        kept.append(cleaned_line)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return cleaned.strip()
