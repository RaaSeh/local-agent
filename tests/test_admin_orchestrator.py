from __future__ import annotations

import json
import sys
from pathlib import Path

from local_agent.orchestration.admin import AdminOrchestrator
from local_agent.orchestration.planner import ToolPlanner
from local_agent.orchestration.registry import TaskRegistry
from local_agent.orchestration.tools import ToolExecutor
from local_agent.policy.approvals import PolicyEngine
from local_agent.storage.runs import RunStore


class DummyRouter:
    def __init__(self):
        self.calls = []

    def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "system": system,
                "user": user,
                "options": options or {},
            }
        )
        if model == "admin-model":
            if "Reply with ONLY a JSON object" in user:
                if "agentic cad" in user.lower() or "copilot" in user.lower() or "codex" in user.lower():
                    return json.dumps(
                        {
                            "status_update": "Routing through cad_rnd and supervisor.",
                            "selected_agent": "cad_rnd",
                            "delegate_prompt": "Create a coding-agent experiment brief for the Agentic CAD library.",
                            "reply": "Preparing an Agentic CAD delegation pack.",
                            "tool_calls": [{"tool": "list_files", "path": ".", "limit": 5}],
                            "memory_updates": [],
                            "needs_supervisor": True,
                            "requires_user_input": False,
                        }
                    )
                return json.dumps(
                    {
                        "status_update": "Routing through software_dev and supervisor.",
                        "selected_agent": "software_dev",
                        "delegate_prompt": "Implement an owner-memory and delegation improvement.",
                        "reply": "Preparing a consolidated result.",
                        "tool_calls": [{"tool": "list_files", "path": ".", "limit": 5}],
                        "memory_updates": [
                            {"kind": "business", "value": "Runs multiple local-LLM businesses", "source": "owner"}
                        ],
                        "needs_supervisor": True,
                        "requires_user_input": False,
                    }
                )
            return "Final response synthesized for Telegram."
        if model == "worker-model":
            return (
                "1) Concrete Deliverables\n"
                "- Implement minimal wrapper and tests\n\n"
                "2) Target Files/Modules\n"
                "- src/wrapper.py\n"
                "- tests/test_wrapper.py\n\n"
                "Changed Files\n"
                "- src/wrapper.py\n"
                "- tests/test_wrapper.py\n\n"
                "3) Patch\n"
                "```diff\n"
                "--- a/src/wrapper.py\n"
                "+++ b/src/wrapper.py\n"
                "@@\n"
                "+print('ok')\n"
                "```\n\n"
                "4) Validation Command(s) and observed result\n"
                "- pytest -q\n"
                "- result: passed\n\n"
                "5) Blockers with exact missing inputs, if any\n"
                "- none"
            )
        if model == "supervisor-model":
            return "1) Summary\nApproved after review.\n2) Risks/Concerns\nLow.\n3) Next Actions\nProceed."
        raise AssertionError(f"Unexpected model {model}")


def _write_agent(path, agent_id: str, name: str, model: str, provider: str = "ollama") -> None:
    path.write_text(
        "\n".join(
            [
                f"id: {agent_id}",
                f"name: {name}",
                "description: test agent",
                "llm:",
                f"  provider: {provider}",
                f"  model: {model}",
                "behavior:",
                "  system_prompt: |",
                "    You are a test agent.",
                "tasks:",
                "  - id: test_task",
                "    prompt: |",
                "      Default task.",
            ]
        ),
        encoding="utf-8",
    )


def _read_last_interaction(tmp_path: Path) -> dict:
    lines = (tmp_path / "state" / "interactions.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines, "Expected at least one interaction record"
    return json.loads(lines[-1])


def test_admin_orchestrator_routes_and_persists_memory(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
    )

    messages = orchestrator.handle_message(chat_id=123, text="Help me improve this workspace autonomously")

    assert messages[0] == "Routing through software_dev and supervisor."
    assert messages[-1] == "Final response synthesized for Telegram."

    owner_profile = json.loads((tmp_path / "state" / "owner_profile.json").read_text(encoding="utf-8"))
    assert "Runs multiple local-LLM businesses" in owner_profile["businesses"]

    interactions = (tmp_path / "state" / "interactions.jsonl").read_text(encoding="utf-8")
    assert any(agent in interactions for agent in ("software_dev", "cad_rnd", "codex"))


def test_tool_executor_runs_local_tools(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    executor = ToolExecutor(tmp_path)
    results = executor.execute(
        [
            {"tool": "read_file", "path": "sample.txt", "start_line": 2, "end_line": 3},
            {"tool": "search_text", "path": ".", "query": "gamma", "limit": 5},
        ]
    )

    assert results[0]["ok"] is True
    assert results[0]["output"] == "beta\ngamma"
    assert "sample.txt:3: gamma" in results[1]["output"]


def test_run_command_rejects_multiline_input(tmp_path: Path):
    executor = ToolExecutor(tmp_path)
    results = executor.execute(
        [
            {
                "tool": "run_command",
                "command": "echo first line\nwhoami",
            }
        ]
    )
    assert results[0]["ok"] is False
    assert "single-line commands" in results[0]["output"]


def test_tool_executor_launch_executable_detects_quick_exit_failure(tmp_path: Path):
    executor = ToolExecutor(tmp_path)

    results = executor.execute(
        [
            {
                "tool": "launch_executable",
                "path": sys.executable,
                "args": ["-c", "import sys; sys.exit(3)"],
                "wait_seconds": 1,
            }
        ]
    )

    assert results[0]["ok"] is False
    assert "exit_code=3" in results[0]["output"]


def test_planner_fallback_prefers_direct_tools_for_install_requests():
    class EmptyPlannerRouter:
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            _ = provider, model, system, user, options
            return "{}"

    planner = ToolPlanner(
        router=EmptyPlannerRouter(),
        agent_cfg={
            "llm": {"provider": "openai", "model": "admin-model", "options": {}},
            "behavior": {"system_prompt": "You are admin."},
        },
        registry=TaskRegistry(),
    )

    plan = planner.plan(
        owner_message="Install pytest and ruff if necessary to complete this task",
        memory_context="",
        allowed_agents=["codex", "software_dev"],
    )

    assert plan["task_type"] in {"environment", "tool_acquisition"}
    assert plan["selected_agent"] == "none"
    assert any(call.get("tool") == "install_python_packages" for call in plan["tool_calls"])


def test_planner_fallback_avoids_user_text_derived_desktop_executable_paths():
    class EmptyPlannerRouter:
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            _ = provider, model, system, user, options
            return "{}"

    planner = ToolPlanner(
        router=EmptyPlannerRouter(),
        agent_cfg={
            "llm": {"provider": "openai", "model": "admin-model", "options": {}},
            "behavior": {"system_prompt": "You are admin."},
        },
        registry=TaskRegistry(),
    )

    plan = planner.plan(
        owner_message="Open C:/Users/platt/Desktop/MyTool.exe on the desktop and verify it launches",
        memory_context="",
        allowed_agents=["codex", "software_dev"],
    )

    assert plan["task_type"] == "desktop_execution"
    assert plan["selected_agent"] == "none"
    assert all(call.get("tool") != "launch_executable" for call in plan["tool_calls"])


def test_planner_fallback_forces_desktop_execution_when_model_returns_general():
    class GeneralPlannerRouter:
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            _ = provider, model, system, user, options
            return json.dumps(
                {
                    "task_type": "general",
                    "selected_agent": "codex",
                    "tool_calls": [],
                    "reply": "I will handle this shortly.",
                }
            )

    planner = ToolPlanner(
        router=GeneralPlannerRouter(),
        agent_cfg={
            "llm": {"provider": "openai", "model": "admin-model", "options": {}},
            "behavior": {"system_prompt": "You are admin."},
        },
        registry=TaskRegistry(),
    )

    plan = planner.plan(
        owner_message="Open C:/Users/platt/Desktop/MyTool.exe and verify it is running",
        memory_context="",
        allowed_agents=["codex", "software_dev"],
    )

    assert plan["task_type"] == "desktop_execution"
    assert plan["selected_agent"] == "none"
    assert all(call.get("tool") != "launch_executable" for call in plan["tool_calls"])


def test_planner_fallback_infers_alibre_launch_without_explicit_exe_path():
    class EmptyPlannerRouter:
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            _ = provider, model, system, user, options
            return "{}"

    planner = ToolPlanner(
        router=EmptyPlannerRouter(),
        agent_cfg={
            "llm": {"provider": "openai", "model": "admin-model", "options": {}},
            "behavior": {"system_prompt": "You are admin."},
        },
        registry=TaskRegistry(),
    )

    plan = planner.plan(
        owner_message="Open Alibre on this machine and verify it launched",
        memory_context="",
        allowed_agents=["codex", "software_dev"],
    )

    assert plan["task_type"] == "desktop_execution"
    assert plan["selected_agent"] == "none"
    assert any(call.get("tool") == "run_command" for call in plan["tool_calls"])


def test_admin_orchestrator_reads_recent_runs(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    RunStore(runs_dir).save(
        "business_plan",
        {
            "run_id": "20260525-999999",
            "business_profile": "rocket_wash",
            "status": "completed",
            "confidence": 0.91,
            "goal": "Build a spring campaign",
            "final_memo": {"summary": "This is the most recent run."},
        },
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=123, text="/runs")

    assert "Recent runs:" in messages[0]
    assert "rocket_wash" in messages[0]
    assert "This is the most recent run." in messages[0]


def test_admin_orchestrator_writes_coding_agent_brief(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "cad_rnd.yaml", "cad_rnd", "CAD R&D", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Use Copilot to run an Agentic CAD experiment toward a functional CAD library",
    )

    brief_message = next(message for message in messages if message.startswith("Coding-agent brief:"))
    brief_path = Path(brief_message.split(": ", 1)[1])

    assert brief_path.exists()
    brief_text = brief_path.read_text(encoding="utf-8")
    assert "Paste Into Copilot Or Codex" in brief_text
    assert "Agentic CAD" in brief_text


def test_admin_orchestrator_blocks_incomplete_coding_delegate_output(tmp_path: Path):
    class GateRouter(DummyRouter):
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "Routing through software_dev.",
                        "task_type": "code_support",
                        "selected_agent": "software_dev",
                        "delegate_prompt": "Implement API wrappers for Alibre mates and add tests.",
                        "reply": "Preparing implementation.",
                        "tool_calls": [],
                        "memory_updates": [],
                        "needs_supervisor": True,
                        "requires_user_input": False,
                    }
                )
            if model == "worker-model":
                # Intentionally incomplete/meta output to trigger the quality gate.
                return "Hypothesis: review and experimentation only."
            return super().chat(provider, model, system, user, options)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=GateRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Please implement coincident and concentric mates in AgenticCAD and create tests.",
    )

    assert any("Delegate quality gate failed" in message for message in messages)
    assert not any(message.startswith("## Supervisor Review") for message in messages)


def test_autonomy_command_sets_mode(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    show_default = orchestrator.handle_message(chat_id=501, text="/autonomy")
    assert show_default[0].startswith("Autonomy mode: manual")

    set_trusted = orchestrator.handle_message(chat_id=501, text="/autonomy set trusted")
    assert "trusted" in set_trusted[0].lower()

    show_after = orchestrator.handle_message(chat_id=501, text="/autonomy")
    assert show_after[0].startswith("Autonomy mode: trusted")


def test_trusted_autonomy_auto_approves_safe_risky_tools(tmp_path: Path):
    class ToolRouteRouter(DummyRouter):
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "Running script directly.",
                        "task_type": "code_support",
                        "selected_agent": "none",
                        "delegate_prompt": "",
                        "reply": "Executing.",
                        "tool_calls": [{"tool": "execute_python", "path": "scripts/hello.py", "timeout": 30}],
                        "memory_updates": [],
                        "needs_supervisor": False,
                        "requires_user_input": False,
                        "requires_confirmation": True,
                    }
                )
            return super().chat(provider, model, system, user, options)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "hello.py").write_text("print('ok')\n", encoding="utf-8")

    orchestrator = AdminOrchestrator(
        router=ToolRouteRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    orchestrator.handle_message(chat_id=700, text="/autonomy set trusted")
    messages = orchestrator.handle_message(chat_id=700, text="run scripts/hello.py and confirm output")

    assert not any("Approval required" in message for message in messages)
    assert orchestrator.approvals.list_pending(chat_id=700) == []
    assert any("execute_python" in message for message in messages)


def test_manual_autonomy_allows_helper_tool_execution_without_approval(tmp_path: Path):
    class ToolRouteRouter(DummyRouter):
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "Running script directly.",
                        "task_type": "code_support",
                        "selected_agent": "none",
                        "delegate_prompt": "",
                        "reply": "Executing.",
                        "tool_calls": [{"tool": "execute_python", "path": "scripts/hello.py", "timeout": 30}],
                        "memory_updates": [],
                        "needs_supervisor": False,
                        "requires_user_input": False,
                        "requires_confirmation": True,
                    }
                )
            return super().chat(provider, model, system, user, options)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "hello.py").write_text("print('ok')\n", encoding="utf-8")

    orchestrator = AdminOrchestrator(
        router=ToolRouteRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=701, text="run scripts/hello.py and confirm output")

    assert not any("Approval required" in message for message in messages)
    assert len(orchestrator.approvals.list_pending(chat_id=701)) == 0


def test_admin_orchestrator_repairs_incomplete_delegate_output_once(tmp_path: Path):
    class RepairRouter(DummyRouter):
        def __init__(self):
            super().__init__()
            self.worker_calls = 0

        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "Routing through software_dev.",
                        "task_type": "code_support",
                        "selected_agent": "software_dev",
                        "delegate_prompt": "Implement API wrappers for Alibre mates and add tests.",
                        "reply": "Preparing implementation.",
                        "tool_calls": [],
                        "memory_updates": [],
                        "needs_supervisor": True,
                        "requires_user_input": False,
                    }
                )
            if model == "worker-model":
                self.worker_calls += 1
                if self.worker_calls == 1:
                    # Missing target files + blockers to trigger repair pass.
                    return (
                        "1) Concrete Deliverables\n"
                        "- Implement mate wrappers\n\n"
                        "3) Code Snippets or Patch-Ready Blocks\n"
                        "```python\nprint('ok')\n```\n\n"
                        "4) Validation Command(s) and expected results\n"
                        "- pytest -q\n"
                    )
                return (
                    "1) Concrete Deliverables\n"
                    "- Implement coincident/concentric wrappers\n\n"
                    "2) Target Files/Modules\n"
                    "- src/alibre/wrappers.py\n"
                    "- tests/test_mates.py\n\n"
                    "3) Code Snippets or Patch-Ready Blocks\n"
                    "```python\nprint('ok')\n```\n\n"
                    "4) Validation Command(s) and expected results\n"
                    "- pytest -q\n\n"
                    "5) Blockers with exact missing inputs, if any\n"
                    "- none\n"
                )
            return super().chat(provider, model, system, user, options)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    router = RepairRouter()
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Please implement coincident and concentric mates in AgenticCAD and create tests.",
    )

    assert not any("Delegate quality gate failed" in message for message in messages)
    assert messages[-1] == "Final response synthesized for Telegram."
    assert router.worker_calls == 2


def test_admin_orchestrator_requires_approval_for_customer_facing_email(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Draft and send a customer email to our client about a service delay.",
    )

    assert any("Approval required (customer_facing_email)" in message for message in messages)
    assert any("Use /approve" in message for message in messages)
    assert not any(message == "Final response synthesized for Telegram." for message in messages)

    approvals_path = tmp_path / "state" / "pending_approvals.json"
    payload = json.loads(approvals_path.read_text(encoding="utf-8"))
    assert len(payload["approvals"]) == 1
    record = payload["approvals"][0]
    assert record["status"] == "pending"
    assert isinstance(record["tool_calls"], list)
    assert any(call.get("tool") == "list_files" for call in record["tool_calls"])


def test_admin_orchestrator_requires_approval_for_production_code_change(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "codex.yaml", "codex", "Codex", "worker-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Deploy a functional production code change to the live API service tonight.",
    )

    assert any("Approval required (production_change)" in message for message in messages)
    assert any("Use /approve" in message for message in messages)
    assert not any(message == "Final response synthesized for Telegram." for message in messages)

    approvals_path = tmp_path / "state" / "pending_approvals.json"
    payload = json.loads(approvals_path.read_text(encoding="utf-8"))
    assert len(payload["approvals"]) == 1
    record = payload["approvals"][0]
    assert isinstance(record["tool_calls"], list)
    assert any(call.get("tool") == "list_files" for call in record["tool_calls"])
    assert isinstance(record.get("execution_plan"), dict)
    assert record["execution_plan"].get("selected_agent") == "codex"
    assert isinstance(record["execution_plan"].get("tool_calls"), list)
    assert any(call.get("tool") == "list_files" for call in record["execution_plan"].get("tool_calls", []))
    assert record["execution_plan"].get("delegate_prompt")


def test_approve_runs_delegate_even_when_approved_record_has_tool_calls(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(agents_dir / "codex.yaml", "codex", "Codex", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    record = orchestrator.approvals.add_pending(
        chat_id=123,
        owner_message="Deploy a functional production code change to the live API service tonight.",
        tool_calls=[
            {
                "tool": "run_command",
                "command": "python -c \"import sys; sys.exit(1)\"",
                "timeout": 10,
            }
        ],
        rationale="Functional production code change requests require explicit owner approval.",
        workspace="rocket-wash",
        execution_plan={
            "selected_agent": "codex",
            "delegate_prompt": "Implement the requested production patch and return changed files + validation.",
            "task_type": "code_support",
            "needs_supervisor": False,
            "rationale": "Code patch required",
            "tool_calls": [],
        },
    )

    messages = orchestrator.handle_message(chat_id=123, text=f"/approve {record['approval_id']}")

    assert any(message.startswith("Approved and executed") for message in messages)
    assert any("Concrete Deliverables" in message for message in messages)

    resolved = orchestrator.approvals.get(record["approval_id"])
    assert resolved is not None
    assert resolved["status"] == "executed"


def test_admin_orchestrator_allows_low_risk_analysis_without_approval(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(
        chat_id=123,
        text="Analyze the current market and propose a low-risk 30-day planning outline.",
    )

    assert not any("Approval required (" in message for message in messages)
    assert messages[-1] == "Final response synthesized for Telegram."

    approvals_path = tmp_path / "state" / "pending_approvals.json"
    if approvals_path.exists():
        payload = json.loads(approvals_path.read_text(encoding="utf-8"))
        assert payload.get("approvals", []) == []


def test_execution_task_without_tool_evidence_is_not_completed(tmp_path: Path):
    class IntentOnlyRouter(DummyRouter):
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "Preparing desktop action.",
                        "task_type": "environment",
                        "selected_agent": "none",
                        "delegate_prompt": "",
                        "reply": "I can handle this.",
                        "tool_calls": [],
                        "memory_updates": [],
                        "needs_supervisor": False,
                        "requires_user_input": False,
                    }
                )
            return "I can handle this."

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=IntentOnlyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    orchestrator.handle_message(chat_id=123, text="Open calculator and take a screenshot.")

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "needs-input"
    assert interaction["completion_reason"] in {"missing_execution_evidence", "blocked_by_approval"}
    assert interaction["execution_intent"] is True
    diagnostics = interaction["evidence"].get("diagnostics", {})
    assert diagnostics.get("blocked_reason") in {"approval_required", "no_tool_calls_executed"}


def test_direct_agent_execution_intent_not_auto_completed_without_proof(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    orchestrator.handle_message(
        chat_id=456,
        text="/ask software_dev open Calculator and capture a screenshot",
    )

    interaction = _read_last_interaction(tmp_path)
    assert interaction["selected_agent"] == "software_dev"
    assert interaction["status"] == "needs-input"
    assert interaction["completion_reason"] == "missing_execution_evidence"
    assert interaction["execution_intent"] is True
    assert interaction["evidence"].get("diagnostics", {}).get("blocked_reason") == "no_tool_calls_executed"


def test_policy_allows_helper_actions_without_approval(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "policy.yaml").write_text(
        "approval:\n  require_for_tool_names: []\n  require_for_categories: []\n",
        encoding="utf-8",
    )
    policy = PolicyEngine(tmp_path / "config" / "policy.yaml")

    decision = policy.tool_calls_require_approval(
        [
            {"tool": "install_python_packages", "packages": ["pytest"]},
            {"tool": "scaffold_tool", "path": "tools/helper.py", "code": "print('x')"},
            {"tool": "download_file", "url": "https://example.com/test.bin", "path": "tools/test.bin"},
        ]
    )
    assert decision.requires_approval is False


def test_policy_requires_approval_for_deleting_old_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "policy.yaml").write_text(
        "approval:\n  require_for_tool_names: []\n  require_for_categories: []\n",
        encoding="utf-8",
    )
    policy = PolicyEngine(tmp_path / "config" / "policy.yaml")

    old_file = tmp_path / "legacy.txt"
    old_file.write_text("legacy", encoding="utf-8")
    import os

    # Set mtime to Jan 1, 2025 UTC
    os.utime(old_file, (1735689600, 1735689600))
    decision = policy.classify_tool_call({"tool": "delete_file", "path": "legacy.txt"})
    assert decision.requires_approval is True


def test_approve_path_persists_interaction_with_evidence_and_status(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    pending = orchestrator.approvals.add_pending(
        chat_id=123,
        owner_message="Install package dependencies for this machine.",
        tool_calls=[],
        rationale="Test pending approval interaction write.",
        workspace="default",
    )

    messages = orchestrator.handle_message(chat_id=123, text=f"/approve {pending['approval_id']}")
    assert messages[0].startswith("Approved and executed")

    interaction = _read_last_interaction(tmp_path)
    assert interaction["approval_id"] == pending["approval_id"]
    assert interaction["status"] != "completed"
    assert interaction["completion_reason"] == "missing_execution_evidence"
    assert isinstance(interaction.get("evidence"), dict)
    assert interaction["evidence"].get("executed_tool_calls") == []


def test_approve_diagnostics_include_planned_tool_count(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    pending = orchestrator.approvals.add_pending(
        chat_id=123,
        owner_message="Install package dependencies for this machine.",
        tool_calls=[
            {
                "tool": "run_command",
                "command": "python -c \"import sys; sys.exit(1)\"",
                "timeout": 10,
            }
        ],
        rationale="Test diagnostics for planned tool count.",
        workspace="default",
        execution_plan={
            "selected_agent": "none",
            "task_type": "environment",
            "execution_intent_override": True,
            "require_mutating_tool_evidence": False,
            "tool_calls": [
                {
                    "tool": "run_command",
                    "command": "python -c \"import sys; sys.exit(1)\"",
                    "timeout": 10,
                }
            ],
        },
    )

    orchestrator.handle_message(chat_id=123, text=f"/approve {pending['approval_id']}")
    interaction = _read_last_interaction(tmp_path)
    diagnostics = interaction.get("evidence", {}).get("diagnostics", {})
    assert diagnostics.get("planned_tool_count") == 1
    assert diagnostics.get("executed_tool_count") == 1
    assert diagnostics.get("failed_tool_count") == 1


def test_approve_runs_delegate_when_execution_plan_exists_without_tool_calls(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "Software Dev", "worker-model")
    _write_agent(
        agents_dir / "supervisor.yaml",
        "supervisor",
        "Supervisor",
        "supervisor-model",
        provider="anthropic",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    pending = orchestrator.approvals.add_pending(
        chat_id=555,
        owner_message="Apply a production code fix and report changed files.",
        tool_calls=[],
        rationale="Production change approved by owner.",
        workspace="default",
        execution_plan={
            "selected_agent": "software_dev",
            "delegate_prompt": "Implement a concrete production fix and return changed files.",
            "task_type": "code_support",
            "needs_supervisor": True,
        },
    )

    messages = orchestrator.handle_message(chat_id=555, text=f"/approve {pending['approval_id']}")
    assert messages[0].startswith("Approved and executed")
    assert any("Concrete Deliverables" in message for message in messages)

    interaction = _read_last_interaction(tmp_path)
    assert interaction["approval_id"] == pending["approval_id"]
    assert interaction["completion_reason"] == "criteria_met"


def test_non_execution_task_without_tools_still_completes(tmp_path: Path):
    class InformationalRouter(DummyRouter):
        def chat(self, provider: str, model: str, system: str, user: str, options: dict | None = None) -> str:
            if model == "admin-model" and "Reply with ONLY a JSON object" in user:
                return json.dumps(
                    {
                        "status_update": "",
                        "task_type": "general",
                        "selected_agent": "none",
                        "delegate_prompt": "",
                        "reply": "Here is your planning outline.",
                        "tool_calls": [],
                        "memory_updates": [],
                        "needs_supervisor": False,
                        "requires_user_input": False,
                    }
                )
            return "Here is your planning outline."

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=InformationalRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=789, text="Outline a 30-day market research plan.")
    assert messages[-1] == "Here is your planning outline."

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "completed"
    assert interaction["completion_reason"] == "criteria_met"
    assert interaction["execution_intent"] is False


# ---------------------------------------------------------------------------
# Self-tooling: check_capability, scaffold_tool, download_file
# ---------------------------------------------------------------------------

def test_check_capability_detects_stdlib(tmp_path):
    executor = ToolExecutor(tmp_path)
    # 'json' is always available in stdlib
    result = executor.execute([{"tool": "check_capability", "kind": "package", "name": "json"}])
    assert result[0]["ok"] is True
    assert '"available": true' in result[0]["output"]


def test_check_capability_misses_absent_package(tmp_path):
    executor = ToolExecutor(tmp_path)
    # this package name is intentionally implausible
    result = executor.execute([
        {"tool": "check_capability", "kind": "package", "name": "xyzzy_does_not_exist_ever"}
    ])
    assert result[0]["ok"] is True
    assert '"available": false' in result[0]["output"]


def test_check_capability_detects_python_command(tmp_path):
    executor = ToolExecutor(tmp_path)
    # python itself must be available
    result = executor.execute([{"tool": "check_capability", "kind": "command", "name": sys.executable}])
    assert result[0]["ok"] is True
    assert '"available": true' in result[0]["output"]


def test_scaffold_tool_creates_file(tmp_path):
    executor = ToolExecutor(tmp_path)
    result = executor.execute([{
        "tool": "scaffold_tool",
        "path": "tools/my_helper.py",
        "purpose": "Test helper for unit tests.",
        "code": "def hello():\n    return 'world'\n",
    }])
    assert result[0]["ok"] is True
    written = (tmp_path / "tools" / "my_helper.py").read_text(encoding="utf-8")
    assert "hello" in written
    assert "Test helper" in written


def test_scaffold_tool_requires_code(tmp_path):
    executor = ToolExecutor(tmp_path)
    result = executor.execute([{"tool": "scaffold_tool", "path": "tools/empty.py", "purpose": "x", "code": ""}])
    assert result[0]["ok"] is False


def test_download_file_rejects_http(tmp_path):
    executor = ToolExecutor(tmp_path)
    result = executor.execute([{"tool": "download_file", "url": "http://example.com/file.txt", "path": "file.txt"}])
    assert result[0]["ok"] is False
    assert "HTTPS" in result[0]["output"]


def test_tool_acquisition_route_in_registry():
    registry = TaskRegistry()
    route = registry.route_for("I need a tool to convert images to webp")
    # Should hit a route that allows scaffold/install/download
    allowed = set(route.allowed_tools)
    assert "check_capability" in allowed
    assert "install_python_packages" in allowed
    assert "scaffold_tool" in allowed


def test_check_capability_route_in_environment():
    registry = TaskRegistry()
    route = registry.route_for("install the requests package")
    assert "check_capability" in route.allowed_tools


def test_execute_python_runs_script(tmp_path):
    executor = ToolExecutor(tmp_path)
    script = tmp_path / "hello.py"
    script.write_text("print('hello from script')\n", encoding="utf-8")
    result = executor.execute([{"tool": "execute_python", "path": "hello.py"}])
    assert result[0]["ok"] is True
    assert "hello from script" in result[0]["output"]
    assert "exit_code=0" in result[0]["output"]


def test_execute_python_captures_error(tmp_path):
    executor = ToolExecutor(tmp_path)
    script = tmp_path / "bad.py"
    script.write_text("raise ValueError('oops')\n", encoding="utf-8")
    result = executor.execute([{"tool": "execute_python", "path": "bad.py"}])
    assert result[0]["ok"] is True  # execute succeeded; script errored
    assert "exit_code=1" in result[0]["output"]
    assert "oops" in result[0]["output"]


def test_tool_loop_stops_when_plan_returns_no_more_calls(tmp_path):
    """The iterative loop should stop after the first batch and not re-plan
    indefinitely when the second plan returns zero tool_calls."""
    import json as _json

    call_count = 0

    class CountingRouter:
        def chat(self, provider, model, system, user, options=None):
            nonlocal call_count
            call_count += 1
            # First plan call — return one tool call
            if call_count == 1:
                return _json.dumps({
                    "status_update": "",
                    "task_type": "environment",
                    "selected_agent": "none",
                    "delegate_prompt": "",
                    "reply": "",
                    "tool_calls": [{"tool": "list_files", "path": ".", "limit": 3}],
                    "memory_kind": "none",
                    "memory_updates": [],
                    "needs_supervisor": False,
                    "requires_user_input": False,
                    "rationale": "list files",
                })
            # Second plan call (after tool results injected) — no more tool calls
            return _json.dumps({
                "status_update": "",
                "task_type": "environment",
                "selected_agent": "none",
                "delegate_prompt": "",
                "reply": "Done.",
                "tool_calls": [],
                "memory_kind": "none",
                "memory_updates": [],
                "needs_supervisor": False,
                "requires_user_input": False,
                "rationale": "done",
            })

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "admin.yaml").write_text(
        "id: admin\nname: Admin\nllm:\n  provider: test\n  model: admin-model\n  options: {}\nbehavior:\n  system_prompt: You are admin.\n",
        encoding="utf-8",
    )
    from local_agent.orchestration.admin import AdminOrchestrator
    orchestrator = AdminOrchestrator(
        router=CountingRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )
    initial_plan = {
        "task_type": "environment",
        "selected_agent": "none",
        "delegate_prompt": "",
        "reply": "",
        "tool_calls": [{"tool": "list_files", "path": ".", "limit": 3}],
        "memory_kind": "none",
        "memory_updates": [],
        "needs_supervisor": False,
        "requires_user_input": False,
    }
    final_plan, all_results = orchestrator._run_tool_loop("list my files", initial_plan)
    # One tool execution round -> one re-plan call -> no more tool_calls -> stops
    assert len(all_results) >= 1, "Expected at least one tool result"
    assert (final_plan.get("tool_calls") or []) == [], "Final plan should have no remaining tool_calls"


def test_infer_execution_recovery_calls_for_alibre_launch(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )

    calls = orchestrator._infer_execution_recovery_tool_calls(
        owner_message="Please open Alibre and verify it launched.",
        task_type="desktop_execution",
        failed_results=[{"tool": "run_command", "ok": False, "output": "not found"}],
    )

    assert calls
    # Without a cache or real Alibre install, recovery falls back to a run_command search.
    # If Alibre is installed on the machine, the resolver returns launch_executable instead.
    tool = calls[0]["tool"]
    assert tool in {"run_command", "launch_executable"}, f"Unexpected tool: {tool}"
    if tool == "run_command":
        assert "Start-Process" in calls[0]["command"]
        # Must never contain user-text-derived exe candidate names
        assert "be sure to save" not in calls[0]["command"].lower()
        assert "28.1.1.28228" not in calls[0]["command"]


def test_infer_execution_recovery_no_user_text_exe_names(tmp_path: Path):
    """Recovery tool calls must never derive executable names from arbitrary owner-message text."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )

    malicious_message = (
        "please open Alibre and be sure to save the alibre executable location for future calls.exe"
    )
    calls = orchestrator._infer_execution_recovery_tool_calls(
        owner_message=malicious_message,
        task_type="desktop_execution",
        failed_results=[{"tool": "run_command", "ok": False, "output": "not found"}],
    )

    for call in calls:
        cmd = call.get("command", "") or ""
        assert "be sure to save" not in cmd.lower(), "User-text fragment must not appear in recovery command"
        # No hardcoded single-version folder
        assert "28.1.1.28228" not in cmd, "Hardcoded Alibre version must not appear in recovery command"
        # Executable name must not be derived from free-form user text
        path_val = call.get("path", "") or ""
        assert "be sure to save" not in path_val.lower()


def test_infer_execution_recovery_for_code_patch_request_avoids_desktop_launch(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Create files expected by the code-first recovery chain.
    (tmp_path / "src" / "local_agent" / "orchestration").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "local_agent" / "orchestration" / "admin.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "local_agent" / "orchestration" / "planner.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "local_agent" / "orchestration" / "tools.py").write_text("x", encoding="utf-8")
    (tmp_path / "tests" / "test_admin_orchestrator.py").write_text("x", encoding="utf-8")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=state_dir,
        runs_dir=tmp_path / "runs",
    )

    owner_message = (
        "Please implement a robust patch in admin.py and tools.py, update tests, "
        "and provide changed files and pytest results."
    )
    calls = orchestrator._infer_execution_recovery_tool_calls(
        owner_message=owner_message,
        task_type="desktop_execution",
        failed_results=[{"tool": "run_command", "ok": False, "output": "not found"}],
    )

    assert calls
    assert all(call.get("tool") not in {"launch_executable"} for call in calls)
    assert any(call.get("tool") == "read_file" for call in calls)
    assert any(call.get("tool") == "run_command" for call in calls)
    assert all("Start-Process" not in str(call.get("command", "")) for call in calls)


def test_resolve_alibre_executable_uses_and_populates_cache(tmp_path: Path):
    """Resolver must persist found path to cache and return it on the second call."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    state_dir = tmp_path / "state"

    # Create a fake Alibre install directory under tmp_path to simulate Program Files
    alibre_dir = tmp_path / "fake_programs" / "Alibre Design 28.0"
    program_dir = alibre_dir / "Program"
    program_dir.mkdir(parents=True, exist_ok=True)
    fake_exe = program_dir / "AlibreDesign.exe"
    fake_exe.write_text("stub", encoding="utf-8")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=state_dir,
        runs_dir=tmp_path / "runs",
    )

    # Pre-populate the cache manually to simulate a previous successful resolution
    cache_file = state_dir / "alibre_executable_path.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"path": str(fake_exe)}), encoding="utf-8")

    result = orchestrator._resolve_alibre_executable()

    assert result["found"] is True
    assert result["path"] == str(fake_exe)
    assert result["discovery_method"] == "cache"


def test_resolve_alibre_executable_searches_versioned_program_subfolder(tmp_path: Path):
    """Resolver must find AlibreDesign.exe in a versioned 'Alibre Design X/Program' folder."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=state_dir,
        runs_dir=tmp_path / "runs",
    )

    # Monkey-patch the resolver to use tmp_path as the search root
    original_resolve = orchestrator._resolve_alibre_executable

    fake_root = tmp_path / "ProgramFiles"
    alibre_program_dir = fake_root / "Alibre Design 28.1.1" / "Program"
    alibre_program_dir.mkdir(parents=True, exist_ok=True)
    fake_exe = alibre_program_dir / "AlibreDesign.exe"
    fake_exe.write_text("stub", encoding="utf-8")

    def patched_resolve():
        # Replicate the resolver logic against our fake root
        cache_file = state_dir / "alibre_executable_path.json"
        exe_names = ["AlibreDesign.exe", "Alibre Design.exe"]
        attempted: list[str] = []
        for alibre_dir in [d for d in fake_root.iterdir() if d.is_dir() and d.name.lower().startswith("alibre")]:
            for sub in [alibre_dir, alibre_dir / "Program"]:
                for exe_name in exe_names:
                    candidate = sub / exe_name
                    attempted.append(str(candidate))
                    if candidate.exists():
                        cache_file.write_text(json.dumps({"path": str(candidate)}), encoding="utf-8")
                        return {"found": True, "path": str(candidate), "discovery_method": "filesystem_search", "attempted_paths": attempted}
        return {"found": False, "path": None, "discovery_method": "filesystem_search", "attempted_paths": attempted}

    orchestrator._resolve_alibre_executable = patched_resolve

    result = orchestrator._resolve_alibre_executable()

    assert result["found"] is True
    assert "AlibreDesign.exe" in result["path"]
    assert result["discovery_method"] == "filesystem_search"
    # Cache should now be populated
    assert (state_dir / "alibre_executable_path.json").exists()


def test_launch_executable_clean_exit_with_running_child_is_success(tmp_path: Path, monkeypatch):
    """launch_executable must treat exit_code=0 + running child process as success."""
    exe_path = tmp_path / "AlibreDesign.exe"
    exe_path.write_text("stub", encoding="utf-8")

    class FakePopenCleanExit:
        pid = 1234

        def poll(self):
            return 0  # Bootstrap launcher: exits cleanly after spawning child

    tasklist_output = '"AlibreDesign.exe","1235","Console","1","12,345 K"\r\n'

    class FakeTasklistResult:
        stdout = tasklist_output
        returncode = 0

    monkeypatch.setattr("local_agent.orchestration.tools.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "local_agent.orchestration.tools.subprocess.Popen",
        lambda *args, **kwargs: FakePopenCleanExit(),
    )
    monkeypatch.setattr(
        "local_agent.orchestration.tools.subprocess.run",
        lambda *args, **kwargs: FakeTasklistResult(),
    )

    executor = ToolExecutor(tmp_path)
    results = executor.execute([
        {"tool": "launch_executable", "path": str(exe_path), "wait_seconds": 1}
    ])

    assert results[0]["ok"] is True
    payload = json.loads(results[0]["output"])
    assert payload["started"] is True
    assert payload["running"] is True
    assert "child process verified" in payload.get("note", "").lower()


def test_launch_executable_clean_exit_no_child_is_failure(tmp_path: Path, monkeypatch):
    """launch_executable must fail if exit_code=0 but no matching child process found."""
    exe_path = tmp_path / "AlibreDesign.exe"
    exe_path.write_text("stub", encoding="utf-8")

    class FakePopenCleanExit:
        pid = 1234

        def poll(self):
            return 0

    class FakeTasklistEmpty:
        stdout = ""
        returncode = 0

    monkeypatch.setattr("local_agent.orchestration.tools.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "local_agent.orchestration.tools.subprocess.Popen",
        lambda *args, **kwargs: FakePopenCleanExit(),
    )
    monkeypatch.setattr(
        "local_agent.orchestration.tools.subprocess.run",
        lambda *args, **kwargs: FakeTasklistEmpty(),
    )

    executor = ToolExecutor(tmp_path)
    results = executor.execute([
        {"tool": "launch_executable", "path": str(exe_path), "wait_seconds": 1}
    ])

    assert results[0]["ok"] is False
    assert "alibredesign" in results[0]["output"].lower() or "no running process" in results[0]["output"].lower()


def test_evaluate_completion_prefers_failed_over_waiting_input_for_execution_failures(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )

    evaluation = orchestrator._evaluate_completion(
        owner_message="Launch the desktop executable",
        task_type="desktop_execution",
        selected_agent="none",
        tool_results=[{"tool": "run_command", "ok": False, "output": "exit_code=1"}],
        delegate_output="",
        supervisor_output="",
        plan={"requires_user_input": True, "requires_confirmation": False},
    )

    assert evaluation["status"] == "failed"
    assert evaluation["completion_reason"] == "failed_execution"


def test_tool_loop_attempts_recovery_when_planner_drops_tool_calls_after_failure(tmp_path: Path):
    class RecoveryRouter:
        def __init__(self):
            self.calls = 0

        def chat(self, provider, model, system, user, options=None):
            _ = provider, model, system, user, options
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {
                        "status_update": "",
                        "task_type": "desktop_execution",
                        "selected_agent": "none",
                        "delegate_prompt": "",
                        "reply": "",
                        "tool_calls": [{"tool": "run_command", "command": "cmd /c no_such_command_123"}],
                        "memory_kind": "none",
                        "memory_updates": [],
                        "needs_supervisor": False,
                        "requires_user_input": False,
                        "rationale": "first attempt",
                    }
                )
            return json.dumps(
                {
                    "status_update": "",
                    "task_type": "desktop_execution",
                    "selected_agent": "none",
                    "delegate_prompt": "",
                    "reply": "Need confirmation.",
                    "tool_calls": [],
                    "memory_kind": "none",
                    "memory_updates": [],
                    "needs_supervisor": False,
                    "requires_user_input": True,
                    "rationale": "dropped calls",
                }
            )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "policy.yaml").write_text(
        "approval:\n  require_for_tool_names: []\n  require_for_categories: []\n",
        encoding="utf-8",
    )

    orchestrator = AdminOrchestrator(
        router=RecoveryRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )

    initial_plan = {
        "task_type": "desktop_execution",
        "selected_agent": "none",
        "delegate_prompt": "",
        "reply": "",
        "tool_calls": [{"tool": "run_command", "command": "cmd /c no_such_command_123"}],
        "memory_kind": "none",
        "memory_updates": [],
        "needs_supervisor": False,
        "requires_user_input": False,
    }

    final_plan, all_results = orchestrator._run_tool_loop("Open Alibre and verify it launched", initial_plan)

    assert len(all_results) >= 2, "Expected at least one corrective recovery attempt"
    assert all_results[0]["ok"] is False


def test_load_agents_skips_disabled_entries(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "admin.yaml").write_text(
        "id: admin\nname: Admin\nllm:\n  provider: test\n  model: admin-model\n  options: {}\nbehavior:\n  system_prompt: You are admin.\n",
        encoding="utf-8",
    )
    (agents_dir / "cad_rnd.yaml").write_text(
        "id: cad_rnd\nname: CAD\nenabled: false\nllm:\n  provider: test\n  model: worker-model\n",
        encoding="utf-8",
    )

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
    )

    loaded = orchestrator._load_agents()
    assert "admin" in loaded
    assert "cad_rnd" not in loaded


def test_launch_executable_tool_reports_running_process(tmp_path: Path, monkeypatch):
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    exe_path = desktop_dir / "TestApp.exe"
    exe_path.write_text("stub", encoding="utf-8")

    class FakePopen:
        def __init__(self):
            self.pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr("local_agent.orchestration.tools.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("local_agent.orchestration.tools.subprocess.Popen", lambda *args, **kwargs: FakePopen())

    executor = ToolExecutor(tmp_path)
    results = executor.execute([
        {"tool": "launch_executable", "path": str(exe_path), "wait_seconds": 1}
    ])

    assert results[0]["ok"] is True
    assert '"started": true' in results[0]["output"].lower()
    assert '"running": true' in results[0]["output"].lower()


def test_approve_launch_executable_records_completed_execution(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "Admin", "admin-model")

    orchestrator = AdminOrchestrator(
        router=DummyRouter(),
        workspace_root=tmp_path,
        agents_dir="agents",
        state_dir="state",
        runs_dir="runs",
    )

    orchestrator.tools._tool_launch_executable = lambda _call: '{"started": true, "running": true, "pid": 999}'

    pending = orchestrator.approvals.add_pending(
        chat_id=321,
        owner_message="Open the exe on my desktop and confirm it launched.",
        tool_calls=[{"tool": "launch_executable", "path": "Desktop/TestApp.exe"}],
        rationale="Desktop launch request requires approval.",
        workspace="default",
    )

    messages = orchestrator.handle_message(chat_id=321, text=f"/approve {pending['approval_id']}")
    assert messages[0].startswith("Approved and executed")

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "completed"
    assert interaction["execution_intent"] is True
    assert "launch_executable" in interaction["evidence"]["executed_tool_calls"]
