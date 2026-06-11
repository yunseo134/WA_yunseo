import requests


class BetaAIClient:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")

    def run(self, text, mode="correction_cards"):
        response = requests.post(
            f"{self.base_url}/beta-public",
            json={"text": text, "mode": mode},
            timeout=120,
        )
        try:
            data = response.json()
        except Exception:
            data = {"detail": response.text}
        if response.status_code >= 400:
            raise RuntimeError(data.get("detail", "Beta AI request failed."))
        return self.normalize_result(data)

    def normalize_result(self, data):
        if not isinstance(data, dict):
            data = {}
        cards = data.get("cards") if isinstance(data.get("cards"), list) else []
        return {
            "title": str(data.get("title") or "Beta").strip(),
            "result_text": str(data.get("result_text") or "").strip(),
            "cards": [self._normalize_card(card) for card in cards if isinstance(card, dict)],
        }

    def format_result(self, data):
        data = self.normalize_result(data)
        sections = [f"[{data['title']}]", ""]
        if data["result_text"]:
            sections.extend([data["result_text"], ""])
        if data["cards"]:
            sections.append("기능 카드:")
            for card in data["cards"]:
                label = card["label"] or "카드"
                body = card["suggestion"] or card["text"] or card["reason"]
                if body:
                    sections.extend([f"- {label}", f"  {body}"])
            sections.append("")
        return "\n".join(sections).rstrip()

    @staticmethod
    def _normalize_card(card):
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
        normalized = {field: str(card.get(field) or "").strip() for field in fields}
        if not normalized["primary_action"]:
            normalized["primary_action"] = "적용" if normalized["suggestion"] else "확인"
        if not normalized["secondary_action"]:
            normalized["secondary_action"] = "무시"
        return normalized
