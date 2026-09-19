const chatEl = document.getElementById("chat");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status");
const statusTextEl = document.getElementById("status-text");
const MAX_INPUT_LINES = 10;

function resizeInput() {
  inputEl.style.height = "auto";
  const style = window.getComputedStyle(inputEl);
  const lineHeight = parseFloat(style.lineHeight) || 22;
  const paddingY =
    parseFloat(style.paddingTop) +
    parseFloat(style.paddingBottom) +
    parseFloat(style.borderTopWidth) +
    parseFloat(style.borderBottomWidth);
  const minHeight = parseFloat(style.minHeight) || 48;
  const maxHeight = lineHeight * MAX_INPUT_LINES + paddingY;
  const nextHeight = Math.min(Math.max(inputEl.scrollHeight, minHeight), maxHeight);

  inputEl.style.height = `${nextHeight}px`;
  inputEl.style.overflowY = inputEl.scrollHeight > maxHeight ? "auto" : "hidden";
}

/** @type {{role: "user" | "assistant", content: string}[]} */
const messages = [];

function appendUserMessage(content) {
  const node = document.createElement("div");
  node.className = "message user";
  node.textContent = content;
  chatEl.appendChild(node);
  chatEl.scrollTop = chatEl.scrollHeight;
  return node;
}

function appendAssistantMessage(content) {
  const node = document.createElement("div");
  node.className = "message assistant";

  const answerEl = document.createElement("div");
  answerEl.className = "answer-text";
  answerEl.textContent = content;
  node.appendChild(answerEl);

  chatEl.appendChild(node);
  chatEl.scrollTop = chatEl.scrollHeight;
  return node;
}

function createStreamingAssistantMessage() {
  const node = document.createElement("div");
  node.className = "message assistant empty";

  const statusEl = document.createElement("div");
  statusEl.className = "answer-status";
  statusEl.textContent = "正在思考...";
  node.appendChild(statusEl);

  const thinkingBlock = document.createElement("div");
  thinkingBlock.className = "thinking-block is-hidden";

  const thinkingLabel = document.createElement("div");
  thinkingLabel.className = "thinking-label";
  thinkingLabel.textContent = "思考过程";

  const thinkingText = document.createElement("div");
  thinkingText.className = "thinking-text";

  thinkingBlock.append(thinkingLabel, thinkingText);

  const answerEl = document.createElement("div");
  answerEl.className = "answer-text is-hidden";

  node.append(thinkingBlock, answerEl);
  chatEl.appendChild(node);
  chatEl.scrollTop = chatEl.scrollHeight;

  return {
    node,
    statusEl,
    thinkingBlock,
    thinkingText,
    answerEl,
    thinking: "",
    reply: "",
  };
}

function updateStreamingAssistant(state) {
  const hasThinking = Boolean(state.thinking.trim());
  const hasReply = Boolean(state.reply.trim());

  if (hasThinking || hasReply) {
    state.node.classList.remove("empty");
    state.statusEl.classList.add("is-hidden");
  }

  if (hasThinking) {
    state.thinkingBlock.classList.remove("is-hidden");
    state.thinkingText.textContent = state.thinking;
  }

  if (hasReply) {
    state.answerEl.classList.remove("is-hidden");
    state.answerEl.textContent = state.reply;
  } else if (hasThinking) {
    state.statusEl.classList.remove("is-hidden");
    state.statusEl.textContent = "正在组织回答...";
  }

  chatEl.scrollTop = chatEl.scrollHeight;
}

function setStatus(online, text) {
  statusEl.classList.toggle("online", online);
  statusEl.classList.toggle("offline", !online);
  statusTextEl.textContent = text;
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const ok = data.ollama?.reachable;
    const model = data.agent?.model || "unknown";
    setStatus(ok, ok ? `已连接 · ${model}` : `Ollama 不可用 · ${model}`);
  } catch {
    setStatus(false, "服务不可用");
  }
}

async function sendMessage(text) {
  messages.push({ role: "user", content: text });
  appendUserMessage(text);

  sendBtn.disabled = true;
  inputEl.disabled = true;

  const streamState = createStreamingAssistantMessage();

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });

    if (!res.ok || !res.body) {
      throw new Error(`请求失败 (${res.status})`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;

        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;

        const data = JSON.parse(payload);
        if (data.error) throw new Error(data.error);

        if (data.type === "thinking" && data.token) {
          streamState.thinking += data.token;
        } else if (data.type === "content" && data.token) {
          streamState.reply += data.token;
        } else if (data.type === "status" && data.text) {
          streamState.statusEl.classList.remove("is-hidden");
          streamState.statusEl.textContent = data.text;
        } else if (data.token) {
          streamState.reply += data.token;
        }

        updateStreamingAssistant(streamState);
      }
    }

    if (!streamState.reply.trim() && !streamState.thinking.trim()) {
      streamState.statusEl.classList.remove("is-hidden");
      streamState.statusEl.textContent = "（没有收到回复）";
      streamState.node.classList.add("empty");
    } else if (streamState.reply.trim()) {
      messages.push({ role: "assistant", content: streamState.reply.trim() });
    }
  } catch (err) {
    streamState.node.classList.add("empty");
    streamState.statusEl.classList.remove("is-hidden");
    streamState.statusEl.textContent = `出错了：${err.message}`;
    streamState.thinkingBlock.classList.add("is-hidden");
    streamState.answerEl.classList.add("is-hidden");
  } finally {
    sendBtn.disabled = false;
    inputEl.disabled = false;
    inputEl.focus();
  }
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  resizeInput();
  await sendMessage(text);
});

inputEl.addEventListener("input", resizeInput);

// 中文输入法：用 Enter 选词时不应发送。部分浏览器在 compositionend 后
// 仍会再冒泡一次 Enter，且此时 isComposing 已是 false，需要额外拦截。
let isComposing = false;
let ignoreEnterUntil = 0;

inputEl.addEventListener("compositionstart", () => {
  isComposing = true;
});

inputEl.addEventListener("compositionend", () => {
  isComposing = false;
  // 给确认候选字后的那次 Enter 一点缓冲
  ignoreEnterUntil = Date.now() + 300;
  resizeInput();
});

inputEl.addEventListener("keydown", (event) => {
  const entering =
    event.key === "Enter" || event.keyCode === 13 || event.code === "Enter";
  if (!entering || event.shiftKey) {
    return;
  }

  if (
    isComposing ||
    event.isComposing ||
    event.keyCode === 229 ||
    Date.now() < ignoreEnterUntil
  ) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }

  event.preventDefault();
  formEl.requestSubmit();
});

resizeInput();
checkHealth();
setInterval(checkHealth, 15000);

appendAssistantMessage(
  `你好！我是 Harmony Auto App Agent，帮你把鸿蒙应用上架到华为 AppGallery Connect。
直接说你想做什么，我会自动调用对应工具。

【我能帮你做的事】

· 创建 APP ID — 需要：应用名、包名
· 申请 Profile（.p7b 签名文件）— 需要：Profile 名称、appId
· 在控制台「新建发布」— 需要：包名（浏览器操作，需先登录 AGC）
· 配置工程签名 — 需要：工程路径、包名、Profile 路径、应用名
· 截取商店截图 + 导出 1024 图标 — 需要：工程路径（建议传 ohos 目录）
· 上传图标和截图到商店 — 需要：appId、截图/图标所在目录
· 分析工程并更新商店信息（分类、介绍、简介；隐私标签默认设「否」）— 需要：appId、工程路径
· 创建并关联隐私协议 — 需要：应用名（同名多个应用时可加包名）
· 填写年龄分级问卷 — 需要：应用名（同名多个应用时可加包名）
· 编译 .app 并上传到 AGC — 需要：工程路径、appId

【推荐上架顺序】

创建 APP ID → 申请 Profile → 新建发布 → 配置签名 → 截图/图标 → 上传素材 → 更新基本信息 → 隐私协议 → 年龄分级 → 编译上传

【想一次性全流程？】

你可以这样说：
「帮我把应用【词汇日积月累】完整上架，包名 com.example.app，工程路径 /path/to/project，按推荐顺序执行。」

我会按顺序逐步完成；某步缺信息时会向你确认。

【浏览器操作前置 · AGC 登录】

新建发布、隐私协议、年龄分级走网页控制台，需先保存华为账号登录态。
· 第一次使用：必须先做
· 登录态过期/工具报未登录时：重新做一遍

在终端执行（进入本项目目录后）：

1. source .venv/bin/activate          # 若使用了虚拟环境
2. playwright install chromium        # 首次需安装浏览器，已装可跳过
3. python -m app.tools.agc_browser_login

脚本会打开 Chromium → 用华为开发者账号登录 → 看到「APP与元服务 / 新建发布」后自动保存。
若已登录但未自动保存，回到终端按 Enter 即可。

登录态保存到：downloads/agc_storage_state.json
与 Agent 网页是否打开无关，登录一次即可复用。`
);
