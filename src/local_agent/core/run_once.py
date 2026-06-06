from __future__ import annotations

import yaml

from local_agent.orchestration.task_router import TaskRouter


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_agent_llm(agent_cfg: dict, task_context: str | None = None) -> dict:
    router = TaskRouter()
    resolved = router.resolve_agent_llm(agent_cfg, task_context=task_context)

    if not resolved["model"]:
        raise RuntimeError(f"No model configured for agent {agent_cfg['id']}")

    return {
        "provider": resolved["provider"],
        "model": resolved["model"],
        "options": resolved["options"],
        "system": agent_cfg["behavior"]["system_prompt"],
    }


def run_agent_task(router, agent_cfg: dict, task_prompt: str, task_context: str | None = None) -> dict:
    resolved = resolve_agent_llm(agent_cfg, task_context=task_context or task_prompt)
    system = resolved["system"]
    provider = resolved["provider"]
    model = resolved["model"]
    options = resolved["options"]

    output = router.chat(
        provider=provider,
        model=model,
        system=system,
        user=task_prompt,
        options=options,
    )
    return {
        "agent_id": agent_cfg["id"],
        "agent_name": agent_cfg["name"],
        "provider": provider,
        "model": model,
        "options": options,
        "task_prompt": task_prompt,
        "output": output,
    }