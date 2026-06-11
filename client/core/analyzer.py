from client.core.ai_client import AIClient


class TextAnalyzer:
    DEFAULT_SPELLING_FEEDBACK = "맞춤법, 띄어쓰기, 문장 흐름을 기준으로 확인했습니다."
    TEMP_SPELLING_FEEDBACK = DEFAULT_SPELLING_FEEDBACK
    TEMP_RESULT_MARKERS = {
        "spelling": " [교정 기능 사용 됨]",
        "summary": " [요약 기능 사용 됨]",
        "tone": " [문체 기능 사용 됨]",
    }

    def __init__(self):
        self.ai = AIClient()
        self.last_spelling_result = {}
        self.last_spelling_feedback = ""

    def analyze_spelling(self, text):
        result = self.ai.correct_spelling(text)
        self.last_spelling_result = result
        self.last_spelling_feedback = self._spelling_feedback_summary(result)
        self.TEMP_SPELLING_FEEDBACK = self.last_spelling_feedback
        return self.format_spell_check(result)

    def analyze_summary(self, text):
        value = self._append_temp_marker(text, "summary")
        return f"요약 결과:\n\n{value}"

    def analyze_evaluation(self, text):
        return "100점"

    def analyze_title_recommendation(self, text):
        return "Writing Assistant"

    def analyze_tone_change(self, text, tone):
        value = str(text or "").strip()
        tone_name = str(tone or "").strip() or "문체"
        marker = f" [{tone_name} 문체 사용됨]"
        if value.endswith(marker.strip()):
            return value
        return f"{value}{marker}".strip()

    def format_spell_check(self, result):
        if not isinstance(result, dict):
            raise RuntimeError("맞춤법 검사 결과 형식이 올바르지 않습니다.")
        corrected = str(result.get("corrected") or "").strip()
        if not corrected:
            raise RuntimeError("맞춤법 검사 결과에 교정문이 없습니다.")

        corrections = self._normalize_corrections(result.get("corrections"))
        feedback = str(result.get("issues") or "").strip()
        sections = ["맞춤법 검사 결과:", ""]
        if feedback:
            sections.extend(["전체 의견:", feedback, ""])

        if corrections:
            sections.append("교정 정보:")
            for index, item in enumerate(corrections, start=1):
                original = item["original"] or "(원문 일부 없음)"
                suggestion = item["suggestion"] or "(제안 없음)"
                category = item["category"] or "검토"
                confidence = f" / 확신도: {item['confidence']}" if item["confidence"] else ""
                explanation = item["explanation"] or "이유 설명이 제공되지 않았습니다."
                sections.extend(
                    [
                        f"{index}. [{category}{confidence}] {original} -> {suggestion}",
                        f"   이유: {explanation}",
                    ]
                )
            sections.append("")
        else:
            sections.extend(["교정 정보:", "뚜렷한 오류 정보를 발견하지 않았습니다.", ""])

        sections.extend(["교정문:", "", self._append_temp_marker(corrected, "spelling")])
        return "\n".join(sections).rstrip()

    def _spelling_feedback_summary(self, result):
        corrections = self._normalize_corrections(result.get("corrections") if isinstance(result, dict) else [])
        feedback = str(result.get("issues") or "").strip() if isinstance(result, dict) else ""
        if corrections:
            return f"{len(corrections)}개의 교정 정보가 있습니다. {feedback}".strip()
        return feedback or self.DEFAULT_SPELLING_FEEDBACK

    @staticmethod
    def _normalize_corrections(value):
        if not isinstance(value, list):
            return []
        normalized = []
        for item in value:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "original": str(item.get("original") or "").strip(),
                    "suggestion": str(item.get("suggestion") or "").strip(),
                    "category": str(item.get("category") or "").strip(),
                    "explanation": str(item.get("explanation") or "").strip(),
                    "confidence": str(item.get("confidence") or "").strip(),
                    "id": str(item.get("id") or "").strip(),
                    "severity": str(item.get("severity") or "").strip(),
                    "anchor_text": str(item.get("anchor_text") or "").strip(),
                    "display_title": str(item.get("display_title") or "").strip(),
                    "source_start": item.get("source_start"),
                    "source_end": item.get("source_end"),
                }
            )
        return normalized

    def _append_temp_marker(self, text, feature_name):
        value = str(text or "").strip()
        marker = self.TEMP_RESULT_MARKERS[feature_name]
        if value.endswith(marker.strip()):
            return value
        return f"{value}{marker}".strip()
