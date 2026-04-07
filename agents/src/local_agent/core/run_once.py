from __future__ import annotations
import yaml

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_agent_task(router, agent_cfg: dict, task_prompt: str) -> dict:
    llm_cfg = agent_cfg["llm"]
    system = agent_cfg["behavior"]["system_prompt"]

    provider = llm_cfg["provider"]
    model = llm_cfg.get("model")

    # Allow model to come from env var for cloud models
    model_env = llm_cfg.get("model_env")
    if model_env:
        import os
        model = os.getenv(model_env, model)

    if not model:
        raise RuntimeError(f"No model configured for agent {agent_cfg['id']}")

    output = router.chat(provider=provider, model=model, system=system, user=task_prompt)
    return {
        "agent_id": agent_cfg["id"],
        "agent_name": agent_cfg["name"],
        "provider": provider,
        "model": model,
        "task_prompt": task_prompt,
        "output": output,
    }