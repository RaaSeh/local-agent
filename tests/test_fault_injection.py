from __future__ import annotations

import pytest

from local_agent.orchestration import planner as planner_module
from local_agent.orchestration.parse_utils import PlanParseError


def test_force_parse_failure_fires_once(monkeypatch):
    monkeypatch.setenv("CHAD_FORCE_PARSE_FAILURE", "1")
    monkeypatch.setattr(planner_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)

    with pytest.raises(PlanParseError) as exc_info:
        planner_module._parse_json('{"ok": true}')

    assert "fault-injected malformed output" in exc_info.value.raw

    parsed = planner_module._parse_json('{"ok": true}')
    assert parsed == {"ok": True}


def test_force_parse_failure_not_set_allows_normal_parse(monkeypatch):
    monkeypatch.delenv("CHAD_FORCE_PARSE_FAILURE", raising=False)
    monkeypatch.setattr(planner_module, "_FAULT_INJECT_PARSE_FAILURE_FIRED", False)

    parsed = planner_module._parse_json('{"ok": true}')
    assert parsed == {"ok": True}