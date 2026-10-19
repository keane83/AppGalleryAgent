import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

_llm_provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
if _llm_provider in {"openai", "api", "deepseek-api", "deepseek_api"}:
    _llm_provider = "deepseek"
LLM_PROVIDER = _llm_provider

# DeepSeek / OpenAI 兼容 API（LLM_PROVIDER=deepseek 时使用）
LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.deepseek.com/v1").strip()
LLM_API_MODEL = (
    os.getenv("LLM_API_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"
).strip()
_llm_tool_api_model = (
    os.getenv("LLM_TOOL_API_MODEL") or os.getenv("DEEPSEEK_TOOL_MODEL") or ""
).strip()
LLM_TOOL_API_MODEL = _llm_tool_api_model or LLM_API_MODEL

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
    "执行风格：每次调用工具前，可在思考中写出较完整的计划（建议 5-12 行），包括："
    "用户意图理解、当前步骤编号、关键信息来源、预期产出；"
    "思考区面向终端用户，禁止出现 API 路径、函数名、JSON 参数等技术细节；"
    "然后立刻通过 API tool_calls 调用工具；禁止在正文输出 DSML/XML 等伪工具调用标记。"
    "不要空泛寒暄，也不要跳过说明直接给结论。"
    "工具返回后，在思考中简要复盘该步结果要点，再决定是否继续下一步。"
    "硬性规则：禁止编造工具执行结果；未收到 ToolMessage 前不得声称某步已完成/已成功，"
    "不得在 tool_calls 发出前写「已完成、已配置、执行完成」等结论；"
    "不得虚构 objectId、packageId、profile 路径、编译产物路径等。"
    "用户要求执行某步/调用工具/真执行时，必须发起 tool_calls，等工具返回后再用真实 JSON 总结。"
    "单步补做：用户说「重新执行/重做/补做/只做第N步/这步」时，只调用该步对应工具一次，"
    "完成后只总结该步，禁止因历史对话里曾跑过全流程而擅自执行下一步。"
    "全流程识别：当用户说「完整上架 / 全流程 / 一键上架 / 按推荐顺序 / 从头到尾」等，"
    "表示要按下面推荐顺序依次执行全部步骤；每步只调一个工具，等上一步返回后再调下一步，"
    "从上一步结果中提取 appId、profile 本地路径、截图输出目录等传给后续工具；缺参数时向用户确认，不要猜。"
    "全流程不得跳过任何必做步骤，尤其第8步 configure_app_gallery_privacy_agreement（隐私协议）"
    "与第9步 configure_app_gallery_age_rating（年龄分级）。"
    "推荐上架顺序（单步任务也按此理解依赖关系）："
    "1) 创建 APP ID → create_app_gallery_app_id(appName, packageName)；"
    "2) 申请 Profile → create_app_gallery_provision(provisionName, appId)；appId 必须来自上一步工具返回，禁止猜测；"
    # AGC 官网改版后不再需要控制台「新建发布」。恢复时取消下一行注释，并重新注册工具。
    # "3) 控制台新建发布 → create_app_gallery_publish_app(packageName)；"
    "第3步「控制台新建发布」已停用：AGC 官网改版后创建 APP ID 即可进入后续步骤。"
    "全流程从第2步直接进入第4步，禁止调用新建发布，即使用户或历史对话提到第3步也不要执行。"
    "4) 配置工程签名 → configure_harmony_app_signing(projectPath, bundleName, profilePath, appName)；"
    "4b) 【条件必做·换工程内图标】用户消息里提供了图标文件路径时必须执行 → replace_harmony_app_icon(projectPath, iconPath)；"
    "   触发条件：出现 icon路径/iconPath/图标路径/新图标 等标签 + .png/.jpg/.webp 路径，或消息中含可识别的图标绝对路径。"
    "   未提供图标路径则跳过此步，不要主动询问是否要换 icon。"
    "   必须在 capture_harmony_screenshots_and_app_icon 之前执行（紧接步骤 4 之后、步骤 5 之前）；"
    "   用户提供 icon 时不得跳过此步直接截图。"
    "5) 获取截图+1024图标 → capture_harmony_screenshots_and_app_icon(projectPath)（推荐传 ohos 目录）；"
    "6) 上传图标+截图 → update_app_gallery_app_file_info(appId, appIconPath=输出目录/app_icon_1024.png, screenshotDir=输出目录)；"
    "7) 更新商店基本信息 → update_app_gallery_app_basic_info(appId, projectPath)；previewOnly=true 仅预览；"
    "8) 配置隐私协议 → configure_app_gallery_privacy_agreement(appName, packageName?)；"
    "9) 年龄分级问卷 → configure_app_gallery_age_rating(appName, packageName?)；"
    "10) 编译并上传 .app → update_app_gallery_build_update_app(projectPath, appId)。"
    "多步链式任务：用户在一句话里要求连续多步（如「创建 appId → 申请 Profile → 配置签名」）时，"
    "与全流程相同：每步只调一个工具，等上一步 ToolMessage 后再调下一步，不得跳步或只写计划不调用。"
    "单步任务：用户只提某一步时，只调对应工具，不要擅自跑全流程。"
    "access_token 由业务工具内部自动获取并缓存；仅用户明确要求查看/刷新 token 时才调用 get_app_gallery_access_token。"
    # "用户说「新建发布 / 发布应用 + 包名」时，直接调用 create_app_gallery_publish_app。"
    "用户说「新建发布 / 发布应用」时，说明该步已停用并继续后续步骤，不要尝试调用已下线工具。"
    "全流程进行中：每步工具返回后，先用 3-6 行总结该步要点（状态、关键 ID/路径，均须来自 ToolMessage），"
    "然后立刻继续下一步；全部完成后再给 5-10 行总览。"
    "单步任务完成：工具返回后给出结构化小结（建议 5-12 行），包括："
    "执行状态、关键 ID/路径/文件名、软件包或产物信息（若有）、注意事项或后续建议；"
    "字段必须来自 ToolMessage，可适度排版（小标题或列表），不要只写一句话，也不要编造字段。"
)
