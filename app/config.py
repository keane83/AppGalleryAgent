import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-4.7-flash:q4_K_M")
_ollama_tool_model = os.getenv("OLLAMA_TOOL_MODEL", "").strip()
OLLAMA_TOOL_MODEL = _ollama_tool_model or OLLAMA_MODEL
OLLAMA_REASONING = os.getenv("OLLAMA_REASONING", "false").lower() in {"1", "true", "yes", "on"}
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
# AppGallery 商店文案分析：fast=关键词分类+小 prompt 只生成文案（推荐）；llm=单次大 prompt；heuristic=纯规则无 LLM
APP_GALLERY_LISTING_ANALYSIS_MODE = os.getenv(
    "APP_GALLERY_LISTING_ANALYSIS_MODE", "fast"
).strip().lower()

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
    "全流程识别：当用户说「完整上架 / 全流程 / 一键上架 / 按推荐顺序 / 从头到尾」等，"
    "表示要按下面推荐顺序依次执行全部步骤；每步只调一个工具，等上一步返回后再调下一步，"
    "从上一步结果中提取 appId、profile 本地路径、截图输出目录等传给后续工具；缺参数时向用户确认，不要猜。"
    "推荐上架顺序（单步任务也按此理解依赖关系）："
    "1) 创建 APP ID → create_app_gallery_app_id(appName, packageName)；"
    "2) 申请 Profile → create_app_gallery_provision(provisionName, appId)；appId 必须来自上一步工具返回，禁止猜测；"
    "3) 控制台新建发布 → create_app_gallery_publish_app(packageName)；"
    "4) 配置工程签名 → configure_harmony_app_signing(projectPath, bundleName, profilePath, appName)；"
    "5) 获取截图+1024图标 → capture_harmony_screenshots_and_app_icon(projectPath)（推荐传 ohos 目录）；"
    "6) 上传图标+截图 → update_app_gallery_app_file_info(appId, appIconPath=输出目录/app_icon_1024.png, screenshotDir=输出目录)；"
    "7) 更新商店基本信息 → update_app_gallery_app_basic_info(appId, projectPath)；previewOnly=true 仅预览；"
    "8) 配置隐私协议 → configure_app_gallery_privacy_agreement(appName, packageName?)；"
    "9) 年龄分级问卷 → configure_app_gallery_age_rating(appName, packageName?)；"
    "10) 编译并上传 .app → update_app_gallery_build_update_app(projectPath, appId)。"
    "单步任务：用户只提某一步时，只调对应工具，不要擅自跑全流程。"
    "access_token 由业务工具内部自动获取并缓存；仅用户明确要求查看/刷新 token 时才调用 get_app_gallery_access_token。"
    "用户说「新建发布 / 发布应用 + 包名」时，直接调用 create_app_gallery_publish_app。"
    "全流程进行中：每步工具返回后只给一句简洁结果（关键 ID / 路径），然后立刻继续下一步，全部完成后再总结。"
    "单步任务完成：工具返回后只给一句简洁结果，不要复述过程。"
)
