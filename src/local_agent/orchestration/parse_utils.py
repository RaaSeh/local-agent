from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PlanParseError(ValueError):
    raw: str
    candidate: str
    source: str = "llm"

    def preview(self, limit: int = 240) -> str:
        text = (self.candidate or self.raw or "").strip().replace("\r", " ").replace("\n", " ")
        text = " ".join(text.split())
        return text[:limit]


def extract_json_candidate(raw: str) -> str:
    candidate = (raw or "").strip()
    if "```" in candidate:
        start = candidate.find("```")
        end = candidate.rfind("```")
        if start != -1 and end > start:
            candidate = candidate[start + 3 : end].strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start : end + 1]
    return candidate
