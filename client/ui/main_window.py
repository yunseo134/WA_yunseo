import json
import re
import ctypes
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pyperclip
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase, QGuiApplication
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QStyle, QSystemTrayIcon

from client.app_settings import DEFAULT_SETTINGS, load_app_settings, save_app_settings
from client.core.auth_api_client import AuthAPIClient, UnauthorizedError
from client.core.analyzer import TextAnalyzer
from client.core.diagnostics import (
    DiagnosticLogger,
    install_global_diagnostics,
    install_qt_message_logging,
)
from client.core.line_structure import preserve_replacement_structure
from client.core.local_server import LocalServer
from client.input.clipboard_monitor import monitor_clipboard
from client.ui.result_panel import ResultPanel


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_UI_INPUT_EVENT_LOG_PATH = _LOG_DIR / "ui_input_events.log"
_REPLACEMENT_STRUCTURE_LOG_PATH = _LOG_DIR / "replacement_structure.log"


def configure_high_dpi_scaling():
    configure_windows_dpi_awareness()
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    rounding_policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
    if rounding_policy is not None and hasattr(QGuiApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(rounding_policy.PassThrough)


def configure_windows_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class SignalBridge(QObject):
    text_signal = pyqtSignal(object)
    spell_check_signal = pyqtSignal(object)
    ai_feature_signal = pyqtSignal(object)
    auth_sync_signal = pyqtSignal(object)


class App:
    def __init__(self):
        self.diagnostics = DiagnosticLogger("client")
        install_global_diagnostics(self.diagnostics)
        configure_high_dpi_scaling()
        self.qt_app = QApplication(sys.argv)
        install_qt_message_logging(self.diagnostics)
        self.diagnostics.event("app_init", environment=self.diagnostics.environment_snapshot())
        self.load_app_font()
        self.local_server = LocalServer()
        self.api_client = AuthAPIClient()
        self._startup_server_error = ""
        self._server_started = False
        self.pending_signup_username = ""
        self.initialize_auth()

        self.settings = self.normalize_settings(load_app_settings())
        self.startup_clipboard_text = self.safe_paste()

        self.panel = ResultPanel(
            initial_dark_mode=self.settings.get("default_dark_mode", False)
        )
        self.panel.set_default_dark_mode_checked(
            self.settings.get("default_dark_mode", False)
        )
        self.panel.set_history_enabled_checked(
            self.settings.get("history_enabled", False)
        )
        self.panel.set_replace_mode_checked(
            self.settings.get("replace_mode", False)
        )
        self.panel.set_spell_scope(self.settings.get("spell_scope", "current_sentence"))

        self.analyzer = TextAnalyzer()
        self.output_applier = None
        self.last_input = ""
        self.previous_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.last_correction_scope = "current_sentence"
        self.last_correction_source_range = (0, 0)
        self.last_output_target = None
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.last_browser_extension_event_at = 0.0
        self.active_input_mode = self.settings.get("input_mode", "clipboard")
        self.clipboard_thread = None
        self.realtime_thread = None
        self.spell_check_request_id = 0
        self.spell_check_running = False
        self.spell_check_debounce_timer = QTimer()
        self.spell_check_debounce_timer.setSingleShot(True)
        self.spell_check_debounce_timer.setInterval(1500)
        self.spell_check_debounce_timer.timeout.connect(self.run_spell_check)
        self.ui_watchdog_timer = QTimer()
        self.ui_watchdog_timer.setInterval(1000)
        self.ui_watchdog_timer.timeout.connect(self.check_ui_watchdog)
        self.ui_watchdog_last_tick = time.monotonic()
        self.ai_feature_request_ids = {}
        self.spelling_info_keys = set()
        self.last_logged_keys = set()
        self.last_local_log_keys = set()

        self.signals = SignalBridge()
        self.signals.text_signal.connect(self.handle_input_event)
        self.signals.spell_check_signal.connect(self.handle_spell_check_result)
        self.signals.ai_feature_signal.connect(self.handle_ai_feature_result)
        self.signals.auth_sync_signal.connect(self.handle_background_auth_sync_result)

        self.panel.set_input_mode(self.active_input_mode)
        self.reset_session_state()

        self.panel.copy_btn.clicked.connect(self.copy_result)
        self.panel.refresh_btn.clicked.connect(self.run_spell_check)
        self.panel.apply_correction_btn.clicked.connect(self.apply_correction_to_source)
        self.panel.apply_tone_btn.clicked.connect(self.apply_tone_to_source)
        self.panel.quit_btn.clicked.connect(self.quit_app)
        self.panel.evaluate_btn.clicked.connect(lambda: self.run_ai_feature_async("evaluation"))
        self.panel.recommend_title_btn.clicked.connect(lambda: self.run_ai_feature_async("title"))
        self.panel.run_summary_btn.clicked.connect(lambda: self.run_ai_feature_async("summary"))
        self.panel.run_tone_btn.clicked.connect(lambda: self.run_ai_feature_async("tone"))
        self.panel.save_settings_btn.clicked.connect(self.save_settings)
        self.panel.close_settings_btn.clicked.connect(self.panel.close_settings_page)
        self.panel.login_btn.clicked.connect(self.handle_login_button)
        self.panel.login_submit_btn.clicked.connect(self.handle_login_submit)
        self.panel.signup_submit_btn.clicked.connect(self.handle_signup_submit)
        self.panel.account_manage_btn.clicked.connect(self.handle_account_manage_button)
        self.panel.account_verify_submit_btn.clicked.connect(self.handle_account_verify_submit)
        self.panel.account_save_btn.clicked.connect(lambda: self.handle_account_update())
        self.panel.account_name_edit_btn.clicked.connect(lambda: self.handle_account_update("display_name"))
        self.panel.account_username_edit_btn.clicked.connect(lambda: self.handle_account_update("username"))
        self.panel.account_password_edit_btn.clicked.connect(lambda: self.handle_account_update("password"))
        self.panel.account_delete_btn.clicked.connect(self.confirm_account_delete)
        self.panel.text_history_btn.clicked.connect(lambda: self.show_history(1))
        self.panel.spell_history_btn.clicked.connect(lambda: self.show_history(2))
        self.panel.summary_history_btn.clicked.connect(lambda: self.show_history(3))
        self.panel.tone_history_btn.clicked.connect(lambda: self.show_history(4))

        self.init_tray()
        self.update_login_state()
        self.ui_watchdog_timer.start()
        self.diagnostics.event(
            "app_ready",
            input_mode=self.active_input_mode,
            history_enabled=self.is_history_enabled(),
            replace_mode=self.settings.get("replace_mode", False),
        )
        QTimer.singleShot(0, self.start_restored_login_sync)

    def initialize_auth(self):
        self.api_client.try_restore_session()

    def load_app_font(self):
        font_path = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "A2Z-Medium.ttf"
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            return

        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return

        font = QFont(families[0], 10)
        font.setPointSize(10)
        font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        if hasattr(QFont, "PreferFullHinting"):
            font.setHintingPreference(QFont.PreferFullHinting)
        self.qt_app.setFont(font)

    def init_tray(self):
        tray_icon = self.qt_app.style().standardIcon(QStyle.SP_FileDialogInfoView)
        self.tray = QSystemTrayIcon(tray_icon, self.qt_app)
        self.tray.setToolTip("Writing Assistant 실행 중")
        self.tray.activated.connect(self.handle_tray_activation)

        menu = QMenu()
        show_action = QAction("열기")
        self.login_action = QAction("로그인")
        quit_action = QAction("종료")

        show_action.triggered.connect(self.show_panel)
        self.login_action.triggered.connect(self.handle_login_button)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(show_action)
        menu.addAction(self.login_action)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def start(self):
        self.reset_session_state()
        self.show_panel()

        self.clipboard_thread = threading.Thread(
            target=self.run_monitor,
            args=(self.startup_clipboard_text,),
            daemon=True,
        )
        self.clipboard_thread.start()
        self.ensure_realtime_monitor_started()

        sys.exit(self.qt_app.exec_())

    def run_monitor(self, initial_text):
        def callback(text):
            self.signals.text_signal.emit(
                {
                    "source": "clipboard",
                    "window_title": "",
                    "text": text,
                }
            )

        monitor_clipboard(callback, initial_text=initial_text)

    def run_realtime_monitor(self):
        from client.input.realtime_text_monitor import monitor_realtime_text

        def callback(event):
            self.signals.text_signal.emit(event)

        monitor_realtime_text(callback)

    def ensure_realtime_monitor_started(self):
        if self.active_input_mode != "realtime":
            return
        if self.realtime_thread and self.realtime_thread.is_alive():
            return
        self.realtime_thread = threading.Thread(
            target=self.run_realtime_monitor,
            daemon=True,
        )
        self.realtime_thread.start()

    def handle_input_event(self, event):
        if not isinstance(event, dict):
            return

        source = event.get("source", "")
        if source != self.active_input_mode:
            return

        text = event.get("text", "")
        reader_name = str(event.get("reader", "")).strip()
        self._log_input_event(event, text, reader_name)
        if source == "realtime" and reader_name.endswith("_closed"):
            self.reset_session_state()
            self.panel.set_active_window_title("")
            return
        if source == "realtime" and not text:
            if not self.last_input:
                self.panel.set_active_window_title(event.get("window_title", ""))
                self.last_output_target = None
                self.panel.show_text_unavailable_placeholder()
            return

        if self._should_ignore_blank_line_downgrade(reader_name, text):
            self._log_input_event(event, text, reader_name, note="ignored_blank_line_downgrade")
            return

        if not text or text == self.last_input:
            return

        if self._is_replacement_echo(text):
            return

        self.panel.set_active_window_title(event.get("window_title", ""))
        if reader_name == "browser_extension":
            self.last_browser_extension_event_at = time.monotonic()
        self.diagnostics.event(
            "input_text_changed",
            source=source,
            reader=reader_name,
            keyboard_recent=bool(event.get("keyboard_recent")),
            window_title=str(event.get("window_title", ""))[:120],
            **self.diagnostics.text_ref(text),
        )
        self.previous_input = self.last_input
        self.last_input = text
        self.last_corrected_text = ""
        self.last_output_target = self._build_output_target(event) if source == "realtime" else None
        self.panel.set_original_text(text)
        self.update_local_info_items(text)
        if self._should_auto_spell_check(event, reader_name):
            self.schedule_spell_check_after_idle()
        else:
            self.defer_spell_check_until_typing()

    def reset_session_state(self):
        self.spell_check_debounce_timer.stop()
        self.previous_input = ""
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.last_correction_scope = "current_sentence"
        self.last_correction_source_range = (0, 0)
        self.last_output_target = None
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.panel.reset_text_tab()
        self.panel.clear_spell_result()
        self.panel.clear_summary_result()
        self.panel.clear_tone_result()
        self.panel.set_active_window_title("")

    def copy_result(self):
        text = self.panel.get_current_text()
        if text:
            self.safe_copy(text)

    def show_panel(self):
        self.panel.showNormal()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def handle_tray_activation(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel()

    def check_ui_watchdog(self):
        now = time.monotonic()
        lag_ms = int((now - self.ui_watchdog_last_tick - 1.0) * 1000)
        self.ui_watchdog_last_tick = now
        if lag_ms >= 1500:
            self.diagnostics.event(
                "ui_event_loop_lag",
                lag_ms=lag_ms,
                active_tab=self.panel.tabs.currentIndex() if hasattr(self.panel, "tabs") else None,
                active_window=self.panel.active_window_label.text()
                if hasattr(self.panel, "active_window_label")
                else "",
            )

    def run_spell_check(self):
        self.spell_check_debounce_timer.stop()
        if not self.last_input:
            return
        scope = self.panel.get_spell_scope()
        source_text, source_start, source_end = self._spell_check_target_text(
            self.last_input,
            scope,
        )
        if not source_text.strip():
            return
        self.last_correction_source_text = source_text
        self.last_correction_scope = scope
        self.last_correction_source_range = (source_start, source_end)
        self.spell_check_request_id += 1
        request_id = self.spell_check_request_id
        source_text = self.last_correction_source_text
        self.spell_check_running = True
        self.diagnostics.event(
            "spell_check_requested",
            request_id=request_id,
            scope=scope,
            source_start=source_start,
            source_end=source_end,
            **self.diagnostics.text_ref(source_text),
        )
        self.panel.set_spell_result(f"{self._spell_scope_label(scope)} 맞춤법 검사 중입니다...")
        self.panel.set_info_item(
            "spelling",
            "맞춤법 검사",
            "검사 중입니다.",
            f"{self._spell_scope_label(scope)}을 읽고 교정 후보와 간단한 피드백을 준비하고 있습니다.",
            "neutral",
            "spell",
        )

        threading.Thread(
            target=self.run_spell_check_worker,
            args=(request_id, source_text),
            daemon=True,
        ).start()

    def schedule_spell_check_after_idle(self):
        if not self.last_input:
            return
        self.panel.set_info_item(
            "spelling",
            "맞춤법 검사",
            "입력이 끝나면 검사합니다.",
            f"마지막 키 입력 이후 약 1.5초 동안 새 입력이 없으면 {self._spell_scope_label(self.panel.get_spell_scope())} 맞춤법 검사를 시작합니다.",
            "neutral",
            "spell",
        )
        self.spell_check_debounce_timer.start()

    def _spell_check_target_text(self, text, scope):
        value = str(text or "")
        normalized_scope = scope if scope in {"current_sentence", "current_paragraph", "full_text"} else "current_sentence"
        if normalized_scope == "full_text":
            return value, 0, len(value)
        anchor = self._changed_text_anchor(value, self.previous_input)
        if normalized_scope == "current_paragraph":
            return self._current_paragraph_at(value, anchor)
        return self._current_sentence_at(value, anchor)

    def _changed_text_anchor(self, current, previous):
        if not previous or current == previous:
            return len(current)
        if current.startswith(previous):
            return len(current)
        if previous.startswith(current):
            return len(current)
        limit = min(len(current), len(previous))
        index = 0
        while index < limit and current[index] == previous[index]:
            index += 1
        return max(0, min(len(current), index))

    def _current_sentence_at(self, text, anchor):
        if not text:
            return "", 0, 0
        anchor = max(0, min(len(text), anchor))
        left_boundaries = ".?!。！？\n"
        right_boundaries = ".?!。！？\n"
        start = anchor
        while start > 0 and text[start - 1] not in left_boundaries:
            start -= 1
        end = anchor
        while end < len(text) and text[end] not in right_boundaries:
            end += 1
        if end < len(text) and text[end] in ".?!。！？":
            end += 1
        return self._trim_scoped_text(text, start, end)

    def _current_paragraph_at(self, text, anchor):
        if not text:
            return "", 0, 0
        anchor = max(0, min(len(text), anchor))
        start = text.rfind("\n\n", 0, anchor)
        start = 0 if start < 0 else start + 2
        end = text.find("\n\n", anchor)
        end = len(text) if end < 0 else end
        return self._trim_scoped_text(text, start, end)

    @staticmethod
    def _trim_scoped_text(full_text, start, end):
        start = max(0, min(len(full_text), start))
        end = max(start, min(len(full_text), end))
        while start < end and full_text[start].isspace():
            start += 1
        while end > start and full_text[end - 1].isspace():
            end -= 1
        return full_text[start:end], start, end

    @staticmethod
    def _spell_scope_label(scope):
        return {
            "current_sentence": "현재 문장",
            "current_paragraph": "현재 문단",
            "full_text": "글 전체",
        }.get(scope, "현재 문장")

    def _should_auto_spell_check(self, event, reader_name):
        if event.get("source") != "realtime":
            return True
        if reader_name == "keyboard":
            return True
        if not event.get("keyboard_recent"):
            return False
        if reader_name == "browser":
            return self._looks_like_browser_editor_text(event.get("text", ""))
        return True

    def _looks_like_browser_editor_text(self, text):
        value = str(text or "").strip()
        if not value:
            return False
        lines = [line for line in value.splitlines() if line.strip()]
        if len(value) > 1600 or len(lines) > 24:
            return False
        page_chrome_terms = {"tooltip", "shorts", "구독", "조회수", "추가 작업"}
        if sum(1 for term in page_chrome_terms if term in value.lower()) >= 2:
            return False
        return True

    def defer_spell_check_until_typing(self):
        self.spell_check_debounce_timer.stop()
        self.spell_check_request_id += 1
        self.spell_check_running = False
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.panel.clear_spell_result()
        self.panel.set_info_item(
            "spelling",
            "맞춤법 검사",
            "키보드 입력 후 실행됩니다.",
            "포커스 이동으로 읽힌 텍스트는 자동 교정하지 않습니다. 같은 창에서 직접 입력이 감지되면 검사합니다.",
            "neutral",
            "spell",
        )

    def run_spell_check_worker(self, request_id, source_text):
        started_at = time.monotonic()
        try:
            if not self.ensure_server_running_silently():
                raise RuntimeError(self._startup_server_error or "Local server is not available.")
            analyzer = TextAnalyzer()
            result = analyzer.analyze_spelling(source_text)
            spelling_feedback = (
                analyzer.last_spelling_feedback
                or analyzer.DEFAULT_SPELLING_FEEDBACK
            )
            spelling_result = analyzer.last_spelling_result or {}
            self.signals.spell_check_signal.emit(
                {
                    "request_id": request_id,
                    "source_text": source_text,
                    "result": result,
                    "spelling_feedback": spelling_feedback,
                    "corrections": spelling_result.get("corrections") or [],
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                }
            )
        except Exception as exc:
            self.diagnostics.exception(
                "spell_check_failed",
                exc,
                request_id=request_id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                **self.diagnostics.text_ref(source_text),
            )
            self.signals.spell_check_signal.emit(
                {
                    "request_id": request_id,
                    "source_text": source_text,
                    "error": str(exc),
                }
            )

    def handle_spell_check_result(self, payload):
        if not isinstance(payload, dict):
            return
        if payload.get("request_id") != self.spell_check_request_id:
            return
        if payload.get("source_text") != self.last_correction_source_text:
            return

        self.spell_check_running = False
        if payload.get("error"):
            self.last_corrected_text = ""
            self.panel.set_spell_result(f"맞춤법 검사 실패:\n\n{payload['error']}")
            self.panel.set_info_item(
                "spelling",
                "맞춤법 검사",
                "검사에 실패했습니다.",
                str(payload["error"]),
                "error",
                "spell",
            )
            self.clear_spelling_correction_info_items()
            return

        result = payload.get("result", "")
        corrections = payload.get("corrections") or []
        corrections = self._offset_corrections(corrections, self.last_correction_source_range[0])
        self.diagnostics.event(
            "spell_check_completed",
            request_id=payload.get("request_id"),
            duration_ms=payload.get("duration_ms"),
            correction_count=len(corrections),
            **self.diagnostics.text_ref(payload.get("source_text")),
        )
        self.last_corrected_text = self._extract_corrected_text(result)
        self.panel.set_spell_result(result)
        feedback = payload.get("spelling_feedback") or "교정 결과가 준비되었습니다."
        changed = self.last_corrected_text.strip() != self.last_correction_source_text.strip()
        summary = "교정 후보가 준비되었습니다." if changed else "큰 수정 사항은 없어 보입니다."
        self.panel.set_info_item(
            "spelling",
            "맞춤법 검사",
            summary,
            f"{feedback}\n\n클릭하면 교정문을 확인합니다.",
            "warn" if changed else "good",
            "spell",
        )
        self.update_spelling_correction_info_items(corrections)
        self.save_history_log_async(
            feature_type=2,
            input_text=self.last_correction_source_text,
            output_text=self.last_corrected_text,
            spelling_feedback=feedback,
        )

    def _offset_corrections(self, corrections, base_offset):
        adjusted = []
        offset = base_offset if isinstance(base_offset, int) else 0
        for item in corrections:
            if not isinstance(item, dict):
                continue
            next_item = dict(item)
            for key in ("source_start", "source_end"):
                value = next_item.get(key)
                if isinstance(value, int):
                    next_item[key] = value + offset
            adjusted.append(next_item)
        return adjusted

    def clear_spelling_correction_info_items(self):
        for key in list(self.spelling_info_keys):
            self.panel.remove_info_item(key)
        self.spelling_info_keys.clear()

    def update_spelling_correction_info_items(self, corrections):
        self.clear_spelling_correction_info_items()
        for index, item in enumerate(corrections[:5], start=1):
            if not isinstance(item, dict):
                continue
            key = f"spell_issue_{index}"
            title = item.get("category") or "교정 후보"
            original = item.get("original") or item.get("anchor_text") or "원문 일부"
            suggestion = item.get("suggestion") or "제안 없음"
            summary = f"{self._short_info_fragment(original)} -> {self._short_info_fragment(suggestion)}"
            position = self._format_source_position(item)
            confidence = item.get("confidence") or "미정"
            explanation = item.get("explanation") or "설명이 제공되지 않았습니다."
            detail = "\n".join(
                part for part in [
                    item.get("display_title") or summary,
                    position,
                    f"확실도: {confidence}",
                    explanation,
                ] if part
            )
            severity = "warn" if item.get("severity") in {"warn", "error"} else "neutral"
            self.panel.set_info_item(key, title, summary, detail, severity, "")
            self.spelling_info_keys.add(key)

    @staticmethod
    def _short_info_fragment(value, limit=18):
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)].rstrip() + "…"

    @staticmethod
    def _format_source_position(item):
        start = item.get("source_start")
        end = item.get("source_end")
        if isinstance(start, int) and isinstance(end, int) and start >= 0 and end >= start:
            return f"원문 위치: {start}-{end}"
        return ""

    def apply_correction_to_source(self):
        if not self.panel.get_replace_mode_checked():
            return
        text = self.last_corrected_text or self._extract_corrected_text(self.panel.spell_box.toPlainText())
        if not text:
            self.panel.set_spell_result("수정할 맞춤법 검사 결과가 없습니다.")
            self.panel.show_notice("원본 수정 불가", "반영할 교정문이 없습니다.")
            return

        output_applier = self.get_output_applier()
        can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
        if not can_replace:
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + "\n\n[원본 수정 실패]\n"
                + (reason or "원본 창을 찾을 수 없습니다.")
            )
            self.panel.show_notice("원본 수정 실패", reason or "원본 창을 찾을 수 없습니다.")
            self.diagnostics.event(
                "source_apply_unavailable",
                result_type="spelling",
                reason=reason or "",
            )
            return

        try:
            previous_spell_text = self.panel.spell_box.toPlainText().rstrip()
            replacement_text, replacement_source = self._correction_replacement_payload(text)
            text = self._prepare_replacement_text(replacement_text, replacement_source)
            if self._same_replacement_text(text, replacement_source):
                self.panel.show_notice("변경 사항 없음", "교정문이 원문과 같아서 수정할 내용이 없습니다.")
                self.diagnostics.event(
                    "source_apply_noop",
                    result_type="spelling",
                    target_mode=getattr(self.last_output_target, "mode", ""),
                    **self.diagnostics.text_ref(text),
                )
                return
            output_applier.apply(self.last_output_target, text)
            self.suppress_replacement_echo_text = text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            self.panel.set_spell_result(
                previous_spell_text
                + "\n\n[원본 수정 완료]\n인식 중이던 원본 창에 교정문을 반영했습니다."
            )
            self.panel.show_notice("원본 수정 완료", "교정문을 원본 창에 반영했습니다.")
            self.diagnostics.event(
                "source_apply_completed",
                result_type="spelling",
                target_mode=getattr(self.last_output_target, "mode", ""),
                **self.diagnostics.text_ref(text),
            )
        except Exception as exc:
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + f"\n\n[원본 수정 실패]\n{exc}"
            )
            self.panel.show_notice("원본 수정 실패", str(exc))
            self.diagnostics.exception(
                "source_apply_failed",
                exc,
                result_type="spelling",
                target_mode=getattr(self.last_output_target, "mode", ""),
            )

    def _correction_replacement_payload(self, corrected_text):
        source_text = self.last_input or self.last_correction_source_text
        start, end = self.last_correction_source_range
        if (
            self.last_correction_scope != "full_text"
            and source_text
            and isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(source_text)
        ):
            return source_text[:start] + str(corrected_text or "") + source_text[end:], source_text
        return str(corrected_text or ""), self.last_correction_source_text or source_text

    def apply_tone_to_source(self):
        if not self.panel.get_replace_mode_checked():
            return
        text = self.panel.tone_box.toPlainText().strip()
        if not text:
            self.panel.show_notice("원본 반영 불가", "반영할 문체 변환 결과가 없습니다.")
            return

        output_applier = self.get_output_applier()
        can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
        if not can_replace:
            self.panel.show_notice("원본 반영 실패", reason or "원본 창을 찾을 수 없습니다.")
            self.diagnostics.event(
                "source_apply_unavailable",
                result_type="tone",
                reason=reason or "",
            )
            return

        try:
            replacement_text = self._prepare_replacement_text(text, self.last_input)
            output_applier.apply(self.last_output_target, replacement_text)
            self.suppress_replacement_echo_text = replacement_text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            self.panel.set_tone_result(
                text.rstrip()
                + "\n\n[원본 반영 완료]\n인식 중이던 원본 창에 문체 변환 결과를 반영했습니다."
            )
            self.diagnostics.event(
                "source_apply_completed",
                result_type="tone",
                target_mode=getattr(self.last_output_target, "mode", ""),
                **self.diagnostics.text_ref(replacement_text),
            )
        except Exception as exc:
            self.panel.show_notice("원본 반영 실패", str(exc))
            self.diagnostics.exception(
                "source_apply_failed",
                exc,
                result_type="tone",
                target_mode=getattr(self.last_output_target, "mode", ""),
            )

    @staticmethod
    def _same_replacement_text(left: str, right: str) -> bool:
        def normalize(value: str) -> str:
            return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

        return normalize(left) == normalize(right)

    def _build_output_target(self, event):
        reader_name = str(event.get("reader", "")).strip()
        if reader_name not in {"browser", "browser_extension", "notepad", "word", "hwp"}:
            return None
        from client.input.output_applier import OutputTarget

        mode = "browser_extension" if reader_name == "browser_extension" else reader_name
        style_info = dict(event.get("style_info") or {})
        source_text = event.get("text", "") or self.last_input
        style_info.setdefault("_source_text", source_text)
        return OutputTarget(
            mode=mode,
            window_handle=event.get("window_handle"),
            window_title=event.get("window_title", ""),
            style_info=style_info,
        )

    def get_output_applier(self):
        if self.output_applier is None:
            from client.input.output_applier import OutputApplier

            self.output_applier = OutputApplier()
        return self.output_applier

    def _extract_corrected_text(self, result):
        text = str(result or "").strip()
        if not text:
            return ""
        lines = [line.rstrip() for line in text.splitlines()]
        heading_indices = [
            index for index, line in enumerate(lines)
            if line.strip().endswith(":")
        ]
        if heading_indices:
            return "\n".join(lines[heading_indices[-1] + 1:]).strip()
        return text

    def _prepare_replacement_text(self, text, source_text=None):
        target = self.last_output_target
        if target and target.mode in {"notepad", "browser", "browser_extension", "word", "hwp"}:
            source_text = source_text if source_text is not None else (self.last_correction_source_text or self.last_input)
            restored = preserve_replacement_structure(source_text, text)
            self._log_replacement_structure(source_text, text, restored, target.mode)
            return restored
        return text

    def _log_replacement_structure(self, source_text, replacement_text, restored_text, mode):
        try:
            with _REPLACEMENT_STRUCTURE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} mode={mode!r} "
                    f"source_len={len(str(source_text or ''))} source_newlines={str(source_text or '').count(chr(10))} "
                    f"replacement_len={len(str(replacement_text or ''))} replacement_newlines={str(replacement_text or '').count(chr(10))} "
                    f"restored_len={len(str(restored_text or ''))} restored_newlines={str(restored_text or '').count(chr(10))} "
                    f"source_sample={str(source_text or '')[:80]!r} replacement_sample={str(replacement_text or '')[:80]!r} "
                    f"restored_sample={str(restored_text or '')[:80]!r}\n"
                )
        except Exception:
            pass

    def _is_replacement_echo(self, text):
        if not self.suppress_replacement_echo_text:
            return False
        if time.monotonic() > self.suppress_replacement_echo_until:
            self.suppress_replacement_echo_text = ""
            self.suppress_replacement_echo_until = 0.0
            return False
        return text.strip() == self.suppress_replacement_echo_text.strip()

    def _should_ignore_blank_line_downgrade(self, reader_name, text):
        if reader_name == "browser_extension":
            return False
        if not self.last_input or "\n\n" not in self.last_input:
            return False
        if time.monotonic() - self.last_browser_extension_event_at > 10.0:
            return False
        if self.last_output_target is None or self.last_output_target.mode != "browser_extension":
            return False
        if self._content_line_count(text) != self._content_line_count(self.last_input):
            return False
        return self._blank_line_count(text) < self._blank_line_count(self.last_input)

    def _content_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if line.strip())

    def _blank_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if not line.strip())

    def _split_lines(self, text):
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def _log_input_event(self, event, text, reader_name, note=""):
        try:
            with _UI_INPUT_EVENT_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"reader={reader_name!r} source={event.get('source')!r} "
                    f"title={str(event.get('window_title') or '')[:80]!r} "
                    f"text_len={len(str(text or ''))} newlines={str(text or '').count(chr(10))} "
                    f"blank_lines={self._blank_line_count(text)} note={note!r} "
                    f"sample={str(text or '')[:120]!r}\n"
                )
        except Exception:
            pass

    def update_local_info_items(self, text):
        calculation = self._detect_simple_calculation(text)
        if calculation:
            expression, result = calculation
            self.panel.set_info_item(
                "calculation",
                "계산",
                f"{expression} = {result}",
                f"텍스트에서 간단한 수식을 발견했습니다.\n{expression} = {result}",
                "good",
                "",
            )
        else:
            self.panel.remove_info_item("calculation")

    def _detect_simple_calculation(self, text):
        match = re.search(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*([xX×*/+\-])\s*(-?\d+(?:\.\d+)?)(?![\w.])", str(text or ""))
        if not match:
            return None
        left_text, operator, right_text = match.groups()
        left = float(left_text)
        right = float(right_text)
        if operator in {"x", "X", "×", "*"}:
            value = left * right
        elif operator == "/":
            if right == 0:
                return (match.group(0), "정의할 수 없음")
            value = left / right
        elif operator == "+":
            value = left + right
        else:
            value = left - right
        result = int(value) if value.is_integer() else round(value, 6)
        return (match.group(0), result)

    def run_ai_feature_async(self, feature):
        if not self.last_input:
            return

        feature = str(feature)
        request_id = self.ai_feature_request_ids.get(feature, 0) + 1
        self.ai_feature_request_ids[feature] = request_id
        source_text = self.last_input
        tone = self.panel.tone_input.text().strip() if feature == "tone" else ""

        self.diagnostics.event(
            "ai_feature_requested",
            feature=feature,
            request_id=request_id,
            tone=tone if feature == "tone" else "",
            **self.diagnostics.text_ref(source_text),
        )
        self.show_ai_feature_pending(feature)
        threading.Thread(
            target=self.run_ai_feature_worker,
            args=(feature, request_id, source_text, tone),
            daemon=True,
        ).start()

    def show_ai_feature_pending(self, feature):
        labels = {
            "summary": ("summary", "요약", "요약 생성 중입니다.", "summary"),
            "evaluation": ("evaluation", "글 평가", "평가 중입니다.", ""),
            "title": ("title", "제목 추천", "제목을 추천하는 중입니다.", ""),
            "tone": ("tone", "문체 변환", "문체를 변환하는 중입니다.", "tone"),
        }
        key, title, summary, action = labels.get(feature, (feature, "분석", "분석 중입니다.", ""))
        self.panel.set_info_item(
            key,
            title,
            summary,
            "서버에 요청을 보냈습니다. 완료되면 이곳에 표시됩니다.",
            "neutral",
            action,
        )
        if feature == "summary":
            self.panel.set_summary_result("요약 생성 중입니다...")
        elif feature == "tone":
            self.panel.set_tone_result("문체 변환 중입니다...")

    def run_ai_feature_worker(self, feature, request_id, source_text, tone):
        started_at = time.monotonic()
        try:
            if not self.ensure_server_running_silently():
                raise RuntimeError(self._startup_server_error or "Local server is not available.")
            analyzer = TextAnalyzer()
            if feature == "summary":
                result = analyzer.analyze_summary(source_text)
                payload = {"result": result}
            elif feature == "evaluation":
                result = analyzer.analyze_evaluation(source_text)
                payload = {
                    "result": result,
                    "feedback": analyzer.last_evaluation_feedback,
                }
            elif feature == "title":
                result = analyzer.analyze_title_recommendation(source_text)
                payload = {"result": result}
            elif feature == "tone":
                result = analyzer.analyze_tone_change(source_text, tone)
                payload = {
                    "result": result,
                    "tone": tone,
                    "feedback": analyzer.last_tone_feedback,
                }
            else:
                raise RuntimeError(f"Unsupported AI feature: {feature}")
            payload.update(
                {
                    "feature": feature,
                    "request_id": request_id,
                    "source_text": source_text,
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                }
            )
            self.signals.ai_feature_signal.emit(payload)
        except Exception as exc:
            self.diagnostics.exception(
                "ai_feature_failed",
                exc,
                feature=feature,
                request_id=request_id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                **self.diagnostics.text_ref(source_text),
            )
            self.signals.ai_feature_signal.emit(
                {
                    "feature": feature,
                    "request_id": request_id,
                    "source_text": source_text,
                    "error": str(exc),
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                }
            )

    def handle_ai_feature_result(self, payload):
        if not isinstance(payload, dict):
            return
        feature = payload.get("feature", "")
        if payload.get("request_id") != self.ai_feature_request_ids.get(feature):
            return
        if payload.get("source_text") != self.last_input:
            return

        if payload.get("error"):
            self.diagnostics.event(
                "ai_feature_error_shown",
                feature=feature,
                request_id=payload.get("request_id"),
                duration_ms=payload.get("duration_ms"),
                error=payload.get("error"),
                **self.diagnostics.text_ref(payload.get("source_text")),
            )
            self.show_ai_feature_error(self._ai_feature_error_title(feature), payload["error"])
            self.panel.set_info_item(
                feature,
                self._ai_feature_title(feature),
                "실패했습니다.",
                str(payload["error"]),
                "error",
                self._ai_feature_action(feature),
            )
            return

        result = payload.get("result", "")
        self.diagnostics.event(
            "ai_feature_completed",
            feature=feature,
            request_id=payload.get("request_id"),
            duration_ms=payload.get("duration_ms"),
            result_len=len(str(result or "")),
            **self.diagnostics.text_ref(payload.get("source_text")),
        )
        if feature == "summary":
            self.panel.set_summary_result(result)
            summary_text = self._strip_result_heading(result)
            self.panel.set_info_item("summary", "요약", "요약 결과가 준비되었습니다.", summary_text, "neutral", "summary")
            self.save_history_log_async(feature_type=3, input_text=self.last_input, output_text=summary_text)
        elif feature == "evaluation":
            self.panel.set_evaluation_score(result)
            feedback = payload.get("feedback") or "글 평가가 완료되었습니다."
            self.panel.set_info_item("evaluation", "글 평가", f"현재 점수는 {result}입니다.", feedback, "neutral", "")
            self.save_history_log_async(feature_type=1, input_text=self.last_input, score=self._parse_score(result))
        elif feature == "title":
            self.panel.set_title_recommendation(result)
            self.panel.set_info_item(
                "title",
                "제목 추천",
                result,
                "추천 제목이 텍스트 탭 상단 제목 영역에 반영되었습니다.",
                "good",
                "",
            )
            self.save_history_log_async(
                feature_type=1,
                input_text=self.last_input,
                title=result,
                score=self._parse_score(self.panel.score_label.text()),
            )
        elif feature == "tone":
            self.panel.set_tone_result(result)
            feedback = payload.get("feedback") or "문체 변환이 완료되었습니다."
            self.panel.set_info_item("tone", "문체 변환", "변환 결과가 준비되었습니다.", f"{feedback}\n\n{result}", "neutral", "tone")
            self.save_history_log_async(feature_type=4, input_text=self.last_input, output_text=result, tone=payload.get("tone") or "")

    def _ai_feature_title(self, feature):
        return {
            "summary": "요약",
            "evaluation": "글 평가",
            "title": "제목 추천",
            "tone": "문체 변환",
        }.get(feature, "분석")

    def _ai_feature_error_title(self, feature):
        return f"{self._ai_feature_title(feature)} 실패"

    def _ai_feature_action(self, feature):
        return {"summary": "summary", "tone": "tone"}.get(feature, "")

    def run_summary(self):
        self.run_ai_feature_async("summary")

    def run_evaluation(self):
        self.run_ai_feature_async("evaluation")

    def run_title_recommendation(self):
        self.run_ai_feature_async("title")

    def run_tone_change(self):
        self.run_ai_feature_async("tone")

    def show_ai_feature_error(self, title, exc):
        message = str(exc)
        if "not_found" in message or "404" in message:
            message = "로컬 서버가 이전 버전으로 실행 중입니다. 앱을 완전히 종료한 뒤 다시 실행해 주세요."
        self.panel.show_notice(title, message)

    def save_history_log_async(self, **kwargs):
        if not kwargs.get("input_text"):
            return
        kwargs["notify_session"] = False
        threading.Thread(
            target=self.save_history_log,
            kwargs=kwargs,
            daemon=True,
        ).start()

    def save_history_log(
        self,
        feature_type,
        input_text,
        output_text="",
        title=None,
        score=None,
        tone=None,
        spelling_feedback=None,
        notify_session=True,
    ):
        if not input_text:
            return
        payload = {
            "feature_type": feature_type,
            "input_text": input_text,
            "output_text": output_text or "",
            "title": title or self._current_title(),
            "score": score,
            "tone": tone,
            "spelling_feedback": spelling_feedback,
        }
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
        )
        if not self.is_logged_in() or not self.is_history_enabled():
            return
        self.write_local_history_log(payload)
        if key in self.last_logged_keys:
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.create_log(payload)
            self.last_logged_keys.add(key)
        except UnauthorizedError as exc:
            if notify_session:
                self.handle_session_expired(str(exc))
        except Exception:
            pass

    def write_local_history_log(self, payload):
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
        )
        if key in self.last_local_log_keys:
            return

        log_dir = Path(__file__).resolve().parents[2] / ".logs" / "history"
        feature_names = {
            1: "text",
            2: "spelling",
            3: "summary",
            4: "tone",
        }
        feature_name = feature_names.get(payload["feature_type"], "unknown")
        log_data = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "feature_name": feature_name,
            "db_sync_enabled": self.is_logged_in() and self.is_history_enabled(),
            **payload,
        }

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{feature_name}_logs.jsonl"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_data, ensure_ascii=False) + "\n")
            self.last_local_log_keys.add(key)
        except Exception:
            pass

    def show_history(self, feature_type):
        if not self.ensure_history_available():
            return
        if not self.ensure_server_available():
            return
        try:
            logs = self.api_client.list_logs(feature_type)
            self.panel.show_history_list(feature_type, logs)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("기록 조회 실패", str(exc))

    def _current_title(self):
        title = self.panel.title_label_box.text().strip()
        return "" if title in {"제목", ""} else title

    def _strip_result_heading(self, text):
        value = str(text or "").strip()
        if "\n\n" in value:
            return value.split("\n\n", 1)[1].strip()
        return value

    def _parse_score(self, text):
        digits = "".join(ch for ch in str(text or "") if ch.isdigit())
        if not digits:
            return None
        return max(0, min(100, int(digits[:3])))

    def normalize_settings(self, settings):
        normalized = DEFAULT_SETTINGS.copy()
        if isinstance(settings, dict):
            normalized.update({key: settings[key] for key in DEFAULT_SETTINGS if key in settings})
        normalized["default_dark_mode"] = bool(normalized.get("default_dark_mode", False))
        normalized["history_enabled"] = bool(normalized.get("history_enabled", False))
        normalized["input_mode"] = (
            "clipboard" if normalized.get("input_mode") == "clipboard" else "realtime"
        )
        normalized["replace_mode"] = (
            bool(normalized.get("replace_mode", False))
            and normalized["input_mode"] == "realtime"
        )
        if normalized.get("spell_scope") not in {"current_sentence", "current_paragraph", "full_text"}:
            normalized["spell_scope"] = "current_sentence"
        return normalized

    def collect_settings_from_panel(self):
        settings = self.settings.copy()
        settings["default_dark_mode"] = self.panel.get_default_dark_mode_checked()
        settings["input_mode"] = self.panel.get_input_mode()
        settings["replace_mode"] = (
            self.panel.get_replace_mode_checked()
            and settings["input_mode"] == "realtime"
        )
        settings["spell_scope"] = self.panel.get_spell_scope()
        if self.is_logged_in():
            settings["history_enabled"] = self.panel.get_history_enabled_checked()
        return self.normalize_settings(settings)

    def apply_settings_state(self, settings, persist=True):
        previous_mode = self.active_input_mode
        self.settings = self.normalize_settings(settings)
        if persist:
            save_app_settings(self.settings)

        self.panel.set_dark_mode(self.settings["default_dark_mode"], animate=False)
        self.panel.set_default_dark_mode_checked(self.settings["default_dark_mode"])
        self.panel.set_history_enabled_checked(self.settings["history_enabled"])
        self.panel.set_input_mode(self.settings["input_mode"])
        self.panel.set_replace_mode_checked(self.settings["replace_mode"])
        self.panel.set_spell_scope(self.settings["spell_scope"])
        self.update_login_state()

        mode_changed = previous_mode != self.settings["input_mode"]
        self.active_input_mode = self.settings["input_mode"]
        self.ensure_realtime_monitor_started()
        if self.active_input_mode != "realtime":
            self.panel.set_active_window_title("")
        if mode_changed:
            self.reset_session_state()

    def ensure_server_available(self):
        if self.ensure_server_running_silently():
            return True
        self.panel.show_notice("서버 시작 실패", self._startup_server_error)
        return False

    def ensure_server_running_silently(self):
        if self._server_started:
            return True
        try:
            self.local_server.ensure_running()
            self._server_started = True
            self._startup_server_error = ""
            return True
        except Exception as exc:
            self._startup_server_error = str(exc)
            return False

    def save_remote_settings(self):
        if not self.is_logged_in():
            return False
        if not self.ensure_server_available():
            return False
        self.api_client.update_settings(self.settings)
        return True

    def load_remote_settings(self):
        if not self.is_logged_in():
            return None
        if not self.ensure_server_available():
            return None
        remote = self.api_client.get_settings()
        if not remote or not remote.get("has_settings"):
            return None
        return self.normalize_settings(remote)

    def sync_restored_login_settings(self):
        if not self.is_logged_in():
            return
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.apply_settings_state(remote_settings)
            else:
                self.save_remote_settings()
        except Exception as exc:
            self.panel.show_notice("설정 동기화 실패", str(exc))

    def start_restored_login_sync(self):
        if not self.is_logged_in():
            return
        threading.Thread(target=self.run_restored_login_sync, daemon=True).start()

    def run_restored_login_sync(self):
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.signals.auth_sync_signal.emit({"settings": remote_settings})
            else:
                self.save_remote_settings()
                self.signals.auth_sync_signal.emit({"settings": None})
        except Exception as exc:
            self.signals.auth_sync_signal.emit({"error": str(exc)})

    def handle_background_auth_sync_result(self, result):
        if not isinstance(result, dict):
            return
        if result.get("error"):
            self.panel.show_notice("설정 동기화 실패", result["error"])
            return
        remote_settings = result.get("settings")
        if remote_settings:
            self.apply_settings_state(remote_settings)
        self.refresh_account_identity()

    def save_settings(self):
        self.apply_settings_state(self.collect_settings_from_panel())
        if self.is_logged_in():
            try:
                self.save_remote_settings()
            except UnauthorizedError as exc:
                self.handle_session_expired(str(exc))
                return
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))
                return
        self.panel.show_settings_saved_notice()

    def quit_app(self):
        self.reset_session_state()
        self.tray.hide()
        self.local_server.stop()
        self.qt_app.quit()

    def logout(self):
        self.api_client.clear_token()
        save_app_settings(self.settings)
        self.panel.set_account_identity("", "")
        self.update_login_state()

    def handle_login_button(self):
        if self.is_logged_in():
            self.logout()
            return
        self.panel.show_auth_page()

    def handle_login_submit(self):
        username = self.panel.login_username_input.text().strip()
        password = self.panel.login_password_input.text().strip()
        remember_me = self.panel.login_remember_checkbox.isChecked()
        if not username or not password:
            self.panel.show_notice("입력 오류", "아이디와 비밀번호를 모두 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            local_settings = self.collect_settings_from_panel()
            self.api_client.login(username, password, remember_me)
            self.update_login_state()
            self.panel.close_auth_page()
            self.handle_login_settings_sync(username, local_settings)
            self.refresh_account_identity()
        except Exception as exc:
            self.panel.show_notice("로그인 실패", str(exc))

    def handle_signup_submit(self):
        username = self.panel.signup_username_input.text().strip()
        password = self.panel.signup_password_input.text().strip()
        password_confirm = self.panel.signup_password_confirm_input.text().strip()
        if not username or not password or not password_confirm:
            self.panel.show_notice("입력 오류", "모든 항목을 입력해 주세요.")
            return
        if password != password_confirm:
            self.panel.show_notice("입력 오류", "비밀번호가 일치하지 않습니다.")
            return
        if len(password) < 4:
            self.panel.show_notice("입력 오류", "비밀번호는 4자 이상으로 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.signup(username, password)
            self.pending_signup_username = username
            self.panel.login_username_input.setText(username)
            self.panel.login_password_input.clear()
            self.panel.show_login_form()
            self.panel.show_prompt(
                "회원가입 완료",
                "회원가입이 완료되었습니다. 로그인해 주세요.",
                yes_callback=self.panel.show_auth_page,
            )
            self.panel.prompt_no_btn.hide()
            self.panel.prompt_yes_btn.setText("확인")
        except Exception as exc:
            self.panel.show_notice("회원가입 실패", str(exc))

    def handle_login_settings_sync(self, username, local_settings):
        try:
            remote_settings = self.load_remote_settings()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("설정 동기화 실패", str(exc))
            return

        is_new_signup = username == self.pending_signup_username
        if is_new_signup or remote_settings is None:
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
                self.pending_signup_username = ""
                self.refresh_account_identity()
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))
            return

        def keep_local_settings():
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))

        def load_account_settings():
            self.apply_settings_state(remote_settings)

        self.panel.show_prompt(
            "설정 상태 유지",
            "비로그인 상태에서 사용하던 설정을 이 계정에도 유지하시겠습니까?\n"
            "유지하면 현재 설정이 계정 설정으로 저장되고, 비유지를 누르면 DB에 저장된 계정 설정을 불러옵니다.",
            yes_callback=keep_local_settings,
            no_callback=load_account_settings,
            yes_text="유지",
            no_text="비유지",
        )

    def refresh_account_identity(self):
        if not self.is_logged_in():
            return
        try:
            account = self.api_client.get_account()
            self.panel.set_account_identity(
                account.get("username", self.api_client.current_username or ""),
                account.get("display_name", ""),
            )
        except Exception:
            self.panel.set_account_identity(self.api_client.current_username or "", "")

    def handle_account_manage_button(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "로그인이 필요합니다",
                "계정 관리는 로그인 후 사용할 수 있습니다.\n지금 로그인하시겠습니까?",
                yes_callback=self.panel.show_auth_page,
            )
            return
        self.panel.show_account_verify_page()

    def handle_account_verify_submit(self):
        password = self.panel.account_verify_password_input.text().strip()
        if not password:
            self.panel.show_notice("입력 오류", "비밀번호를 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.verify_account(password)
            account = self.api_client.get_account()
            self.panel.show_account_page(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("인증 실패", str(exc))

    def handle_account_update(self, field=None):
        payload = self.panel.get_account_payload(field)
        if not payload:
            return
        if "username" in payload and not payload["username"]:
            self.panel.show_notice("입력 오류", "아이디를 입력해 주세요.")
            return
        if "password" in payload and len(payload["password"]) < 4:
            self.panel.show_notice("입력 오류", "비밀번호는 4자 이상으로 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            account = self.api_client.update_account(payload)
            self.panel.set_account_info(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
            self.update_login_state()
            self.panel.show_account_saved_notice()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("계정 수정 실패", str(exc))

    def confirm_account_delete(self):
        self.panel.show_prompt(
            "계정 탈퇴",
            "계정을 탈퇴하면 저장된 계정 정보와 기록이 삭제됩니다.\n정말 탈퇴하시겠습니까?",
            yes_callback=self.delete_account,
            yes_text="탈퇴",
            no_text="취소",
        )

    def delete_account(self):
        if not self.ensure_server_available():
            return
        try:
            self.api_client.delete_account()
            self.logout()
            self.panel.close_account_pages()
            self.panel.show_notice("계정 탈퇴 완료", "계정이 삭제되었습니다.")
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("계정 탈퇴 실패", str(exc))

    def handle_session_expired(self, message):
        self.panel.show_notice("로그인 만료", message or "다시 로그인해 주세요.")
        self.logout()

    def ensure_history_available(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "로그인이 필요합니다",
                "기록 기능은 로그인 후 사용할 수 있습니다.\n지금 로그인하시겠습니까?",
                yes_callback=self.panel.show_auth_page,
            )
            return False
        if self.is_history_enabled():
            return True
        self.panel.show_prompt(
            "기록 기능 비활성화",
            "기록을 사용하려면 설정에서 '기록 사용'을 켜 주세요.",
            yes_callback=self.panel.open_settings_tab,
        )
        return False

    def is_logged_in(self):
        return bool(self.api_client.access_token and self.api_client.current_username)

    def is_history_enabled(self):
        return bool(self.settings.get("history_enabled", False))

    def update_login_state(self):
        logged_in = self.is_logged_in()
        username = self.api_client.current_username or ""
        if hasattr(self, "panel"):
            self.panel.update_login_state(logged_in, username, self.is_history_enabled())
        if hasattr(self, "login_action"):
            self.login_action.setText("로그아웃" if logged_in else "로그인")

    def safe_paste(self, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                return pyperclip.paste()
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return ""

    def safe_copy(self, text, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                pyperclip.copy(text)
                return True
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return False


