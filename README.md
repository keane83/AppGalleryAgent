# Harmony Auto App Agent

基于 **LangGraph** 的鸿蒙应用 **AppGallery Connect 上架自动化 Agent**：创建 APP ID、申请 Profile、工程签名、替换图标、模拟器截图、更新商店信息与素材、隐私协议、年龄分级、编译上传 `.app` 等。

**LLM 后端**：本地 **Ollama** 或 **DeepSeek API**（OpenAI 兼容，`LLM_PROVIDER=deepseek`）。  
**AGC 能力**：Connect **REST API** + **Playwright** 浏览器自动化。

## 功能概览

- LangGraph 状态图 Agent（工具调用 + 流式 Web UI）
- 推荐 **10 步上架流程**（含条件步骤「替换工程内图标」）
- 支持工程类型：**原生 ArkTS**、**WebView + 本地 HTML**、**Flutter OHOS**
- 截图：模拟器 `hdc` + `uitest screenCap` / `snapshot_display`，自动规划 3 张 1080×1920 商店图
- 商店文案：关键词 + 可选 LLM（`APP_GALLERY_LISTING_ANALYSIS_MODE`）

## 环境要求

- Python **3.10+**（项目可在 3.14 venv 下运行）
- **二选一 LLM**：
  - Ollama：已安装并 `ollama pull` 对应模型
  - DeepSeek：`.env` 中 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`
- 鸿蒙侧：**DevEco Studio**、HarmonyOS SDK、`hdc`、**模拟器或真机**（截图/安装 HAP 需要）
- 浏览器自动化：`playwright install chromium`
- 签名材料：默认使用仓库内 `AppAutoBAT/`（p12/cer，密码在 `.env`）

## 快速开始

```bash
cd HarmonyAutoAppAgent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# 编辑 .env：LLM_PROVIDER、API 密钥、签名密码、模拟器名称等

./run.sh
```

浏览器打开 **http://127.0.0.1:8000**。

等价命令：`uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

### AGC 浏览器登录（控制台步骤前置）

隐私协议、年龄分级、基本信息里的隐私标签等依赖华为账号登录态：

```bash
python -m app.tools.agc_browser_login
```

登录态默认：`downloads/agc_storage_state.json`；也可在 `.env` 开启 `AGC_BROWSER_USE_PERSISTENT_PROFILE` 使用持久化 Chromium Profile。

---

## Agent 工具（当前注册 12 个）

`create_app_gallery_publish_app`（控制台「新建发布」）**已停用**，未注册。  
业务工具内部会自动换取并缓存 `access_token`，一般**不必**手动调用 `get_app_gallery_access_token`。

### 推荐上架顺序

| 步骤 | 工具 | 说明 |
|------|------|------|
| 1 | `create_app_gallery_app_id` | API 创建 APP ID |
| 2 | `create_app_gallery_provision` | API 申请 Profile（.p7b） |
| — | ~~新建发布~~ | **已跳过**（AGC 改版后不需要） |
| 4 | `configure_harmony_app_signing` | 写 bundleName、签名、`build-profile.json5` |
| 4b | `replace_harmony_app_icon` | **用户提供了 icon 路径时必做**，且必须在步骤 5 之前 |
| 5 | `capture_harmony_screenshots_and_app_icon` | 3 张截图 + `app_icon_1024.png` |
| 6 | `update_app_gallery_app_file_info` | API 上传图标与截图 |
| 7 | `update_app_gallery_app_basic_info` | 分类、介绍、简介、负责人等 |
| 8 | `configure_app_gallery_privacy_agreement` | 浏览器：隐私协议 + 关联 |
| 9 | `configure_app_gallery_age_rating` | 浏览器：年龄分级问卷 |
| 10 | `update_app_gallery_build_update_app` | `hvigor assembleApp` / Flutter build + 上传 `.app` |

全流程示例话术（Agent 会按序调用工具）：

```text
APP名字：xxx
包名：com.example.app
工程路径：/path/to/ohos/project
icon路径：/path/to/icon.png
帮我把上面应用上架，按顺序执行整个全流程。
```

---

### 工具摘要

#### `get_app_gallery_access_token`

获取 Connect API `access_token`（带缓存）。仅调试或强制换票时使用。

#### `create_app_gallery_app_id`

| 参数 | 必填 | 说明 |
|------|------|------|
| `appName` | 是 | 应用名称 |
| `packageName` | 是 | 包名 / bundleName |

#### `create_app_gallery_provision`

| 参数 | 必填 | 说明 |
|------|------|------|
| `provisionName` | 是 | Profile 名称 |
| `appId` | 是 | 上一步返回的 appId |

`certId` 来自 `.env` 的 `APP_GALLERY_API_CERT_ID`。下载目录默认 `downloads/provisions/`。

#### `configure_harmony_app_signing`

| 参数 | 必填 | 说明 |
|------|------|------|
| `projectPath` | 是 | 鸿蒙工程根（含 `AppScope`）；Flutter 可传 `ohos` 目录 |
| `bundleName` | 是 | 包名 |
| `profilePath` | 是 | `.p7b` 本地路径 |
| `appName` | 是 | 桌面显示名 |

写入 `AppScope/app.json5`、`build-profile.json5` 及显示名字符串；p12/cer 路径与密码来自 `.env` / `AppAutoBAT`。

#### `replace_harmony_app_icon`

| 参数 | 必填 | 说明 |
|------|------|------|
| `projectPath` | 是 | 同签名步骤 |
| `iconPath` | 是 | 新图标 PNG/JPG 等 |

按 `app.json5` / `module.json5` 中的 `icon`、`startWindowIcon` 替换对应资源；备份在工程 `.harmony-auto-backups/`。

#### `capture_harmony_screenshots_and_app_icon`

| 参数 | 必填 | 说明 |
|------|------|------|
| `projectPath` | 是 | 推荐传 **ohos 根目录** |

**输出**：`{HARMONY_SCREENSHOT_DIR}/{应用名}/{时间戳}/`

- `01_*.png` ~ `03_*.png` — 1080×1920 介绍截图  
- `app_icon_1024.png` — 商店用 1024 图标  

**工程类型**：`native_arkts` | `webview_html` | `flutter_ohos`  

**流程要点**：

- 自动启动或复用 `.env` 中 `HARMONY_EMULATOR_NAME` 对应模拟器；DevEco **手动已开模拟器** 时会尝试 `hdc tconn`（默认 `127.0.0.1:5555`）。
- WebView：注入截图脚本 + `EntryAbility`/`Index` patch；**不会覆盖** JingPin 等工程里完整的 `HarmonyAutoBridge.ets`（仅合并 `scriptIdx`）。
- 截屏优先 `uitest screenCap`，失败再用 `snapshot_display`；检测黑屏/白屏/低对比度，默认无效图**重试后报错**（避免上传黑图）。

#### `update_app_gallery_app_file_info`

上传 **图标 + 3 张截图**（`PUT /publish/v3/app-file-info`）。常用：`appId`、`appIconPath`、`screenshotDir`（步骤 5 输出目录）。

#### `update_app_gallery_app_basic_info`

LLM/启发式分析工程 + 更新分类、介绍、一句话简介、负责人等；默认浏览器将隐私标签「是否收集个人信息」设为否（`.env` 可配）。

#### `configure_app_gallery_privacy_agreement` / `configure_app_gallery_age_rating`

浏览器自动化，需先 `agc_browser_login`。参数：`appName`，可选 `packageName` 消歧。

#### `update_app_gallery_build_update_app`

编译 Release `.app` 并上传 AGC。需已完成签名配置；上传后控制台解析约需数分钟。

---

## 配置说明

### LLM

| 变量 | 说明 |
|------|------|
| `LLM_PROVIDER` | `ollama`（默认）或 `deepseek` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 本地 Ollama |
| `DEEPSEEK_API_KEY` / `LLM_API_BASE_URL` / `LLM_API_MODEL` | DeepSeek 或兼容 API |
| `OLLAMA_TOOL_MODEL` / `LLM_TOOL_API_MODEL` | 工具内小模型（文案、WebView 规划等） |
| `OLLAMA_REASONING` | 建议 `false`；为 `true` 时 Agent 绑工具会尽量关闭 reasoning，避免只输出文字不调工具 |
| `APP_GALLERY_LISTING_ANALYSIS_MODE` | `fast`（默认）/ `llm` / `heuristic` |

### AppGallery API

| 变量 | 说明 |
|------|------|
| `APP_GALLERY_API_CLIENT_ID` / `SECRET` | API 客户端 |
| `APP_GALLERY_API_PROJECT_ID` | 创建 APP ID 的项目 |
| `APP_GALLERY_API_CERT_ID` | 申请 Profile 的证书 ID |
| `APP_GALLERY_DEFAULT_CONTACT_*` | 默认负责人信息 |
| `APP_GALLERY_DEFAULT_APP_TYPE` | 默认 `2`（单机） |

### 鸿蒙签名与编译

| 变量 | 说明 |
|------|------|
| `HARMONY_SIGNING_*` | p12 别名、store/key 密码等 |
| `DEVECO_STUDIO_PATH` | DevEco 安装根目录 |
| `HARMONY_HVIGORW_PATH` / `HARMONY_HDC_PATH` | 可选，默认可从 DevEco SDK 推导 |
| `FLUTTER_BIN` | Flutter OHOS 工程编译时使用 |

### 模拟器与截图（重要）

| 变量 | 说明 |
|------|------|
| `HARMONY_EMULATOR_NAME` | 与 `Emulator -list` 名称一致，如 `Mate 70 Pro` |
| `HARMONY_HDC_EMULATOR_ADDR` | 默认 `127.0.0.1:5555`；DevEco 手动开模拟器后 Agent 自动 `tconn` |
| `HARMONY_HDC_TARGET` | 可选，固定 `hdc -t` 的 connect-key |
| `HARMONY_EMULATOR_BOOT_TIMEOUT_SEC` | 等待 hdc 就绪（秒） |
| `HARMONY_SCREENSHOT_DIR` | 截图根目录 |
| `HARMONY_WEBVIEW_*` / `HARMONY_FLUTTER_*` | 各类型渲染等待、LLM 自动规划开关 |
| `HARMONY_SCREENSHOT_USE_UITEST` | 默认优先 `uitest screenCap` |
| `HARMONY_SCREENSHOT_FAIL_ON_INVALID` | 默认 `true`，黑/白图用尽重试后抛错 |
| `HARMONY_SCREENSHOT_BLACK_RATIO` | 黑像素占比阈值（默认 0.85） |

完整项见 **`.env.example`**。

---

## HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 非流式对话 |
| POST | `/api/chat/stream` | SSE 流式对话 |

---

## 项目结构（节选）

```
HarmonyAutoAppAgent/
├── app/
│   ├── agent.py                 # LangGraph Agent
│   ├── config.py                # SYSTEM_PROMPT、环境变量
│   ├── main.py                  # FastAPI
│   └── tools/
│       ├── __init__.py          # TOOLS 注册（12 个）
│       ├── harmony_hdc.py         # hdc / 模拟器 / 截屏
│       ├── harmony_capture_screenshots.py
│       ├── harmony_configure_signing.py
│       ├── harmony_replace_app_icon.py
│       ├── harmony_webview_patch.py
│       ├── harmony_screenshot_planner.py
│       └── app_gallery_*.py     # AGC API / 浏览器
├── AppAutoBAT/                  # 默认签名材料
├── downloads/
│   ├── provisions/
│   ├── screenshots/
│   └── browser/
├── static/                      # Web UI
├── run.sh
├── requirements.txt
└── .env.example
```

---

## 常见问题

### Agent / LLM

- **Ollama 连接失败**：确认 `ollama serve`，且 `OLLAMA_MODEL` 已 pull。  
- **只说话不调工具**：检查 `OLLAMA_REASONING`；DeepSeek 时确认 API Key 与模型名。  
- **启动报 SyntaxError**：更新到最新代码后重启 `./run.sh`（勿使用未保存的旧 `harmony_hdc.py`）。

### 模拟器与 hdc

- **步骤 5：`need connect-key` / 未检测到设备**  
  - DevEco 里能看见模拟器 ≠ Agent 的 `hdc` 已连接。  
  - 终端验证：  
    `.../toolchains/hdc list targets`  
    若为空：`hdc tconn 127.0.0.1:5555` 后再 list。  
  - `.env` 配置 `HARMONY_HDC_EMULATOR_ADDR`、`HARMONY_EMULATOR_NAME`。  
- **截图全黑但模拟器有画面**：多为截屏通道异常；**冷启动模拟器**后重试。Agent 默认会检测黑图并报错，避免上传商店。  
- **包名已改但启动失败「找不到 App」**：多为 **HAP 编译失败未安装**；看 hvigor 日志。JingPin 工程需保留完整 `HarmonyAutoBridge.ets`（Agent 已改为只合并 `scriptIdx`）。

### 签名 / 编译 / 上传

- **编译失败**：确认已 `configure_harmony_app_signing`，Profile 包名与 `bundleName` 一致。  
- **浏览器步骤失败**：重新 `python -m app.tools.agc_browser_login`；失败截图在 `downloads/browser/`。  
- **上传 .app 后无包**：AGC 解析需等待约 2 分钟。

### 全流程参数

- **工程路径**：WebView / 原生传 **ohos 根**（含 `AppScope`）；Flutter 建议传 **`ohos` 子目录**。  
- **icon 路径**：用户提供时 Agent 应在步骤 5 前调用 `replace_harmony_app_icon`；图标也会从步骤 5 输出目录导出 1024 版用于步骤 6。

---

## 许可与注意

- 勿将 `.env` 中的 API Key、签名密码提交到 Git。  
- `create_app_gallery_publish_app` 实现仍保留在仓库中，若 AGC 流程再次变化可按需恢复注册。
