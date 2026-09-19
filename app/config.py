import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-4.7-flash:q4_K_M")
OLLAMA_REASONING = os.getenv("OLLAMA_REASONING", "false").lower() in {"1", "true", "yes", "on"}
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))

APP_GALLERY_API_CLIENT_ID = os.getenv("APP_GALLERY_API_CLIENT_ID", "")
APP_GALLERY_API_CLIENT_SECRET = os.getenv("APP_GALLERY_API_CLIENT_SECRET", "")
APP_GALLERY_API_PROJECT_ID = os.getenv("APP_GALLERY_API_PROJECT_ID", "")
APP_GALLERY_API_CERT_ID = os.getenv("APP_GALLERY_API_CERT_ID", "")
APP_GALLERY_API_TOKEN_URL = os.getenv(
    "APP_GALLERY_API_TOKEN_URL",
    "https://connect-api.cloud.huawei.com/api/oauth2/v1/token",
)
APP_GALLERY_API_BASE_URL = os.getenv(
    "APP_GALLERY_API_BASE_URL",
    "https://connect-api.cloud.huawei.com/api",
)
APP_GALLERY_DEFAULT_CONTACT_NAME = os.getenv("APP_GALLERY_DEFAULT_CONTACT_NAME", "刘健")
APP_GALLERY_DEFAULT_CONTACT_PHONE = os.getenv("APP_GALLERY_DEFAULT_CONTACT_PHONE", "15509840527")
APP_GALLERY_DEFAULT_CONTACT_EMAIL = os.getenv(
    "APP_GALLERY_DEFAULT_CONTACT_EMAIL",
    "liujian830527@126.com",
)
APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO = int(
    os.getenv("APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO", "0")
)
APP_GALLERY_DEFAULT_AICG_TYPE = int(os.getenv("APP_GALLERY_DEFAULT_AICG_TYPE", "0"))
# registeredDclType: 0=国内服务器 1=海外服务器 2=单机 APP
APP_GALLERY_DEFAULT_APP_TYPE = int(os.getenv("APP_GALLERY_DEFAULT_APP_TYPE", "2"))
PROVISION_DOWNLOAD_DIR = Path(
    os.getenv(
        "PROVISION_DOWNLOAD_DIR",
        str(Path(__file__).resolve().parent.parent / "downloads" / "provisions"),
    )
)
BROWSER_ARTIFACT_DIR = Path(
    os.getenv(
        "BROWSER_ARTIFACT_DIR",
        str(Path(__file__).resolve().parent.parent / "downloads" / "browser"),
    )
)
AGC_STORAGE_STATE_PATH = Path(
    os.getenv(
        "AGC_STORAGE_STATE_PATH",
        str(Path(__file__).resolve().parent.parent / "downloads" / "agc_storage_state.json"),
    )
)
AGC_MYAPP_URL = os.getenv(
    "AGC_MYAPP_URL",
    "https://developer.huawei.com/consumer/cn/service/josp/agc/index.html#/myApp",
)
AGC_BROWSER_HEADLESS = os.getenv("AGC_BROWSER_HEADLESS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AGC_BROWSER_SLOW_MO_MS = int(os.getenv("AGC_BROWSER_SLOW_MO_MS", "50"))
# 可选：持久化 Chromium Profile（比 storage_state 更稳，推荐本地开发开启）
_agc_profile = os.getenv("AGC_BROWSER_USER_DATA_DIR", "").strip()
AGC_BROWSER_USER_DATA_DIR = (
    Path(_agc_profile).expanduser()
    if _agc_profile
    else Path(__file__).resolve().parent.parent / "downloads" / "agc_chrome_profile"
    if os.getenv("AGC_BROWSER_USE_PERSISTENT_PROFILE", "false").lower()
    in {"1", "true", "yes", "on"}
    else None
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARMONY_SIGNING_MATERIAL_DIR = Path(
    os.getenv(
        "HARMONY_SIGNING_MATERIAL_DIR",
        str(_PROJECT_ROOT / "AppAutoBAT"),
    )
)
HARMONY_SIGNING_STORE_FILE = os.getenv("HARMONY_SIGNING_STORE_FILE", "appbat123A.p12")
HARMONY_SIGNING_CERT_FILE = os.getenv("HARMONY_SIGNING_CERT_FILE", "appbat123A.cer")
HARMONY_SIGNING_STORE_PASSWORD = os.getenv("HARMONY_SIGNING_STORE_PASSWORD", "")
HARMONY_SIGNING_KEY_ALIAS = os.getenv("HARMONY_SIGNING_KEY_ALIAS", "appbat123A")
HARMONY_SIGNING_KEY_PASSWORD = os.getenv("HARMONY_SIGNING_KEY_PASSWORD", "")
HARMONY_SIGNING_SIGN_ALG = os.getenv("HARMONY_SIGNING_SIGN_ALG", "SHA256withECDSA")
HARMONY_SCREENSHOT_DIR = Path(
    os.getenv(
        "HARMONY_SCREENSHOT_DIR",
        str(_PROJECT_ROOT / "downloads" / "screenshots"),
    )
)
DEVECO_STUDIO_PATH = os.getenv(
    "DEVECO_STUDIO_PATH",
    "/Applications/DevEco-Studio.app/Contents",
)
HARMONY_HDC_PATH = os.getenv("HARMONY_HDC_PATH", "")
HARMONY_EMULATOR_NAME = os.getenv("HARMONY_EMULATOR_NAME", "Mate 70 Pro")

SYSTEM_PROMPT = (
    "你是 Harmony Auto App Agent，面向鸿蒙应用商店自动化。"
    "执行风格：少思考、少解释，意图明确时立刻调用工具，不要先长篇分析。"
    "工具选择："
    "1) 创建 APP ID → create_app_gallery_app_id(appName, packageName)；"
    "2) 申请 Profile → create_app_gallery_provision(provisionName, appId)；"
    "3) 控制台新建发布 → create_app_gallery_publish_app(packageName)；"
    "3b) 控制台配置隐私协议 → configure_app_gallery_privacy_agreement(appName)；同名多应用时可传 packageName；"
    "4) 配置工程签名 → configure_harmony_app_signing(projectPath, bundleName, profilePath, appName)；"
    "5) 获取截图+1024图标 → capture_harmony_screenshots_and_app_icon(projectPath)（输出 3 张截图 + app_icon_1024.png；推荐传 ohos 目录）；"
    "6) 上传图标+截图到 AGC → update_app_gallery_app_file_info(appId, appIconPath=输出目录/app_icon_1024.png, screenshotDir=输出目录)；"
    "7) 分析工程并更新 AGC 基本信息 → update_app_gallery_app_basic_info(appId, projectPath)；返回 summary 含 harmonyChildType/kindMainTag/文案；previewOnly=true 仅预览；"
    "8) 编译并上传 .app 软件包 → update_app_gallery_build_update_app(projectPath, appId)；"
    "9) 仅当用户明确要求查看/刷新 token 时，才调用 get_app_gallery_access_token。"
    "注意：access_token 由业务工具内部自动获取并缓存，不要每个任务都先换 token。"
    "用户说「新建发布 / 发布应用 + 包名」时，直接调用 create_app_gallery_publish_app。"
    "工具返回后只给一句简洁结果（关键 ID / 路径），不要复述过程。"
    "时间或计算必须调用 get_current_time / calculator，不要猜测。"
)
