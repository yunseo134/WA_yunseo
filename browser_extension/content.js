(() => {
  const SESSION_ID = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const POLL_MS = 500;
  const CAPTURE_DEBOUNCE_MS = 600;
  const MAX_CAPTURE_TEXT_LENGTH = 50000;
  const EDITABLE_SELECTOR = [
    "textarea",
    "input",
    "[contenteditable='']",
    "[contenteditable='true']",
    "[contenteditable='plaintext-only']"
  ].join(",");
  const EDITOR_ROOT_SELECTOR = [
    "[role='textbox']",
    ".ProseMirror",
    ".ql-editor",
    ".toastui-editor-contents",
    ".cm-content",
    ".se-main-container",
    ".se-component-content",
    ".se-module-text",
    ".se-text-paragraph"
  ].join(",");
  const BROAD_EDITOR_ROOT_SELECTOR = [
    ".se-main-container",
    ".ProseMirror",
    ".ql-editor",
    ".toastui-editor-contents",
    ".cm-content",
    "[role='textbox']"
  ].join(",");
  const EDITOR_QUERY_SELECTOR = `${EDITABLE_SELECTOR},${EDITOR_ROOT_SELECTOR}`;
  const TEXT_INPUT_TYPES = new Set(["", "text", "search", "url", "tel", "email", "number"]);
  const SMALL_BUFFER_TEXT_LENGTH = 12;

  let captureTimer = null;
  let settleTimers = [];
  let lastSignature = "";
  let lastCaptureSentAt = 0;
  let lastEditable = null;
  let lastCaptureState = { ready: false, reason: "loaded", sessionId: SESSION_ID };
  let observedEditable = null;
  let editableObserver = null;

  console.info("[Writing Assistant Bridge] content script loaded", {
    sessionId: SESSION_ID,
    url: location.href
  });

  function bridgeRequest(type, payload = {}) {
    return new Promise((resolve, reject) => {
      if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
        reject(new Error("Extension runtime is unavailable."));
        return;
      }
      chrome.runtime.sendMessage({ type, ...payload }, (response) => {
        const runtimeError = chrome.runtime.lastError;
        if (runtimeError) {
          reject(new Error(runtimeError.message));
          return;
        }
        if (!response || response.ok === false) {
          reject(new Error(response?.error || "Local bridge request failed."));
          return;
        }
        resolve(response);
      });
    });
  }

  function isEditableElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    const tag = node.tagName.toLowerCase();
    return tag === "textarea" || isSupportedTextInput(node) || node.isContentEditable || isEditorRootElement(node);
  }

  function isEditorRootElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE || !node.matches) return false;
    if (!node.matches(EDITOR_ROOT_SELECTOR)) return false;
    if (isPlainTextControl(node) || isEditorChromeNode(node)) return false;
    return Boolean(String(node.textContent || "").trim() || node.querySelector(EDITABLE_SELECTOR));
  }

  function isSupportedTextInput(element) {
    if (!element || element.tagName?.toLowerCase() !== "input") return false;
    const type = String(element.getAttribute("type") || element.type || "text").toLowerCase();
    return TEXT_INPUT_TYPES.has(type) && !isSensitiveEditable(element);
  }

  function isPlainTextControl(element) {
    return Boolean(element && element.matches && (element.matches("textarea") || isSupportedTextInput(element)));
  }

  function isSensitiveEditable(element) {
    if (!element || !element.matches) return true;
    const type = String(element.getAttribute("type") || element.type || "").toLowerCase();
    if (["password", "hidden", "file", "checkbox", "radio", "submit", "button", "reset", "image", "color", "range", "date", "datetime-local", "month", "time", "week"].includes(type)) {
      return true;
    }
    const autocomplete = String(element.getAttribute("autocomplete") || "").toLowerCase();
    if (/password|cc-|credit-card|one-time-code|otp/.test(autocomplete)) return true;
    const label = `${element.id || ""} ${element.name || ""} ${element.getAttribute("aria-label") || ""} ${element.getAttribute("placeholder") || ""}`.toLowerCase();
    return /password|passwd|secret|token|otp|인증|비밀번호|암호|카드|보안/.test(label);
  }

  function closestEditableFromNode(node) {
    if (!node) return null;
    const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!element || !element.closest) return null;
    if (isEditorChromeNode(element)) return null;
    return element.closest(EDITOR_QUERY_SELECTOR);
  }

  function activeEditable() {
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0) {
      const editableFromSelection = closestEditableFromNode(selection.anchorNode);
      if (isCaptureReadyEditable(editableFromSelection)) {
        const preferred = preferredEditableForElement(editableFromSelection);
        lastEditable = preferred;
        observeEditable(preferred);
        return preferred;
      }
    }

    const active = deepActiveElement();
    if (isCaptureReadyEditable(active)) {
      const preferred = preferredEditableForActiveControl(active);
      lastEditable = preferred;
      observeEditable(preferred);
      return preferred;
    }

    const fallback = bestEditableCandidate();
    if (fallback) {
      const preferred = preferredEditableForElement(fallback);
      lastEditable = preferred;
      observeEditable(preferred);
      return preferred;
    }
    return null;
  }

  function deepActiveElement(root = document) {
    let active = root.activeElement || document.activeElement;
    const seen = new Set();
    while (active && active.shadowRoot && active.shadowRoot.activeElement && !seen.has(active)) {
      seen.add(active);
      active = active.shadowRoot.activeElement;
    }
    return active;
  }

  function fallbackEditable() {
    if (isUsableEditable(lastEditable)) return lastEditable;
    const fallback = bestEditableCandidate();
    if (fallback) {
      const preferred = preferredEditableForElement(fallback);
      lastEditable = preferred;
      observeEditable(preferred);
      return preferred;
    }
    return null;
  }

  function isUsableEditable(element) {
    return Boolean(element && document.contains(element) && isEditableElement(element));
  }

  function bestEditableCandidate() {
    const candidates = queryEditableCandidates(document)
      .filter((element) => isCaptureReadyEditable(element) && editableCandidateText(element).trim());
    if (!candidates.length) return null;
    if (candidates.length === 1) return candidates[0];
    return candidates.sort((left, right) => {
      const leftScore = editableCandidateText(left).length + visibleArea(left) / 100 + editorLikeScore(left);
      const rightScore = editableCandidateText(right).length + visibleArea(right) / 100 + editorLikeScore(right);
      return rightScore - leftScore;
    })[0];
  }

  function queryEditableCandidates(root) {
    const result = [];
    const pushAll = (scope) => {
      if (!scope || !scope.querySelectorAll) return;
      if (scope.nodeType === Node.ELEMENT_NODE && scope.matches?.(EDITOR_QUERY_SELECTOR)) {
        result.push(scope);
      }
      scope.querySelectorAll(EDITOR_QUERY_SELECTOR).forEach((element) => result.push(element));
      scope.querySelectorAll("*").forEach((element) => {
        if (element.shadowRoot) pushAll(element.shadowRoot);
      });
    };
    pushAll(root);
    return Array.from(new Set(result));
  }

  function isVisibleEditable(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }

  function isCaptureReadyEditable(element) {
    return Boolean(isEditableElement(element) && isVisibleEditable(element) && !isSensitiveEditable(element) && !isTransientInputBuffer(element));
  }

  function preferredEditableForActiveControl(active) {
    if (!isPlainTextControl(active)) return preferredEditableForElement(active);
    const activeText = editableCandidateText(active).trim();
    if (activeText.length > SMALL_BUFFER_TEXT_LENGTH && !looksLikeInputBuffer(active)) {
      return active;
    }

    const richer = bestRichEditableCandidate(active, activeText.length);
    return richer || active;
  }

  function preferredEditableForElement(element) {
    if (!element || isPlainTextControl(element)) return element;
    const promoted = promotedEditorRoot(element);
    return promoted || element;
  }

  function promotedEditorRoot(element) {
    const currentTextLength = editableCandidateText(element).trim().length;
    const candidates = [];
    let cursor = element;
    while (cursor && cursor.nodeType === Node.ELEMENT_NODE) {
      if (cursor.matches?.(BROAD_EDITOR_ROOT_SELECTOR) || cursor.matches?.(".se-component-content")) {
        candidates.push(cursor);
      }
      cursor = cursor.parentElement;
    }

    const usable = candidates
      .filter((candidate) => candidate !== element)
      .filter((candidate) => isCaptureReadyEditable(candidate))
      .map((candidate) => ({
        element: candidate,
        textLength: editableCandidateText(candidate).trim().length
      }))
      .filter((candidate) => candidate.textLength >= Math.max(currentTextLength, 1));

    if (!usable.length) return null;
    usable.sort((left, right) => {
      const leftScore = left.textLength + visibleArea(left.element) / 100 + editorLikeScore(left.element);
      const rightScore = right.textLength + visibleArea(right.element) / 100 + editorLikeScore(right.element);
      return rightScore - leftScore;
    });
    return usable[0].element;
  }

  function bestRichEditableCandidate(active, activeTextLength) {
    const candidates = queryEditableCandidates(document)
      .filter((element) => element !== active)
      .filter((element) => !isPlainTextControl(element))
      .filter((element) => isCaptureReadyEditable(element))
      .map((element) => ({
        element,
        text: editableCandidateText(element).trim()
      }))
      .filter((candidate) => candidate.text.length >= Math.max(3, activeTextLength + 1));

    if (!candidates.length) return null;
    candidates.sort((left, right) => richCandidateScore(right, active) - richCandidateScore(left, active));
    return candidates[0].element;
  }

  function richCandidateScore(candidate, active) {
    const element = candidate.element;
    let score = candidate.text.length + visibleArea(element) / 100 + editorLikeScore(element);
    if (element.contains(active) || active.closest?.(EDITOR_ROOT_SELECTOR) === element) score += 2500;
    if (isNaverSmartEditorElement(element)) score += 1200;
    return score;
  }

  function looksLikeInputBuffer(element) {
    if (!element || !element.matches) return false;
    if (element.matches("[data-input-buffer]")) return true;
    const attrs = `${element.id || ""} ${element.className || ""} ${element.getAttribute("aria-label") || ""}`.toLowerCase();
    return /input-buffer|inputbuffer|composition|hidden|dummy/.test(attrs);
  }

  function isNaverSmartEditorElement(element) {
    if (!element || !element.matches) return false;
    return Boolean(element.matches(".se-main-container,.se-component-content,.se-module-text,.se-text-paragraph"));
  }

  function isTransientInputBuffer(element) {
    if (!element || !element.matches) return true;
    if (element.matches("[data-input-buffer]")) return true;
    const style = window.getComputedStyle(element);
    const inlineStyle = element.getAttribute("style") || "";
    const transformText = `${style.transform || ""} ${inlineStyle}`.toLowerCase();
    if (transformText.includes("rotatex(90deg)")) return true;
    if (!element.querySelector("[data-input-buffer]")) return false;
    return !editorContentText(element).trim();
  }

  function editorContentText(element) {
    if (!element) return "";
    if (element.matches && isPlainTextControl(element)) return element.value || "";
    const chunks = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, element) && (node.nodeValue || "").trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      }
    });
    while (walker.nextNode()) {
      chunks.push(walker.currentNode.nodeValue || "");
    }
    return chunks.join("");
  }

  function editorLikeScore(element) {
    const attrs = `${element.id || ""} ${element.className || ""} ${element.getAttribute("role") || ""} ${element.getAttribute("aria-label") || ""}`.toLowerCase();
    let score = 0;
    if (/editor|write|content|article|post|se-|smarteditor|prosemirror|textbox/.test(attrs)) score += 500;
    if (element.getAttribute("role") === "textbox") score += 300;
    if (isNaverSmartEditorElement(element)) score += 700;
    if (isPlainTextControl(element)) score += 200;
    return score;
  }

  function editableCandidateText(element) {
    if (!element) return "";
    if (isPlainTextControl(element)) return element.value || "";
    return editorContentText(element);
  }

  function visibleArea(element) {
    const rect = element.getBoundingClientRect();
    return Math.max(0, rect.width) * Math.max(0, rect.height);
  }

  function captureInput(element) {
    const text = element.value || "";
    return {
      text: clampCaptureText(text),
      html: "",
      // The desktop bridge currently accepts "textarea" and "contenteditable".
      // Plain input controls use the textarea path and keep the real kind in dom_debug.
      target_kind: "textarea",
      selection: {
        start: element.selectionStart || 0,
        end: element.selectionEnd || 0
      },
      dom_debug: {
        actualTargetKind: element.tagName.toLowerCase(),
        inputType: element.getAttribute("type") || element.type || "",
        textPreview: String(text || "").slice(0, 160)
      },
      segments: []
    };
  }

  function captureContentEditable(element) {
    const range = selectedRangeInside(element);
    const extraction = extractEditableText(element);
    const text = clampCaptureText(extraction.text);
    const segments = styleSegmentsFromElement(element, text);
    const debug = domDebug(element, range);
    debug.extractionStrategy = extraction.strategy;
    debug.extractionCandidates = extraction.candidates;
    return {
      text,
      html: element.innerHTML,
      target_kind: "contenteditable",
      dom_debug: debug,
      extraction_strategy: extraction.strategy,
      selection: {},
      segments
    };
  }

  function clampCaptureText(text) {
    const value = String(text || "");
    if (value.length <= MAX_CAPTURE_TEXT_LENGTH) return value;
    return value.slice(0, MAX_CAPTURE_TEXT_LENGTH);
  }

  function normalizeComparableText(text) {
    return String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  }

  function editablePlainText(element) {
    return extractEditableText(element).text;
  }

  function extractEditableText(element) {
    const candidates = [];
    const gmailCandidates = gmailTextCandidates(element);
    if (gmailCandidates.length) {
      const usableGmail = gmailCandidates.filter((candidate) => candidate.normalized);
      if (usableGmail.length) {
        const bestGmail = usableGmail[0];
        return {
          text: bestGmail.text,
          strategy: bestGmail.name,
          candidates: gmailCandidates.map(candidateSummary)
        };
      }
    }

    candidates.push(textCandidate("generic-block", blockBasedPlainText(element)));
    candidates.push(textCandidate("inline", inlinePlainText(element)));
    candidates.push(textCandidate("dom-order", domOrderPlainText(element)));

    const usable = candidates.filter((candidate) => candidate.normalized);
    if (!usable.length) {
      return {
        text: "",
        strategy: "empty",
        candidates: candidates.map(candidateSummary)
      };
    }

    const best = usable.sort((left, right) => extractionScore(right) - extractionScore(left))[0];
    return {
      text: best.text,
      strategy: best.name,
      candidates: candidates.map(candidateSummary)
    };
  }

  function textCandidate(name, text) {
    const value = normalizeExtractedText(text);
    return {
      name,
      text: value,
      length: value.length,
      lineBreaks: countLineBreaks(value),
      blankLines: countBlankLines(value),
      normalized: normalizeExtractionContent(value)
    };
  }

  function candidateSummary(candidate) {
    return {
      name: candidate.name,
      length: candidate.length,
      lineBreaks: candidate.lineBreaks,
      blankLines: candidate.blankLines,
      preview: candidate.text.slice(0, 120)
    };
  }

  function extractionScore(candidate) {
    // Prefer candidates that preserve structure. A huge length difference usually
    // means the candidate captured editor chrome, so length is only a mild bonus.
    return candidate.lineBreaks * 4 + candidate.blankLines * 8 + Math.min(candidate.length, 2000) / 1000;
  }

  function normalizeExtractedText(text) {
    return String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/\n+$/g, "");
  }

  function normalizeExtractionContent(text) {
    return String(text || "").replace(/\s+/g, "");
  }

  function countBlankLines(text) {
    return (String(text || "").match(/\n\s*\n/g) || []).length;
  }

  function blockBasedPlainText(element) {
    const blockSelector = "p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre,.se-text-paragraph";
    const blocks = leafTextBlocks(element, blockSelector);
    if (!blocks.length) {
      return inlinePlainText(element);
    }

    const lines = blocks.map((block) => blockPlainText(block));
    return lines.join("\n");
  }

  function domOrderPlainText(element) {
    const chunks = [];
    walkDomOrderText(element, chunks, element, true);
    return chunks.join("");
  }

  function walkDomOrderText(node, chunks, root, isRoot = false) {
    if (!isEditorContentNode(node, root)) return;
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const tag = node.tagName ? node.tagName.toLowerCase() : "";
    if (tag === "br") {
      chunks.push("\n");
      return;
    }
    const isBlock = !isRoot && isTextBlockElement(node);
    const beforeLength = chunks.length;
    Array.from(node.childNodes).forEach((child) => walkDomOrderText(child, chunks, root, false));
    if (isBlock) {
      if (chunks.length === beforeLength || !lastChunkEndsWithNewline(chunks)) {
        chunks.push("\n");
      }
    }
  }

  function lastChunkEndsWithNewline(chunks) {
    for (let index = chunks.length - 1; index >= 0; index -= 1) {
      const chunk = String(chunks[index] || "");
      if (!chunk) continue;
      return chunk.endsWith("\n");
    }
    return false;
  }

  function isTextBlockElement(element) {
    if (!element || !element.matches) return false;
    const tag = element.tagName ? element.tagName.toLowerCase() : "";
    return ["address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul"].includes(tag) ||
      element.matches(".se-text-paragraph,[role='paragraph']");
  }

  function gmailTextCandidates(element) {
    if (!isGmailEditorElement(element)) return [];
    const root = gmailEditorRoot(element);
    if (!root) return [];
    return [
      textCandidate("gmail-lines", gmailLinesPlainText(root))
    ];
  }

  function gmailLinesPlainText(root) {
    const lines = [];
    let pendingInline = "";

    Array.from(root.childNodes).forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        pendingInline += node.nodeValue || "";
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;

      const tag = node.tagName ? node.tagName.toLowerCase() : "";
      if (tag === "br") {
        lines.push(pendingInline);
        pendingInline = "";
        return;
      }

      if (isGmailLineElement(node)) {
        if (pendingInline) {
          lines.push(pendingInline);
          pendingInline = "";
        }
        lines.push(gmailLineText(node));
        return;
      }

      pendingInline += inlinePlainText(node);
    });

    if (pendingInline || !lines.length) {
      lines.push(pendingInline);
    }
    return lines.join("\n");
  }

  function isGmailLineElement(element) {
    if (!element || !element.matches) return false;
    const tag = element.tagName ? element.tagName.toLowerCase() : "";
    return ["div", "p", "li", "blockquote", "pre"].includes(tag);
  }

  function gmailLineText(element) {
    if (isEmptyGmailLine(element)) return "";
    return inlinePlainText(element);
  }

  function isEmptyGmailLine(element) {
    return !normalizedVisibleText(element).trim() && Boolean(element.querySelector("br") || !element.childNodes.length);
  }

  function isGmailEditorElement(element) {
    if (!/(\.|^)mail\.google\.com$|(\.|^)googlemail\.com$/.test(location.hostname)) return false;
    const root = gmailEditorRoot(element);
    return Boolean(root);
  }

  function gmailEditorRoot(element) {
    if (!element || !element.closest) return null;
    const textboxRoot = element.closest("[contenteditable='true'][role='textbox'],div[aria-label='Message Body'][contenteditable='true']");
    if (textboxRoot) return textboxRoot;
    const root = element.closest("[contenteditable='true'][role='textbox'][aria-label],div[aria-label='Message Body'][contenteditable='true'],div[aria-label='편지 본문'][contenteditable='true']");
    if (!root) return null;
    const label = `${root.getAttribute("aria-label") || ""} ${root.getAttribute("role") || ""}`.toLowerCase();
    if (label.includes("message body") || label.includes("편지") || root.getAttribute("role") === "textbox") {
      return root;
    }
    return null;
  }

  function leafTextBlocks(element, blockSelector) {
    const allBlocks = Array.from(element.querySelectorAll(blockSelector));
    return allBlocks.filter((block) => {
      if (!isEditorContentNode(block, element) || !blockHasEditorText(block, element)) return false;
      const nestedBlocks = Array.from(block.querySelectorAll(blockSelector));
      return !nestedBlocks.some((nested) => nested !== block && isEditorContentNode(nested, element) && blockHasEditorText(nested, element));
    });
  }

  function blockPlainText(block) {
    if (isEmptyBlock(block)) return "";
    return inlinePlainText(block);
  }

  function isEmptyBlock(block) {
    if (normalizedVisibleText(block).trim()) return false;
    return Boolean(
      block.querySelector("br") ||
      block.children.length === 0 ||
      isEditableLineBlock(block)
    );
  }

  function isEditableLineBlock(block) {
    if (!block || !block.matches) return false;
    const tag = block.tagName ? block.tagName.toLowerCase() : "";
    return tag === "p" || tag === "li" || tag === "pre" || block.matches(".se-text-paragraph");
  }

  function normalizedVisibleText(element) {
    return String(element?.textContent || "")
      .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
      .replace(/\u00a0/g, " ");
  }

  function inlinePlainText(element) {
    const chunks = [];
    walkInlineText(element, chunks, element);
    return chunks.join("").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/g, "");
  }

  function walkInlineText(node, chunks, root) {
    if (!isEditorContentNode(node, root)) return;
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName && node.tagName.toLowerCase() === "br") {
      chunks.push("\n");
      return;
    }
    Array.from(node.childNodes).forEach((child) => walkInlineText(child, chunks, root || node));
  }

  function isEditorContentNode(node, root) {
    if (!node) return false;
    const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!element) return false;
    if (root && element !== root && !root.contains(element)) return false;
    if (element === root) return true;
    return !isEditorChromeNode(element);
  }

  function isEditorChromeNode(element) {
    if (!element || !element.closest) return false;
    return Boolean(element.closest(
      "[data-input-buffer],[contenteditable='false'],[aria-hidden='true'],button,input,select,textarea,script,style,noscript," +
      "[role='button'],[role='toolbar'],[role='menu'],[role='menubar'],[role='menuitem'],[role='tab'],[role='switch']"
    ));
  }

  function blockHasEditorText(block, root) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, root) && (node.nodeValue || "").trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      }
    });
    return Boolean(walker.nextNode()) || isEmptyBlock(block);
  }

  function domDebug(element, range) {
    const textNodes = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, element) && (node.nodeValue || "").trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    while (walker.nextNode() && textNodes.length < 8) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      textNodes.push({
        text: (node.nodeValue || "").slice(0, 40),
        parent: parent ? parent.tagName.toLowerCase() : "",
        parentClass: parent ? parent.className || "" : "",
        parentStyle: parent ? parent.getAttribute("style") || "" : "",
        style: parent ? styleFromElement(parent) : {}
      });
    }
    return {
      target: element.tagName ? element.tagName.toLowerCase() : "",
      targetClass: String(element.className || "").slice(0, 160),
      textPreview: editablePlainText(element).slice(0, 500),
      htmlPreview: (element.innerHTML || "").slice(0, 500),
      childElementCount: element.querySelectorAll("*").length,
      hasRange: Boolean(range),
      textNodes
    };
  }

  function selectedRangeInside(element) {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    const container = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    return container && element.contains(container) ? range : null;
  }

  function fragmentHtml(range) {
    const container = document.createElement("div");
    container.appendChild(range.cloneContents());
    return container.innerHTML;
  }

  function styleSegmentsFromRange(range, fallbackElement, text) {
    if (!text) return [];
    if (!range) {
      return [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];
    }

    const walkerRoot = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    if (!walkerRoot) return [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];

    const walker = document.createTreeWalker(walkerRoot, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, fallbackElement) && range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });

    const segments = [];
    let position = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      let chunk = node.nodeValue || "";
      if (!chunk) continue;
      if (node === range.startContainer) chunk = chunk.slice(range.startOffset);
      if (node === range.endContainer) chunk = chunk.slice(0, Math.max(0, range.endOffset - (node === range.startContainer ? range.startOffset : 0)));
      if (!chunk) continue;
      const style = styleFromElement(node.parentElement || fallbackElement);
      appendSegment(segments, position, position + chunk.length, style);
      position += chunk.length;
    }

    return segments.length ? segments : [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];
  }

  function styleSegmentsFromElement(element, text) {
    if (!text) return [];
    const blockSelector = "p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre,.se-text-paragraph";
    const blocks = leafTextBlocks(element, blockSelector);
    if (blocks.length) {
      const segments = [];
      let cursor = 0;
      blocks.forEach((block, index) => {
        const blockText = blockPlainText(block);
        appendSegmentsForContainer(segments, block, blockText, cursor, styleFromElement(block));
        cursor += blockText.length;
        if (index < blocks.length - 1) cursor += 1;
      });
      return completeSegments(segments, text, styleFromElement(element));
    }

    const segments = [];
    appendSegmentsForContainer(segments, element, text, 0, styleFromElement(element));
    return completeSegments(segments, text, styleFromElement(element));
  }

  function appendSegmentsForContainer(segments, container, containerText, offset, fallbackStyle) {
    if (!containerText) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, container) && (node.nodeValue || "").length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });

    let position = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const chunk = node.nodeValue || "";
      if (!chunk) continue;
      const foundAt = containerText.indexOf(chunk, position);
      const localStart = foundAt >= 0 ? foundAt : position;
      const localEnd = Math.min(containerText.length, localStart + chunk.length);
      if (localEnd <= localStart) continue;
      appendSegment(segments, offset + localStart, offset + localEnd, styleFromNode(node, fallbackStyle));
      position = localEnd;
    }
  }

  function completeSegments(segments, text, fallbackStyle) {
    if (!segments.length) {
      return [{ start: 0, end: text.length, style: fallbackStyle }];
    }
    if (segments[0].start > 0) {
      segments.unshift({ start: 0, end: segments[0].start, style: fallbackStyle });
    }
    const last = segments[segments.length - 1];
    if (last.end < text.length) {
      appendSegment(segments, last.end, text.length, fallbackStyle);
    }
    return segments;
  }

  function styleFromNode(node, fallbackStyle) {
    const parent = node && node.parentElement;
    if (!parent) return fallbackStyle || {};
    return styleFromElement(parent);
  }

  function styleFromElement(element) {
    const computed = window.getComputedStyle(element);
    const decoration = effectiveTextDecoration(element, computed);
    const textColor = effectiveTextColor(element, computed);
    return {
      fontFamily: computed.fontFamily,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      fontStyle: computed.fontStyle,
      color: textColor,
      webkitTextFillColor: textColor,
      verticalAlign: effectiveVerticalAlign(element, computed),
      textDecorationLine: decoration.line,
      textDecorationColor: decoration.color,
      textDecorationStyle: decoration.style,
      textDecorationThickness: decoration.thickness,
      backgroundColor: effectiveBackgroundColor(element, computed)
    };
  }

  function effectiveTextDecoration(element, computed) {
    const lines = [];
    let color = computed.textDecorationColor;
    let style = computed.textDecorationStyle;
    let thickness = computed.textDecorationThickness;
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const currentStyle = current === element ? computed : window.getComputedStyle(current);
      const line = currentStyle.textDecorationLine || "none";
      if (line && line !== "none") {
        line.split(/\s+/).forEach((part) => {
          if (part && part !== "none" && !lines.includes(part)) lines.push(part);
        });
        color = currentStyle.textDecorationColor || color;
        style = currentStyle.textDecorationStyle || style;
        thickness = currentStyle.textDecorationThickness || thickness;
      }
      current = current.parentElement;
    }
    return {
      line: lines.length ? lines.join(" ") : "none",
      color,
      style,
      thickness
    };
  }

  function effectiveTextColor(element, computed) {
    const fill = computed.webkitTextFillColor;
    if (fill && fill !== "transparent" && fill !== "rgba(0, 0, 0, 0)") {
      return fill;
    }
    return computed.color;
  }

  function effectiveVerticalAlign(element, computed) {
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const tag = current.tagName ? current.tagName.toLowerCase() : "";
      if (tag === "sup") return "super";
      if (tag === "sub") return "sub";
      const style = current === element ? computed : window.getComputedStyle(current);
      const align = style.verticalAlign || "baseline";
      if (align && align !== "baseline" && align !== "normal" && align !== "0px") {
        return align;
      }
      current = current.parentElement;
    }
    return computed.verticalAlign;
  }

  function effectiveBackgroundColor(element, computed) {
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const style = current === element ? computed : window.getComputedStyle(current);
      const color = style.backgroundColor;
      if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)") {
        return color;
      }
      current = current.parentElement;
    }
    return computed.backgroundColor;
  }

  function appendSegment(segments, start, end, style) {
    const signature = JSON.stringify(style);
    const previous = segments[segments.length - 1];
    if (previous && previous.signature === signature && previous.end === start) {
      previous.end = end;
      return;
    }
    segments.push({ start, end, style, signature });
  }

  function cleanSegments(segments) {
    return segments.map(({ signature, ...segment }) => segment);
  }

  function scheduleCapture() {
    window.clearTimeout(captureTimer);
    captureTimer = window.setTimeout(captureNow, CAPTURE_DEBOUNCE_MS);
  }

  function scheduleSettledCaptures() {
    settleTimers.forEach((timer) => window.clearTimeout(timer));
    settleTimers = [0, 250, 800, 1500, 2500].map((delay) => window.setTimeout(captureNow, delay));
  }

  function observeEditable(element) {
    if (!element || isPlainTextControl(element) || observedEditable === element) return;
    if (editableObserver) editableObserver.disconnect();
    observedEditable = element;
    editableObserver = new MutationObserver(() => scheduleCapture());
    editableObserver.observe(element, {
      childList: true,
      characterData: true,
      subtree: true
    });
  }

  function shouldCaptureDocument() {
    if (document.visibilityState && document.visibilityState !== "visible") return false;
    if (!document.hasFocus()) return false;
    return true;
  }

  async function captureNow() {
    if (!shouldCaptureDocument()) {
      lastCaptureState = { ready: false, reason: "document_not_focused", sessionId: SESSION_ID, url: location.href };
      return;
    }
    const element = activeEditable();
    if (!element) {
      lastCaptureState = { ready: false, reason: "no_editable_target", sessionId: SESSION_ID, url: location.href };
      return;
    }
    if (!shouldCaptureDocument()) {
      lastCaptureState = { ready: false, reason: "focus_lost", sessionId: SESSION_ID, url: location.href };
      return;
    }
    const payload = isPlainTextControl(element) ? captureInput(element) : captureContentEditable(element);
    if (!payload.text.trim()) {
      lastCaptureState = { ready: false, reason: "empty_text", sessionId: SESSION_ID, url: location.href };
      return;
    }
    payload.segments = cleanSegments(payload.segments || []);
    payload.session_id = SESSION_ID;
    payload.url = location.href;
    payload.title = document.title;

    const signature = JSON.stringify([payload.url, payload.text, payload.html, payload.selection, payload.segments]);
    const now = Date.now();
    if (signature === lastSignature && now - lastCaptureSentAt < 5000) return;

    try {
      const response = await bridgeRequest("writingAssistantBridgeCapture", { payload });
      if (response.ok) {
        lastSignature = signature;
        lastCaptureSentAt = now;
        lastCaptureState = {
          ready: true,
          reason: "captured",
          sessionId: SESSION_ID,
          targetKind: payload.target_kind,
          extractionStrategy: payload.extraction_strategy || payload.dom_debug?.extractionStrategy || "",
          textLength: payload.text.length,
          lineBreaks: countLineBreaks(payload.text),
          blankLines: countBlankLines(payload.text),
          preview: payload.text.slice(0, 120),
          title: document.title,
          url: location.href
        };
      }
    } catch (_error) {
      // The desktop app may not be running yet.
      lastCaptureState = { ready: false, reason: "bridge_unreachable", sessionId: SESSION_ID, url: location.href };
    }
  }

  async function pollCommand() {
    try {
      const response = await bridgeRequest("writingAssistantBridgeCommand", { sessionId: SESSION_ID });
      const data = response.data || response;
      if (data && data.command && data.command.type === "replace_selection") {
        applyReplacement(data.command.text || "", data.command.style_info || {});
        scheduleCapture();
      }
    } catch (_error) {
      // Keep polling quietly.
    } finally {
      window.setTimeout(pollCommand, POLL_MS);
    }
  }

  function applyReplacement(text, styleInfo) {
    const element = activeEditable();
    if (!element) return;
    if (isPlainTextControl(element)) {
      const start = element.selectionStart || 0;
      const end = element.selectionEnd || start;
      if (typeof element.setRangeText === "function") {
        element.setRangeText(text, start, end, "end");
      } else {
        element.value = `${element.value.slice(0, start)}${text}${element.value.slice(end)}`;
        const caret = start + String(text).length;
        element.setSelectionRange?.(caret, caret);
      }
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      reportApplied("plain_text_control", text, {}, {
        actualTargetKind: element.tagName.toLowerCase(),
        inputType: element.getAttribute("type") || element.type || "",
        textPreview: String(element.value || "").slice(0, 160)
      });
      return;
    }

    const selection = window.getSelection();
    const range = selectedRangeInside(element) || rangeForElementContents(element);
    const beforeDebug = domDebug(element, range);
    if (applyNaverSmartEditorReplacement(element, text)) {
      reportApplied("naver_smart_editor_blocks", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
      scheduleSettledCaptures();
      return;
    }
    if (!isNaverSmartEditorElement(element) && applyWithNativeInsertText(element, text)) {
      reportApplied("native_insert_text", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
      scheduleSettledCaptures();
      return;
    }
    if (replaceTextPreservingDom(range, element, text)) {
      selection.removeAllRanges();
      const newRange = document.createRange();
      newRange.selectNodeContents(element);
      newRange.collapse(false);
      selection.addRange(newRange);
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      reportApplied("dom_preserve", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
      return;
    }

    range.deleteContents();
    const fragment = buildReplacementFragment(text, styleInfo, beforeDebug);
    range.insertNode(fragment);
    selection.removeAllRanges();
    const newRange = document.createRange();
    newRange.selectNodeContents(element);
    newRange.collapse(false);
    selection.addRange(newRange);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    reportApplied("fragment", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
  }

  function applyNaverSmartEditorReplacement(element, text) {
    if (!isNaverSmartEditorElement(element)) return false;
    const blocks = smartEditorParagraphBlocks(element);
    if (!blocks.length) return false;

    const lines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const template = blocks.find((block) => block.querySelector("span.__se-node,span")) || blocks[0];
    let previous = null;
    const activeBlocks = blocks.slice();

    lines.forEach((line, index) => {
      let block = activeBlocks[index];
      if (!block) {
        block = cloneSmartEditorParagraph(template);
        insertAfter(previous || activeBlocks[activeBlocks.length - 1] || template, block);
        activeBlocks.push(block);
      }
      setSmartEditorParagraphText(block, line);
      previous = block;
    });

    activeBlocks.slice(lines.length).forEach((block) => block.remove());
    const focusTarget = editableFocusTarget(element) || element;
    dispatchEditorInputEvents(focusTarget, text);
    return true;
  }

  function smartEditorParagraphBlocks(element) {
    return Array.from(element.querySelectorAll(".se-text-paragraph"))
      .filter((block) => isEditorContentNode(block, element));
  }

  function cloneSmartEditorParagraph(template) {
    const clone = template.cloneNode(true);
    clone.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
    clone.querySelectorAll("[data-placeholder]").forEach((node) => node.removeAttribute("data-placeholder"));
    return clone;
  }

  function insertAfter(reference, node) {
    const parent = reference?.parentNode;
    if (!parent) return;
    parent.insertBefore(node, reference.nextSibling);
  }

  function setSmartEditorParagraphText(block, line) {
    const host = block.querySelector("span.__se-node,span") || block;
    Array.from(host.childNodes).forEach((child) => child.remove());
    if (line) {
      host.appendChild(document.createTextNode(line));
    } else {
      host.appendChild(document.createTextNode("\u200b"));
    }
  }

  function applyWithNativeInsertText(element, text) {
    if (!element || !String(text || "")) return false;
    const focusTarget = editableFocusTarget(element);
    if (!focusTarget) return false;
    const selection = window.getSelection();
    if (!selection) return false;

    try {
      focusTarget.focus?.({ preventScroll: true });
    } catch (_error) {
      try {
        focusTarget.focus?.();
      } catch (__error) {
        // Keep trying with the current document selection.
      }
    }

    const range = rangeForElementContents(element);
    selection.removeAllRanges();
    selection.addRange(range);

    let applied = false;
    try {
      applied = Boolean(document.execCommand?.("insertText", false, String(text)));
    } catch (_error) {
      applied = false;
    }
    if (!applied) return false;

    dispatchEditorInputEvents(focusTarget, text);
    return true;
  }

  function editableFocusTarget(element) {
    if (!element) return null;
    if (isPlainTextControl(element) || element.isContentEditable) return element;
    const active = deepActiveElement();
    if (active && element.contains(active) && (active.isContentEditable || isPlainTextControl(active))) return active;
    return element.querySelector?.("[contenteditable='true'],[contenteditable='plaintext-only'],[contenteditable=''],textarea,input") || null;
  }

  function dispatchEditorInputEvents(element, text) {
    try {
      element.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, inputType: "insertReplacementText", data: text }));
    } catch (_error) {
      // Some browser/editor combinations do not allow synthetic beforeinput.
    }
    try {
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
    } catch (_error) {
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function reportApplied(method, text, before, after) {
    try {
      await bridgeRequest("writingAssistantBridgeApplied", {
        payload: {
          session_id: SESSION_ID,
          method,
          text,
          before,
          after,
          url: location.href,
          title: document.title
        }
      });
    } catch (_error) {
      // Diagnostics are best effort.
    }
  }

  function replaceTextPreservingDom(range, element, text) {
    if (String(text).includes("\n")) return false;
    const entries = textNodeEntriesForRange(range, element);
    if (!entries.length) return false;

    let cursor = 0;
    entries.forEach((entry, index) => {
      const original = entry.node.nodeValue || "";
      const before = original.slice(0, entry.startOffset);
      const after = original.slice(entry.endOffset);
      const originalLength = Math.max(0, entry.endOffset - entry.startOffset);
      const isLast = index === entries.length - 1;
      const nextCursor = isLast ? text.length : Math.min(text.length, cursor + originalLength);
      const chunk = text.slice(cursor, nextCursor);
      entry.node.nodeValue = before + chunk + after;
      cursor = nextCursor;
    });
    return true;
  }

  function textNodeEntriesForRange(range, element) {
    const entries = [];
    const root = element;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, element) && range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });

    while (walker.nextNode()) {
      const node = walker.currentNode;
      const value = node.nodeValue || "";
      let startOffset = 0;
      let endOffset = value.length;
      if (node === range.startContainer) startOffset = range.startOffset;
      if (node === range.endContainer) endOffset = range.endOffset;
      if (endOffset > startOffset) {
        entries.push({ node, startOffset, endOffset });
      }
    }
    return entries;
  }

  function rangeForElementContents(element) {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range;
  }

  function buildReplacementFragment(text, styleInfo, beforeDebug) {
    if (String(text).includes("\n") && shouldUseBlockFragment(beforeDebug)) {
      return buildBlockReplacementFragment(text, styleInfo);
    }

    const fragment = document.createDocumentFragment();
    const segments = Array.isArray(styleInfo.segments) ? styleInfo.segments : [];
    if (!segments.length) {
      appendTextWithBreaks(fragment, text);
      return fragment;
    }

    const normalized = normalizedSegmentsForText(segments, text);
    let cursor = 0;
    for (const segment of normalized) {
      if (segment.start > cursor) {
        appendTextWithBreaks(fragment, text.slice(cursor, segment.start));
      }
      const chunk = text.slice(segment.start, segment.end);
      if (chunk) {
        const span = document.createElement("span");
        applyInlineStyle(span, segment.style || {});
        appendTextWithBreaks(span, chunk);
        fragment.appendChild(span);
      }
      cursor = Math.max(cursor, segment.end);
    }
    if (cursor < String(text || "").length) {
      appendTextWithBreaks(fragment, text.slice(cursor));
    }
    return fragment;
  }

  function shouldUseBlockFragment(beforeDebug) {
    const html = String((beforeDebug || {}).htmlPreview || "");
    return /<\/?(p|div|li|h[1-6]|blockquote|pre)(\s|>|\/)/i.test(html);
  }

  function buildBlockReplacementFragment(text, styleInfo) {
    const fragment = document.createDocumentFragment();
    const lines = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const segments = Array.isArray(styleInfo.segments) ? normalizedSegmentsForText(styleInfo.segments, text) : [];
    let cursor = 0;

    lines.forEach((line) => {
      const paragraph = document.createElement("p");
      if (!line) {
        paragraph.appendChild(document.createElement("br"));
        fragment.appendChild(paragraph);
        cursor += 1;
        return;
      }

      appendStyledInlineRange(paragraph, text, cursor, cursor + line.length, segments);
      fragment.appendChild(paragraph);
      cursor += line.length + 1;
    });
    return fragment;
  }

  function appendStyledInlineRange(parent, text, start, end, segments) {
    const overlapping = segments.filter((segment) => segment.end > start && segment.start < end);
    if (!overlapping.length) {
      parent.appendChild(document.createTextNode(text.slice(start, end)));
      return;
    }

    let cursor = start;
    for (const segment of overlapping) {
      const segmentStart = Math.max(start, segment.start);
      const segmentEnd = Math.min(end, segment.end);
      if (segmentStart > cursor) {
        parent.appendChild(document.createTextNode(text.slice(cursor, segmentStart)));
      }
      const span = document.createElement("span");
      applyInlineStyle(span, segment.style || {});
      span.appendChild(document.createTextNode(text.slice(segmentStart, segmentEnd)));
      parent.appendChild(span);
      cursor = segmentEnd;
    }
    if (cursor < end) {
      parent.appendChild(document.createTextNode(text.slice(cursor, end)));
    }
  }

  function normalizedSegmentsForText(segments, text) {
    const length = String(text || "").length;
    const result = [];
    const sorted = (Array.isArray(segments) ? segments : [])
      .map((source) => {
        const start = Number.isFinite(source.start) ? source.start : parseInt(source.start || "0", 10);
        const end = Number.isFinite(source.end) ? source.end : parseInt(source.end || "0", 10);
        return {
          start: Math.max(0, Math.min(length, start || 0)),
          end: Math.max(0, Math.min(length, end || 0)),
          style: source.style || {}
        };
      })
      .filter((segment) => segment.end > segment.start)
      .sort((left, right) => left.start - right.start || left.end - right.end);

    for (const segment of sorted) {
      const previous = result[result.length - 1];
      const start = previous ? Math.max(segment.start, previous.end) : segment.start;
      if (segment.end <= start) continue;
      splitSegmentByLine(result, text, start, segment.end, segment.style);
    }

    return result;
  }

  function splitSegmentByLine(result, text, start, end, style) {
    let cursor = start;
    while (cursor < end) {
      const newline = String(text || "").indexOf("\n", cursor);
      const segmentEnd = newline >= 0 && newline < end ? newline : end;
      if (segmentEnd > cursor) {
        result.push({ start: cursor, end: segmentEnd, style });
      }
      if (newline < 0 || newline >= end) break;
      cursor = newline + 1;
    }
  }

  function appendTextWithBreaks(parent, text) {
    const lines = String(text).split("\n");
    lines.forEach((line, index) => {
      if (index > 0) parent.appendChild(document.createElement("br"));
      if (line) parent.appendChild(document.createTextNode(line));
    });
  }

  function applyInlineStyle(element, style) {
    const assignments = {
      fontFamily: "fontFamily",
      fontSize: "fontSize",
      fontWeight: "fontWeight",
      fontStyle: "fontStyle",
      color: "color",
      backgroundColor: "backgroundColor",
      webkitTextFillColor: "webkitTextFillColor",
      verticalAlign: "verticalAlign",
      textDecorationLine: "textDecorationLine",
      textDecorationColor: "textDecorationColor",
      textDecorationStyle: "textDecorationStyle",
      textDecorationThickness: "textDecorationThickness"
    };
    for (const [source, target] of Object.entries(assignments)) {
      const value = style[source];
      if (typeof value === "string" && value && value !== "rgba(0, 0, 0, 0)") {
        element.style[target] = value;
      }
    }
  }

  function statusTextForElement(element) {
    if (!element) return "";
    if (isPlainTextControl(element)) return element.value || "";
    return editablePlainText(element);
  }

  function statusExtractionForElement(element) {
    if (!element) return { text: "", strategy: "" };
    if (isPlainTextControl(element)) return { text: element.value || "", strategy: "plain-control" };
    return extractEditableText(element);
  }

  function countLineBreaks(text) {
    return (String(text || "").match(/\n/g) || []).length;
  }

  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || message.type !== "writingAssistantBridgeStatus") return false;
      const element = activeEditable();
      const extraction = statusExtractionForElement(element);
      const text = extraction.text || "";
      sendResponse({
        ...lastCaptureState,
        sessionId: SESSION_ID,
        currentTargetKind: element ? (isPlainTextControl(element) ? "textarea" : "contenteditable") : "",
        currentExtractionStrategy: extraction.strategy || "",
        currentTextLength: text.length,
        currentLineBreaks: countLineBreaks(text),
        currentBlankLines: countBlankLines(text),
        currentPreview: text.slice(0, 120),
        title: document.title,
        url: location.href
      });
      return true;
    });
  }

  document.addEventListener("selectionchange", scheduleCapture, true);
  document.addEventListener("beforeinput", scheduleSettledCaptures, true);
  document.addEventListener("input", scheduleSettledCaptures, true);
  document.addEventListener("paste", scheduleSettledCaptures, true);
  document.addEventListener("keyup", scheduleSettledCaptures, true);
  document.addEventListener("mouseup", scheduleSettledCaptures, true);
  document.addEventListener("focusin", scheduleSettledCaptures, true);
  window.addEventListener("load", scheduleCapture, true);
  pollCommand();
})();
