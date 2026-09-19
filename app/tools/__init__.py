"""Agent 可调用工具集合。后续新增 tool 在此目录按模块拆分，并在本文件汇总注册。"""

from app.tools.app_gallery_auth import get_app_gallery_access_token, safe_tool_log_content
from app.tools.app_gallery_create_appid import create_app_gallery_app_id
from app.tools.app_gallery_create_provision import create_app_gallery_provision
from app.tools.app_gallery_configure_privacy_agreement import (
    configure_app_gallery_privacy_agreement,
)
from app.tools.app_gallery_create_publish_app import create_app_gallery_publish_app
from app.tools.app_gallery_update_app_file_info import update_app_gallery_app_file_info
from app.tools.app_gallery_build_update_app import update_app_gallery_build_update_app
from app.tools.app_gallery_update_app_info import update_app_gallery_app_basic_info
from app.tools.basic import calculator, get_current_time
from app.tools.harmony_capture_screenshots import (
    capture_harmony_app_screenshots,
    capture_harmony_screenshots_and_app_icon,
)
from app.tools.harmony_configure_signing import configure_harmony_app_signing

TOOLS = [
    get_current_time,
    calculator,
    get_app_gallery_access_token,
    create_app_gallery_app_id,
    create_app_gallery_provision,
    create_app_gallery_publish_app,
    configure_app_gallery_privacy_agreement,
    update_app_gallery_app_file_info,
    update_app_gallery_app_basic_info,
    update_app_gallery_build_update_app,
    configure_harmony_app_signing,
    capture_harmony_screenshots_and_app_icon,
]

__all__ = [
    "TOOLS",
    "calculator",
    "create_app_gallery_app_id",
    "create_app_gallery_provision",
    "create_app_gallery_publish_app",
    "configure_app_gallery_privacy_agreement",
    "update_app_gallery_app_file_info",
    "update_app_gallery_app_basic_info",
    "update_app_gallery_build_update_app",
    "capture_harmony_app_screenshots",
    "capture_harmony_screenshots_and_app_icon",
    "configure_harmony_app_signing",
    "get_app_gallery_access_token",
    "get_current_time",
    "safe_tool_log_content",
]
