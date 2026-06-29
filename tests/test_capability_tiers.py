from __future__ import annotations

from local_agent.orchestration.registry import TaskRegistry


def test_effective_allowed_tools_includes_base_without_trusted_tools() -> None:
    registry = TaskRegistry()

    allowed = registry.effective_allowed_tools("inspection", trusted=False)

    for name in TaskRegistry.BASE_TOOLS:
        assert name in allowed
    assert "install_python_packages" not in allowed


def test_effective_allowed_tools_includes_trusted_tools_but_not_destructive_auto_adds() -> None:
    registry = TaskRegistry()

    allowed = registry.effective_allowed_tools("inspection", trusted=True)

    assert "install_python_packages" in allowed
    assert "scaffold_tool" in allowed
    assert "execute_python" in allowed
    assert "delete_file" not in allowed
    assert "launch_executable" not in allowed
    assert "rename_path" not in allowed
    assert "download_file" not in allowed


def test_filter_tool_calls_respects_trusted_flag() -> None:
    registry = TaskRegistry()
    calls = [{"tool": "install_python_packages", "packages": ["pytest"]}]

    trusted_calls = registry.filter_tool_calls("inspection", calls, trusted=True)
    manual_calls = registry.filter_tool_calls("inspection", calls, trusted=False)

    assert trusted_calls == calls
    assert manual_calls == []
