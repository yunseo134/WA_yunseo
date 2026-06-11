const statusBox = document.getElementById("status");
const statusText = document.getElementById("statusText");
const bridgeEl = document.getElementById("bridge");
const tabEl = document.getElementById("tab");
const contentEl = document.getElementById("content");
const capturesEl = document.getElementById("captures");
const pendingEl = document.getElementById("pending");
const previewEl = document.getElementById("preview");

async function loadStatus() {
  const tab = await loadTabStatus();
  await loadBridgeStatus();
  await loadContentStatus(tab);
}

async function loadTabStatus() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    tabEl.textContent = tab?.title || tab?.url || "-";
    return tab || null;
  } catch (_error) {
    tabEl.textContent = "탭 정보를 읽을 수 없음";
    return null;
  }
}

async function loadBridgeStatus() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "writingAssistantBridgeHealth" });
    if (!response || response.ok === false) {
      throw new Error(response?.error || "Bridge unavailable");
    }
    const stats = response.stats || response.data?.stats || {};
    statusBox.classList.add("ok");
    statusBox.classList.remove("warn");
    statusText.textContent = "로컬 앱과 연결됨";
    bridgeEl.textContent = response.service || response.data?.service || "ok";
    capturesEl.textContent = String(stats.captures ?? "-");
    pendingEl.textContent = String(stats.pending_sessions ?? "-");
  } catch (_error) {
    statusBox.classList.add("warn");
    statusBox.classList.remove("ok");
    statusText.textContent = "로컬 앱 브리지와 연결되지 않음";
    bridgeEl.textContent = "http://127.0.0.1:8766/health";
    capturesEl.textContent = "-";
    pendingEl.textContent = "-";
  }
}

async function loadContentStatus(tab) {
  if (!tab?.id) {
    renderEmptyStatus("탭 없음", "-");
    return;
  }

  try {
    const backgroundStatus = await chrome.runtime.sendMessage({
      type: "writingAssistantBridgeTabStatus",
      tabId: tab.id
    });
    if (backgroundStatus?.status) {
      renderCaptureStatus(backgroundStatus.status);
      return;
    }
  } catch (_error) {
    // Fall back to directly asking the top frame.
  }

  try {
    const response = await chrome.tabs.sendMessage(tab.id, { type: "writingAssistantBridgeStatus" });
    renderCaptureStatus(response);
  } catch (_error) {
    renderEmptyStatus(
      "콘텐츠 스크립트 응답 없음",
      "이 탭에서는 확장이 아직 로드되지 않았거나, chrome:// 같은 제한된 페이지일 수 있습니다."
    );
  }
}

function renderCaptureStatus(response) {
  const currentLength = Number(response?.currentTextLength || 0);
  const sentLength = Number(response?.textLength || 0);
  const currentLineBreaks = Number(response?.currentLineBreaks || 0);
  const sentLineBreaks = Number(response?.lineBreaks || 0);
  const currentBlankLines = Number(response?.currentBlankLines || 0);
  const sentBlankLines = Number(response?.blankLines || 0);
  const kind = response?.currentTargetKind || response?.targetKind || "-";
  const strategy = response?.currentExtractionStrategy || response?.extractionStrategy || "-";
  const frame = response?.frameId === null || response?.frameId === undefined ? "" : `, frame ${response.frameId}`;
  contentEl.textContent =
    `${kind}${frame}, ${strategy}, ` +
    `현재 ${currentLength}자/${currentLineBreaks}줄바꿈/${currentBlankLines}빈줄, ` +
    `전송 ${sentLength}자/${sentLineBreaks}줄바꿈/${sentBlankLines}빈줄`;
  previewEl.textContent = response?.currentPreview || response?.preview || "(캡처된 텍스트 없음)";
}

function renderEmptyStatus(status, preview) {
  contentEl.textContent = status;
  previewEl.textContent = preview;
}

loadStatus();
