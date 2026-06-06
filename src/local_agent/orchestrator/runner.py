from __future__ import annotations

from pathlib import Path

from local_agent.orchestration.project_scanner import (
    detect_project_path_in_text,
    get_external_project_dir,
    scan_external_project,
)
from local_agent.orchestrator.policies import EscalationPolicy
from local_agent.storage.runs import RunStore
from local_agent.workflows.business_plan import run_business_plan


class OrchestratorRunner:
    def __init__(
        self,
        openai_client,
        anthropic_client,
        perplexity_client,
        prompt_dir: str | Path = "prompts",
        runs_dir: str | Path = "runs",
        confidence_threshold: float = 0.7,
        budget_cap_usd: float = 3.0,
    ):
        self.openai_client = openai_client
        self.anthropic_client = anthropic_client
        self.perplexity_client = perplexity_client
        self.prompt_dir = Path(prompt_dir)
        self.policy = EscalationPolicy(confidence_threshold=confidence_threshold)
        self.budget_cap_usd = budget_cap_usd
        self.store = RunStore(runs_dir=runs_dir)

    def _build_workspace_context(self, goal: str) -> str:
        """Scan the external project referenced in the goal (or env var) and
        return a compact text snapshot to inject into the pipeline."""
        project_dir = detect_project_path_in_text(goal) or get_external_project_dir()
        if not project_dir:
            return ""
        snapshot = scan_external_project(project_dir)
        if snapshot:
            return (
                "\n\n--- ACTUAL WORKSPACE SNAPSHOT (read from disk before this run) ---\n"
                + snapshot
                + "\n--- END WORKSPACE SNAPSHOT ---"
            )
        return ""

    def run_business_plan(self, goal: str, business_profile: str, on_stage=None) -> dict:
        workspace_context = self._build_workspace_context(goal)
        payload = run_business_plan(
            goal=goal + workspace_context,
            business_profile=business_profile,
            openai_client=self.openai_client,
            anthropic_client=self.anthropic_client,
            perplexity_client=self.perplexity_client,
            prompt_dir=self.prompt_dir,
            policy=self.policy,
            budget_cap_usd=self.budget_cap_usd,
            on_stage=on_stage,
        )
        run_path = self.store.save(workflow="business_plan", payload=payload)
        self.store.append_index(
            {
                "run_id": payload.get("run_id"),
                "workflow": "business_plan",
                "status": payload.get("status"),
                "business_profile": business_profile,
                "confidence": payload.get("confidence"),
                "total_estimated_cost_usd": payload.get("total_estimated_cost_usd"),
                "path": str(run_path),
            }
        )
        payload["run_path"] = str(run_path)
        return payload
