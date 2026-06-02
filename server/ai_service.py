import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

try:
    from ai_cache import AICache
except ImportError:
    from server.ai_cache import AICache


class AIService:
    DEFAULT_MODELS = {
        "correction": "gpt-5-mini",
        "summary": "gpt-5-nano",
        "evaluation": "gpt-5-nano",
        "title": "gpt-5-nano",
        "tone": "gpt-5-nano",
    }

    PROMPT_VERSION = "2026-05-product-ai-v4-learning-corrections"

    FEATURE_SPECS = {
        "correction": {
            "json_keys": ("corrected_text", "feedback", "corrections"),
            "instructions": (
                "You are a Korean writing tutor and editor. Correct spelling, spacing, grammar, "
                "punctuation, and awkward wording while preserving meaning, paragraph order, "
                "and blank lines. Also identify each likely issue as a learning aid. "
                "Return only valid JSON with keys corrected_text, feedback, and corrections. "
                "corrections must be an array of objects with original, suggestion, category, "
                "explanation, and confidence. Use concise Korean explanations. If there are no "
                "clear issues, return an empty corrections array."
            ),
            "task": (
                "다음 글을 교정하고, 오류 또는 오류 가능성이 있는 부분별로 교정안과 이유를 알려 주세요. "
                "의미를 바꾸지 말고 JSON 객체만 반환하세요."
            ),
        },
        "summary": {
            "json_keys": ("summary",),
            "instructions": (
                "You are a Korean writing assistant. Summarize the user's text in Korean. "
                "Preserve the writer's intent, avoid adding facts, and keep the result concise. "
                "Do not copy the source verbatim unless it is already a very short sentence. "
                "Return only valid JSON with key summary."
            ),
            "task": "다음 글의 핵심을 2~4문장으로 요약해 주세요. JSON 객체만 반환하세요.",
        },
        "evaluation": {
            "json_keys": ("score", "feedback"),
            "instructions": (
                "You are a Korean writing coach. Evaluate clarity, coherence, grammar, "
                "readability, and persuasiveness. Return only valid JSON with keys score "
                "and feedback. score must be an integer from 0 to 100. feedback must be "
                "short, practical Korean advice."
            ),
            "task": "다음 글을 평가해 주세요. JSON 객체만 반환하세요.",
        },
        "title": {
            "json_keys": ("title",),
            "instructions": (
                "You are a Korean editor. Recommend one concise title for the user's text. "
                "Return only valid JSON with key title. The title should be natural, "
                "specific, and no longer than 40 Korean characters unless necessary."
            ),
            "task": "다음 글에 어울리는 제목 하나를 추천해 주세요. JSON 객체만 반환하세요.",
        },
        "tone": {
            "json_keys": ("converted_text", "feedback"),
            "instructions": (
                "You are a Korean rewriting assistant. Rewrite the user's text into the "
                "requested tone or style while preserving meaning, facts, paragraph order, "
                "and blank lines. Return only valid JSON with keys converted_text and feedback."
            ),
            "task": "다음 글을 요청한 문체/말투로 변환해 주세요. JSON 객체만 반환하세요.",
        },
    }

    def __init__(self):
        self._client = None
        cache_path = Path(__file__).resolve().parents[1] / ".logs" / "ai_response_cache.json"
        self.event_log_path = Path(__file__).resolve().parents[1] / ".logs" / "ai_events.jsonl"
        self.cache = AICache(cache_path, max_entries=self._env_int("OPENAI_CACHE_MAX_ENTRIES", 300))

    @property
    def client(self):
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
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

    def correct_text(self, text: str) -> dict[str, str]:
        source_text = self._require_text(text)
        data = self._run_json_feature("correction", source_text)
        corrected_text = str(data.get("corrected_text") or "").strip()
        if not corrected_text:
            raise RuntimeError("OpenAI correction response did not include corrected_text.")
        return {
            "corrected_text": corrected_text,
            "feedback": str(data.get("feedback") or "").strip(),
            "corrections": self._normalize_corrections(data.get("corrections"), source_text),
        }

    def summarize_text(self, text: str) -> dict[str, str]:
        source_text = self._require_text(text)
        data = self._run_json_feature("summary", source_text)
        summary = str(data.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("OpenAI summary response did not include summary.")
        return {"summary": summary}

    def evaluate_text(self, text: str) -> dict[str, object]:
        source_text = self._require_text(text)
        data = self._run_json_feature("evaluation", source_text)
        feedback = str(data.get("feedback") or "").strip()
        if not feedback:
            raise RuntimeError("OpenAI evaluation response did not include feedback.")
        return {
            "score": self._clamp_score(data.get("score")),
            "feedback": feedback,
        }

    def recommend_title(self, text: str) -> dict[str, str]:
        source_text = self._require_text(text)
        data = self._run_json_feature("title", source_text)
        title = str(data.get("title") or "").strip().strip("\"' \n\t")
        if not title:
            raise RuntimeError("OpenAI title response did not include title.")
        return {"title": title}

    def convert_tone(self, text: str, tone: str = "") -> dict[str, str]:
        source_text = self._require_text(text)
        requested_tone = str(tone or "").strip() or "자연스럽고 읽기 쉬운 문체"
        data = self._run_json_feature("tone", source_text, {"tone": requested_tone})
        converted_text = str(data.get("converted_text") or "").strip()
        if not converted_text:
            raise RuntimeError("OpenAI tone response did not include converted_text.")
        return {
            "converted_text": converted_text,
            "feedback": str(data.get("feedback") or "").strip(),
        }

    def _run_json_feature(self, feature: str, source_text: str, extra: dict | None = None) -> dict:
        spec = self.FEATURE_SPECS[feature]
        model = self.model_for(feature)
        input_text = self._build_input(feature, spec, source_text, extra or {})
        cache_key = self._cache_key(feature, model, input_text)
        started_at = time.monotonic()

        if self._cache_enabled():
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._log_ai_event(
                    "ai_cache_hit",
                    feature=feature,
                    model=model,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    **self._text_ref(source_text),
                )
                return cached

        response = self._create_json_response(feature, spec, model, input_text, source_text)
        output_text = self._extract_response_text(response)
        self._raise_for_empty_or_incomplete_response(feature, response, output_text)
        data = self._parse_json_object(output_text)
        if not data:
            self._log_ai_event(
                "ai_json_parse_failed",
                feature=feature,
                model=model,
                output_len=len(output_text),
                output_preview=output_text[:160],
                **self._text_ref(source_text),
            )
            raise RuntimeError(f"OpenAI {feature} response was not valid JSON.")

        if self._cache_enabled():
            self.cache.set(cache_key, data)
        self._log_ai_event(
            "ai_request_completed",
            feature=feature,
            model=model,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            output_len=len(output_text),
            response_status=str(getattr(response, "status", "") or ""),
            **self._text_ref(source_text),
        )
        return data

    def _build_input(self, feature: str, spec: dict, source_text: str, extra: dict) -> str:
        trimmed_text = self._trim_input(source_text)
        lines = [spec["task"]]
        if feature == "tone":
            lines.append(f"요청 문체/말투: {extra.get('tone') or '자연스럽게'}")
        lines.extend(["", "원문:", trimmed_text])
        return "\n".join(lines)

    def _json_schema_format(self, feature: str, spec: dict) -> dict:
        properties = {}
        for key in spec["json_keys"]:
            if key == "score":
                properties[key] = {"type": "integer"}
            elif key == "corrections":
                properties[key] = {
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
                }
            else:
                properties[key] = {"type": "string"}
        return {
            "type": "json_schema",
            "name": f"writing_assistant_{feature}",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(spec["json_keys"]),
                "additionalProperties": False,
            },
        }

    def _create_json_response(self, feature: str, spec: dict, model: str, input_text: str, source_text: str):
        params = {
            "model": model,
            "instructions": spec["instructions"],
            "input": input_text,
            "max_output_tokens": self._env_int("OPENAI_MAX_OUTPUT_TOKENS", 700),
            "reasoning": {"effort": "minimal"},
            "text": {
                "format": self._json_schema_format(feature, spec),
                "verbosity": "low",
            },
        }
        try:
            return self.client.responses.create(**params)
        except Exception as exc:
            self._log_ai_event(
                "ai_json_schema_request_failed",
                feature=feature,
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

    def _raise_for_empty_or_incomplete_response(self, feature: str, response, output_text: str):
        status = str(getattr(response, "status", "") or "")
        incomplete = getattr(response, "incomplete_details", None)
        error = getattr(response, "error", None)
        reason = str(getattr(incomplete, "reason", "") or "")
        error_message = str(getattr(error, "message", "") or "")
        if status == "incomplete" or reason:
            self._log_ai_event(
                "ai_response_incomplete",
                feature=feature,
                status=status,
                reason=reason,
                output_len=len(output_text),
            )
            raise RuntimeError(f"OpenAI response was incomplete: {reason or status}")
        if error_message:
            self._log_ai_event(
                "ai_response_error",
                feature=feature,
                status=status,
                error=error_message[:240],
            )
            raise RuntimeError(f"OpenAI response error: {error_message}")
        if not output_text:
            self._log_ai_event(
                "ai_response_empty",
                feature=feature,
                status=status,
                output_items=len(getattr(response, "output", []) or []),
            )
            raise RuntimeError("OpenAI returned an empty response.")

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
            correction_id = f"spell-{len(corrections) + 1:02d}"
            category = str(item.get("category") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            anchor_text = original or suggestion
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
                    "anchor_text": anchor_text,
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

    def _clamp_score(self, value) -> int:
        try:
            score = int(round(float(value)))
        except Exception:
            return 70
        return max(0, min(100, score))

    def _env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except Exception:
            return default
