# Writing Assistant Browser Extension

Chrome/Edge content-script bridge for Writing Assistant.

## Install for local testing

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select this `browser_extension` folder.
5. Start Writing Assistant with realtime recognition enabled.

The content script sends focused browser editor text to the extension background worker.
The background worker then forwards it to `http://127.0.0.1:8766/capture`.
Click the extension icon to check local bridge health.

## Current scope

- Reads the currently focused `textarea`.
- Reads common text-like `input` controls. Password, token, OTP, card, hidden, and file inputs are ignored.
- Reads visible `contenteditable` editors and basic computed CSS segments.
- Searches open Shadow DOM for focused editors.
- Runs in all frames where Chrome allows the content script.
- Local app communication is handled by the extension background worker, not by the website context.
- Applies text back to `input` and `textarea` with `setRangeText()`.
- Applies plain text back to `contenteditable` with DOM Range replacement.

## Design rule

The extension first tries a generic active-editor detector. Site-specific adapters should be added only when this generic path fails.

## Known limits

- Cross-origin iframes are handled only when the content script is allowed to run in that frame.
- Closed Shadow DOM cannot be inspected.
- Canvas-based editors and some virtual editors such as Google Docs may require site-specific adapters.
- The desktop bridge currently accepts `textarea` and `contenteditable`; plain `input` controls are sent through the textarea path with their real kind in diagnostics.
