# Feature Backlog

Last updated: 2026-05-22

This memo is for Codex to reread before implementing the next features.

## OpenAI API integration

- Real spelling/grammar correction
  - Replace fake output in `client/core/ai_client.py` and/or `server/ai_service.py`.
  - Return corrected text plus short feedback.
  - Preserve paragraphs and blank lines for source replacement.
  - Status: first implementation added on 2026-05-22.
    - `server/ai_service.py` calls OpenAI Responses API.
    - `server/main.py` exposes `/correct-public` for non-login correction.
    - authenticated `/correct` also uses the same OpenAI path and stores feedback.
    - `client/core/ai_client.py` calls `/correct-public`.
    - `client/core/analyzer.py` no longer uses fake spelling correction.
    - `client/ui/main_window.py` ensures the local server is running before spelling correction.
    - `server/ai_service.py` now supports feature-specific model environment variables:
      - `OPENAI_CORRECTION_MODEL`
      - `OPENAI_SUMMARY_MODEL`
      - `OPENAI_EVALUATION_MODEL`
      - `OPENAI_TITLE_MODEL`
      - `OPENAI_TONE_MODEL`
    - `server/database.py` accepts legacy/current local `DATABASE_URL1` as a fallback if `DATABASE_URL` is missing.
  - Verification done: `python -m compileall` for changed client/server files.
  - Verification still needed: run the app with `OPENAI_API_KEY` set and check live correction.

- Real summary
  - Generate concise Korean summaries.
  - Keep output suitable for the current Summary tab.
  - Add a length/style option later if needed.
  - Status: first OpenAI implementation added on 2026-05-22 via `/summary-public`.

- Real writing evaluation
  - Return score from 0 to 100.
  - Return short feedback explaining the score.
  - Current UI already has a score area.
  - Status: first OpenAI implementation added on 2026-05-22 via `/evaluation-public`.

- Real title recommendation
  - Generate several candidate titles or one best title.
  - Decide whether UI should show a list or only the selected title.
  - Status: first OpenAI implementation added on 2026-05-22 via `/title-public`; UI currently shows one best title.

- Real tone/style conversion
  - Use the user's requested tone.
  - Preserve meaning and structure.
  - Return only converted text unless feedback is explicitly needed.
  - Status: first OpenAI implementation added on 2026-05-22 via `/tone-public`.

- Central AI request service
  - Prefer one API path instead of separate fake client/server behavior.
  - Likely direction: desktop client calls local FastAPI server, server calls OpenAI.
  - Keep API key on the server side through environment variables.

- Structured model outputs
  - Define stable JSON-like structures for correction, summary, evaluation, title, and tone.
  - Make UI parsing simple and resilient.
  - Status: server now requests JSON objects for all five AI features and has fallback parsing for non-JSON responses.

## UI improvements

- Clean Korean UI text
  - Fix mojibake/corrupted strings in visible labels, buttons, notices, and prompts.
  - Start with `client/ui/result_panel.py` and `client/ui/main_window.py`.

- Integrated info feed
  - Status: first implementation added on 2026-05-22.
  - Text tab now includes a compact information feed under the original text.
  - Spelling correction posts status cards: running, failed, completed.
  - Clicking the spelling card opens the spelling tab without forcing tab changes.
  - Summary, evaluation, title, tone, and simple arithmetic detection can also post feed items.
  - Design intent: keep automatic analysis discoverable without stealing focus from the user's current tab.

- Improve result presentation
  - Split spelling feedback and corrected text visually.
  - Make source replacement status less intrusive.
  - Show loading/progress while OpenAI requests are running.

- Add API/error states
  - Missing API key
  - Network/API failure
  - Rate limit or quota error
  - Empty/too-long input

- Add model/settings UI
  - OpenAI model selection may be useful later.
  - Keep default simple at first.
  - Do not expose raw advanced settings until needed.

- Improve DPI/font rendering
  - Keep bundled A2Z font.
  - Continue testing high-DPI behavior and text crispness.

## Reliability and safety features

- Debounce AI requests
  - Realtime mode currently can trigger analysis as text changes.
  - Add delay/cancellation so typing does not call the API too aggressively.

- Request cancellation or stale-result guard
  - If the user changes source text while a request is running, avoid applying old results.

- Source replacement confirmation
  - Especially useful for realtime mode with Word/HWP/browser.
  - Could be optional in settings.

- History improvements
  - Store model name, feature type, and timestamp.
  - Store structured feedback fields where available.

- Diagnostics panel
  - Local server status.
  - Browser bridge status.
  - Current input mode and last captured source.
  - Last API error.

## Suggested implementation order

1. Clean the visible Korean UI strings enough to make testing readable.
2. Implement one real OpenAI API path for spelling correction.
3. Add loading/error states around AI calls.
4. Implement summary.
5. Implement evaluation and title recommendation.
6. Implement tone conversion.
7. Add request debouncing/stale-result protection.
8. Improve source replacement safety.
