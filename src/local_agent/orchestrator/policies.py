from __future__ import annotations

from dataclasses import dataclass

from local_agent.orchestrator.schemas import EscalationResult


@dataclass
class EscalationPolicy:
    confidence_threshold: float = 0.7
    max_questions: int = 3

    def evaluate(self, confidence: float, assumptions: list[str], cost_estimate: float, budget_cap: float) -> EscalationResult:
        reasons: list[str] = []
        questions: list[str] = []

        if confidence < self.confidence_threshold:
            reasons.append(
                f"Confidence {confidence:.2f} is below threshold {self.confidence_threshold:.2f}."
            )
            questions.append("Do you want a second pass with tighter assumptions? (yes/no)")

        uncertain_assumptions = [
            a for a in assumptions if any(token in a.lower() for token in ("uncertain", "unknown", "assume"))
        ]
        if uncertain_assumptions:
            reasons.append("Material assumptions are uncertain and may change recommendations.")
            questions.append("Which uncertain assumption should be locked first?")

        if cost_estimate > budget_cap:
            reasons.append(
                f"Estimated run cost ${cost_estimate:.3f} exceeds budget cap ${budget_cap:.3f}."
            )
            questions.append("Increase budget cap for this run? (yes/no)")

        return EscalationResult(
            required=bool(reasons),
            reasons=reasons,
            questions=questions[: self.max_questions],
        )
