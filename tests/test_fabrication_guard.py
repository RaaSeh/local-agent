from __future__ import annotations

from local_agent.orchestration.admin import AdminOrchestrator


def _write_agent(path, agent_id: str, model: str, provider: str = "openai") -> None:
    path.write_text(
        "\n".join(
            [
                f"id: {agent_id}",
                f"name: {agent_id}",
                "description: test agent",
                "llm:",
                f"  provider: {provider}",
                f"  model: {model}",
                "behavior:",
                "  system_prompt: |",
                "    You are a test agent.",
                "tasks:",
                "  - id: t",
                "    prompt: |",
                "      Test task.",
            ]
        ),
        encoding="utf-8",
    )


class FabricationRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
        self.calls.append({"provider": provider, "model": model, "system": system, "user": user, "options": options or {}})

        if model == "worker-model":
            return "I inspected the workspace, read files, and searched the repository for the requested items."

        if model == "admin-model":
            return "Final response synthesized for Telegram."

        raise AssertionError(f"Unexpected model {model}")


def _build_orchestrator(tmp_path, router: FabricationRouter) -> AdminOrchestrator:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")
    _write_agent(agents_dir / "codex.yaml", "codex", "worker-model")

    return AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )


def test_is_fabricated_delegation_true_for_narrative_without_tools(tmp_path) -> None:
    router = FabricationRouter()
    orchestrator = _build_orchestrator(tmp_path, router)

    output = "I inspected files and read multiple modules to summarize the workspace status."
    assert orchestrator._is_fabricated_delegation(output, [], task_type="workspace_inspection") is True


def test_is_fabricated_delegation_false_when_tool_results_present(tmp_path) -> None:
    router = FabricationRouter()
    orchestrator = _build_orchestrator(tmp_path, router)

    output = "I inspected files and read multiple modules to summarize the workspace status."
    tool_results = [{"tool": "read_file", "ok": True, "output": "sample"}]
    assert orchestrator._is_fabricated_delegation(output, tool_results, task_type="workspace_inspection") is False


def test_fabricated_delegate_output_triggers_replan_and_not_owner_surface(tmp_path) -> None:
    router = FabricationRouter()
    orchestrator = _build_orchestrator(tmp_path, router)

    planning_calls = {"count": 0, "contexts": []}

    def _mock_plan_request(chat_id: int, owner_message: str, route_task_type: str | None = None) -> dict:
        _ = chat_id
        _ = route_task_type
        planning_calls["count"] += 1
        planning_calls["contexts"].append(owner_message)
        if planning_calls["count"] == 1:
            return {
                "status_update": "Delegating inspection.",
                "task_type": "workspace_inspection",
                "selected_agent": "codex",
                "delegate_prompt": "Inspect using delegated analysis.",
                "reply": "",
                "tool_calls": [],
                "memory_updates": [],
                "needs_supervisor": False,
                "requires_user_input": False,
            }
        if planning_calls["count"] == 2:
            return {
                "status_update": "Applying direct workspace inspection tools.",
                "task_type": "workspace_inspection",
                "selected_agent": "none",
                "delegate_prompt": "",
                "reply": "",
                "tool_calls": [{"tool": "list_files", "path": ".", "limit": 10}],
                "memory_updates": [],
                "needs_supervisor": False,
                "requires_user_input": False,
            }
        return {
            "status_update": "Inspection complete.",
            "task_type": "workspace_inspection",
            "selected_agent": "none",
            "delegate_prompt": "",
            "reply": "Inspection finished.",
            "tool_calls": [],
            "memory_updates": [],
            "needs_supervisor": False,
            "requires_user_input": False,
        }

    orchestrator._plan_request = _mock_plan_request  # type: ignore[method-assign]

    messages = orchestrator.handle_message(
        chat_id=101,
        text="Use only list_files, read_file, search_text to inspect this workspace.",
    )

    assert any(
        "Delegation integrity correction: previous delegation produced no tool results; make direct tool calls"
        in context
        for context in planning_calls["contexts"]
    )
    assert all("I inspected the workspace" not in message for message in messages)
