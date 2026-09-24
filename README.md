# Harmony Auto App Agent

基于 **LangGraph** + **Ollama** 的本地 AI Agent，面向鸿蒙应用 **AppGallery Connect 上架自动化**（创建 APP ID、配置签名、截图/图标、更新商店信息、隐私协议、年龄分级、编译上传等）。

## 功能

- LangGraph 状态图 Agent（支持工具调用）
- 连接本地 Ollama 大模型
- 网页流式聊天界面
- AppGallery Connect **API** + **Playwright 浏览器** 混合自动化

## 环境要求

- Python 3.10+
- [Ollama](https://ollama.com/) 已安装并在运行
- 鸿蒙上架相关：DevEco Studio / HarmonyOS SDK、`hdc`、模拟器（截图工具需要）
- 浏览器自动化：`playwright install chromium`

## 快速开始

```bash
cd HarmonyAutoAppAgent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# 编辑 .env：OLLAMA_MODEL、APP_GALLERY_API_*、签名材料路径等

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

或：`./run.sh` → 浏览器打开 **http://127.0.0.1:8000**

### AGC 浏览器登录（控制台自动化前置）

以下 Tool 依赖已保存的华为账号登录态：

```bash
python -m app.tools.agc_browser_login
```

登录态默认保存到 `downloads/browser/agc_storage_state.json`（或通过 `.env` 配置持久化 Profile）。

---

## Agent 工具一览

共 **11 个** Tool。API 类工具内部会自动换取 `access_token`，一般**不必**每次手动调用 `get_app_gallery_access_token`。

### 推荐上架顺序

1. `create_app_gallery_app_id`
2. `create_app_gallery_provision`
3. `create_app_gallery_publish_app`
4. `configure_harmony_app_signing`
5. `capture_harmony_screenshots_and_app_icon`
6. `update_app_gallery_app_file_info`
7. `update_app_gallery_app_basic_info`
8. `configure_app_gallery_privacy_agreement`
9. `configure_app_gallery_age_rating`
10. `update_app_gallery_build_update_app`

---

### 1. `get_app_gallery_access_token`

**功能**：获取华为 AppGallery Connect API 的 `access_token`（带缓存）。

**参数**：无

**说明**：仅当用户明确要求查看/刷新 token，或鉴权失败需强制换票时调用。

---

### 2. `create_app_gallery_app_id`

**功能**：通过 API 创建 HarmonyOS 应用的 **APP ID**。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appName` | string | 是 | 应用名称 |
| `packageName` | string | 是 | 包名 / bundle id |

**返回**：`appId`、包名、项目 ID 等。

---

### 3. `create_app_gallery_provision`

**功能**：通过 API 申请 **Profile**（.p7b）并下载到本地。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provisionName` | string | 是 | Profile 名称 |
| `appId` | string | 是 | 应用 ID |

**返回**：`provisionId`、`localPath` 等。`certId` 从 `.env` 的 `APP_GALLERY_API_CERT_ID` 读取。

---

### 4. `create_app_gallery_publish_app`

**功能**：浏览器在 AGC 控制台 **「新建发布」**，按包名创建发布应用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `packageName` | string | 是 | 包名 / bundle id |

**说明**：无对应 API；需先 `agc_browser_login`。

---

### 5. `configure_harmony_app_signing`

**功能**：配置鸿蒙工程 **发布签名**（`build-profile.json5`、bundleName、应用显示名等）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectPath` | string | 是 | 鸿蒙工程根目录 |
| `bundleName` | string | 是 | 包名 |
| `profilePath` | string | 是 | .p7b 路径 |
| `appName` | string | 是 | 桌面显示名称 |

**说明**：p12/cer 及密码从 `.env` 读取并写入工程。

---

### 6. `capture_harmony_screenshots_and_app_icon`

**功能**：模拟器截取 **3 张 1080×1920 商店截图** + 导出 **1024×1024 应用图标**。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectPath` | string | 是 | 工程路径（原生/WebView 传 ohos 根；Flutter 推荐传 `ohos` 目录） |

**输出目录**：`{HARMONY_SCREENSHOT_DIR}/{应用名}/{时间戳}/`

- `01_*.png` ~ `03_*.png` — 介绍截图
- `app_icon_1024.png` — 应用图标

支持工程类型：`native_arkts`、`webview_html`、`flutter_ohos`。

---

### 7. `update_app_gallery_app_file_info`

**功能**：API 上传 **图标 + 3 张截图** 到 AGC（`PUT /publish/v3/app-file-info`）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appId` | string | 是 | 应用 ID |
| `appIconPath` | string | 否 | 本地图标 PNG/JPG |
| `appIconObjectId` | string | 否 | 已上传图标的 objectId |
| `screenshotDir` | string | 否 | 含 3 张 PNG 的目录 |
| `screenshotObjectIds` | string | 否 | 已上传截图 objectId（逗号或 JSON） |
| `lang` | string | 否 | 默认 `zh-CN` |
| `deviceType` | int | 否 | 默认 `4`（手机） |
| `showType` | int | 否 | 默认 `0`（竖屏） |
| `releaseType` | int | 否 | 默认 `1`（全网） |
| `releasePhase` | int | 否 | 默认 `0` |

---

### 8. `update_app_gallery_app_basic_info`

**功能**：**LLM 分析工程** + API 更新应用基本信息；默认还会在浏览器将 **隐私标签「是否涉及个人信息收集」设为否**。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appId` | string | 是 | 应用 ID |
| `projectPath` | string | 是 | 鸿蒙工程路径 |
| `lang` | string | 否 | 默认 `zh-CN` |
| `contactName` | string | 否 | 负责人（默认 `.env`） |
| `contactPhone` | string | 否 | 联系电话 |
| `contactEmail` | string | 否 | 联系邮箱 |
| `collectPersonInfo` | int | 否 | 默认 `0`（否）；`-1` 用 `.env` 默认 |
| `aicgType` | int | 否 | AI 声明，默认 `0`（否） |
| `appType` | int | 否 | APP 类型，默认单机 |
| `releaseType` | int | 否 | 默认 `1`（全网） |
| `releasePhase` | int | 否 | 默认 `0` |
| `previewOnly` | bool | 否 | `true` 仅预览分析，不调 API/浏览器 |

**API**：

- `PUT /publish/v3/app-info` — 分类、APP 类型、AI 声明、负责人等
- `PUT /publish/v3/app-language-info` — 应用介绍、一句话简介

**自动生成**：二级分类、标签、应用介绍（≤500 字）、一句话简介（≤15 字）。

**边界**：不涉及隐私协议（用 `configure_app_gallery_privacy_agreement`）。

---

### 9. `configure_app_gallery_privacy_agreement`

**功能**：浏览器创建 **隐私协议** 并在「准备提交」页 **关联隐私政策**。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appName` | string | 是 | 应用名（列表搜索、协议名、下拉选项） |
| `packageName` | string | 否 | 同名应用消歧 |
| `contactPhone` | string | 否 | 商业联系电话（默认 `.env`） |

**流程**：myApp → 协议服务 → 新建协议 → 填草稿 → 生成协议 → 版本信息 → 准备提交 → 隐私政策 → 保存。

**说明**：需 `agc_browser_login`。

---

### 10. `configure_app_gallery_age_rating`

**功能**：浏览器完成 **年龄分级 / 内容分级问卷**（各分类单选题均选「否」）并保存。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appName` | string | 是 | 应用名 |
| `packageName` | string | 否 | 同名应用消歧 |

**流程**：准备提交 → 年龄分级「设置」→ 填写问卷 → 验证 → 提交 →「是否仅面向儿童」选否 → 保存。

**说明**：需 `agc_browser_login`。

---

### 11. `update_app_gallery_build_update_app`

**功能**：**编译**鸿蒙工程为 `.app` 并 **上传到 AGC**。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectPath` | string | 是 | 工程路径（ohos 根或 Flutter 根） |
| `appId` | string | 是 | 应用 ID |
| `release` | bool | 否 | 默认 `true`（Release 编译） |
| `releaseType` | int | 否 | 默认 `1`（全网） |

**固定值**：登记软件包时 `releasePhase` 始终为 `0`（不可通过参数修改）。

**流程**：编译 `.app` → 获取 OBS 上传地址 → 上传 → 登记软件包信息。

**说明**：需先 `configure_harmony_app_signing`；上传后 AGC 约 2 分钟解析包。

---

## 工具职责边界

| 能力 | Tool |
|------|------|
| 隐私协议 + 隐私政策关联 | `configure_app_gallery_privacy_agreement` |
| 隐私标签「是否收集个人信息=否」 | `update_app_gallery_app_basic_info`（内置浏览器） |
| 年龄分级问卷 | `configure_app_gallery_age_rating` |
| 图标 / 截图上传 | `update_app_gallery_app_file_info` |
| 分类 / 介绍 / 简介 | `update_app_gallery_app_basic_info` |

---

## 配置

| 变量 | 说明 |
|------|------|
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Ollama 服务与模型（Agent 对话） |
| `OLLAMA_TOOL_MODEL` | 工具内 LLM（可选，默认同 `OLLAMA_MODEL`；建议更小模型加速文案分析） |
| `APP_GALLERY_LISTING_ANALYSIS_MODE` | 商店文案分析：`fast`（默认）/ `llm` / `heuristic` |
| `APP_GALLERY_API_CLIENT_ID` / `SECRET` | Connect API 鉴权 |
| `APP_GALLERY_API_PROJECT_ID` | 创建 APP ID 的项目 ID |
| `APP_GALLERY_API_CERT_ID` | 申请 Profile 的证书 ID |
| `APP_GALLERY_DEFAULT_CONTACT_*` | 默认负责人/电话/邮箱 |
| `APP_GALLERY_DEFAULT_COLLECT_PERSON_INFO` | 隐私标签默认（0=否） |
| `HARMONY_SCREENSHOT_DIR` | 截图输出目录 |
| `DEVECO_STUDIO_PATH` / `HARMONY_HDC_PATH` | 编译与 hdc 路径 |
| `AGC_BROWSER_*` | 浏览器自动化（登录态、headless 等） |

完整项见 `.env.example`。

---

## API

- `GET /api/health` — 健康检查
- `POST /api/chat` — 非流式对话
- `POST /api/chat/stream` — 流式对话（SSE）

请求体示例：

```json
{
  "messages": [
    { "role": "user", "content": "为应用「词汇日积月累」配置年龄分级，appId 已有" }
  ]
}
```

---

## 项目结构

```
HarmonyAutoAppAgent/
├── app/
│   ├── agent.py
│   ├── config.py              # SYSTEM_PROMPT、环境变量
│   ├── main.py                # FastAPI
│   └── tools/
│       ├── __init__.py        # TOOLS 注册表
│       ├── app_gallery_auth.py
│       ├── app_gallery_create_appid.py
│       ├── app_gallery_create_provision.py
│       ├── app_gallery_create_publish_app.py
│       ├── app_gallery_configure_privacy_agreement.py
│       ├── app_gallery_configure_age_rating.py
│       ├── app_gallery_update_app_info.py
│       ├── app_gallery_update_app_file_info.py
│       ├── app_gallery_build_update_app.py
│       ├── app_gallery_privacy_tag_browser.py   # 基本信息 tool 内部调用
│       ├── app_gallery_age_rating_browser.py    # 年龄分级 tool 内部调用
│       ├── agc_browser.py                       # 浏览器公共能力
│       ├── agc_browser_login.py
│       ├── harmony_configure_signing.py
│       └── harmony_capture_screenshots.py
├── downloads/provisions/
├── downloads/browser/
├── static/
├── requirements.txt
└── .env.example
```

---

## 常见问题

**Ollama 连接失败** — 确认 `ollama serve` 已运行，且 `.env` 中 `OLLAMA_MODEL` 已通过 `ollama pull` 下载。

**浏览器自动化失败** — 重新运行 `python -m app.tools.agc_browser_login`；失败截图在 `downloads/browser/`。

**编译 / 上传失败** — 确认已 `configure_harmony_app_signing`，且 Profile 与包名一致。
