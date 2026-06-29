from __future__ import annotations

import json

from local_agent.orchestration.planner import ToolPlanner
from local_agent.orchestration.registry import TaskRegistry


class DelegatingInspectionRouter:
    def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
        _ = provider, model, system, user, options
        return json.dumps(
            {
                "task_type": "workspace_inspection",
                "selected_agent": "codex",
                "delegate_prompt": "Inspect the workspace and summarize findings.",
                "tool_calls": [],
            }
        )


def test_workspace_inspection_plan_emits_direct_tool_calls_and_no_delegate() -> None:
    planner = ToolPlanner(
        router=DelegatingInspectionRouter(),
        agent_cfg={
            "llm": {"provider": "openai", "model": "admin-model", "options": {}},
            "behavior": {"system_prompt": "You are admin."},
        },
        registry=TaskRegistry(),
    )

    plan = planner.plan(
        owner_message="Use only list_files, read_file, search_text to inspect this repository.",
        memory_context="",
        allowed_agents=["codex", "software_dev"],
    )

    assert plan["task_type"] == "workspace_inspection"
    assert plan["selected_agent"] == "none"
    assert plan["delegate_prompt"] in {"", None}
    assert isinstance(plan["tool_calls"], list)
    assert len(plan["tool_calls"]) > 0
    assert all(call.get("tool") in {"list_files", "read_file", "search_text"} for call in plan["tool_calls"])
