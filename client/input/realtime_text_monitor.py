import time
from pathlib import Path

from client.input.ai_grammary_text_reader import (
    UniversalActiveTextReader,
    WORD_PROCESS_NAMES,
    get_foreground_hwnd,
    get_process_name,
    get_window_title,
)
from client.input.browser_extension_bridge import get_browser_extension_bridge
from client.input.keyboard_monitor import monitor_typed_text

try:
    import win32api
except Exception:  # pragma: no cover - optional Windows dependency
    win32api = None


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_ERROR_LOG_PATH = _LOG_DIR / "realtime_monitor_errors.log"


def monitor_realtime_text(callback, poll_interval=0.25, debug=False):
    """Poll active apps with the AI-grammary reader stack.

    The public callback contract is kept identical to writing-assistant 0.1.0:
    callback receives {"source": "realtime", "window_title": str, "text": str}.
    """
    browser_bridge = get_browser_extension_bridge()
    try:
        browser_bridge.start()
    except Exception as exc:
        _log_error("browser_bridge_start", exc)

    try:
        reader = UniversalActiveTextReader(debug=debug)
    except Exception as exc:
        _log_error("reader_init", exc)
        monitor_typed_text(lambda text: callback(_typed_text_event(text)))
        return

    input_activity = _KeyboardActivityTracker()

    while True:
        try:
            input_activity.observe()
            browser_event = browser_bridge.poll_event()
            if browser_event is not None:
                callback(input_activity.annotate_event(browser_event))
                time.sleep(poll_interval)
                continue

            if input_activity.should_skip_poll():
                time.sleep(poll_interval)
                continue

            snapshot = reader.poll_snapshot()
            if snapshot is not None:
                callback(
                    input_activity.annotate_event(
                        {
                            "source": snapshot.source,
                            "window_title": snapshot.window_title,
                            "text": snapshot.text,
                            "reader": snapshot.reader_name,
                            "window_handle": snapshot.window_handle,
                            "style_info": snapshot.style_info,
                        }
                    )
                )
        except Exception as exc:
            _log_error("reader_poll", exc)

        time.sleep(poll_interval)


class _KeyboardActivityTracker:
    RECENT_INPUT_SECONDS = 3.0
    SKIP_AFTER_KEY_SECONDS = 0.55
    KEY_RANGE = range(0x08, 0xFF)

    def __init__(self):
        self.last_key_activity = 0.0
        self.last_key_hwnd = None
        self.last_key_window_title = ""

    def should_skip_poll(self) -> bool:
        now = time.monotonic()
        return self._is_foreground_word() and now - self.last_key_activity < self.SKIP_AFTER_KEY_SECONDS

    def observe(self):
        if not self._has_keyboard_activity():
            return
        self.last_key_activity = time.monotonic()
        self.last_key_hwnd = self._foreground_hwnd()
        self.last_key_window_title = self._window_title(self.last_key_hwnd)

    def annotate_event(self, event):
        enriched = dict(event)
        event_hwnd = enriched.get("window_handle")
        if event_hwnd is None:
            event_hwnd = self._foreground_hwnd()
        enriched["keyboard_recent"] = self.is_recent_for(
            event_hwnd,
            str(enriched.get("window_title") or ""),
        )
        return enriched

    def is_recent_for(self, hwnd, window_title="") -> bool:
        if not self.last_key_activity:
            return False
        if time.monotonic() - self.last_key_activity > self.RECENT_INPUT_SECONDS:
            return False
        if hwnd and self.last_key_hwnd and int(hwnd) == int(self.last_key_hwnd):
            return True
        if not hwnd:
            current_hwnd = self._foreground_hwnd()
            if current_hwnd and self.last_key_hwnd and int(current_hwnd) == int(self.last_key_hwnd):
                return True
        normalized_title = str(window_title or "").strip()
        return bool(normalized_title and normalized_title == self.last_key_window_title)

    def _is_foreground_word(self) -> bool:
        try:
            hwnd = get_foreground_hwnd()
            return get_process_name(hwnd) in WORD_PROCESS_NAMES
        except Exception:
            return False

    def _foreground_hwnd(self):
        try:
            return get_foreground_hwnd()
        except Exception:
            return None

    def _window_title(self, hwnd):
        if not hwnd:
            return ""
        try:
            return get_window_title(hwnd)
        except Exception:
            return ""

    def _has_keyboard_activity(self) -> bool:
        if win32api is None:
            return False
        for key_code in self.KEY_RANGE:
            try:
                state = win32api.GetAsyncKeyState(key_code)
                if state & 0x8000 or state & 0x0001:
                    return True
            except Exception:
                return False
        return False


def _typed_text_event(text):
    return {
        "source": "realtime",
        "window_title": "",
        "text": text,
        "reader": "keyboard",
        "keyboard_recent": True,
    }


def _log_error(stage, exc):
    try:
        with _ERROR_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{stage}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass

