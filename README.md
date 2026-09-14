# Harmony Auto App Agent

基于 **LangGraph** + **Ollama** 的本地 AI Agent，面向鸿蒙应用商店自动化（从 HTML + WebView 模版生成 App、配置上架信息）。当前为可运行脚手架，业务工具后续接入。

## 功能

- LangGraph 状态图 Agent（支持工具调用）
- 连接本地 Ollama 大模型
- 网页流式聊天界面
- 内置占位工具：获取当前时间、简单计算器（用于验证 tool-calling）

## 环境要求

- Python 3.10+
- [Ollama](https://ollama.com/) 已安装并在运行
- 至少一个已 pull 的聊天模型

你当前机器上的模型示例：

```bash
ollama list
```

## 快速开始

```bash
# 1. 进入项目目录
cd HarmonyAutoAppAgent

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 配置模型（可选）
cp .env.example .env
# 编辑 .env 中的 OLLAMA_MODEL，例如 qwen3-vl:8b

# 4. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

或一键启动：

```bash
./run.sh
```

浏览器打开：**http://127.0.0.1:8000**

## 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3-vl:8b` | 使用的模型名称 |
| `APP_GALLERY_API_CLIENT_ID` | （空） | 华为 Connect API 客户端 ID |
| `APP_GALLERY_API_CLIENT_SECRET` | （空） | 华为 Connect API 客户端密钥 |
| `APP_GALLERY_API_PROJECT_ID` | （空） | 创建 APP ID 所用项目 ID |
| `APP_GALLERY_API_CERT_ID` | （空） | 申请 Profile 所用证书 ID |

Agent 工具：

- `get_app_gallery_access_token`：换取 access_token
- `create_app_gallery_app_id(appName, packageName)`：创建 APP ID（`parentType=13`），返回 `appId`
- `create_app_gallery_provision(provisionName, appId)`：申请 Profile（默认 `provisionType=2`），下载 `.p7b` 并返回 `localPath`
- `create_app_gallery_publish_app(packageName)`：用 Playwright 打开 AGC 控制台「新建发布」，按 bundle id 选择包名并确认

创建 APP ID 接口：`POST https://connect-api.cloud.huawei.com/api/publish/v3/app?projectId=...`  
（文档：[创建APP ID](https://developer.huawei.com/consumer/cn/doc/app/agc-help-provision-api-create-appid-0000002526543939)）

申请 Profile 接口：`POST https://connect-api.cloud.huawei.com/api/publish/v3/provision`  
（文档：[申请Profile](https://developer.huawei.com/consumer/cn/doc/app/agc-help-provision-api-apply-provision-0000002271000689)）

### 控制台「新建发布」（Playwright）

官方无对应 API，因此通过浏览器自动化完成：

1. 先安装浏览器内核：`playwright install chromium`
2. 首次使用先手动登录并保存登录态：

```bash
python -m app.tools.agc_browser_login
```

3. 再调用工具 `create_app_gallery_publish_app(packageName)`，例如 `com.keane.ppa`  
   流程：打开 [myApp](https://developer.huawei.com/consumer/cn/service/josp/agc/index.html#/myApp) → 点击「新建发布」→ 选择包名 → 等待约 3 秒自动填充 → 点击「确认」

## API

- `GET /api/health` — 健康检查
- `POST /api/chat` — 非流式对话（完整 LangGraph + 工具）
- `POST /api/chat/stream` — 流式对话（SSE 真流式）

请求体示例：

```json
{
  "messages": [
    { "role": "user", "content": "现在几点了？" }
  ]
}
```

## 项目结构

```
HarmonyAutoAppAgent/
├── app/
│   ├── agent.py           # LangGraph Agent 编排
│   ├── config.py          # 配置与 SYSTEM_PROMPT
│   ├── logging_config.py
│   ├── main.py            # FastAPI 服务
│   ├── thinking.py        # 思考/正文分离
│   └── tools/             # Agent 工具（按模块扩展）
│       ├── __init__.py    # TOOLS 注册表
│       ├── basic.py
│       ├── app_gallery_auth.py
│       ├── app_gallery_create_appid.py
│       ├── app_gallery_create_provision.py
│       ├── app_gallery_create_publish_app.py
│       ├── agc_browser.py
│       └── agc_browser_login.py
├── downloads/provisions/  # Profile 本地下载目录
├── downloads/browser/     # Playwright 截图与登录态
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── requirements.txt
├── run.sh
└── .env.example
```

## 常见问题

**Ollama 连接失败**

确认 Ollama 在运行：

```bash
ollama serve
```

**模型不存在**

```bash
ollama pull qwen3-vl:8b
```

然后在 `.env` 里把 `OLLAMA_MODEL` 改成你 pull 的模型名。
