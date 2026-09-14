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

SYSTEM_PROMPT = (
    "你是 Harmony Auto App Agent，面向鸿蒙应用商店自动化。"
    "执行风格：少思考、少解释，意图明确时立刻调用工具，不要先长篇分析。"
    "工具选择："
    "1) 创建 APP ID → create_app_gallery_app_id(appName, packageName)；"
    "2) 申请 Profile → create_app_gallery_provision(provisionName, appId)；"
    "3) 控制台新建发布 → create_app_gallery_publish_app(packageName)；"
    "4) 仅当用户明确要求查看/刷新 token 时，才调用 get_app_gallery_access_token。"
    "注意：access_token 由业务工具内部自动获取并缓存，不要每个任务都先换 token。"
    "用户说「新建发布 / 发布应用 + 包名」时，直接调用 create_app_gallery_publish_app。"
    "工具返回后只给一句简洁结果（关键 ID / 路径），不要复述过程。"
    "时间或计算必须调用 get_current_time / calculator，不要猜测。"
)
