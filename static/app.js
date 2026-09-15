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
  "你好！我是 Harmony Auto App Agent，面向鸿蒙应用商店自动化。"
    + "可以说「帮我获取 AppGallery API token」来换取访问令牌；"
    + "也可先问我现在几点或帮你算数验证工具调用。"
);
