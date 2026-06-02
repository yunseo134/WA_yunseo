# Project Notes

Last updated: 2026-05-22

## Important paths

- Entry point: `main.py`
- Desktop app controller: `client/ui/main_window.py`
- Result panel UI: `client/ui/result_panel.py`
- Local server launcher: `client/core/local_server.py`
- Auth API client: `client/core/auth_api_client.py`
- Analyzer facade: `client/core/analyzer.py`
- Temporary AI client: `client/core/ai_client.py`
- Realtime monitor: `client/input/realtime_text_monitor.py`
- Browser bridge server: `client/input/browser_extension_bridge.py`
- Source replacement: `client/input/output_applier.py`
- FastAPI app: `server/main.py`
- Auth helpers: `server/auth.py`
- DB models: `server/models.py`
- DB connection: `server/database.py`
- Server AI service: `server/ai_service.py`
- Browser extension script: `browser_extension/content.js`

## Local ports

- FastAPI server: `127.0.0.1:8765`
- Browser extension bridge: `127.0.0.1:8766`

## Runtime files to treat carefully

- `server/.env`
- `server/app.db`
- `.auth_session.json`
- `.logs/`
- `.backup/`
- `__pycache__/`
- `*.pyc`

## Current settings observed

`user_settings.json` currently has:

```json
{
  "default_dark_mode": false,
  "input_mode": "realtime",
  "replace_mode": true,
  "history_enabled": false
}
```

This means the app is configured to monitor active text in realtime and allows source replacement mode in the UI.

## Verification already run

Command:

```powershell
python -m compileall .
```

Result:

- Python compilation completed without reported syntax errors.
- The command generated Python 3.14 `__pycache__` files in the working tree.

## Current work direction

As of 2026-05-22, the next development focus is:

- Replace dummy AI features with real OpenAI API calls.
- Update the UI while doing that work.
- The memo folder is for Codex to reread in later turns, not for external documentation.

## 2026-05-29 debugging note

- User reported AI results sometimes repeating the source text and HWP "원본 수정" appearing to do nothing.
- `server/ai_service.py` now uses OpenAI Responses structured output via `text.format=json_schema`, with a `json_object` retry if schema mode is rejected.
- After screenshot showed summary repeating source text, logs revealed `response.output_text` was empty. `server/ai_service.py` now extracts text from `response.output[*].content[*]`, treats empty/incomplete responses as failures instead of falling back to source text, and sends `reasoning.effort=minimal`.
- `AIService.PROMPT_VERSION` was bumped to `2026-05-product-ai-v3-response-extract`, so old bad cached AI responses should be bypassed.
- `server/main.py` root now returns `build_id=2026-05-29-response-extract`; `client/core/local_server.py` requires that build ID so an old already-running local server is stopped and replaced.
- `OPENAI_MAX_OUTPUT_TOKENS` was raised from 700 to 1200 in `.env`/`.env.example`; this is a cap, not guaranteed spend, and should reduce empty output from reasoning-token exhaustion.
- User asked to stop hiding broken AI responses behind deep fallback logic. Correction now requires valid JSON and required fields; invalid JSON raises an error instead of showing source text or raw JSON as a result.
- Correction output contract now includes `corrections[]` with `original`, `suggestion`, `category`, `explanation`, and `confidence`. Client renders this as a learning-oriented spelling report before the final corrected text.
- `AIService.PROMPT_VERSION` is now `2026-05-product-ai-v4-learning-corrections`; server/client build ID is `2026-05-29-learning-corrections`.
- Correction issues now get UI/overlay-ready metadata: `id`, `severity`, `source_start`, `source_end`, `anchor_text`, and `display_title`. Server computes source spans by matching `original` snippets against the source text. Client currently shows up to five correction issue cards in the info feed; future overlay can map those source spans to screen coordinates.
- Realtime spell checking now supports scope selection. Default is `current_sentence` to reduce token usage; `current_paragraph` and `full_text` are available in the spell tab. The app stores the scope in local/user settings as `spell_scope`.
- Scoped correction stores `(source_start, source_end)` and, when applying back to the original source, rebuilds the full document with only that scoped range replaced. This avoids sending a corrected sentence as if it were the whole document.
- Server/client build ID for this change is `2026-05-29-spell-scope`.
- Correction model changed from `gpt-5-nano` to `gpt-5-mini` in `server/.env`, `.env.example`, and `AIService.DEFAULT_MODELS` because nano was too inaccurate for spelling/grammar tutoring.
- Info feed cards now render as compact two-line cards and no longer show long native OS tooltips. Clicking a correction issue card opens the in-app detail notice with position/confidence/explanation.
- `client/ui/main_window.py` now shows notices/logs for spelling source apply success, failure, unavailable target, and no-op cases where the corrected text is identical to the source.

## UI changes already made

- Enabled Qt high-DPI scaling before `QApplication` is created in `client/ui/main_window.py`.
- Enabled high-DPI pixmaps.
- Set Qt high-DPI rounding policy to `PassThrough` when the installed PyQt5/Qt version supports it.
- Investigated the fuzzy/broken-looking Korean text in the window.
  - `client/assets/fonts/A2Z-Medium.ttf` is present and contains Hangul glyphs, so this is not a missing-font install issue.
  - Keep using the bundled A2Z font.
  - `load_app_font()` now applies A2Z with Qt font rendering hints: `PreferAntialias`, `PreferQuality`, and `PreferFullHinting` when supported.

## Git working tree notes

Before creating this memo folder, the worktree already had many modified/untracked runtime files:

- `.logs/*`
- `.backup/`
- `__pycache__/`
- Python 3.14 `.pyc` files
- `client/input/output_applier.py` was already modified

Do not assume those changes were created by this memo task.

## PowerShell note

Several command outputs show this recurring warning:

```text
Microsoft.PowerShell_profile.ps1 cannot be loaded because script execution is disabled.
```

This appears to be a local PowerShell execution-policy/profile issue. It does not by itself mean the project failed to run, but it makes command output noisy.

## Encoding note

PowerShell output showed mojibake for Korean text in several files. `rg` sometimes showed the same text more cleanly than `Get-Content`, so this may be a console encoding issue, actual file corruption, or a mix of both. Verify by opening the files in an editor with UTF-8 before rewriting strings.

2026-05-22 follow-up:

- `client/ui/result_panel.py` had actual corrupted UI literals in several visible areas, not just console-output mojibake.
- Restored visible Korean labels/placeholders/status text across the main panel, settings/account/history pages, and the integrated info feed.
- Verified with:
  - `python -m compileall client\ui\result_panel.py client\ui\main_window.py`
  - import check for `client.ui.result_panel` and `client.ui.main_window`
  - offscreen `ResultPanel` instantiation checking key labels/tabs/buttons.
- Remaining design risk: the info feed is now functional and no longer giant, but the overall visual hierarchy may still need a deliberate redesign pass rather than incremental patching.

2026-05-22 realtime/UI follow-up:

- Info feed chips now use a wrapping flow layout instead of a single hidden horizontal row.
- The ready chip is removed as soon as a real info item appears.
- Realtime auto spelling is gated by recent keyboard activity from the same foreground window.
- Generic browser/UIA snapshots get an extra guard: long/page-chrome-like browser text is not auto-corrected even if a key was pressed recently.
- Manual "다시 분석" still runs spelling regardless of the auto gate.

2026-05-22 follow-up 2:

- Simple calculation info is local deterministic code in `client/ui/main_window.py`, not OpenAI.
- Auto spelling is now debounced by a 1.5 second single-shot `QTimer`; eligible realtime text schedules the check instead of running immediately.
- Moving between mixed-DPI monitors caused reported render/click mismatch. `configure_high_dpi_scaling()` now requests Windows Per-Monitor DPI Awareness v2 before `QApplication` creation, with older Windows fallbacks.
- DPI change only takes effect after a full app restart because process DPI awareness must be set before Qt starts.

2026-05-22 feature completion pass:

- Replaced remaining dummy analysis paths for summary, evaluation, title recommendation, and tone conversion.
- Added public server endpoints:
  - `/summary-public`
  - `/evaluation-public`
  - `/title-public`
  - `/tone-public`
- `server/ai_service.py` now has per-feature OpenAI calls using the existing model env names.
- `client/core/ai_client.py` and `client/core/analyzer.py` now call those real endpoints.
- UI button handlers now ensure the local server is available before calling those features.
- Verified with compile/import checks and FastAPI `TestClient` endpoint tests using monkeypatched AI service methods, not live OpenAI calls.

2026-05-22 title crash fix:

- User reported the app exits when using title recommendation.
- `.logs/local_server.log` showed the running local server returned `404 Not Found` for `/title-public`, meaning an older uvicorn process was still bound to port 8765.
- Added required endpoint compatibility checks to `client/core/local_server.py`; if an outdated local server is detected on Windows, the app attempts to stop the listening process before starting the current server.
- Wrapped summary/evaluation/title/tone button handlers in try/except so API/server errors show a notice instead of escaping the Qt slot.

2026-05-22 nonblocking AI/UI follow-up:

- The settings button is intentionally a 40x40 header icon button with a 20x20 rendered/tinted icon; prefer `settings.svg` over `settings.png`.
- Summary, evaluation, title recommendation, and tone conversion buttons now go through `run_ai_feature_async()` instead of calling OpenAI-backed analyzer methods on the UI thread.
- Each AI feature request runs in a daemon worker thread, emits results through `ai_feature_signal`, and ignores stale responses when the source text/request id has changed.
- Background workers instantiate their own `TextAnalyzer` so OpenAI response metadata is not shared across concurrent UI-triggered jobs.
- History save/sync calls from spelling and AI feature completions now use `save_history_log_async()` to avoid blocking the UI after an AI response arrives.
- This is not full server-push yet. It is a safer intermediate design: the client fires a local background job and resumes immediately; the UI updates only when the Qt signal returns with a result.

2026-05-22 text tab move/render freeze follow-up:

- User reported a severe render freeze when moving the window while the Text tab is visible.
- Likely risk points were the frameless manual drag loop (`self.move()` on every mouse move) and Text-tab-only info feed height recalculation during resize/screen changes.
- `ResultPanel.mousePressEvent()` now tries Qt's native `windowHandle().startSystemMove()` first so Windows/Qt handle monitor/DPI transitions instead of the app manually moving every mouse event.
- Info feed height refresh is now deferred with `schedule_info_feed_height_refresh()` and guarded against re-entry; it only changes fixed height when the target height actually differs.
- Move/screen-change events schedule a throttled `refresh_render_surfaces()` that updates QTextEdit viewports and the card after moving between screens.

2026-05-22 cheaper model follow-up:

- User reported token/cost usage is too high.
- `server/.env` model settings were changed so correction, summary, evaluation, title, and tone all use `gpt-5-nano`.
- `server/ai_service.py` fallback defaults were also changed to `gpt-5-nano` for every feature, so missing env values stay on the cheap model.
- Official OpenAI pricing checked on 2026-05-22: `gpt-5-nano` is much cheaper than `gpt-5-mini`, and far cheaper than `gpt-5.2`; quality may drop on nuanced evaluation/tone rewriting.

2026-05-28 product rebuild pass:

- User asked to stop being bound by earlier patch-by-patch decisions and rebuild the program from a product/developer perspective.
- Created `memo/PRODUCT_REBUILD_PLAN.md` for future Codex passes.
- First rebuild target was the AI pipeline because it affects cost, quality, freezing, and extensibility.
- Added `server/ai_cache.py` and rewrote `server/ai_service.py` around centralized feature specs, clean prompts, gpt-5-nano defaults, input/output caps, and persistent caching.
- Rewrote `client/core/ai_client.py` and `client/core/analyzer.py` to remove corrupted Korean strings and keep all display formatting predictable.
- Removed unreachable old synchronous AI feature bodies from `client/ui/main_window.py`; all AI feature entrypoints now funnel into `run_ai_feature_async`.
- Cleaned `server/database.py` and made `server/auth.py` load `.env` directly.
- Fixed a BOM issue in `server/.env`; dotenv had seen `SECRET_KEY` as `\ufeffSECRET_KEY`, which could break server imports.

2026-05-29 observability pass:

- User suggested adding logging and other data collection methods to guide improvement, while calling out essential manual tests when needed.
- Added `client/core/diagnostics.py`.
- Client now writes structured JSONL diagnostics to `.logs/client_diagnostics.jsonl`.
- Installed global Python, thread, and Qt message diagnostics.
- Added a UI watchdog timer in `App`; event-loop lag over roughly 1.5 seconds logs `ui_event_loop_lag`.
- Instrumented accepted input events, spelling checks, and AI feature requests/completions/failures with timing and text references. Text references are length/line/hash, not raw text.
- Server now writes request timing to `.logs/server_requests.jsonl`.
- `server/ai_service.py` now writes AI cache hits and live AI completions to `.logs/ai_events.jsonl`.
- Essential manual tests remain: mixed-DPI window moves, real browser/Notepad/Word/HWP input capture, and source replacement in external apps.

2026-05-29 source apply feature follow-up:

- User confirmed mixed-DPI movement is usable though not perfectly smooth, HWP detection succeeds, and moving the window did not trigger AI requests.
- User asked what "원본 반영" means. In this app it means writing the generated result back into the original source app/document, not merely copying it.
- Added a "원본 반영" button to the tone tab when replacement mode is enabled.
- Added `App.apply_tone_to_source()`, reusing `OutputApplier` so tone conversion results can be applied back to browser/Notepad/Word/HWP targets.
- Logged tone source-apply unavailable/completed/failed events through client diagnostics.
- Fixed `run_spell_check_worker()` to use `TextAnalyzer.DEFAULT_SPELLING_FEEDBACK` after the analyzer cleanup renamed the old fallback constant.

2026-05-29 source apply safety follow-up:

- User reported that source apply mostly works, but whole-document replacement destroys formatting, removes line breaks, and deletes images.
- Added `_source_text` to `OutputTarget.style_info` in `App._build_output_target()` so appliers can diff the captured original against the generated result.
- Added diff-based minimal patch support in `client/input/output_applier.py` for Word and HWP.
- Word now uses `document.Range(Start, End).Text = ...` on changed ranges only when `_source_text` is available.
- HWP now maps changed character ranges to HWP positions and applies only those selected ranges.
- Broad or too-complex patches are blocked for Word/HWP instead of falling back to full-document replacement, to protect images/tables/formatting.
- This is intentionally conservative: large rewrites may fail with a notice rather than risk document damage.

2026-05-29 HWP source apply correction:

- User reported HWP source apply still behaves incorrectly.
- Added a current HWP text verification step before minimal patching. If the active HWP document no longer matches the text captured when the correction was created, source apply now fails loudly instead of editing a stale range.
- Verification tries multiple HWP scan ranges and prefers the snapshot that matches the captured source, because HWP can expose either selected text or full document text depending on current selection state.
- Changed HWP minimal patch execution from selection + InsertText to selection + Delete + InsertText + Cancel. This should reduce leftover selected blocks and prevent InsertText from inserting into the wrong selection state.
- Added per-op HWP patch logging to `.logs/hwp_replace.log` for the next manual test.

2026-05-29 HWP formatting inheritance experiment:

- User suggested the "insert after original character, then delete original character" trick because HWP tends to inherit the preceding character style.
- Implemented a safer variant first: before deleting a changed HWP range, capture the original range or adjacent character CharShape, then delete/insert and apply that CharShape to the inserted text.
- This specifically targets the document-start problem where there is no preceding character to inherit style from.
- If this still fails, next experiment is a slower per-character patch mode for short replacements.
- Correction: user clarified the trick must rely on HWP's own style inheritance, not programmatic CharShape reapplication. Removed the style-reapply path from minimal HWP patching and changed patch order to insert replacement text at the end of the original range first, then delete the original range.
- Follow-up: absolute SelectText deletion still failed near the document start. Changed the deletion phase to use the cursor after insertion: MoveLeft over the inserted text, then MoveSelLeft over the original text, then Delete. Absolute selection is now only a fallback.
- Follow-up from before/after screenshot: inserted periods in the first line were placed at the document start, which suggests HWP `SetPos(0, para, pos)` used the wrong text-list id. Minimal HWP patch now reads the active body list id via `MovePos(2)` + `GetPos()` and passes that id into all `SetPos()` calls.
- Follow-up rollback: body list id read fails in this HWP instance and length-changing patches can corrupt layout/paragraphs. Minimal HWP patch is now deliberately limited to same-length `replace` ops only. It applies those one character at a time by selecting the original character and inserting the replacement character. Insert/delete/length-changing edits fail loudly for now.
