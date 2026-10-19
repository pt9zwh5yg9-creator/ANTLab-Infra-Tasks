const log = document.querySelector("#chat-log");
const promptInput = document.querySelector("#prompt");
const sendButton = document.querySelector("#send");
const fileInput = document.querySelector("#file");
const fileLabel = document.querySelector("#file-label");
const statusBox = document.querySelector("#chat-status");

let fileText = null;
let busy = false;

function scrollToBottom() {
  log.scrollTop = log.scrollHeight;
}

function addMessage(role, text = "") {
  const bubble = document.createElement("div");
  bubble.className = `chat-message ${role}`;
  const content = document.createElement("div");
  content.className = "chat-content";
  content.textContent = text;
  bubble.appendChild(content);
  const meta = document.createElement("div");
  meta.className = "chat-meta";
  bubble.appendChild(meta);
  log.appendChild(bubble);
  scrollToBottom();
  return { bubble, content, meta };
}

async function checkStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const data = await response.json();
    statusBox.textContent = data.llama_ok
      ? `服务正常 · ${data.model || "本地模型"}`
      : "模型服务不可用，请检查 llama-server";
    statusBox.classList.toggle("bad", !data.llama_ok);
    statusBox.classList.toggle("ok", Boolean(data.llama_ok));
  } catch (error) {
    statusBox.textContent = `无法连接服务：${error.message}`;
    statusBox.classList.add("bad");
  }
}

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) {
    fileText = null;
    fileLabel.textContent = "未选择文件";
    return;
  }
  fileText = await file.text();
  fileLabel.textContent = `${file.name} · ${fileText.length.toLocaleString()} 字符`;
});

async function send() {
  const prompt = promptInput.value.trim();
  if (!prompt || busy) return;

  busy = true;
  sendButton.disabled = true;
  promptInput.value = "";
  addMessage("user", prompt);
  const assistant = addMessage("assistant", "");
  const requestId = `web-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  const started = performance.now();
  let firstTokenAt = null;
  let output = "";

  try {
    const response = await fetch("/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        document: fileText,
        max_tokens: 1024,
        request_id: requestId,
      }),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const event of events) {
        const line = event.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const dataText = line.slice(6).trim();
        if (dataText === "[DONE]") continue;

        let data;
        try {
          data = JSON.parse(dataText);
        } catch {
          continue;
        }
        if (data.request_id && !data.choices) continue;
        if (data.error) {
          output += `\n[错误] ${data.error}`;
          assistant.content.textContent = output;
          continue;
        }
        const delta = data.choices?.[0]?.delta?.content;
        if (delta) {
          if (firstTokenAt === null) firstTokenAt = performance.now();
          output += delta;
          assistant.content.textContent = output;
          scrollToBottom();
        }
        if (data.timings) {
          assistant.meta.dataset.predicted = data.timings.predicted_n;
          assistant.meta.dataset.input = (data.timings.cache_n || 0) + (data.timings.prompt_n || 0);
        }
      }
    }

    const elapsed = ((performance.now() - started) / 1000).toFixed(2);
    const ttft = firstTokenAt === null ? "—" : ((firstTokenAt - started) / 1000).toFixed(2);
    const inputTokens = assistant.meta.dataset.input || "—";
    const outputTokens = assistant.meta.dataset.predicted || "—";
    assistant.meta.textContent =
      `${requestId} · 首字 ${ttft}s · 总耗时 ${elapsed}s · 输入 ${inputTokens} / 输出 ${outputTokens} tokens`;
    if (!output) {
      assistant.content.textContent = "没有收到内容，请检查服务日志。";
    }
  } catch (error) {
    assistant.content.textContent = `请求失败：${error.message}`;
    assistant.bubble.classList.add("error");
  } finally {
    busy = false;
    sendButton.disabled = false;
    promptInput.focus();
    scrollToBottom();
  }
}

sendButton.addEventListener("click", send);
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

checkStatus();
setInterval(checkStatus, 10000);
