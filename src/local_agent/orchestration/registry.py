from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments: list[str] = field(default_factory=list)
    risky: bool = False


@dataclass(frozen=True)
class TaskRoute:
    task_type: str
    summary: str
    recommended_agent: str = "none"
    allowed_tools: list[str] = field(default_factory=list)
    requires_supervisor: bool = False
    requires_confirmation: bool = False


class TaskRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, ToolDefinition] = {
            "list_files": ToolDefinition(
                name="list_files",
                description="List files and folders inside a workspace path.",
                arguments=["path", "limit"],
            ),
            "read_file": ToolDefinition(
                name="read_file",
                description="Read a text file from the workspace.",
                arguments=["path", "start_line", "end_line"],
            ),
            "search_text": ToolDefinition(
                name="search_text",
                description="Search text across files in the workspace.",
                arguments=["path", "query", "limit"],
            ),
            "write_file": ToolDefinition(
                name="write_file",
                description="Create or overwrite a text file in the workspace.",
                arguments=["path", "content"],
            ),
            "replace_text": ToolDefinition(
                name="replace_text",
                description="Replace exactly one string match inside a file.",
                arguments=["path", "old_text", "new_text"],
            ),
            "append_file": ToolDefinition(
                name="append_file",
                description="Append text to the end of a file.",
                arguments=["path", "content"],
            ),
            "make_directory": ToolDefinition(
                name="make_directory",
                description="Create a directory path.",
                arguments=["path"],
            ),
            "delete_file": ToolDefinition(
                name="delete_file",
                description="Delete a file or directory.",
                arguments=["path"],
                risky=True,
            ),
            "rename_path": ToolDefinition(
                name="rename_path",
                description="Rename or move a file or directory.",
                arguments=["path", "target"],
                risky=True,
            ),
            "run_command": ToolDefinition(
                name="run_command",
                description="Run a shell command in the workspace.",
                arguments=["command", "cwd", "timeout"],
                risky=True,
            ),
            "launch_executable": ToolDefinition(
                name="launch_executable",
                description="Launch a local .exe file and report whether a process started.",
                arguments=["path", "args", "wait_seconds"],
                risky=True,
            ),
            "install_python_packages": ToolDefinition(
                name="install_python_packages",
                description="Install Python packages into the active environment.",
                arguments=["packages", "package_list", "timeout"],
                risky=True,
            ),
            "check_capability": ToolDefinition(
                name="check_capability",
                description="Check whether a Python package or CLI command is already available on this machine.",
                arguments=["kind", "name"],
            ),
            "scaffold_tool": ToolDefinition(
                name="scaffold_tool",
                description="Write a new Python helper/tool script into the workspace (creates file with provided code).",
                arguments=["path", "purpose", "code"],
                risky=True,
            ),
            "download_file": ToolDefinition(
                name="download_file",
                description="Download a file from an HTTPS URL to a local workspace path.",
                arguments=["url", "path"],
                risky=True,
            ),
            "execute_python": ToolDefinition(
                name="execute_python",
                description="Run a .py script that exists in the workspace and return its stdout/stderr/exit code.",
                arguments=["path", "args", "timeout"],
                risky=True,
            ),
        }

    def route_for(self, owner_message: str) -> TaskRoute:
        text = (owner_message or "").strip().lower()

        if not text:
            return TaskRoute(
                task_type="status",
                summary="Empty request; ask for clarification.",
            )

        if any(token in text for token in ("pdf", "export", "save this to pdf", "write this to pdf")):
            return TaskRoute(
                task_type="document_export",
                summary="Create or export a document artifact.",
                recommended_agent="none",
                allowed_tools=["read_file", "write_file", "append_file", "run_command"],
                requires_confirmation=True,
            )

        desktop_target = any(token in text for token in ("desktop", ".exe", "executable", "desktop app", "alibre"))
        desktop_action = any(token in text for token in ("open", "launch", "start", "run", "execute"))
        if desktop_target and desktop_action:
            return TaskRoute(
                task_type="desktop_execution",
                summary="Find and launch a desktop executable with verifiable execution evidence.",
                recommended_agent="none",
                allowed_tools=[
                    "list_files",
                    "search_text",
                    "check_capability",
                    "install_python_packages",
                    "launch_executable",
                    "run_command",
                    "read_file",
                ],
                requires_supervisor=True,
            )

        if any(token in text for token in ("install", "pip", "package", "dependency", "venv", "environment")):
            return TaskRoute(
                task_type="environment",
                summary="Manage the Python environment or install packages.",
                recommended_agent="none",
                allowed_tools=["install_python_packages", "run_command", "read_file", "list_files", "check_capability"],
                requires_supervisor=True,
            )

        if any(
            token in text
            for token in (
                "need a tool",
                "need to install",
                "missing tool",
                "download",
                "create a script",
                "write a script",
                "scaffold",
                "capability",
                "not available",
                "can you get",
                "fetch",
                "build a tool",
                "make a tool",
                "acquire",
                "set up",
            )
        ):
            return TaskRoute(
                task_type="tool_acquisition",
                summary="Detect missing capability, then install packages, scaffold scripts, or download files as needed.",
                recommended_agent="none",
                allowed_tools=[
                    "check_capability",
                    "install_python_packages",
                    "scaffold_tool",
                    "download_file",
                    "execute_python",
                    "run_command",
                    "write_file",
                    "list_files",
                    "read_file",
                ],
                requires_supervisor=True,
            )

        cad_related = any(
            token in text
            for token in (
                "agentic cad",
                "agenticcad",
                "concentric mate",
                "alibre",
                "industrial shelter",
                "geometry",
                "parametric",
                "obj",
                "copilot",
                "codex",
                "coding agent",
                "cad library",
                "cad r&d",
                "mate",
                "assembly",
                "constraint",
            )
        )
        cad_implementation_intent = any(
            token in text
            for token in (
                "implement",
                "build",
                "create",
                "write",
                "develop",
                "wrapper",
                "api",
                "library",
                "test part",
                "test assembly",
            )
        )

        if cad_related and cad_implementation_intent:
            return TaskRoute(
                task_type="code_support",
                summary="Implement CAD/Alibre code changes and tests with concrete deliverables.",
                recommended_agent="codex",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "run_command",
                    "execute_python",
                    "install_python_packages",
                    "check_capability",
                    "scaffold_tool",
                ],
                requires_supervisor=True,
            )

        if cad_related:
            return TaskRoute(
                task_type="cad_rnd",
                summary="Coordinate Agentic CAD experiments or coding-agent delegation.",
                recommended_agent="codex",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "run_command",
                    "install_python_packages",
                ],
                requires_supervisor=True,
            )

        if any(token in text for token in ("rename", "delete", "move", "create folder", "make directory", "folder", "directory")):
            return TaskRoute(
                task_type="workspace_edit",
                summary="Edit workspace files or folders.",
                recommended_agent="none",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "delete_file",
                    "rename_path",
                    "run_command",
                ],
                requires_supervisor=True,
                requires_confirmation=True,
            )

        if any(token in text for token in ("code", "debug", "fix", "test", "refactor", "python", "script", "repo")):
            return TaskRoute(
                task_type="code_support",
                summary="Inspect or modify code in the workspace.",
                recommended_agent="codex",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "run_command",
                    "execute_python",
                    "install_python_packages",
                    "check_capability",
                    "scaffold_tool",
                ],
                requires_supervisor=True,
            )

        if any(token in text for token in ("read", "search", "find", "inspect", "show me", "status", "runs", "memory", "list")):
            return TaskRoute(
                task_type="inspection",
                summary="Read or search the workspace without mutating it.",
                recommended_agent="none",
                allowed_tools=["list_files", "read_file", "search_text"],
            )

        return TaskRoute(
            task_type="general",
            summary="General request; decide whether to delegate or answer directly.",
            recommended_agent="none",
            allowed_tools=["list_files", "read_file", "search_text"],
        )

    def _route_for_task_type(self, task_type: str) -> TaskRoute:
        normalized = (task_type or "").strip().lower()
        if normalized == "document_export":
            return TaskRoute(
                task_type="document_export",
                summary="Create or export a document artifact.",
                recommended_agent="none",
                allowed_tools=["read_file", "write_file", "append_file", "run_command"],
                requires_confirmation=True,
            )
        if normalized == "desktop_execution":
            return TaskRoute(
                task_type="desktop_execution",
                summary="Find and launch a desktop executable with verifiable execution evidence.",
                recommended_agent="none",
                allowed_tools=[
                    "list_files",
                    "search_text",
                    "check_capability",
                    "install_python_packages",
                    "launch_executable",
                    "run_command",
                    "read_file",
                ],
                requires_supervisor=True,
            )
        if normalized == "environment":
            return TaskRoute(
                task_type="environment",
                summary="Manage the Python environment or install packages.",
                recommended_agent="none",
                allowed_tools=["install_python_packages", "run_command", "read_file", "list_files", "check_capability"],
                requires_supervisor=True,
            )
        if normalized == "tool_acquisition":
            return TaskRoute(
                task_type="tool_acquisition",
                summary="Detect missing capability, then install packages, scaffold scripts, or download files as needed.",
                recommended_agent="none",
                allowed_tools=[
                    "check_capability",
                    "install_python_packages",
                    "scaffold_tool",
                    "download_file",
                    "execute_python",
                    "run_command",
                    "write_file",
                    "list_files",
                    "read_file",
                ],
                requires_supervisor=True,
            )
        if normalized == "workspace_edit":
            return TaskRoute(
                task_type="workspace_edit",
                summary="Edit workspace files or folders.",
                recommended_agent="none",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "delete_file",
                    "rename_path",
                    "run_command",
                ],
                requires_supervisor=True,
                requires_confirmation=True,
            )
        if normalized == "code_support":
            return TaskRoute(
                task_type="code_support",
                summary="Inspect or modify code in the workspace.",
                recommended_agent="codex",
                allowed_tools=[
                    "list_files",
                    "read_file",
                    "search_text",
                    "write_file",
                    "replace_text",
                    "append_file",
                    "make_directory",
                    "run_command",
                    "execute_python",
                    "install_python_packages",
                    "check_capability",
                    "scaffold_tool",
                ],
                requires_supervisor=True,
            )
        if normalized == "inspection":
            return TaskRoute(
                task_type="inspection",
                summary="Read or search the workspace without mutating it.",
                recommended_agent="none",
                allowed_tools=["list_files", "read_file", "search_text"],
            )
        if normalized in {"general", "status", "cad_rnd"}:
            return self.route_for(normalized)
        return self.route_for(normalized)

    def allowed_tools_for(self, task_type: str) -> list[str]:
        route = self._route_for_task_type(task_type)
        return route.allowed_tools

    def render_tool_manifest(self, task_type: str) -> str:
        route = self._route_for_task_type(task_type)
        tools = [self.tools[name] for name in route.allowed_tools if name in self.tools]
        lines = [
            f"Task type: {route.task_type}",
            f"Summary: {route.summary}",
            f"Recommended agent: {route.recommended_agent}",
            f"Requires supervisor: {str(route.requires_supervisor).lower()}",
            f"Requires confirmation: {str(route.requires_confirmation).lower()}",
            "Allowed tools:",
        ]
        for tool in tools:
            args = ", ".join(tool.arguments) if tool.arguments else "none"
            risk = "risky" if tool.risky else "safe"
            lines.append(f"- {tool.name}: {tool.description} | args={args} | {risk}")
        return "\n".join(lines)

    def filter_tool_calls(self, task_type: str, tool_calls: list[dict] | None) -> list[dict]:
        route = self._route_for_task_type(task_type)
        allowed = set(route.allowed_tools)
        filtered: list[dict] = []
        for call in tool_calls or []:
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool", "")).strip().lower()
            if tool_name and tool_name in allowed:
                filtered.append(call)
        return filtered[:5]

    def confirmation_required(self, task_type: str, tool_calls: list[dict] | None) -> bool:
        route = self._route_for_task_type(task_type)
        if route.requires_confirmation:
            return True
        for call in tool_calls or []:
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool", "")).strip().lower()
            if tool_name in {"delete_file", "rename_path", "run_command", "launch_executable", "install_python_packages"}:
                return True
        return False

    def to_json(self, task_type: str) -> str:
        route = self._route_for_task_type(task_type)
        payload = {
            "task_type": route.task_type,
            "summary": route.summary,
            "recommended_agent": route.recommended_agent,
            "allowed_tools": route.allowed_tools,
            "requires_supervisor": route.requires_supervisor,
            "requires_confirmation": route.requires_confirmation,
        }
        return json.dumps(payload, indent=2)