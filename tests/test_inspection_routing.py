from __future__ import annotations

from local_agent.orchestration.registry import TaskRegistry


def test_owner_named_read_only_tools_route_to_workspace_inspection_before_code_support() -> None:
    registry = TaskRegistry()

    route = registry.route_for(
        "Please fix code issues, but use only list_files, read_file, search_text for read-only inspection."
    )

    assert route.task_type == "workspace_inspection"
    assert route.task_type != "code_support"
    assert route.recommended_agent == "none"
    assert route.requires_confirmation is False
    assert route.allowed_tools == ["list_files", "read_file", "search_text"]


def test_workspace_inspection_manifest_contains_expected_read_only_tools() -> None:
    registry = TaskRegistry()

    route = registry.route_for("Use only list_files, read_file, search_text and inspect the repo")
    effective = registry.effective_allowed_tools(route.task_type, trusted=False)

    assert route.task_type == "workspace_inspection"
    assert route.allowed_tools == ["list_files", "read_file", "search_text"]
    assert set(["list_files", "read_file", "search_text"]).issubset(set(effective))
