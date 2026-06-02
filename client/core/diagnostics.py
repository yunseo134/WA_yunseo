import hashlib
import json
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"


class DiagnosticLogger:
    def __init__(self, name: str, max_field_chars: int = 500):
        self.name = str(name or "app")
        self.max_field_chars = max(80, int(max_field_chars or 500))
        self.path = LOG_DIR / f"{self.name}_diagnostics.jsonl"
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields):
        payload = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "uptime_ms": int((time.monotonic() - self._started_at) * 1000),
            "event": str(event),
            **{key: self._clean_value(value) for key, value in fields.items()},
        }
        self._write(payload)

    def exception(self, event: str, exc: BaseException | None = None, **fields):
        exc = exc or sys.exc_info()[1]
        payload = dict(fields)
        if exc is not None:
            payload.update(
                {
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "traceback": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                }
            )
        self.event(event, **payload)

    def text_ref(self, text: str | None) -> dict:
        value = str(text or "")
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        return {
            "text_len": len(value),
            "text_lines": value.count("\n") + (1 if value else 0),
            "text_hash": digest[:16],
        }

    def environment_snapshot(self):
        return {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
        }

    def _write(self, payload: dict):
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as log_file:
                    log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _clean_value(self, value):
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(key): self._clean_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean_value(item) for item in value[:20]]
        text = str(value)
        if len(text) > self.max_field_chars:
            return text[: self.max_field_chars] + "...[truncated]"
        return text


def install_global_diagnostics(logger: DiagnosticLogger):
    def excepthook(exc_type, exc, tb):
        logger.exception(
            "unhandled_exception",
            exc,
            traceback="".join(traceback.format_exception(exc_type, exc, tb)),
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook

    if hasattr(threading, "excepthook"):
        original_threading_hook = threading.excepthook

        def threading_excepthook(args):
            logger.exception(
                "thread_exception",
                args.exc_value,
                thread_name=getattr(args.thread, "name", ""),
                traceback="".join(
                    traceback.format_exception(
                        args.exc_type,
                        args.exc_value,
                        args.exc_traceback,
                    )
                ),
            )
            original_threading_hook(args)

        threading.excepthook = threading_excepthook


def install_qt_message_logging(logger: DiagnosticLogger):
    try:
        from PyQt5.QtCore import qInstallMessageHandler
    except Exception:
        return

    def handler(mode, context, message):
        logger.event(
            "qt_message",
            mode=int(mode),
            message=message,
            file=getattr(context, "file", ""),
            line=getattr(context, "line", 0),
            function=getattr(context, "function", ""),
        )

    try:
        qInstallMessageHandler(handler)
    except Exception as exc:
        logger.exception("qt_message_handler_install_failed", exc)
