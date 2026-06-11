const BRIDGE = "http://127.0.0.1:8766";
const latestCaptureByTab = new Map();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then(sendResponse)
    .catch((error) => {
      sendResponse({
        ok: false,
        error: error?.message || String(error || "Unknown bridge error")
      });
    });
  return true;
});

async function handleMessage(message, sender) {
  if (!message || typeof message.type !== "string") {
    return { ok: false, error: "Invalid message" };
  }

  if (message.type === "writingAssistantBridgeHealth") {
    return bridgeFetch("/health", { method: "GET" });
  }

  if (message.type === "writingAssistantBridgeCapture") {
    const payload = message.payload || {};
    const result = await bridgeFetch("/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (result.ok && result.accepted !== false) {
      rememberCapture(sender, payload);
    }
    return result;
  }

  if (message.type === "writingAssistantBridgeTabStatus") {
    const tabId = Number(message.tabId);
    return {
      ok: true,
      status: Number.isFinite(tabId) ? latestCaptureByTab.get(tabId) || null : null
    };
  }

  if (message.type === "writingAssistantBridgeCommand") {
    const sessionId = encodeURIComponent(String(message.sessionId || ""));
    return bridgeFetch(`/command?session_id=${sessionId}`, { method: "GET" });
  }

  if (message.type === "writingAssistantBridgeApplied") {
    return bridgeFetch("/applied", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload || {})
    });
  }

  return { ok: false, error: `Unknown message type: ${message.type}` };
}

function rememberCapture(sender, payload) {
  const tabId = sender?.tab?.id;
  if (!Number.isFinite(tabId)) return;
  const text = String(payload?.text || "");
  latestCaptureByTab.set(tabId, {
    ready: true,
    reason: "captured",
    sessionId: String(payload?.session_id || ""),
    targetKind: String(payload?.target_kind || ""),
    currentTargetKind: String(payload?.target_kind || ""),
    currentTextLength: text.length,
    textLength: text.length,
    currentLineBreaks: countLineBreaks(text),
    lineBreaks: countLineBreaks(text),
    currentPreview: text.slice(0, 160),
    preview: text.slice(0, 160),
    title: String(payload?.title || sender?.tab?.title || ""),
    url: String(payload?.url || sender?.tab?.url || ""),
    frameId: sender?.frameId ?? null,
    capturedAt: Date.now()
  });
}

function countLineBreaks(text) {
  return (String(text || "").match(/\n/g) || []).length;
}

async function bridgeFetch(path, options) {
  const response = await fetch(`${BRIDGE}${path}`, {
    cache: "no-store",
    ...options
  });
  const data = await readJson(response);
  if (!response.ok) {
    return {
      ok: false,
      status: response.status,
      error: `Local bridge HTTP ${response.status}`,
      data
    };
  }
  return {
    ok: true,
    status: response.status,
    data,
    accepted: data?.accepted,
    command: data?.command,
    stats: data?.stats,
    service: data?.service
  };
}

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_error) {
    return { raw: text };
  }
}
