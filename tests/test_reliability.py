"""Reliability hardening tests: retries, timeouts, error surfacing, partial progress.

Covers Phase 2 goals from IMPLEMENTATION_ROADMAP.md:
  - Retries and timeouts per provider call
  - Run status model (failed / blocked / completed)
  - Clear error messages surfaced on failure
  - Partial stage artifacts preserved when a mid-run stage fails

Rerun-with-same-input is noted as a Phase 2 gap at the bottom of this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from local_agent.providers.anthropic_client import AnthropicClient
from local_agent.providers.openai_client import OpenAIClient
from local_agent.providers.perplexity_client import PerplexityClient
from local_agent.orchestrator.policies import EscalationPolicy
from local_agent.workflows.business_plan import run_business_plan


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ok_response_openai(text: str = '{"summary": "ok"}') -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.text = json.dumps({"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    resp.raise_for_status.return_value = None
    return resp


def _ok_response_anthropic(text: str = '{"summary": "ok"}') -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    resp.raise_for_status.return_value = None
    return resp


def _error_response(status_code: int) -> MagicMock:
    """Build a mock HTTP error response for retryable status codes."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    resp.text = f"HTTP {status_code}"
    resp.request = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status_code}", request=resp.request, response=resp
    )
    return resp


def _patch_client(module_path: str, post_side_effects: list):
    """
    Context-manager helper that patches httpx.Client inside *module_path* so
    that each ``with httpx.Client(...) as c: c.post(...)`` call consumes the
    next item from *post_side_effects*.
    """
    inner = MagicMock()
    inner.post.side_effect = post_side_effects

    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=inner)
    ctx_mgr.__exit__ = MagicMock(return_value=False)

    mock_cls = MagicMock(return_value=ctx_mgr)
    return patch(f"{module_path}.httpx.Client", mock_cls), inner


# ---------------------------------------------------------------------------
# FakeClient – workflow-level helpers
# ---------------------------------------------------------------------------

class _FakeClient:
    """Deterministic fake provider client for workflow-level tests."""

    def __init__(self, provider: str, model: str, payloads: list[dict]):
        self.provider = provider
        self.default_model = model
        self._payloads = list(payloads)

    def chat(self, model: str, system: str, user: str, **_kwargs) -> dict:
        if not self._payloads:
            raise AssertionError("_FakeClient: no more payloads configured")
        payload = self._payloads.pop(0)
        return {
            "text": json.dumps(payload),
            "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            "provider": self.provider,
            "model": model,
        }


class _FailingClient:
    """Client that always raises the given exception."""

    def __init__(self, provider: str, model: str, exc: Exception):
        self.provider = provider
        self.default_model = model
        self._exc = exc

    def chat(self, model: str, system: str, user: str, **_kwargs) -> dict:
        raise self._exc


class _PartialClient:
    """Succeeds for the first N payloads, then raises."""

    def __init__(self, provider: str, model: str, payloads: list[dict], then_raise: Exception | None = None):
        self.provider = provider
        self.default_model = model
        self._payloads = list(payloads)
        self._then_raise = then_raise

    def chat(self, model: str, system: str, user: str, **_kwargs) -> dict:
        if self._payloads:
            payload = self._payloads.pop(0)
            return {
                "text": json.dumps(payload),
                "usage": {"prompt_tokens": 100, "completion_tokens": 60},
                "provider": self.provider,
                "model": model,
            }
        if self._then_raise is not None:
            raise self._then_raise
        raise AssertionError("_PartialClient: exhausted payloads with no failure configured")


_GOOD_PACKET = {
    "summary": "summary",
    "assumptions": [],
    "evidence": ["e"],
    "risks": ["r"],
    "recommendations": ["rec"],
    "confidence": 0.8,
    "next_steps": ["next"],
}


def _write_prompts(prompt_dir: Path) -> None:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for name in ("research.txt", "planner.txt", "critic.txt", "final_memo.txt"):
        (prompt_dir / name).write_text("Dummy prompt", encoding="utf-8")


def _make_full_run_clients():
    """Return (perplexity, openai, anthropic) fake clients for a happy-path run."""
    perplexity = _FakeClient("perplexity", "sonar-pro", [_GOOD_PACKET.copy()])
    openai = _FakeClient("openai", "gpt-4.1-mini", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])
    anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])
    return perplexity, openai, anthropic


# ===========================================================================
# 1. Provider-level retry tests (HTTP mocking, no real network calls)
# ===========================================================================

class TestOpenAIClientRetries:
    def test_retries_on_429_then_succeeds(self):
        """A single 429 is retried and the second attempt succeeds."""
        ok = _ok_response_openai()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [_error_response(429), ok],
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep") as mock_sleep:
            client = OpenAIClient(api_key="test-key")
            result = client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert result["provider"] == "openai"
        assert inner.post.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retries_on_500_then_succeeds(self):
        ok = _ok_response_openai()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [_error_response(500), ok],
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep"):
            client = OpenAIClient(api_key="test-key")
            result = client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert result["provider"] == "openai"
        assert inner.post.call_count == 2

    def test_retries_on_timeout_then_succeeds(self):
        """A ReadTimeout is retried; the second attempt delivers a valid response."""
        ok = _ok_response_openai()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [httpx.ReadTimeout("timed out"), ok],
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep") as mock_sleep:
            client = OpenAIClient(api_key="test-key")
            result = client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert result["provider"] == "openai"
        assert inner.post.call_count == 2
        assert mock_sleep.call_count >= 1

    def test_exhausts_retries_and_raises_http_error(self):
        """All MAX_RETRIES=4 attempts return 503; the exception is re-raised."""
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [_error_response(503)] * 4,
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep"):
            client = OpenAIClient(api_key="test-key")
            with pytest.raises(httpx.HTTPStatusError):
                client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert inner.post.call_count == 4

    def test_exhausts_retries_and_raises_on_repeated_timeout(self):
        """MAX_RETRIES consecutive timeouts bubble up as TimeoutException."""
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [httpx.ReadTimeout("timed out")] * 4,
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep"):
            client = OpenAIClient(api_key="test-key")
            with pytest.raises(httpx.TimeoutException):
                client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert inner.post.call_count == 4

    def test_non_retryable_4xx_raises_immediately(self):
        """A 401 is not retried — it raises immediately on the first attempt."""
        patch_ctx, inner = _patch_client(
            "local_agent.providers.openai_client",
            [_error_response(401)],
        )
        with patch_ctx, patch("local_agent.providers.openai_client.time.sleep"):
            client = OpenAIClient(api_key="bad-key")
            with pytest.raises(httpx.HTTPStatusError):
                client.chat(model="gpt-4.1-mini", system="sys", user="usr")

        assert inner.post.call_count == 1


class TestAnthropicClientRetries:
    def test_retries_on_429_then_succeeds(self):
        ok = _ok_response_anthropic()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.anthropic_client",
            [_error_response(429), ok],
        )
        with patch_ctx, patch("local_agent.providers.anthropic_client.time.sleep") as mock_sleep:
            client = AnthropicClient(api_key="test-key")
            result = client.chat(model="claude-sonnet-4-20250514", system="sys", user="usr")

        assert result["provider"] == "anthropic"
        assert inner.post.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retries_on_timeout_then_succeeds(self):
        ok = _ok_response_anthropic()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.anthropic_client",
            [httpx.ReadTimeout("timed out"), ok],
        )
        with patch_ctx, patch("local_agent.providers.anthropic_client.time.sleep"):
            client = AnthropicClient(api_key="test-key")
            result = client.chat(model="claude-sonnet-4-20250514", system="sys", user="usr")

        assert result["provider"] == "anthropic"
        assert inner.post.call_count == 2


class TestPerplexityClientRetries:
    def test_retries_on_503_then_succeeds(self):
        ok = _ok_response_openai()  # Perplexity uses the same OpenAI-compatible schema
        patch_ctx, inner = _patch_client(
            "local_agent.providers.perplexity_client",
            [_error_response(503), ok],
        )
        with patch_ctx, patch("local_agent.providers.perplexity_client.time.sleep") as mock_sleep:
            client = PerplexityClient(api_key="test-key")
            result = client.chat(model="sonar-pro", system="sys", user="usr")

        assert result["provider"] == "perplexity"
        assert inner.post.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retries_on_timeout_then_succeeds(self):
        ok = _ok_response_openai()
        patch_ctx, inner = _patch_client(
            "local_agent.providers.perplexity_client",
            [httpx.ReadTimeout("timed out"), ok],
        )
        with patch_ctx, patch("local_agent.providers.perplexity_client.time.sleep"):
            client = PerplexityClient(api_key="test-key")
            result = client.chat(model="sonar-pro", system="sys", user="usr")

        assert result["provider"] == "perplexity"
        assert inner.post.call_count == 2


# ===========================================================================
# 2. Workflow-level failure handling
# ===========================================================================

class TestWorkflowFailedStatus:
    def test_first_stage_failure_returns_failed_status(self, tmp_path):
        """If the research stage raises, the workflow returns status='failed'."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FailingClient("perplexity", "sonar-pro", RuntimeError("quota exceeded"))
        openai = _FakeClient("openai", "gpt-4.1-mini", [])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["status"] == "failed"
        assert result["failed_stage"] == "research"
        assert "failed_stage" in result
        assert result["run_id"]

    def test_failed_error_message_names_stage_and_cause(self, tmp_path):
        """error_message must identify the stage and include the original exception text."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        exc_text = "OpenAI quota exhausted"
        openai = _FailingClient("openai", "gpt-4.1-mini", RuntimeError(exc_text))
        perplexity = _FakeClient("perplexity", "sonar-pro", [_GOOD_PACKET.copy()])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["status"] == "failed"
        assert result["failed_stage"] == "plan"
        msg = result["error_message"]
        assert "plan" in msg
        assert exc_text in msg

    def test_timeout_produces_failed_status_with_message(self, tmp_path):
        """A provider timeout is treated as a failure with an actionable message."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FailingClient("perplexity", "sonar-pro", httpx.ReadTimeout("connection timed out"))
        openai = _FakeClient("openai", "gpt-4.1-mini", [])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["status"] == "failed"
        msg = result["error_message"]
        assert msg  # non-empty
        assert "connectivity" in msg.lower() or "failed" in msg.lower()

    def test_failed_result_has_required_keys(self, tmp_path):
        """A failed run result must carry the same envelope keys as a completed run."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FailingClient("perplexity", "sonar-pro", RuntimeError("boom"))
        openai = _FakeClient("openai", "gpt-4.1-mini", [])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        required_keys = {
            "run_id", "status", "failed_stage", "error_message",
            "stages", "total_estimated_cost_usd", "total_estimated_tokens",
            "confidence", "escalation_questions", "escalation_reasons",
        }
        assert required_keys.issubset(result)


# ===========================================================================
# 3. Partial progress preservation
# ===========================================================================

class TestPartialProgressPreserved:
    def test_completed_stages_retained_after_mid_run_failure(self, tmp_path):
        """Stages completed before the failure are preserved in the result."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        # Research succeeds; plan raises on first openai call
        perplexity = _FakeClient("perplexity", "sonar-pro", [_GOOD_PACKET.copy()])
        openai = _FailingClient("openai", "gpt-4.1-mini", RuntimeError("provider down"))
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["status"] == "failed"
        assert result["failed_stage"] == "plan"
        # Research stage artifact must be preserved
        assert len(result["stages"]) == 1
        assert result["stages"][0]["stage"] == "research"

    def test_partial_cost_and_token_accounting(self, tmp_path):
        """Cost and token totals cover only the stages that completed."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FakeClient("perplexity", "sonar-pro", [_GOOD_PACKET.copy()])
        openai = _PartialClient(
            "openai", "gpt-4.1-mini",
            payloads=[_GOOD_PACKET.copy()],          # plan succeeds
            then_raise=RuntimeError("critique failed"),  # critique raises via anthropic below
        )
        # anthropic raises on first call (critique)
        anthropic = _FailingClient("anthropic", "claude-sonnet-4-20250514", RuntimeError("anthropic down"))

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["status"] == "failed"
        assert result["failed_stage"] == "critique"
        # 2 stages completed (research + plan); each has 160 tokens
        assert len(result["stages"]) == 2
        assert result["total_estimated_tokens"] == 320
        assert result["total_estimated_cost_usd"] > 0

    def test_zero_stages_completed_on_first_stage_failure(self, tmp_path):
        """When the very first stage fails, stages list is empty and cost is 0."""
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FailingClient("perplexity", "sonar-pro", RuntimeError("no quota"))
        openai = _FakeClient("openai", "gpt-4.1-mini", [])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        result = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )

        assert result["stages"] == []
        assert result["total_estimated_cost_usd"] == 0.0
        assert result["total_estimated_tokens"] == 0

    def test_runner_saves_failed_run_artifact_to_disk(self, tmp_path):
        """OrchestratorRunner persists a failed run JSON so it can be diagnosed."""
        from local_agent.orchestrator.runner import OrchestratorRunner

        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        perplexity = _FailingClient("perplexity", "sonar-pro", RuntimeError("disk full"))
        openai = _FakeClient("openai", "gpt-4.1-mini", [])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [])

        runner = OrchestratorRunner(
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            runs_dir=tmp_path / "runs",
        )
        result = runner.run_business_plan(goal="Test goal", business_profile="test_biz")

        assert result["status"] == "failed"
        run_path = Path(result["run_path"])
        assert run_path.exists(), "Failed run artifact must be written to disk"

        import json as _json
        saved = _json.loads(run_path.read_text(encoding="utf-8"))
        assert saved["status"] == "failed"
        assert saved["error_message"]


# ===========================================================================
# 4. Retry path at workflow level (transient failure → success)
# ===========================================================================

class TestWorkflowTransientRetry:
    def test_transient_failure_followed_by_success(self, tmp_path):
        """
        A client that raises once then succeeds simulates a transient provider
        error that the caller retries.  The workflow receives a good response
        on the second attempt and completes normally.
        """
        prompt_dir = tmp_path / "prompts"
        _write_prompts(prompt_dir)

        call_count = {"n": 0}

        class _TransientPerplexity:
            provider = "perplexity"
            default_model = "sonar-pro"

            def chat(self, model, system, user, **_kw):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("transient 503")
                return {
                    "text": json.dumps(_GOOD_PACKET),
                    "usage": {"prompt_tokens": 100, "completion_tokens": 60},
                    "provider": "perplexity",
                    "model": model,
                }

        perplexity = _TransientPerplexity()
        openai = _FakeClient("openai", "gpt-4.1-mini", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])
        anthropic = _FakeClient("anthropic", "claude-sonnet-4-20250514", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])

        # First run fails on research
        result1 = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai,
            anthropic_client=anthropic,
            perplexity_client=perplexity,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )
        assert result1["status"] == "failed"

        # Re-instantiate fresh clients and retry — second attempt succeeds
        perplexity2 = _FakeClient("perplexity", "sonar-pro", [_GOOD_PACKET.copy()])
        openai2 = _FakeClient("openai", "gpt-4.1-mini", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])
        anthropic2 = _FakeClient("anthropic", "claude-sonnet-4-20250514", [_GOOD_PACKET.copy(), _GOOD_PACKET.copy()])

        result2 = run_business_plan(
            goal="Test goal",
            business_profile="test_biz",
            openai_client=openai2,
            anthropic_client=anthropic2,
            perplexity_client=perplexity2,
            prompt_dir=prompt_dir,
            policy=EscalationPolicy(),
            budget_cap_usd=10.0,
        )
        assert result2["status"] == "completed"
        assert len(result2["stages"]) == 5


# ===========================================================================
# 5. Gap: rerun-with-same-input
# ===========================================================================

@pytest.mark.skip(
    reason=(
        "GAP (Phase 2): Rerun-with-same-input is not yet implemented. "
        "OrchestratorRunner has no API to resume or replay a run by run_id. "
        "When implemented, validate that (a) the same run_id is preserved, "
        "(b) already-completed stage artifacts are reused without re-calling "
        "providers, and (c) only the failed stage and later stages are re-executed."
    )
)
def test_rerun_with_same_input_resumes_from_failed_stage():
    pass
