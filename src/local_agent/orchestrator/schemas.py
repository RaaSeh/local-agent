from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StageArtifact:
    stage: str
    prompt_id: str
    provider: str
    model: str
    started_at: str
    finished_at: str
    output: dict
    usage: dict = field(default_factory=dict)
    confidence: float | None = None
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    seconds: float = 0.0


@dataclass
class EscalationResult:
    required: bool
    reasons: list[str]
    questions: list[str]


@dataclass
class BusinessPlanResult:
    run_id: str
    goal: str
    business_profile: str
    status: str
    confidence: float
    stages: list[StageArtifact]
    total_estimated_tokens: int
    research_packet: dict
    draft_plan: dict
    critique_packet: dict
    revised_plan: dict
    final_memo: dict
    escalation_questions: list[str]
    total_estimated_cost_usd: float
