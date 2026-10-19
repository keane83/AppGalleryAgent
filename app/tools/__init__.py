"""Agent 可调用工具集合。后续新增 tool 在此目录按模块拆分，并在本文件汇总注册。"""

from app.tools.app_gallery_auth import get_app_gallery_access_token, safe_tool_log_content
from app.tools.app_gallery_configure_age_rating import configure_app_gallery_age_rating
from app.tools.app_gallery_configure_privacy_agreement import (
    configure_app_gallery_privacy_agreement,
)
from app.tools.app_gallery_create_appid import create_app_gallery_app_id
from app.tools.app_gallery_create_provision import create_app_gallery_provision
# AGC 官网改版后不再需要「新建发布」。实现保留在 app_gallery_create_publish_app.py。
# from app.tools.app_gallery_create_publish_app import create_app_gallery_publish_app
from app.tools.app_gallery_build_update_app import update_app_gallery_build_update_app
from app.tools.app_gallery_update_app_file_info import update_app_gallery_app_file_info
from app.tools.app_gallery_update_app_info import update_app_gallery_app_basic_info
from app.tools.harmony_capture_screenshots import (
    capture_harmony_app_screenshots,
    capture_harmony_screenshots_and_app_icon,
)
from app.tools.harmony_configure_signing import configure_harmony_app_signing
from app.tools.harmony_replace_app_icon import replace_harmony_app_icon

TOOLS = [
    get_app_gallery_access_token,
    create_app_gallery_app_id,
    create_app_gallery_provision,
    # create_app_gallery_publish_app,
    configure_app_gallery_privacy_agreement,
    configure_app_gallery_age_rating,
    update_app_gallery_app_file_info,
    update_app_gallery_app_basic_info,
    update_app_gallery_build_update_app,
    configure_harmony_app_signing,
    capture_harmony_screenshots_and_app_icon,
    replace_harmony_app_icon,
]

__all__ = [
    "TOOLS",
    "create_app_gallery_app_id",
    "create_app_gallery_provision",
    # "create_app_gallery_publish_app",
    "configure_app_gallery_privacy_agreement",
    "configure_app_gallery_age_rating",
    "update_app_gallery_app_file_info",
    "update_app_gallery_app_basic_info",
    "update_app_gallery_build_update_app",
    "capture_harmony_app_screenshots",
    "capture_harmony_screenshots_and_app_icon",
    "configure_harmony_app_signing",
    "replace_harmony_app_icon",
    "get_app_gallery_access_token",
    "safe_tool_log_content",
]
