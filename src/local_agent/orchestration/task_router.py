from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping

from local_agent.orchestration.registry import TaskRegistry, TaskRoute


@dataclass(frozen=True)
class TaskRouteDecision:
    task_type: str
    summary: str
    selected_agent: str
    provider: str
    model: str
    options: dict
    policy: str
    requires_supervisor: bool
    requires_confirmation: bool


class TaskRouter:
    """Deterministic routing helpers for task type and model selection.

    The router keeps the current agent defaults unless an explicit routing
    policy override is configured via environment variables.
    """

    def __init__(self, registry: TaskRegistry | None = None):
        self.registry = registry or TaskRegistry()

    def route_owner_message(
        self,
        owner_message: str,
        available_agents: Mapping[str, dict] | None = None,
    ) -> TaskRouteDecision:
        hint = self.registry.route_for(owner_message)
        selected_agent = self._select_agent(hint, available_agents)
        agent_cfg = dict(available_agents.get(selected_agent, {})) if available_agents else {"id": selected_agent, "llm": {}}
        resolved = self.resolve_agent_llm(agent_cfg, task_context=owner_message)
        return TaskRouteDecision(
            task_type=hint.task_type,
            summary=hint.summary,
            selected_agent=selected_agent,
            provider=resolved["provider"],
            model=resolved["model"],
            options=resolved["options"],
            policy=resolved.get("policy", "agent_cfg"),
            requires_supervisor=hint.requires_supervisor,
            requires_confirmation=hint.requires_confirmation,
        )

    def resolve_agent_llm(self, agent_cfg: dict, task_context: str | None = None) -> dict:
        llm_cfg = deepcopy(agent_cfg.get("llm") or {})
        agent_id = str(agent_cfg.get("id", "")).strip().lower()
        task_type = self._task_type_for_context(task_context)

        provider = str(llm_cfg.get("provider") or "").strip()
        model = str(llm_cfg.get("model") or "").strip()
        options = deepcopy(llm_cfg.get("options") or {})

        model_env = str(llm_cfg.get("model_env") or "").strip()
        if model_env:
            env_model = os.getenv(model_env, "").strip()
            if env_model:
                model = env_model

        options_env = str(llm_cfg.get("options_env") or "").strip()
        if options_env:
            raw_options = os.getenv(options_env, "").strip()
            if raw_options:
                parsed = json.loads(raw_options)
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"{options_env} must contain a JSON object")
                options = parsed

        policy_prefixes = self._policy_prefixes(agent_id, task_type)
        provider, provider_source = self._apply_env_override(provider, policy_prefixes, "PROVIDER")
        model, model_source = self._apply_env_override(model, policy_prefixes, "MODEL")

        hosted_provider_source = ""
        hosted_model_source = ""
        if self._prefer_hosted_specialist(task_type=task_type):
            if not provider_source and not provider:
                provider = os.getenv("TASK_ROUTER_SPECIALIST_PROVIDER", "openai").strip() or "openai"
                hosted_provider_source = "TASK_ROUTER_SPECIALIST_PROVIDER(default=openai)"
            if not model_source and not model:
                model = os.getenv("TASK_ROUTER_SPECIALIST_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")).strip()
                hosted_model_source = "TASK_ROUTER_SPECIALIST_MODEL(default=OPENAI_MODEL)"

        if not provider:
            provider = self._default_provider(agent_id)
        if not model:
            model = self._default_model(agent_id)

        policy = (
            model_source
            or provider_source
            or hosted_model_source
            or hosted_provider_source
            or llm_cfg.get("provider")
            or "agent_cfg"
        )
        return {
            "provider": provider,
            "model": model,
            "options": options,
            "policy": policy,
        }

    def apply_to_agent_cfg(self, agent_cfg: dict, task_context: str | None = None) -> dict:
        resolved = self.resolve_agent_llm(agent_cfg, task_context=task_context)
        updated = deepcopy(agent_cfg)
        updated.setdefault("llm", {})
        updated["llm"]["provider"] = resolved["provider"]
        updated["llm"]["model"] = resolved["model"]
        updated["llm"]["options"] = resolved["options"]
        return updated

    def _select_agent(
        self,
        hint: TaskRoute,
        available_agents: Mapping[str, dict] | None,
    ) -> str:
        if hint.recommended_agent == "none":
            return "none"

        if not available_agents:
            return hint.recommended_agent

        if hint.recommended_agent in available_agents:
            return hint.recommended_agent

        fallback_order = [
            hint.recommended_agent,
            "admin",
            "codex",
            "software_dev",
            "trade_marketing",
            "software_marketing",
            "pwasher_marketing",
            "supervisor",
        ]
        for candidate in fallback_order:
            if candidate in available_agents:
                return candidate

        return next(iter(available_agents.keys()))

    def _task_type_for_context(self, task_context: str | None) -> str:
        if not task_context:
            return ""
        return self.registry.route_for(task_context).task_type

    def _policy_prefixes(self, agent_id: str, task_type: str) -> list[str]:
        prefixes: list[str] = []

        if task_type == "planning":
            prefixes.extend(["PLANNER", "OPENAI", "ADMIN"])
        elif task_type == "supervision":
            prefixes.extend(["SUPERVISOR", "ANTHROPIC"])
        elif task_type in {"code_support", "tool_acquisition"}:
            prefixes.extend(["SOFTWARE_DEV", "CODE", "LOCAL"])
        elif task_type == "cad_rnd":
            prefixes.extend(["CAD_RND", "CAD", "CODE", "LOCAL"])
        elif task_type in {"workspace_edit", "document_export", "desktop_execution", "inspection", "general", "status"}:
            prefixes.extend(["ADMIN", "LOCAL"])
        elif task_type == "environment":
            prefixes.extend(["SOFTWARE_DEV", "CODE", "LOCAL"])

        if agent_id:
            prefixes.insert(0, agent_id.upper())

        # Preserve current behavior unless an override is explicitly configured.
        return list(dict.fromkeys(prefixes))

    def _apply_env_override(self, current_value: str, prefixes: list[str], suffix: str) -> tuple[str, str]:
        for prefix in prefixes:
            env_name = f"TASK_ROUTER_{prefix}_{suffix}"
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                return env_value, env_name
        return current_value, ""

    def _default_provider(self, agent_id: str) -> str:
        if agent_id == "admin":
            return os.getenv("ORCHESTRATOR_PRIMARY_PROVIDER", "openai")
        if agent_id == "supervisor":
            return "anthropic"
        return "ollama"

    def _prefer_hosted_specialist(self, task_type: str) -> bool:
        raw = os.getenv("TASK_ROUTER_PREFER_HOSTED_SPECIALISTS", "true").strip().lower()
        enabled = raw in {"1", "true", "yes", "on"}
        if not enabled:
            return False
        return task_type in {"code_support", "cad_rnd", "tool_acquisition", "environment"}

    def _default_model(self, agent_id: str) -> str:
        if agent_id == "admin":
            return os.getenv("ORCHESTRATOR_PRIMARY_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
        if agent_id == "supervisor":
            return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        if agent_id in {"admin", "trade_marketing", "software_marketing", "pwasher_marketing"}:
            return "mistral:latest"
        if agent_id in {"software_dev", "cad_rnd", "codex"}:
            return "llama3.1:8b"
        return "mistral:latest"