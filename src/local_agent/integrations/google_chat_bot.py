from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from local_agent.config import load_env, get_env
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.router import LLMRouter

app = FastAPI(title="Local Agent Google Chat Bridge")


def _build_router() -> LLMRouter:
    load_env()
    return LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY", "")),
    )


def _load_agents(agents_dir: str = "agents") -> dict[str, dict]:
    agent_map: dict[str, dict] = {}
    for path in sorted(Path(agents_dir).glob("*.yaml")):
        cfg = load_yaml(str(path))
        agent_id = str(cfg.get("id", "")).strip()
        if not agent_id:
            continue
        agent_map[agent_id] = cfg
    return agent_map


def _allowed_users() -> set[str]:
    raw = os.getenv("CHAT_ALLOWED_USERS", "")
    emails = [x.strip().lower() for x in raw.split(",") if x.strip()]
    return set(emails)


def _validate_request_token(payload: dict) -> None:
    expected = os.getenv("GOOGLE_CHAT_VERIFICATION_TOKEN", "").strip()
    if not expected:
        return
    actual = str(payload.get("token", "")).strip()
    if actual != expected:
        raise HTTPException(status_code=401, detail="Invalid Google Chat token")


def _parse_command(text: str) -> tuple[str, str]:
    cleaned = (text or "").strip()
    if cleaned.lower() == "/agents":
        return "__list_agents__", ""

    if cleaned.lower().startswith("/ask "):
        body = cleaned[5:].strip()
        if not body:
            raise ValueError("Usage: /ask <agent_id> <prompt>")
        first_space = body.find(" ")
        if first_space <= 0:
            raise ValueError("Usage: /ask <agent_id> <prompt>")
        return body[:first_space].strip(), body[first_space + 1 :].strip()

    if ":" in cleaned:
        maybe_agent, maybe_prompt = cleaned.split(":", 1)
        if maybe_agent.strip() and maybe_prompt.strip():
            return maybe_agent.strip(), maybe_prompt.strip()

    raise ValueError("Use /agents or /ask <agent_id> <prompt>")


def _format_agent_list(agent_map: dict[str, dict]) -> str:
    lines = ["Available agents:"]
    for agent_id in sorted(agent_map):
        cfg = agent_map[agent_id]
        model = cfg.get("llm", {}).get("model") or cfg.get("llm", {}).get("model_env", "")
        lines.append(f"- {agent_id}: {cfg.get('name', '')} [{model}]")
    return "\n".join(lines)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/google-chat/events")
async def google_chat_event(request: Request) -> dict[str, str]:
    payload = await request.json()
    _validate_request_token(payload)

    event_type = payload.get("type", "")
    if event_type == "ADDED_TO_SPACE":
        return {"text": "Local Agent connected. Use /agents to list worker IDs."}

    if event_type != "MESSAGE":
        return {"text": "Unsupported event type."}

    user_email = str(payload.get("user", {}).get("email", "")).lower().strip()
    allowed = _allowed_users()
    if allowed and user_email not in allowed:
        return {"text": "You are not authorized to use this bot."}

    text = str(payload.get("message", {}).get("text", ""))
    try:
        agent_id, prompt = _parse_command(text)
    except ValueError as exc:
        return {"text": str(exc)}

    agent_map = _load_agents("agents")
    if agent_id == "__list_agents__":
        return {"text": _format_agent_list(agent_map)}

    agent_cfg = agent_map.get(agent_id)
    if not agent_cfg:
        return {"text": f"Unknown agent '{agent_id}'. Use /agents to see valid IDs."}

    if not prompt:
        prompt = str(agent_cfg.get("tasks", [{}])[0].get("prompt", "")).strip()

    router = _build_router()
    result = run_agent_task(router=router, agent_cfg=agent_cfg, task_prompt=prompt)

    output = str(result.get("output", "")).strip()
    if not output:
        output = "Agent returned empty output."

    max_chars = int(os.getenv("CHAT_MAX_RESPONSE_CHARS", "3500"))
    if len(output) > max_chars:
        output = output[: max_chars - 15] + "\n\n[truncated]"

    return {"text": f"{result['agent_name']} ({result['agent_id']}):\n\n{output}"}
