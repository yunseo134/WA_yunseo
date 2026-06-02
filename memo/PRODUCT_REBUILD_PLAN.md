# Product Rebuild Notes

This memo is for future Codex passes.

## Product direction

The app should become a lightweight writing companion, not a collection of tabs that each fire a separate expensive AI call.

Core principles:

- The desktop client must stay responsive even when AI is slow.
- The local server owns AI policy: model choice, prompt versioning, caching, input trimming, output limits, and error translation.
- The client should present one calm "writing insight stream" and only reveal deeper results when the user asks.
- AI requests should be deduplicated aggressively because realtime text capture can repeat the same content many times.
- Browser/page chrome text should be filtered before AI whenever possible.

## Structural target

Near-term architecture:

- `client/ui/*`: view rendering and user interaction only.
- `client/core/*`: client-side orchestration, request scheduling, formatting, and source replacement decisions.
- `server/ai_service.py`: all OpenAI prompt/model/cost/cache policy.
- `server/main.py`: HTTP boundary only; no prompt or model logic.
- `server/ai_cache.py`: persistent local response cache.

Longer-term:

- Replace feature-specific public endpoints with one `/ai/jobs` API that returns a job id.
- Add `/ai/jobs/{id}` polling or server-sent events.
- Move realtime eligibility/filtering into a dedicated text-intake policy module.
- Split `ResultPanel` into smaller widgets: header, text view, insight feed, settings, account, history.
- Split `App` into controllers: input controller, AI controller, auth controller, history controller.

## Changes made in this rebuild pass

- Added `server/ai_cache.py`, a small persistent JSON cache with LRU-style eviction.
- Rewrote `server/ai_service.py` around feature specs:
  - clean prompts,
  - `gpt-5-nano` defaults,
  - input trimming via `OPENAI_MAX_INPUT_CHARS`,
  - output limit via `OPENAI_MAX_OUTPUT_TOKENS`,
  - cache toggles via `OPENAI_CACHE_ENABLED` and `OPENAI_CACHE_MAX_ENTRIES`,
  - centralized JSON parsing and fallback behavior.
- Rewrote `client/core/ai_client.py` and `client/core/analyzer.py` to remove corrupted visible strings and keep formatting predictable.
- Removed unreachable old synchronous AI feature bodies in `client/ui/main_window.py`; feature buttons now funnel through `run_ai_feature_async`.
- Rewrote `server/database.py` to remove corrupted comments and keep DB setup clean.
- Made `server/auth.py` load `server/.env` directly and use a clean missing-secret error.
- Fixed `server/.env` BOM issue caused by PowerShell writing UTF-8 with BOM; `SECRET_KEY` now parses as `SECRET_KEY`, not `\ufeffSECRET_KEY`.

## 2026-05-29 observability pass

- Added `client/core/diagnostics.py`.
- Client diagnostics now write JSONL to `.logs/client_diagnostics.jsonl`.
- Global Python exceptions, background thread exceptions, and Qt messages are captured.
- `App` logs:
  - startup environment snapshot,
  - accepted input changes with text length/line count/hash instead of raw text,
  - spelling request/completion/failure timing,
  - AI feature request/completion/failure timing,
  - UI event-loop lag over roughly 1.5 seconds.
- Server diagnostics now write HTTP request timing to `.logs/server_requests.jsonl`.
- Server AI service now writes cache hits and live AI request completions to `.logs/ai_events.jsonl`.
- Local server startup error text was cleaned so user-facing failure notices are readable.

## Required user tests

Some behavior cannot be trusted from automated offscreen checks because it depends on Windows focus, mixed-DPI displays, and third-party apps:

- Move the app between the 2800x1800 monitor and the 1920x1080 monitor while the Text tab is visible.
- Type in the actual target apps: browser editor, Notepad, Word, and HWP if available.
- Trigger source replacement in Word/HWP/browser extension, because COM/UIA behavior depends on the installed app and active document state.
- Leave realtime mode running for several minutes while switching focus between browser pages and editors, then inspect `.logs/client_diagnostics.jsonl` for `ui_event_loop_lag`, repeated input hashes, or unexpected AI requests.

## 2026-05-29 source-apply strategy

The old source apply path performed whole-document replacement for rich documents. That is too dangerous because it can remove images, tables, paragraph breaks, and detailed formatting.

New direction:

- Pass captured original text as `_source_text` in `OutputTarget.style_info`.
- For Word/HWP, compute a text diff between `_source_text` and the generated result.
- Apply changed ranges in reverse order so earlier offsets stay valid.
- Block broad patches instead of falling back to full replacement.

Manual tests needed:

- HWP document with an image and two paragraphs: correct one word and confirm image/paragraph break remains.
- HWP document with a line break correction: confirm Enter/newline survives.
- Word document with mixed bold/normal text: correct one typo and confirm surrounding formatting remains.
- Large tone rewrite in HWP/Word: verify it fails safely rather than deleting document structure.

## Verification

- `python -m compileall server\ai_cache.py server\ai_service.py server\database.py server\auth.py server\main.py client\core\ai_client.py client\core\analyzer.py client\ui\main_window.py`
- FastAPI `TestClient` endpoint checks for correction, summary, evaluation, title, and tone with monkeypatched AI service methods.
- Analyzer formatting check with unicode-escape output.

## Cautions

- Live OpenAI calls were not run in this pass.
- Existing UI files still contain some console mojibake when viewed through PowerShell, but the touched client/core files now have clean Korean strings.
- `ResultPanel` and `App` are still too large. The next high-value refactor is to split them into smaller widgets/controllers.
