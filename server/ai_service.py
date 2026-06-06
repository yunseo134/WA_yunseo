import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

try:
    from ai_cache import AICache
except ImportError:
    from server.ai_cache import AICache


load_dotenv(Path(__file__).resolve().parent / ".env")


class AIService:
    DEFAULT_MODELS = {
        "correction": "gpt-5-mini",
        "summary": "gpt-5-nano",
        "evaluation": "gpt-5-nano",
        "title": "gpt-5-nano",
        "tone": "gpt-5-nano",
    }

    PROMPT_VERSION = "2026-06-yunseo-spelling-v1"

    CORRECTION_INSTRUCTIONS = (
        "You are a Korean writing tutor and editor. Correct spelling, spacing, grammar, "
        "punctuation, and awkward wording while preserving meaning, paragraph order, "
        "and blank lines. Also identify each likely issue as a learning aid. "
        "Return only valid JSON with keys corrected_text, feedback, and corrections. "
        "corrections must be an array of objects with original, suggestion, category, "
        "explanation, and confidence. Use concise Korean explanations. If there are no "
        "clear issues, return an empty corrections array."
    )

    def __init__(self):
        self._client = None
        root = Path(__file__).resolve().parents[1]
        self.event_log_path = root / ".logs" / "ai_events.jsonl"
        self.cache = AICache(
            root / ".logs" / "ai_response_cache.json",
            max_entries=self._env_int("OPENAI_CACHE_MAX_ENTRIES", 300),
        )

    @property
    def client(self):
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set in server/.env or environment.")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def model_for(self, feature: str) -> str:
        env_name = f"OPENAI_{feature.upper()}_MODEL"
        return (
            os.getenv(env_name)
            or os.getenv("OPENAI_MODEL")
            or self.DEFAULT_MODELS.get(feature)
            or "gpt-5-nano"
        ).strip()

    def correct_text(self, text: str) -> dict[str, object]:
        source_text = self._require_text(text)
        model = self.model_for("correction")
        input_text = self._build_correction_input(source_text)
        cache_key = self._cache_key("correction", model, input_text)
        started_at = time.monotonic()

        if self._cache_enabled():
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._log_ai_event(
                    "ai_cache_hit",
                    feature="correction",
                    model=model,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    **self._text_ref(source_text),
                )
                return self._normalize_correction_result(cached, source_text)

        response = self._create_correction_response(model, input_text, source_text)
        output_text = self._extract_response_text(response)
        self._raise_for_empty_or_incomplete_response(response, output_text)
        data = self._parse_json_object(output_text)
        if not data:
            self._log_ai_event(
                "ai_json_parse_failed",
                feature="correction",
                model=model,
                output_len=len(output_text),
                output_preview=output_text[:160],
                **self._text_ref(source_text),
            )
            raise RuntimeError("OpenAI correction response was not valid JSON.")

        if self._cache_enabled():
            self.cache.set(cache_key, data)
        self._log_ai_event(
            "ai_request_completed",
            feature="correction",
            model=model,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            output_len=len(output_text),
            response_status=str(getattr(response, "status", "") or ""),
            **self._text_ref(source_text),
        )
        return self._normalize_correction_result(data, source_text)

    def summarize_text(self, text: str) -> dict[str, str]:
        raise NotImplementedError("Summary AI is not enabled in this build.")

    def evaluate_text(self, text: str) -> dict[str, object]:
        raise NotImplementedError("Evaluation AI is not enabled in this build.")

    def recommend_title(self, text: str) -> dict[str, str]:
        raise NotImplementedError("Title AI is not enabled in this build.")

    def convert_tone(self, text: str, tone: str = "") -> dict[str, str]:
        raise NotImplementedError("Tone AI is not enabled in this build.")

    def _build_correction_input(self, source_text: str) -> str:
        return "\n".join(
            [
                "다음 글을 교정하고, 오류 또는 오류 가능성이 있는 부분별로 교정안과 이유를 알려 주세요.",
                "의미를 바꾸지 말고, 줄바꿈과 빈 줄을 가능한 한 보존하세요.",
                "JSON 객체만 반환하세요.",
                "",
                "원문:",
                self._trim_input(source_text),
            ]
        )

    def _create_correction_response(self, model: str, input_text: str, source_text: str):
        params = {
            "model": model,
            "instructions": self.CORRECTION_INSTRUCTIONS,
            "input": input_text,
            "max_output_tokens": self._env_int("OPENAI_MAX_OUTPUT_TOKENS", 900),
            "reasoning": {"effort": "minimal"},
            "text": {
                "format": self._correction_schema_format(),
                "verbosity": "low",
            },
        }
        try:
            return self.client.responses.create(**params)
        except Exception as exc:
            self._log_ai_event(
                "ai_json_schema_request_failed",
                feature="correction",
                model=model,
                error_type=type(exc).__name__,
                error=str(exc)[:240],
                **self._text_ref(source_text),
            )
            params["text"] = {
                "format": {"type": "json_object"},
                "verbosity": "low",
            }
            return self.client.responses.create(**params)

    def _correction_schema_format(self) -> dict:
        return {
            "type": "json_schema",
            "name": "writing_assistant_correction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "corrected_text": {"type": "string"},
                    "feedback": {"type": "string"},
                    "corrections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string"},
                                "suggestion": {"type": "string"},
                                "category": {"type": "string"},
                                "explanation": {"type": "string"},
                                "confidence": {"type": "string"},
                            },
                            "required": [
                                "original",
                                "suggestion",
                                "category",
                                "explanation",
                                "confidence",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["corrected_text", "feedback", "corrections"],
                "additionalProperties": False,
            },
        }

    def _normalize_correction_result(self, data: dict, source_text: str) -> dict[str, object]:
        corrected_text = str(data.get("corrected_text") or "").strip()
        if not corrected_text:
            raise RuntimeError("OpenAI correction response did not include corrected_text.")
        return {
            "corrected_text": corrected_text,
            "feedback": str(data.get("feedback") or "").strip(),
            "corrections": self._normalize_corrections(data.get("corrections"), source_text),
        }

    def _normalize_corrections(self, value, source_text: str) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        corrections = []
        cursor = 0
        for item in value:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            if not (original or suggestion or explanation):
                continue
            start, end = self._find_correction_span(source_text, original, cursor)
            if start is not None and end is not None:
                cursor = end
            category = str(item.get("category") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            correction_id = f"spell-{len(corrections) + 1:02d}"
            corrections.append(
                {
                    "id": correction_id,
                    "original": original,
                    "suggestion": suggestion,
                    "category": category,
                    "explanation": explanation,
                    "confidence": confidence,
                    "severity": self._correction_severity(category, confidence),
                    "source_start": start,
                    "source_end": end,
                    "anchor_text": original or suggestion,
                    "display_title": self._correction_display_title(category, original, suggestion),
                }
            )
        return corrections

    def _find_correction_span(self, source_text: str, original: str, start_at: int) -> tuple[int | None, int | None]:
        source = str(source_text or "")
        needle = str(original or "").strip()
        if not source or not needle:
            return None, None
        start = source.find(needle, max(0, start_at))
        if start < 0:
            start = source.find(needle)
        if start < 0:
            return None, None
        return start, start + len(needle)

    def _correction_severity(self, category: str, confidence: str) -> str:
        text = f"{category} {confidence}".lower()
        if any(token in text for token in ("낮", "possible", "검토", "제안")):
            return "info"
        if any(token in text for token in ("높", "확실", "오류", "맞춤법", "띄어쓰기", "문법")):
            return "warn"
        return "neutral"

    def _correction_display_title(self, category: str, original: str, suggestion: str) -> str:
        label = category or "교정"
        if original and suggestion:
            return f"{label}: {original} -> {suggestion}"
        return label

    def _extract_response_text(self, response) -> str:
        texts = []
        output_text = str(getattr(response, "output_text", "") or "")
        if output_text:
            texts.append(output_text)
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    texts.append(str(text))
                parsed = getattr(content, "parsed", None)
                if parsed:
                    try:
                        texts.append(json.dumps(parsed, ensure_ascii=False))
                    except Exception:
                        pass
        return "\n".join(part for part in texts if part).strip()

    def _raise_for_empty_or_incomplete_response(self, response, output_text: str):
        status = str(getattr(response, "status", "") or "")
        incomplete = getattr(response, "incomplete_details", None)
        error = getattr(response, "error", None)
        reason = str(getattr(incomplete, "reason", "") or "")
        error_message = str(getattr(error, "message", "") or "")
        if status == "incomplete" or reason:
            self._log_ai_event("ai_response_incomplete", status=status, reason=reason, output_len=len(output_text))
            raise RuntimeError(f"OpenAI response was incomplete: {reason or status}")
        if error_message:
            self._log_ai_event("ai_response_error", status=status, error=error_message[:240])
            raise RuntimeError(f"OpenAI response error: {error_message}")
        if not output_text:
            self._log_ai_event("ai_response_empty", status=status, output_items=len(getattr(response, "output", []) or []))
            raise RuntimeError("OpenAI returned an empty response.")

    def _parse_json_object(self, output_text: str) -> dict:
        raw_text = str(output_text or "").strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`").strip()
            if raw_text.lower().startswith("json"):
                raw_text = raw_text[4:].strip()
        if not raw_text:
            return {}
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(raw_text)
        except json.JSONDecodeError:
            if "{" not in raw_text:
                return {}
            try:
                data, _ = decoder.raw_decode(raw_text[raw_text.find("{") :])
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}

    def _trim_input(self, text: str) -> str:
        value = str(text or "")
        max_chars = self._env_int("OPENAI_MAX_INPUT_CHARS", 6000)
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return value[:max_chars].rstrip() + "\n\n[입력이 길어 앞부분만 분석했습니다.]"

    def _cache_key(self, feature: str, model: str, input_text: str) -> str:
        payload = {
            "version": self.PROMPT_VERSION,
            "feature": feature,
            "model": model,
            "input": input_text,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _text_ref(self, text: str) -> dict:
        value = str(text or "")
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        return {
            "text_len": len(value),
            "text_lines": value.count("\n") + (1 if value else 0),
            "text_hash": digest[:16],
        }

    def _log_ai_event(self, event: str, **fields):
        payload = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        try:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _cache_enabled(self) -> bool:
        return os.getenv("OPENAI_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _require_text(self, text: str) -> str:
        source_text = str(text or "")
        if not source_text.strip():
            raise ValueError("Text is required.")
        return source_text

    def _env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except Exception:
            return default
