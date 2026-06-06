from __future__ import annotations

from local_agent.core.run_once import resolve_agent_llm
from local_agent.orchestration.task_router import TaskRouter


def test_task_router_prefers_codex_for_cad_implementation_requests():
    router = TaskRouter()
    agents = {
        "codex": {
            "id": "codex",
            "name": "Codex",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
        "cad_rnd": {
            "id": "cad_rnd",
            "name": "CAD R&D",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
        "software_dev": {
            "id": "software_dev",
            "name": "Software Dev",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
    }

    decision = router.route_owner_message("Please implement the concentric mate in AgenticCAD.", agents)

    assert decision.task_type == "code_support"
    assert decision.selected_agent == "codex"


def test_task_router_prefers_codex_for_cad_experiment_requests():
    router = TaskRouter()
    agents = {
        "codex": {
            "id": "codex",
            "name": "Codex",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
        "cad_rnd": {
            "id": "cad_rnd",
            "name": "CAD R&D",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
        "software_dev": {
            "id": "software_dev",
            "name": "Software Dev",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
    }

    decision = router.route_owner_message("Design one Agentic CAD experiment for mate solving.", agents)

    assert decision.task_type == "cad_rnd"
    assert decision.selected_agent == "codex"


def test_resolve_agent_llm_honors_task_router_env_override(monkeypatch):
    monkeypatch.setenv("TASK_ROUTER_SOFTWARE_DEV_PROVIDER", "ollama")
    monkeypatch.setenv("TASK_ROUTER_SOFTWARE_DEV_MODEL", "qwen2.5-coder:14b")

    agent_cfg = {
        "id": "software_dev",
        "name": "Software Dev",
        "llm": {
            "provider": "ollama",
            "model": "llama3.1:8b",
            "options": {"temperature": 0.1},
        },
        "behavior": {"system_prompt": "You are a coder."},
    }

    resolved = resolve_agent_llm(agent_cfg, task_context="fix this python bug")

    assert resolved["provider"] == "ollama"
    assert resolved["model"] == "qwen2.5-coder:14b"
    assert resolved["options"]["temperature"] == 0.1


def test_task_router_routes_desktop_exe_launch_to_direct_tools():
    router = TaskRouter()
    agents = {
        "codex": {
            "id": "codex",
            "name": "Codex",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
        "software_dev": {
            "id": "software_dev",
            "name": "Software Dev",
            "llm": {"provider": "ollama", "model": "llama3.1:8b", "options": {}},
        },
    }

    decision = router.route_owner_message("Open the exe on my desktop and confirm it started.", agents)

    assert decision.task_type == "desktop_execution"
    assert decision.selected_agent == "none"