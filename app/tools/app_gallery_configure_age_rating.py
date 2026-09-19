"""AppGallery Connect 控制台：年龄分级 / 内容分级问卷自动化。"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.tools.agc_browser import run_sync_in_thread
from app.tools.app_gallery_age_rating_browser import configure_age_rating


@tool
def configure_app_gallery_age_rating(
    appName: str,
    packageName: str = "",
) -> str:
    """在 AppGallery Connect「准备提交」页完成年龄分级 / 内容分级问卷。

    因官方无完整 API，使用 Playwright 浏览器自动化完成：

    1. myApp → 进入 HarmonyOS 应用 →「版本信息」→「准备提交」
    2.「内容分级」→「年龄分级」旁点击「设置」
    3.「华为年龄分级标准」弹框 →「填写问卷」
    4.「内容分级」弹框：展开各分类（暴力内容、令人恐惧的内容等），所有单选题均选「否」
    5. 点击「验证」→ 验证完成后点击「提交」
    6. 确认弹框「是否仅面向儿童」选「否」→「确认」
    7. 回到版本页后点击右上角「保存」

    参数：
    - appName: 应用名称（必填；用于 myApp 列表搜索）
    - packageName: 应用包名 / bundle id（可选；同名应用消歧）

    使用前需先运行：python -m app.tools.agc_browser_login 保存登录态。
    """
    try:
        result = run_sync_in_thread(
            configure_age_rating,
            app_name=appName,
            bundle_id=packageName.strip(),
        )
    except PlaywrightTimeoutError as exc:
        return f"配置年龄分级失败（页面超时）: {exc}"
    except Exception as exc:
        return f"配置年龄分级失败: {exc}"

    return json.dumps(result, ensure_ascii=False)
