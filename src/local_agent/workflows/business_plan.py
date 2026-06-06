from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from local_agent.orchestrator.policies import EscalationPolicy
from local_agent.orchestrator.schemas import BusinessPlanResult, StageArtifact


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _read_prompt(prompt_dir: Path, name: str) -> str:
    path = prompt_dir / name
    return path.read_text(encoding="utf-8")


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != -1 and end > start:
            text = text[start + 3 : end].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    return {
        "summary": raw_text.strip(),
        "assumptions": ["uncertain: model did not return valid JSON"],
        "risks": ["Unable to parse structured output"],
        "recommendations": [],
        "confidence": 0.4,
        "next_steps": ["Re-run with tighter prompt constraints"],
    }


def _estimate_cost_usd(provider: str, usage: dict) -> float:
    # Conservative placeholders for quick operator visibility; replace with exact rates later.
    in_tokens = float(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out_tokens = float(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    if provider == "openai":
        return (in_tokens * 0.000005) + (out_tokens * 0.000015)
    if provider == "anthropic":
        return (in_tokens * 0.000006) + (out_tokens * 0.000020)
    if provider == "perplexity":
        return (in_tokens * 0.000004) + (out_tokens * 0.000012)
    return 0.0


def _estimate_token_count(usage: dict) -> int:
    in_tokens = int(float(usage.get("prompt_tokens") or usage.get("input_tokens") or 0))
    out_tokens = int(float(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
    return in_tokens + out_tokens


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_stage(
    name: str,
    prompt_id: str,
    provider_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    call_fn: Callable[[str, str, str], dict],
) -> StageArtifact:
    started_at = _utc_timestamp()
    start = time.perf_counter()
    response = call_fn(model, system_prompt, user_prompt)
    elapsed = time.perf_counter() - start
    finished_at = _utc_timestamp()
    payload = _extract_json(str(response.get("text", "")))
    usage = response.get("usage", {})
    confidence = payload.get("confidence")
    if isinstance(confidence, (int, float)):
        confidence = float(confidence)
    else:
        confidence = None
    estimated_input_tokens = int(float(usage.get("prompt_tokens") or usage.get("input_tokens") or 0))
    estimated_output_tokens = int(float(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
    estimated_total_tokens = _estimate_token_count(usage)
    estimated_cost_usd = round(_estimate_cost_usd(provider_name, usage), 4)

    return StageArtifact(
        stage=name,
        prompt_id=prompt_id,
        provider=provider_name,
        model=model,
        started_at=started_at,
        finished_at=finished_at,
        output=payload,
        usage=usage,
        confidence=confidence,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_total_tokens=estimated_total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        seconds=round(elapsed, 3),
    )


def run_business_plan(
    goal: str,
    business_profile: str,
    openai_client,
    anthropic_client,
    perplexity_client,
    prompt_dir: Path,
    policy: EscalationPolicy,
    budget_cap_usd: float,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    run_id = _utc_run_id()

    research_prompt = _read_prompt(prompt_dir, "research.txt")
    planner_prompt = _read_prompt(prompt_dir, "planner.txt")
    critic_prompt = _read_prompt(prompt_dir, "critic.txt")
    final_prompt = _read_prompt(prompt_dir, "final_memo.txt")

    def emit(stage_name: str) -> None:
        if on_stage:
            on_stage(stage_name)

    completed_stages: list[StageArtifact] = []
    current_stage_name = "research"

    try:
        emit("research")
        research = _run_stage(
            name="research",
            prompt_id="research",
            provider_name="perplexity",
            model=perplexity_client.default_model,
            system_prompt=research_prompt,
            user_prompt=f"Business profile: {business_profile}\nGoal: {goal}",
            call_fn=lambda model, system, user: perplexity_client.chat(model=model, system=system, user=user),
        )
        completed_stages.append(research)

        current_stage_name = "plan"
        emit("plan")
        plan = _run_stage(
            name="plan",
            prompt_id="planner",
            provider_name="openai",
            model=openai_client.default_model,
            system_prompt=planner_prompt,
            user_prompt=(
                f"Business profile: {business_profile}\nGoal: {goal}\nResearch packet:\n"
                + json.dumps(research.output, indent=2)
            ),
            call_fn=lambda model, system, user: openai_client.chat(model=model, system=system, user=user),
        )
        completed_stages.append(plan)

        current_stage_name = "critique"
        emit("critique")
        critique = _run_stage(
            name="critique",
            prompt_id="critic",
            provider_name="anthropic",
            model=anthropic_client.default_model,
            system_prompt=critic_prompt,
            user_prompt=(
                f"Business profile: {business_profile}\nGoal: {goal}\nDraft plan:\n"
                + json.dumps(plan.output, indent=2)
            ),
            call_fn=lambda model, system, user: anthropic_client.chat(model=model, system=system, user=user),
        )
        completed_stages.append(critique)

        current_stage_name = "revise"
        emit("revise")
        revised = _run_stage(
            name="revise",
            prompt_id="planner",
            provider_name="openai",
            model=openai_client.default_model,
            system_prompt=planner_prompt,
            user_prompt=(
                f"Business profile: {business_profile}\nGoal: {goal}\nDraft plan:\n"
                + json.dumps(plan.output, indent=2)
                + "\nCritique packet:\n"
                + json.dumps(critique.output, indent=2)
            ),
            call_fn=lambda model, system, user: openai_client.chat(model=model, system=system, user=user),
        )
        completed_stages.append(revised)

        current_stage_name = "final_memo"
        emit("final_memo")
        final = _run_stage(
            name="final_memo",
            prompt_id="final_memo",
            provider_name="anthropic",
            model=anthropic_client.default_model,
            system_prompt=final_prompt,
            user_prompt=(
                f"Business profile: {business_profile}\nGoal: {goal}\nRevised plan:\n"
                + json.dumps(revised.output, indent=2)
                + "\nResearch packet:\n"
                + json.dumps(research.output, indent=2)
            ),
            call_fn=lambda model, system, user: anthropic_client.chat(model=model, system=system, user=user),
        )
        completed_stages.append(final)

    except Exception as exc:
        partial_cost = round(
            sum(_estimate_cost_usd(s.provider, s.usage) for s in completed_stages), 4
        )
        partial_tokens = sum(_estimate_token_count(s.usage) for s in completed_stages)
        return {
            "run_id": run_id,
            "goal": goal,
            "business_profile": business_profile,
            "status": "failed",
            "failed_stage": current_stage_name,
            "error_message": (
                f"Stage '{current_stage_name}' failed: {exc}. "
                "Check provider credentials, quota, and network connectivity."
            ),
            "stages": [asdict(s) for s in completed_stages],
            "total_estimated_cost_usd": partial_cost,
            "total_estimated_tokens": partial_tokens,
            "confidence": None,
            "escalation_questions": [],
            "escalation_reasons": [],
        }

    assumptions = []
    for packet in (research.output, plan.output, critique.output, revised.output, final.output):
        assumptions.extend([str(item) for item in packet.get("assumptions", [])])

    confidence_values = []
    for packet in (research.output, plan.output, critique.output, revised.output, final.output):
        value = packet.get("confidence")
        if isinstance(value, (int, float)):
            confidence_values.append(float(value))

    overall_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.5

    total_cost = 0.0
    total_tokens = 0
    for stage in (research, plan, critique, revised, final):
        total_cost += _estimate_cost_usd(stage.provider, stage.usage)
        total_tokens += _estimate_token_count(stage.usage)
    total_cost = round(total_cost, 4)

    escalation = policy.evaluate(
        confidence=overall_confidence,
        assumptions=assumptions,
        cost_estimate=total_cost,
        budget_cap=budget_cap_usd,
    )

    status = "needs_input" if escalation.required else "completed"

    result = BusinessPlanResult(
        run_id=run_id,
        goal=goal,
        business_profile=business_profile,
        status=status,
        confidence=overall_confidence,
        stages=[research, plan, critique, revised, final],
        total_estimated_tokens=total_tokens,
        research_packet=research.output,
        draft_plan=plan.output,
        critique_packet=critique.output,
        revised_plan=revised.output,
        final_memo=final.output,
        escalation_questions=escalation.questions,
        total_estimated_cost_usd=total_cost,
    )

    as_dict = asdict(result)
    as_dict["escalation_reasons"] = escalation.reasons
    return as_dict
