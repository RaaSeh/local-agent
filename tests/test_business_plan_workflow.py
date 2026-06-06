from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from local_agent.orchestrator.runner import OrchestratorRunner


class FakeClient:
    def __init__(self, provider: str, model: str, payloads: list[dict]):
        self.provider = provider
        self.default_model = model
        self._payloads = payloads

    def chat(self, model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1800) -> dict:
        assert model == self.default_model
        if not self._payloads:
            raise AssertionError("No payloads configured")
        payload = self._payloads.pop(0)
        return {
            "text": json.dumps(payload),
            "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            "provider": self.provider,
            "model": model,
        }


def _write_prompts(prompt_dir: Path) -> None:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for name in ("research.txt", "planner.txt", "critic.txt", "final_memo.txt"):
        (prompt_dir / name).write_text("Return JSON", encoding="utf-8")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _assert_stage_contract(stage: dict, expected_prompt_id: str, expected_provider: str, expected_model: str) -> None:
    required_keys = {
        "stage",
        "prompt_id",
        "provider",
        "model",
        "started_at",
        "finished_at",
        "output",
        "usage",
        "confidence",
        "estimated_input_tokens",
        "estimated_output_tokens",
        "estimated_total_tokens",
        "estimated_cost_usd",
        "seconds",
    }
    assert required_keys.issubset(stage)
    assert stage["prompt_id"] == expected_prompt_id
    assert stage["provider"] == expected_provider
    assert stage["model"] == expected_model
    assert stage["stage"]
    assert stage["estimated_input_tokens"] == 100
    assert stage["estimated_output_tokens"] == 60
    assert stage["estimated_total_tokens"] == 160
    assert stage["estimated_cost_usd"] > 0
    assert stage["confidence"] is not None
    assert _parse_timestamp(stage["started_at"]) <= _parse_timestamp(stage["finished_at"])
    assert stage["seconds"] >= 0


def _assert_stage_output(packet: dict, expected_summary: str) -> None:
    required_keys = {"summary", "assumptions", "evidence", "risks", "recommendations", "confidence", "next_steps"}
    assert required_keys.issubset(packet)
    assert packet["summary"] == expected_summary
    assert isinstance(packet["assumptions"], list)
    assert isinstance(packet["evidence"], list)
    assert isinstance(packet["risks"], list)
    assert isinstance(packet["recommendations"], list)
    assert isinstance(packet["next_steps"], list)
    assert isinstance(packet["confidence"], (int, float))


def test_runner_produces_expected_artifacts(tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    _write_prompts(prompt_dir)

    perplexity = FakeClient(
        provider="perplexity",
        model="sonar-pro",
        payloads=[
            {
                "summary": "Research summary",
                "assumptions": [],
                "evidence": ["Market growing"],
                "risks": ["Competition"],
                "recommendations": ["Focus on top niche"],
                "confidence": 0.8,
                "next_steps": ["Prioritize segments"],
            }
        ],
    )

    openai = FakeClient(
        provider="openai",
        model="gpt-4.1-mini",
        payloads=[
            {
                "summary": "Draft plan",
                "assumptions": ["Known pricing bands"],
                "evidence": ["Benchmark pricing"],
                "risks": ["Execution bandwidth"],
                "recommendations": ["Launch pilot in 30 days"],
                "confidence": 0.78,
                "next_steps": ["Build pilot funnel"],
            },
            {
                "summary": "Revised plan",
                "assumptions": ["Known pricing bands"],
                "evidence": ["Benchmark pricing"],
                "risks": ["Execution bandwidth"],
                "recommendations": ["Narrow ICP and sequence channels"],
                "confidence": 0.82,
                "next_steps": ["Run 2-week experiment"],
            },
        ],
    )

    anthropic = FakeClient(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        payloads=[
            {
                "summary": "Critique",
                "assumptions": ["Need stronger evidence on channel CAC"],
                "evidence": ["Missing CAC source"],
                "risks": ["Overly broad channel mix"],
                "recommendations": ["Constrain channel test matrix"],
                "confidence": 0.74,
                "next_steps": ["Add hard CAC assumptions"],
            },
            {
                "summary": "Final memo",
                "assumptions": ["Known pricing bands"],
                "evidence": ["Market growing", "Benchmark pricing"],
                "risks": ["Execution bandwidth"],
                "recommendations": ["Execute 90-day plan with staged gates"],
                "confidence": 0.8,
                "next_steps": ["Start week 1 action list"],
            },
        ],
    )

    runner = OrchestratorRunner(
        openai_client=openai,
        anthropic_client=anthropic,
        perplexity_client=perplexity,
        prompt_dir=prompt_dir,
        runs_dir=tmp_path / "runs",
        confidence_threshold=0.7,
        budget_cap_usd=3.0,
    )

    result = runner.run_business_plan(
        goal="Build a 90-day growth plan",
        business_profile="rocket_wash",
    )

    required_metadata = {
        "run_id",
        "status",
        "confidence",
        "total_estimated_cost_usd",
        "total_estimated_tokens",
        "stages",
        "escalation_questions",
        "escalation_reasons",
        "run_path",
    }
    assert required_metadata.issubset(result)
    assert result["status"] == "completed"
    assert result["run_id"]
    assert isinstance(result["confidence"], (int, float))
    assert result["total_estimated_cost_usd"] > 0
    assert result["total_estimated_tokens"] == 800

    _assert_stage_output(result["research_packet"], "Research summary")
    _assert_stage_output(result["draft_plan"], "Draft plan")
    _assert_stage_output(result["critique_packet"], "Critique")
    _assert_stage_output(result["revised_plan"], "Revised plan")
    _assert_stage_output(result["final_memo"], "Final memo")

    assert len(result["stages"]) == 5
    _assert_stage_contract(result["stages"][0], "research", "perplexity", "sonar-pro")
    _assert_stage_contract(result["stages"][1], "planner", "openai", "gpt-4.1-mini")
    _assert_stage_contract(result["stages"][2], "critic", "anthropic", "claude-sonnet-4-20250514")
    _assert_stage_contract(result["stages"][3], "planner", "openai", "gpt-4.1-mini")
    _assert_stage_contract(result["stages"][4], "final_memo", "anthropic", "claude-sonnet-4-20250514")

    total_stage_cost = sum(stage["estimated_cost_usd"] for stage in result["stages"])
    assert abs(result["total_estimated_cost_usd"] - total_stage_cost) < 0.0001
    assert result["total_estimated_tokens"] == sum(stage["estimated_total_tokens"] for stage in result["stages"])

    run_path = Path(result["run_path"])
    assert run_path.exists()


def test_runner_escalates_on_uncertain_assumption(tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    _write_prompts(prompt_dir)

    uncertain_payload = {
        "summary": "Output",
        "assumptions": ["uncertain: no pricing evidence for this segment"],
        "evidence": ["Thin evidence"],
        "risks": ["Mispricing"],
        "recommendations": ["Validate pricing"],
        "confidence": 0.9,
        "next_steps": ["Interview customers"],
    }

    runner = OrchestratorRunner(
        openai_client=FakeClient("openai", "gpt-4.1-mini", [uncertain_payload, uncertain_payload]),
        anthropic_client=FakeClient(
            "anthropic",
            "claude-sonnet-4-20250514",
            [uncertain_payload, uncertain_payload],
        ),
        perplexity_client=FakeClient("perplexity", "sonar-pro", [uncertain_payload]),
        prompt_dir=prompt_dir,
        runs_dir=tmp_path / "runs",
        confidence_threshold=0.6,
        budget_cap_usd=3.0,
    )

    result = runner.run_business_plan(goal="Test escalation", business_profile="rocket_wash")

    assert result["status"] == "needs_input"
    assert result["escalation_questions"]
    assert result["escalation_reasons"]
    assert result["total_estimated_cost_usd"] > 0
    assert result["total_estimated_tokens"] > 0
