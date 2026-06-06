from __future__ import annotations

import os
import time
from fastapi.testclient import TestClient

from local_agent.integrations import google_chat_bot
from local_agent.integrations import telegram_bot
from local_agent.orchestration.memory import MemoryStore
from local_agent.workflows.intake import IntakeSession


class _DummyRunner:
    def __init__(self):
        self.anthropic_client = object()

    def run_business_plan(self, goal: str, business_profile: str, on_stage):
        return {
            "final_memo": {"summary": "ok"},
            "confidence": 1.0,
            "total_estimated_cost_usd": 0.0,
            "status": "completed",
            "run_path": "runs/test.json",
            "escalation_questions": [],
        }


class _DummyAdmin:
    def __init__(self):
        self.calls: list[tuple[int, str]] = []
        self.known_agent_ids = [
            "admin",
            "cad_rnd",
            "codex",
            "pwasher_marketing",
            "software_dev",
            "software_marketing",
            "supervisor",
        ]

    def handle_message(self, chat_id: int, text: str) -> list[str]:
        self.calls.append((chat_id, text))
        cleaned = text.strip()
        if cleaned == "/agents":
            return [
                "Available agents:\n"
                + "\n".join(f"- {agent_id}: test [{agent_id}-model]" for agent_id in self.known_agent_ids)
            ]
        if cleaned.lower().startswith("/ask "):
            body = cleaned[5:].strip()
            first_space = body.find(" ")
            if first_space > 0:
                agent_id = body[:first_space].strip()
                if agent_id not in self.known_agent_ids:
                    return [f"Unknown agent '{agent_id}'. Use /agents to see valid IDs."]
        return [f"admin:{text}"]


class _DummyIntakeConductor:
    def __init__(self, anthropic_client, prompt_dir):
        _ = anthropic_client
        _ = prompt_dir

    def start(self, session: IntakeSession, initial_text: str) -> str:
        _ = session
        _ = initial_text
        return "intake-question"

    def reply(self, session: IntakeSession, user_message: str) -> str:
        _ = session
        _ = user_message
        return "intake-followup"


def _build_bot(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)

    admin = _DummyAdmin()
    monkeypatch.setattr(telegram_bot, "_build_runner", lambda: _DummyRunner())
    monkeypatch.setattr(telegram_bot, "_build_admin_orchestrator", lambda: admin)
    monkeypatch.setattr(telegram_bot, "IntakeConductor", _DummyIntakeConductor)

    bot = telegram_bot.TelegramBotRunner(token="token")
    sent: list[tuple[int, str]] = []
    bot._send_message = lambda chat_id, text: sent.append((chat_id, text))
    return bot, admin, sent


def test_work_command_with_bot_mention_routes_to_workspace_and_clears_intake(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)

    chat_id = 42
    bot._set_mode(chat_id, "plan")
    bot._intake_sessions[chat_id] = IntakeSession(business_profile="rocket_wash")

    bot._reply_with_agent(chat_id, "/work@GogaChadPC_bot implement all mates")

    assert chat_id not in bot._intake_sessions
    assert admin.calls == [(chat_id, "implement all mates")]
    assert sent[-1] == (chat_id, "admin:implement all mates")


def test_work_markdown_deeplink_routes_to_workspace_and_clears_intake(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)

    chat_id = 142
    bot._set_mode(chat_id, "plan")
    bot._intake_sessions[chat_id] = IntakeSession(business_profile="rocket_wash")

    bot._reply_with_agent(chat_id, "[/work](tg://bot_command?command=work) list files in desktop/RW_Media")

    assert chat_id not in bot._intake_sessions
    assert admin.calls == [(chat_id, "list files in desktop/RW_Media")]
    assert sent[-1] == (chat_id, "admin:list files in desktop/RW_Media")


def test_work_plain_deeplink_routes_to_workspace(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)

    chat_id = 143
    bot._reply_with_agent(chat_id, "tg://bot_command?command=work list files in desktop/RW_Media")

    assert admin.calls == [(chat_id, "list files in desktop/RW_Media")]
    assert sent[-1] == (chat_id, "admin:list files in desktop/RW_Media")


def test_plan_command_with_bot_mention_starts_intake(monkeypatch):
    bot, _admin, sent = _build_bot(monkeypatch)

    chat_id = 77
    bot._reply_with_agent(chat_id, "/plan@GogaChadPC_bot rocket_wash|build next quarter plan")

    assert chat_id in bot._intake_sessions
    assert bot._mode(chat_id) == "plan"
    assert sent[0][1].startswith("Starting intake for *rocket_wash*")
    assert sent[1] == (chat_id, "intake-question")


def test_instance_lock_blocks_second_live_pid(tmp_path, monkeypatch):
    lock_path = tmp_path / "telegram_bot.lock"
    lock_path.write_text("12345\n", encoding="utf-8")

    lock = telegram_bot._TelegramBotInstanceLock(lock_path)
    monkeypatch.setattr(lock, "_pid_exists", lambda pid: True)

    try:
        lock.acquire()
        assert False, "Expected RuntimeError when lock pid is alive"
    except RuntimeError as exc:
        assert "already running" in str(exc).lower()


def test_instance_lock_replaces_stale_pid(tmp_path, monkeypatch):
    lock_path = tmp_path / "telegram_bot.lock"
    lock_path.write_text("999999\n", encoding="utf-8")
    stale_time = time.time() - 120
    os.utime(lock_path, (stale_time, stale_time))

    lock = telegram_bot._TelegramBotInstanceLock(lock_path)
    monkeypatch.setattr(lock, "_pid_exists", lambda pid: False)

    lock.acquire()
    try:
        contents = lock_path.read_text(encoding="utf-8").strip()
        assert contents == str(os.getpid())
    finally:
        lock.release()

    assert not lock_path.exists()


def test_instance_lock_blocks_recent_pid_when_pid_check_false(tmp_path, monkeypatch):
    lock_path = tmp_path / "telegram_bot.lock"
    lock_path.write_text("7777\n", encoding="utf-8")

    lock = telegram_bot._TelegramBotInstanceLock(lock_path)
    monkeypatch.setattr(lock, "_pid_exists", lambda pid: False)

    try:
        lock.acquire()
        assert False, "Expected RuntimeError for recent pid lock to avoid lock stealing"
    except RuntimeError as exc:
        assert "lock is currently held" in str(exc).lower()


def test_instance_lock_blocks_recent_empty_lock(tmp_path):
    lock_path = tmp_path / "telegram_bot.lock"
    lock_path.write_text("", encoding="utf-8")

    lock = telegram_bot._TelegramBotInstanceLock(lock_path)

    try:
        lock.acquire()
        assert False, "Expected RuntimeError when lock file exists but startup is in progress"
    except RuntimeError as exc:
        assert "startup already in progress" in str(exc).lower()


def test_instance_lock_replaces_stale_empty_lock(tmp_path):
    lock_path = tmp_path / "telegram_bot.lock"
    lock_path.write_text("", encoding="utf-8")
    stale_time = time.time() - 120
    os.utime(lock_path, (stale_time, stale_time))

    lock = telegram_bot._TelegramBotInstanceLock(lock_path)

    lock.acquire()
    try:
        contents = lock_path.read_text(encoding="utf-8").strip()
        assert contents == str(os.getpid())
    finally:
        lock.release()


def test_agents_command_returns_expected_agent_ids(monkeypatch):
    bot, _admin, sent = _build_bot(monkeypatch)

    bot._reply_with_agent(5, "/agents")

    assert len(sent) == 1
    payload = sent[0][1]
    assert "Available agents:" in payload
    for expected in (
        "admin",
        "cad_rnd",
        "codex",
        "pwasher_marketing",
        "software_dev",
        "software_marketing",
        "supervisor",
    ):
        assert f"- {expected}:" in payload


def test_unknown_agent_id_returns_safe_error(monkeypatch):
    bot, _admin, sent = _build_bot(monkeypatch)

    bot._reply_with_agent(11, "/ask no_such_agent draft plan")

    assert sent[-1] == (11, "Unknown agent 'no_such_agent'. Use /agents to see valid IDs.")


def test_autonomy_command_routes_to_admin_handler(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)

    bot._reply_with_agent(12, "/autonomy")

    assert admin.calls == [(12, "/autonomy")]
    assert sent[-1] == (12, "admin:/autonomy")


def test_allowlisted_user_succeeds_when_allowlist_enabled(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42,77")

    bot._reply_with_agent(42, "/work ping")

    assert admin.calls == [(42, "ping")]
    assert sent[-1] == (42, "admin:ping")


def test_non_allowlisted_user_is_blocked(monkeypatch):
    bot, admin, sent = _build_bot(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")

    bot._reply_with_agent(7, "/work ping")

    assert admin.calls == []
    assert sent == [(7, "You are not authorized to use this bot.")]


def test_added_to_space_event_returns_onboarding_response(monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_VERIFICATION_TOKEN", raising=False)
    client = TestClient(google_chat_bot.app)

    response = client.post("/google-chat/events", json={"type": "ADDED_TO_SPACE"})

    assert response.status_code == 200
    assert response.json() == {"text": "Local Agent connected. Use /agents to list worker IDs."}


def test_long_output_is_truncated_clearly_and_safely(monkeypatch):
    class _LongOutputRunner:
        def __init__(self):
            self.anthropic_client = object()

        def run_business_plan(self, goal: str, business_profile: str, on_stage):
            _ = goal
            _ = business_profile
            _ = on_stage
            return {
                "final_memo": {"summary": "x" * 2000},
                "confidence": 0.99,
                "total_estimated_cost_usd": 0.1234,
                "status": "completed",
                "run_path": "runs/test-long.json",
                "escalation_questions": [],
            }

    monkeypatch.setenv("TELEGRAM_MAX_RESPONSE_CHARS", "120")
    bot, _admin, sent = _build_bot(monkeypatch)
    bot.runner = _LongOutputRunner()
    session = IntakeSession(business_profile="rocket_wash", refined_goal="test long output")

    bot._run_pipeline(99, session)

    assert sent[0] == (99, "Scope confirmed. Launching 5-stage pipeline for *rocket_wash*...")
    final_message = sent[-1][1]
    assert final_message.endswith("\n\n[truncated]")
    assert len(final_message) <= 120


def test_status_renderer_shows_evidence_marker_for_completed_execution_task(tmp_path):
    memory = MemoryStore(tmp_path / "state")
    memory.append_interaction(
        {
            "chat_id": 1,
            "user_message": "Install dependencies.",
            "selected_agent": "admin",
            "status": "completed",
            "summary": "Install completed.",
            "execution_intent": True,
            "evidence": {
                "executed_tool_calls": ["install_python_packages"],
                "successful_tools": ["install_python_packages"],
                "verification_signals": ["successful_tools:1"],
                "artifact_paths": [],
                "notes": [],
            },
        }
    )

    rendered = memory.render_status()
    assert "[proof:1t]" in rendered
