import requests


class AIClient:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")

    def correct_spelling(self, text):
        data = self._post("/correct-public", {"text": text})
        corrected = data.get("corrected_text")
        if not corrected:
            raise RuntimeError("맞춤법 검사 응답에 교정문이 없습니다.")
        return {
            "issues": data.get("spelling_feedback") or "",
            "corrected": corrected,
            "corrections": data.get("corrections") or [],
        }

    def summarize(self, text):
        data = self._post("/summary-public", {"text": text})
        if not data.get("summary"):
            raise RuntimeError("요약 응답이 비어 있습니다.")
        return data["summary"]

    def evaluate(self, text):
        data = self._post("/evaluation-public", {"text": text})
        return {
            "score": int(data.get("score") or 0),
            "feedback": data.get("feedback") or "",
        }

    def recommend_title(self, text):
        data = self._post("/title-public", {"text": text})
        if not data.get("title"):
            raise RuntimeError("제목 추천 응답이 비어 있습니다.")
        return data["title"]

    def convert_tone(self, text, tone):
        data = self._post("/tone-public", {"text": text, "tone": tone or ""})
        if not data.get("converted_text"):
            raise RuntimeError("문체 변환 응답이 비어 있습니다.")
        return {
            "converted_text": data["converted_text"],
            "feedback": data.get("feedback") or "",
        }

    def request(self, prompt):
        return self.correct_spelling(prompt)

    def _post(self, path, payload):
        response = requests.post(
            f"{self.base_url}{path}",
            json=payload,
            timeout=90,
        )
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response):
        try:
            data = response.json()
        except Exception:
            data = {"detail": response.text}
        if response.status_code >= 400:
            raise RuntimeError(data.get("detail", "AI request failed."))
        return data
