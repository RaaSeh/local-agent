from __future__ import annotations

import json

from local_agent.orchestration.tools import ToolExecutor
from local_agent.orchestration.registry import TaskRegistry
from local_agent.policy.approvals import PolicyEngine


class _FakeHttpResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload

    def getcode(self) -> int:
        return self.status


def test_tool_executor_respects_configurable_call_budget(tmp_path):
    calls = [{"tool": "list_files", "path": ".", "limit": 1} for _ in range(12)]

    default_executor = ToolExecutor(tmp_path)
    default_results = default_executor.execute(calls)
    assert len(default_results) == 5

    expanded_executor = ToolExecutor(tmp_path, max_tool_calls=12)
    expanded_results = expanded_executor.execute(calls)
    assert len(expanded_results) == 12


def test_http_request_rejects_non_https_and_allowlist(tmp_path, monkeypatch):
    executor = ToolExecutor(tmp_path)

    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "example.com")
    non_https = executor.execute(
        [
            {
                "tool": "http_request",
                "method": "GET",
                "url": "http://example.com/api",
            }
        ]
    )[0]
    assert non_https["ok"] is False
    assert "HTTPS" in non_https["output"]

    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "")
    empty_allowlist = executor.execute(
        [
            {
                "tool": "http_request",
                "method": "GET",
                "url": "https://example.com/api",
            }
        ]
    )[0]
    assert empty_allowlist["ok"] is False
    assert "not allowlisted" in empty_allowlist["output"]

    def _fake_urlopen_default_allowlist(request, timeout=30, context=None):
        _ = timeout
        _ = context
        assert request.full_url == "https://httpbin.org/get"
        return _FakeHttpResponse(payload=b'{"origin":"127.0.0.1"}', status=200)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_default_allowlist)
    default_allowlisted = executor.execute(
        [
            {
                "tool": "http_request",
                "method": "GET",
                "url": "https://httpbin.org/get",
            }
        ]
    )[0]
    assert default_allowlisted["ok"] is True

    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "allowed.example")
    not_allowlisted = executor.execute(
        [
            {
                "tool": "http_request",
                "method": "GET",
                "url": "https://example.com/api",
            }
        ]
    )[0]
    assert not_allowlisted["ok"] is False
    assert "not allowlisted" in not_allowlisted["output"]


def test_http_request_get_allowlisted_host_masks_headers(tmp_path, monkeypatch):
    executor = ToolExecutor(tmp_path)
    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "example.com")

    def _fake_urlopen(request, timeout=30, context=None):
        _ = timeout
        _ = context
        assert request.full_url == "https://example.com/v1/test"
        return _FakeHttpResponse(payload=b'{"result":"ok"}', status=200)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = executor.execute(
        [
            {
                "tool": "http_request",
                "method": "GET",
                "url": "https://example.com/v1/test",
                "headers": {"Authorization": "Bearer super-secret-token"},
                "timeout": 15,
            }
        ]
    )[0]

    assert result["ok"] is True
    payload = json.loads(result["output"])
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert "Authorization" not in result["output"]
    assert "super-secret-token" not in result["output"]


def test_policy_classifies_http_request_for_approval(monkeypatch):
    policy = PolicyEngine()

    monkeypatch.delenv("HTTP_TOOL_PAID_HOSTS", raising=False)
    external = policy.classify_tool_call({"tool": "http_request", "url": "https://example.com/api"})
    assert external.requires_approval is True
    assert external.category == "external_action"

    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "example.com")
    allowlisted_get = policy.classify_tool_call(
        {"tool": "http_request", "method": "GET", "url": "https://example.com/api"}
    )
    assert allowlisted_get.requires_approval is False

    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "")
    default_allowlisted_get = policy.classify_tool_call(
        {"tool": "http_request", "method": "GET", "url": "https://httpbin.org/get"}
    )
    assert default_allowlisted_get.requires_approval is False

    monkeypatch.setenv("HTTP_TOOL_PAID_HOSTS", "paid.example")
    spend = policy.classify_tool_call({"tool": "http_request", "url": "https://paid.example/v1/lookup"})
    assert spend.requires_approval is True
    assert spend.category == "spend_money"


def test_policy_allows_plain_text_rename_but_blocks_workflow_changes():
    policy = PolicyEngine()

    low_risk = policy.classify_tool_call(
        {"tool": "rename_path", "path": "lead_01.txt", "target": "123.txt"}
    )
    risky = policy.classify_tool_call(
        {"tool": "rename_path", "path": "agents/foo.yaml", "target": "agents/foo_old.yaml"}
    )

    assert low_risk.requires_approval is False
    assert low_risk.category == "safe_internal_action"
    assert risky.requires_approval is True
    assert risky.category == "workflow_change"


def test_registry_confirmation_required_http_get_default_allowlist(monkeypatch):
    registry = TaskRegistry()
    monkeypatch.setenv("HTTP_TOOL_ALLOWLIST", "")

    requires_confirmation = registry.confirmation_required(
        "tool_acquisition",
        [{"tool": "http_request", "method": "GET", "url": "https://httpbin.org/get"}],
    )
    assert requires_confirmation is False

    requires_confirmation_other = registry.confirmation_required(
        "tool_acquisition",
        [{"tool": "http_request", "method": "GET", "url": "https://example.com/api"}],
    )
    assert requires_confirmation_other is True
