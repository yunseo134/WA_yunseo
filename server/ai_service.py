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
        "beta_correction_cards": "gpt-5-mini",
        "beta_sentence_polish": "gpt-5-mini",
        "beta_reply": "gpt-5-mini",
        "beta_purpose": "gpt-5-mini",
        "beta_risk": "gpt-5-mini",
        "beta_voice": "gpt-5-mini",
        "beta_temperature": "gpt-5-mini",
        "beta_oneclick": "gpt-5-mini",
    }

    PROMPT_VERSION = "2026-06-beta-tab-v2"

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

        response = self._create_json_response(
            feature="correction",
            model=model,
            source_text=source_text,
            instructions=self.CORRECTION_INSTRUCTIONS,
            input_text=input_text,
            schema_format=self._correction_schema_format(),
            max_output_tokens=self._env_int("OPENAI_MAX_OUTPUT_TOKENS", 900),
        )
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
        self._log_completed("correction", model, source_text, response, output_text, started_at)
        return self._normalize_correction_result(data, source_text)

    def summarize_text(self, text: str) -> dict[str, str]:
        raise NotImplementedError("Summary AI is not enabled in the regular feature set.")

    def evaluate_text(self, text: str) -> dict[str, object]:
        raise NotImplementedError("Evaluation AI is not enabled in the regular feature set.")

    def recommend_title(self, text: str) -> dict[str, str]:
        raise NotImplementedError("Title AI is not enabled in the regular feature set.")

    def convert_tone(self, text: str, tone: str = "") -> dict[str, str]:
        raise NotImplementedError("Tone AI is not enabled in the regular feature set.")

    def beta_showcase(self, text: str, mode: str = "correction_cards") -> dict[str, object]:
        source_text = self._require_text(text)
        mode_value = str(mode or "correction_cards").strip().lower()
        if mode_value == "showcase":
            mode_value = "correction_cards"

        configs = self._beta_mode_configs()
        config = configs.get(mode_value, configs["correction_cards"])
        feature = f"beta_{config['mode']}"
        model = self.model_for(feature)
        input_text = self._build_beta_input(source_text, config)
        cache_key = self._cache_key(feature, model, input_text)
        started_at = time.monotonic()

        if self._cache_enabled():
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict):
                self._log_ai_event(
                    "ai_cache_hit",
                    feature=feature,
                    model=model,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    **self._text_ref(source_text),
                )
                normalized = self._normalize_beta_result(cached, config["label"])
                self._log_ai_event(
                    "beta_result_normalized",
                    feature=feature,
                    model=model,
                    cached=True,
                    card_count=len(normalized.get("cards") or []),
                    result=normalized,
                    **self._text_ref(source_text),
                )
                return normalized

        response = self._create_json_response(
            feature=feature,
            model=model,
            source_text=source_text,
            instructions=(
                "You are a Korean writing-product prototype engine. Return only valid JSON. "
                "Build concrete, usable beta-feature output. Avoid generic marketing copy. "
                "Every card must be actionable and short enough for a desktop app card."
            ),
            input_text=input_text,
            schema_format=self._beta_schema_format(),
            max_output_tokens=self._env_int("OPENAI_BETA_MAX_OUTPUT_TOKENS", 1600),
        )
        output_text = self._extract_response_text(response)
        self._raise_for_empty_or_incomplete_response(response, output_text)
        data = self._parse_json_object(output_text)
        if not data:
            raise RuntimeError("OpenAI beta response was not valid JSON.")

        if self._cache_enabled():
            self.cache.set(cache_key, data)
        self._log_completed(feature, model, source_text, response, output_text, started_at)
        normalized = self._normalize_beta_result(data, config["label"])
        self._log_ai_event(
            "beta_result_normalized",
            feature=feature,
            model=model,
            card_count=len(normalized.get("cards") or []),
            result=normalized,
            **self._text_ref(source_text),
        )
        return normalized

    def _beta_mode_configs(self) -> dict[str, dict[str, str]]:
        return {
            "correction_cards": {
                "mode": "correction_cards",
                "label": "선택 교정 카드",
                "goal": (
                    "틀린 부분 또는 어색한 부분을 각각 카드로 제시한다. "
                    "각 카드는 original, suggestion, reason을 반드시 채운다. "
                    "사용자가 적용/무시/이유 보기를 누를 수 있어야 하므로 한 카드에는 하나의 수정만 넣는다."
                ),
            },
            "sentence_polish": {
                "mode": "sentence_polish",
                "label": "현재 문장 즉시 다듬기",
                "goal": (
                    "입력의 마지막 문장 또는 가장 최근에 작성한 문장을 대상으로 "
                    "더 자연스럽게, 더 공손하게, 더 짧게, 더 명확하게, 감정 덜 세게 버전을 제시한다."
                ),
            },
            "reply": {
                "mode": "reply",
                "label": "답장 초안 추천",
                "goal": (
                    "메일이나 문의 글에 바로 쓸 수 있는 답장 초안을 만든다. "
                    "정중한 답장, 거절 답장, 일정 조율, 짧게 확인만 같은 선택지를 카드로 제시한다."
                ),
            },
            "purpose": {
                "mode": "purpose",
                "label": "글 목적 선택",
                "goal": (
                    "사과문, 문의글, 공지, 메일, 과제, 리뷰, 중고거래, 커뮤니티 글 중 "
                    "어떤 목적에 가까운지 판단하고 목적별 교정 기준을 카드로 제시한다."
                ),
            },
            "risk": {
                "mode": "risk",
                "label": "위험 문장 감지",
                "goal": (
                    "공격적으로 들림, 오해 소지, 책임 회피, 개인정보 포함, 학교/회사에 부적절함을 감지한다. "
                    "문제가 되는 원문과 더 안전한 대안을 카드로 제시한다."
                ),
            },
            "voice": {
                "mode": "voice",
                "label": "내 말투 유지 교정",
                "goal": (
                    "AI가 쓴 티를 줄이고 사용자의 문장 구조와 말투를 최대한 보존한다. "
                    "단어 선택과 어색한 부분만 최소 수정한 대안을 카드로 제시한다."
                ),
            },
            "temperature": {
                "mode": "temperature",
                "label": "문장 온도계",
                "goal": (
                    "공손함, 단호함, 친근함, 공격성, 장황함을 0~100 숫자로 평가하고 "
                    "조금 더 공손하게, 덜 장황하게, 더 단호하게 같은 조절 버튼 후보를 카드로 제시한다."
                ),
            },
            "oneclick": {
                "mode": "oneclick",
                "label": "원클릭 글 정리",
                "goal": (
                    "대충 쓴 메모를 제목, 요약, 핵심 bullet, 보낼 문장으로 정리한다. "
                    "카페 글, 이메일, 공지, 과제 초안에 바로 붙일 수 있게 만든다."
                ),
            },
        }

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

    def _build_beta_input(self, source_text: str, config: dict[str, str]) -> str:
        return "\n".join(
            [
                f"베타 기능: {config['label']}",
                f"목표: {config['goal']}",
                "",
                "반환 규칙:",
                "- title: 기능 이름 또는 결과 제목",
                "- result_text: 사용자가 바로 읽을 핵심 결과",
                "- cards: 1~8개의 카드",
                "- correction_cards/risk/voice에서는 original, suggestion, reason을 최대한 채운다.",
                "- sentence_polish/reply/oneclick에서는 suggestion에 바로 쓸 문장을 넣는다.",
                "- temperature에서는 score에 숫자 또는 '항목: 점수'를 넣는다.",
                "- 버튼 라벨은 primary_action, secondary_action에 짧게 넣는다.",
                "- 원문에 없는 사실을 확정적으로 만들지 않는다.",
                "",
                "원문:",
                self._trim_input(source_text),
            ]
        )

    def _create_json_response(
        self,
        feature: str,
        model: str,
        source_text: str,
        instructions: str,
        input_text: str,
        schema_format: dict,
        max_output_tokens: int,
    ):
        params = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": "minimal"},
            "text": {"format": schema_format, "verbosity": "low"},
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
            params["text"] = {"format": {"type": "json_object"}, "verbosity": "low"}
            return self.client.responses.create(**params)

    def _correction_schema_format(self) -> dict:
        return self._json_schema(
            "writing_assistant_correction",
            {
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
                            "required": ["original", "suggestion", "category", "explanation", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["corrected_text", "feedback", "corrections"],
                "additionalProperties": False,
            },
        )

    def _beta_schema_format(self) -> dict:
        card_schema = {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "text": {"type": "string"},
                "original": {"type": "string"},
                "suggestion": {"type": "string"},
                "reason": {"type": "string"},
                "category": {"type": "string"},
                "score": {"type": "string"},
                "primary_action": {"type": "string"},
                "secondary_action": {"type": "string"},
            },
            "required": [
                "label",
                "text",
                "original",
                "suggestion",
                "reason",
                "category",
                "score",
                "primary_action",
                "secondary_action",
            ],
            "additionalProperties": False,
        }
        return self._json_schema(
            "writing_assistant_beta",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "result_text": {"type": "string"},
                    "cards": {"type": "array", "items": card_schema},
                },
                "required": ["title", "result_text", "cards"],
                "additionalProperties": False,
            },
        )

    def _json_schema(self, name: str, schema: dict) -> dict:
        return {"type": "json_schema", "name": name, "strict": True, "schema": schema}

    def _normalize_correction_result(self, data: dict, source_text: str) -> dict[str, object]:
        corrected_text = str(data.get("corrected_text") or "").strip()
        if not corrected_text:
            raise RuntimeError("OpenAI correction response did not include corrected_text.")
        return {
            "corrected_text": corrected_text,
            "feedback": str(data.get("feedback") or "").strip(),
            "corrections": self._normalize_corrections(data.get("corrections"), source_text),
        }

    def _normalize_beta_result(self, data: dict, fallback_title: str) -> dict[str, object]:
        result_text = str(data.get("result_text") or "").strip()
        cards = self._card_list(data.get("cards"), limit=8)
        return {
            "title": str(data.get("title") or fallback_title).strip(),
            "result_text": result_text,
            "cards": cards,
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
            corrections.append(
                {
                    "id": f"spell-{len(corrections) + 1:02d}",
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

    def _card_list(self, value, limit: int = 8) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        fields = (
            "label",
            "text",
            "original",
            "suggestion",
            "reason",
            "category",
            "score",
            "primary_action",
            "secondary_action",
        )
        result = []
        for item in value:
            if not isinstance(item, dict):
                continue
            card = {field: str(item.get(field) or "").strip() for field in fields}
            if not card["label"]:
                card["label"] = card["category"] or f"카드 {len(result) + 1}"
            if not card["text"]:
                card["text"] = card["suggestion"] or card["reason"] or card["original"]
            if not card["primary_action"]:
                card["primary_action"] = "적용" if card["suggestion"] else "확인"
            if not card["secondary_action"]:
                card["secondary_action"] = "무시"
            if card["label"] or card["text"]:
                result.append(card)
            if len(result) >= limit:
                break
        return result

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
        if any(token in text for token in ("possible", "가능", "제안")):
            return "info"
        if any(token in text for token in ("오류", "맞춤법", "띄어쓰기", "문법")):
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

    def _response_usage_fields(self, response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return {}

        def read(name: str):
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)

        fields = {}
        for source_name, target_name in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            value = read(source_name)
            if value is None or target_name in fields:
                continue
            try:
                fields[target_name] = int(value)
            except Exception:
                pass
        if "total_tokens" not in fields and {"input_tokens", "output_tokens"} <= fields.keys():
            fields["total_tokens"] = fields["input_tokens"] + fields["output_tokens"]
        return fields

    def _log_completed(self, feature: str, model: str, source_text: str, response, output_text: str, started_at: float):
        self._log_ai_event(
            "ai_request_completed",
            feature=feature,
            model=model,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            output_len=len(output_text),
            response_status=str(getattr(response, "status", "") or ""),
            **self._response_usage_fields(response),
            **self._text_ref(source_text),
        )

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
