from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_agent.integrations import telegram_bot
from local_agent.orchestration import planner as planner_module
from local_agent.orchestration import supervisor as supervisor_module
from local_agent.orchestration.admin import AdminOrchestrator
from local_agent.orchestration.parse_utils import PlanParseError


class _ParseFailureRouter:
    def __init__(self, planner_response: str, supervisor_response: str, worker_response: str = "Worker output."):
        self.planner_response = planner_response
        self.supervisor_response = supervisor_response
        self.worker_response = worker_response
        self.calls: list[dict] = []

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
            return self.planner_response
        if model == "worker-model":
            return self.worker_response
        if model == "supervisor-model":
            return self.supervisor_response
        raise AssertionError(f"Unexpected model {model}")


class _DummyIntakeConductor:
    def __init__(self, *args, **kwargs):
        pass

    def start(self, session, initial_goal):
        return "intake"

    def reply(self, session, text):
        return "intake"


class _DummyRunner:
    def __init__(self):
        self.anthropic_client = object()


def _write_agent(path: Path, agent_id: str, model: str, provider: str = "ollama") -> None:
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


def _latest_admin_run(tmp_path: Path) -> dict:
    run_files = sorted((tmp_path / "runs").glob("admin-*.json"), key=lambda path: path.stat().st_mtime)
    assert run_files, "Expected at least one admin run artifact"
    return json.loads(run_files[-1].read_text(encoding="utf-8"))


def test_malformed_planner_json_blocks_run_and_records_raw_snippet(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")

    router = _ParseFailureRouter(planner_response="{not valid json", supervisor_response="{}")
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=11, text="inspect the workspace")

    assert any("blocked" in message.lower() for message in messages)
    assert any("raw snippet" in message.lower() for message in messages)

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "blocked"
    assert interaction["completion_reason"] == "planner_parse_failure"
    assert "not valid json" in str(interaction.get("parse_error_raw", ""))

    run_payload = _latest_admin_run(tmp_path)
    assert run_payload["status"] == "blocked"
    assert run_payload["completion_reason"] == "planner_parse_failure"
    assert "not valid json" in str(run_payload.get("parse_error_raw", ""))


def test_planner_fault_injection_is_env_gated_and_one_shot(monkeypatch):
    monkeypatch.setenv("CHAD_FORCE_PARSE_FAILURE", "1")
    monkeypatch.setattr(planner_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)

    with pytest.raises(PlanParseError) as exc_info:
        planner_module._parse_json('{"ok": true}')

    assert "fault-injected malformed output" in exc_info.value.raw
    assert planner_module._parse_json('{"ok": true}') == {"ok": True}


def test_supervisor_fault_injection_is_env_gated_and_one_shot(monkeypatch):
    monkeypatch.setenv("CHAD_FORCE_PARSE_FAILURE", "1")
    monkeypatch.setattr(supervisor_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)

    with pytest.raises(PlanParseError) as exc_info:
        supervisor_module._parse_json('{"ok": true}')

    assert "fault-injected malformed output" in exc_info.value.raw
    assert supervisor_module._parse_json('{"ok": true}') == {"ok": True}


def test_empty_tool_calls_still_complete_normally(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")

    planner_response = json.dumps(
        {
            "status_update": "Ready.",
            "selected_agent": "none",
            "delegate_prompt": "",
            "reply": "Done.",
            "tool_calls": [],
            "memory_kind": "none",
            "memory_updates": [],
            "needs_supervisor": False,
            "requires_user_input": False,
            "rationale": "No tools needed.",
            "tool_research": "",
            "requires_confirmation": False,
        }
    )
    router = _ParseFailureRouter(planner_response=planner_response, supervisor_response="{}")
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=12, text="say hello")

    assert any("Done." in message for message in messages)

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "completed"
    assert interaction["completion_reason"] == "criteria_met"

    run_payload = _latest_admin_run(tmp_path)
    assert run_payload["status"] == "completed"
    assert run_payload["completion_reason"] == "criteria_met"


def test_list_files_task_completes_normally_when_fault_hook_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAD_FORCE_PARSE_FAILURE", raising=False)
    monkeypatch.setattr(planner_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)
    monkeypatch.setattr(supervisor_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")

    planner_response = json.dumps(
        {
            "status_update": "Listing files.",
            "selected_agent": "none",
            "delegate_prompt": "",
            "reply": "Here are the files.",
            "tool_calls": [{"tool": "list_files", "path": ".", "limit": 5}],
            "memory_kind": "none",
            "memory_updates": [],
            "needs_supervisor": False,
            "requires_user_input": False,
            "rationale": "Need file listing.",
            "tool_research": "",
            "requires_confirmation": False,
        }
    )
    router = _ParseFailureRouter(planner_response=planner_response, supervisor_response="{}")
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=14, text="list all files")

    assert any("Here are the files." in message for message in messages)
    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "completed"
    assert interaction["completion_reason"] == "criteria_met"


def test_supervisor_malformed_json_blocks_run_and_records_raw_snippet(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "worker-model")
    _write_agent(agents_dir / "supervisor.yaml", "supervisor", "supervisor-model", provider="anthropic")

    planner_response = json.dumps(
        {
            "status_update": "Delegating.",
            "selected_agent": "software_dev",
            "delegate_prompt": "Inspect the issue.",
            "reply": "Working.",
            "tool_calls": [],
            "memory_kind": "none",
            "memory_updates": [],
            "needs_supervisor": True,
            "requires_user_input": False,
            "rationale": "Needs a worker.",
            "tool_research": "",
            "requires_confirmation": False,
        }
    )
    router = _ParseFailureRouter(
        planner_response=planner_response,
        supervisor_response="not valid json at all",
        worker_response="Worker output.",
    )
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    messages = orchestrator.handle_message(chat_id=13, text="review this change")

    assert any("blocked" in message.lower() for message in messages)
    assert any("raw snippet" in message.lower() for message in messages)

    interaction = _read_last_interaction(tmp_path)
    assert interaction["status"] == "blocked"
    assert interaction["completion_reason"] == "supervisor_parse_failure"
    assert "not valid json" in str(interaction.get("parse_error_raw", ""))

    run_payload = _latest_admin_run(tmp_path)
    assert run_payload["status"] == "blocked"
    assert run_payload["completion_reason"] == "supervisor_parse_failure"


def test_telegram_bot_relays_blocked_parse_failure(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")

    router = _ParseFailureRouter(planner_response="{broken json", supervisor_response="{}")
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    monkeypatch.setattr(telegram_bot, "_build_admin_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(telegram_bot, "_build_runner", lambda: _DummyRunner())
    monkeypatch.setattr(telegram_bot, "IntakeConductor", _DummyIntakeConductor)

    bot = telegram_bot.TelegramBotRunner(token="token")
    sent: list[tuple[int, str]] = []
    bot._send_message = lambda chat_id, text: sent.append((chat_id, text))

    bot._reply_with_agent(99, "/work inspect the workspace")

    assert sent, "Expected Telegram output"
    assert any("blocked" in text.lower() for _, text in sent)
    assert any("raw snippet" in text.lower() for _, text in sent)


def test_telegram_bot_relays_blocked_supervisor_parse_failure(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir / "admin.yaml", "admin", "admin-model")
    _write_agent(agents_dir / "software_dev.yaml", "software_dev", "worker-model")
    _write_agent(agents_dir / "supervisor.yaml", "supervisor", "supervisor-model", provider="anthropic")

    planner_response = json.dumps(
        {
            "status_update": "Delegating.",
            "selected_agent": "software_dev",
            "delegate_prompt": "Inspect the issue.",
            "reply": "Working.",
            "tool_calls": [],
            "memory_kind": "none",
            "memory_updates": [],
            "needs_supervisor": True,
            "requires_user_input": False,
            "rationale": "Needs a worker.",
            "tool_research": "",
            "requires_confirmation": False,
        }
    )
    router = _ParseFailureRouter(
        planner_response=planner_response,
        supervisor_response="not valid json at all",
        worker_response="Worker output.",
    )
    orchestrator = AdminOrchestrator(
        router=router,
        workspace_root=tmp_path,
        agents_dir=agents_dir,
        state_dir="state",
        runs_dir="runs",
    )

    monkeypatch.setattr(telegram_bot, "_build_admin_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(telegram_bot, "_build_runner", lambda: _DummyRunner())
    monkeypatch.setattr(telegram_bot, "IntakeConductor", _DummyIntakeConductor)

    bot = telegram_bot.TelegramBotRunner(token="token")
    sent: list[tuple[int, str]] = []
    bot._send_message = lambda chat_id, text: sent.append((chat_id, text))

    bot._reply_with_agent(100, "/work review this change")

    assert sent, "Expected Telegram output"
    assert any("blocked" in text.lower() for _, text in sent)
    assert any("raw snippet" in text.lower() for _, text in sent)
