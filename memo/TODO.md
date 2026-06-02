# Writing Assistant TODO

Last updated: 2026-05-22

## Priority 0: make the app safe to run and share

- Add or update `.gitignore` for generated/runtime files:
  - `__pycache__/`
  - `*.pyc`
  - `.logs/`
  - `.backup/`
  - `.auth_session.json`
  - `server/app.db`
  - `server/.env`
- Remove committed or accidentally tracked runtime artifacts from version control if they are already tracked.
- Check `server/.env` manually before sharing the repository. It may contain real secrets or DB credentials.
- Decide whether `server/app.db` is sample data or local runtime data. If it is runtime data, keep it out of git.

## Priority 1: restore real AI behavior

- Replace the temporary AI implementation in `server/ai_service.py`.
  - Current behavior returns the input text with a test-mode suffix.
  - The commented OpenAI client initialization should be restored or replaced with the selected production path.
- Replace the client-side fake analyzer path in `client/core/ai_client.py`.
  - Current behavior uses `fake_spell_check` and `fake_summary`.
  - Decide whether AI calls should happen only through the FastAPI server or directly in the client.
- Remove temporary result markers from `client/core/analyzer.py` once real model output is used.
- Define stable response schemas for:
  - spelling correction
  - spelling feedback
  - summary
  - score/evaluation
  - title recommendation
  - tone conversion

## Priority 2: encoding and Korean text cleanup

- Audit files containing Korean UI/API strings.
  - Several messages appear mojibake in terminal output.
  - Confirm whether this is only PowerShell output encoding or actual file corruption.
- Rewrite corrupted visible UI strings in UTF-8.
- Rewrite corrupted server error messages in UTF-8.
- Rewrite README text in clean UTF-8 Korean.
- Consider adding a short encoding note for Windows contributors.

## Priority 3: server and database cleanup

- Replace runtime schema patching in `server/main.py` with a real migration flow.
  - Current startup uses `ALTER TABLE` checks for missing columns.
  - For a project this size, Alembic or explicit migration scripts would be cleaner.
- Add validation constraints to request schemas.
  - username length and allowed characters
  - password minimum length
  - input text maximum length
  - tone length
  - title length
- Review `REMEMBER_ACCESS_TOKEN_EXPIRE_DAYS = 3650`.
  - Ten-year tokens are convenient but risky.
  - Consider refresh tokens or a shorter remember-me lifetime.
- Avoid printing token expiry on every authenticated request unless debug logging is enabled.
- Add CORS policy deliberately if the server ever needs browser access beyond local development.

## Priority 4: desktop client reliability

- Add graceful shutdown for background monitor threads where possible.
- Add port-conflict handling for:
  - FastAPI server on `127.0.0.1:8765`
  - browser extension bridge on `127.0.0.1:8766`
- Make server startup errors visible with enough detail for debugging.
- Consider a settings screen item showing whether the local server and browser bridge are running.
- Review current default/current user settings before demos:
  - `input_mode` is `realtime`
  - `replace_mode` is `true`
  - This can modify source windows if the user clicks apply.

## Priority 5: source replacement hardening

- Add automated tests for `client/core/line_structure.py`.
- Add focused tests or manual test scripts for:
  - browser textarea replacement
  - browser contenteditable replacement
  - Notepad replacement
  - Word replacement with styles
  - HWP replacement with styles
- Add a confirmation option before replacing source text in high-risk apps.
- Log source replacement failures with a compact user-facing reason and detailed debug log separately.
- Review `client/input/output_applier.py`, which is large and high-risk.
  - Split browser, Notepad, Word, and HWP replacement into separate modules when time allows.

## Priority 6: tests and project hygiene

- Add a minimal test suite.
  - auth helpers
  - API endpoints with FastAPI TestClient
  - settings normalization
  - history payload creation
  - line structure preservation
- Add a simple smoke test command to README.
- Add setup instructions for:
  - Python version
  - dependencies
  - `server/.env`
  - browser extension loading
  - Windows-only dependencies
- Decide whether this repo targets only Windows. If yes, state it clearly.

