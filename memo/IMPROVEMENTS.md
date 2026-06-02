# Improvement Notes

Last updated: 2026-05-22

## Current architecture

The app is a Windows desktop writing assistant with three major parts:

- PyQt5 desktop client in `client/`
- FastAPI account/history/settings server in `server/`
- Chromium browser extension bridge in `browser_extension/`

The desktop client reads text from clipboard or active windows, runs analysis, shows results in a floating panel, and can apply corrected text back to the original source window.

## Main data flow

1. `main.py` starts `client.ui.main_window.App`.
2. `App.start()` opens the panel and starts input monitoring.
3. Clipboard mode reads text through `client/input/clipboard_monitor.py`.
4. Realtime mode reads active app text through `client/input/realtime_text_monitor.py`.
5. Browser text can also arrive through the local bridge at `127.0.0.1:8766`.
6. `TextAnalyzer` currently returns fake or temporary analysis output.
7. The UI displays spelling, summary, score, title, and tone results.
8. If replace mode is enabled, `OutputApplier` can write the correction back to the source app.
9. Login, account, remote settings, and history use the FastAPI server on `127.0.0.1:8765`.

## Strong parts

- The project already has a clear desktop/server/browser split.
- The UI controller handles many user workflows:
  - login/signup
  - account update/delete
  - local and remote settings
  - history
  - text capture
  - source replacement
- Word and HWP handling are unusually advanced for a prototype.
- The browser extension bridge is a good design choice because DOM/CSS data is hard to capture from desktop UI automation alone.
- Password hashing uses PBKDF2 for new passwords and supports legacy bcrypt verification.

## Weak parts

- AI behavior is still mostly fake/test-mode.
- Many Korean strings appear corrupted in terminal output.
- Runtime files and generated files are mixed into the working tree.
- `OutputApplier` and `ai_grammary_text_reader.py` are very large modules with many responsibilities.
- The current server migration strategy is startup-time `ALTER TABLE`.
- The local browser bridge accepts cross-origin requests broadly.
- There is no visible test suite.

## Suggested refactor map

Short-term refactors:

- Move AI request/response logic behind one interface.
- Decide whether all AI requests go through `server/` or whether the client may call OpenAI directly.
- Split source replacement by target app:
  - `browser_output_applier.py`
  - `notepad_output_applier.py`
  - `word_output_applier.py`
  - `hwp_output_applier.py`
- Split active text readers by source app if future work continues.
- Centralize user-facing Korean strings after encoding cleanup.

Medium-term refactors:

- Add Alembic or a simple migration command.
- Add structured logging with log levels.
- Add integration tests for server endpoints.
- Add a small diagnostics screen to the app.
- Add packaging notes for Windows users.

Long-term ideas:

- Add a queue/state machine for analysis requests to avoid repeated analysis while users are still typing.
- Add cancellation/debounce for expensive AI calls.
- Store history with model metadata and feature version.
- Add a reversible source replacement history for undo support.
- Add per-app replacement permissions.

