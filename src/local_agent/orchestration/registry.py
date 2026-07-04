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
    BASE_TOOLS = ["list_files", "read_file", "search_text", "check_capability"]
    TRUSTED_LOCAL_TOOLS = ["install_python_packages", "scaffold_tool", "execute_python"]

    _INSPECTION_TOOL_NAMES = ("list_files", "read_file", "search_text")
    _READ_ONLY_INSPECTION_HINTS = (
        "read-only",
        "read only",
        "inspection",
        "inspect",
    )

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
            # --- Sanity multi-tenant CMS (Phase 1, draft-only) ---
            # Every write below creates/updates a Sanity DRAFT (drafts.* _id) only.
            # Publishing is a human action in Sanity Studio; there is no publish tool.
            "provision_tenant": ToolDefinition(
                name="provision_tenant",
                description="Create a DRAFT Sanity 'tenant' document for a service business (draft-only, never publishes).",
                arguments=["name", "subdomain_slug", "brand"],
            ),
            "draft_tenant_page": ToolDefinition(
                name="draft_tenant_page",
                description="Create a DRAFT Sanity 'page' document referencing a tenant, with SEO fields (draft-only).",
                arguments=[
                    "tenant_ref",
                    "title",
                    "body",
                    "seo_title",
                    "seo_description",
                    "slug",
                    "structured_data",
                ],
            ),
            "update_tenant_page": ToolDefinition(
                name="update_tenant_page",
                description="Patch fields on an existing DRAFT Sanity page (draft id required, draft-only).",
                arguments=["draft_page_id", "fields"],
            ),
            "list_tenants": ToolDefinition(
                name="list_tenants",
                description="Read-only GROQ query listing all Sanity tenant documents.",
                arguments=[],
            ),
            "list_tenant_pages": ToolDefinition(
                name="list_tenant_pages",
                description="Read-only GROQ query listing pages for a tenant.",
                arguments=["tenant_ref"],
            ),
            "seo_audit_tenant": ToolDefinition(
                name="seo_audit_tenant",
                description="Read-only SEO audit of a tenant's pages; flags weak/missing SEO and thin content.",
                arguments=["tenant_ref"],
            ),
        }

    def route_for(self, owner_message: str) -> TaskRoute:
        text = (owner_message or "").strip().lower()

        if not text:
            return TaskRoute(
                task_type="status",
                summary="Empty request; ask for clarification.",
            )

        if any(
            token in text
            for token in (
                "tenant",
                "sanity",
                "subdomain",
                "seo audit",
                "draft page",
                "provision",
                "headless cms",
            )
        ):
            return TaskRoute(
                task_type="cms_provisioning",
                summary="Provision tenants and draft pages/SEO in Sanity (draft-only; a human publishes).",
                recommended_agent="none",
                allowed_tools=[
                    "provision_tenant",
                    "draft_tenant_page",
                    "update_tenant_page",
                    "list_tenants",
                    "list_tenant_pages",
                    "seo_audit_tenant",
                ],
                requires_supervisor=True,
                requires_confirmation=True,
            )

        if any(token in text for token in ("pdf", "export", "save this to pdf", "write this to pdf")):
            return TaskRoute(
                task_type="document_export",
                summary="Create or export a document artifact.",
                recommended_agent="none",
                allowed_tools=["read_file", "write_file", "append_file", "run_command"],
                requires_confirmation=True,
            )

        if any(token in text for token in ("create", "add", "make", "build", "scaffold", "set up", "setup", "modify", "edit")) and any(
            re.search(pattern, text)
            for pattern in (
                r"\bagent(s)?\b",
                r"\btool\s*box\b",
                r"\byaml\b",
                r"\bconfig\b",
                r"\bdirectory\b",
                r"\bfolder\b",
                r"\bfile(s)?\b",
                r"\bworkspace\b",
                r"\bproject\b",
            )
        ):
            return TaskRoute(
                task_type="workspace_edit",
                summary="Inspect and modify workspace files, agent configs, or directory structure.",
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

        if any(
            token in text
            for token in (
                "your own code",
                "modify yourself",
                "self-modify",
                "self modify",
                "patch your",
                "edit your code",
            )
        ):
            return TaskRoute(
                task_type="self_modify",
                summary="Modify the orchestrator's own source with git checkpoint + test gating.",
                recommended_agent="codex",
                allowed_tools=["list_files", "read_file", "search_text", "replace_text", "write_file", "run_command"],
                requires_supervisor=True,
                requires_confirmation=True,
            )

        if self._is_workspace_inspection_request(text):
            return TaskRoute(
                task_type="workspace_inspection",
                summary="Read or search the workspace without mutating it.",
                recommended_agent="none",
                allowed_tools=["list_files", "read_file", "search_text"],
                requires_supervisor=False,
                requires_confirmation=False,
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
                task_type="workspace_inspection",
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
        if normalized == "cms_provisioning":
            return TaskRoute(
                task_type="cms_provisioning",
                summary="Provision tenants and draft pages/SEO in Sanity (draft-only; a human publishes).",
                recommended_agent="none",
                allowed_tools=[
                    "provision_tenant",
                    "draft_tenant_page",
                    "update_tenant_page",
                    "list_tenants",
                    "list_tenant_pages",
                    "seo_audit_tenant",
                ],
                requires_supervisor=True,
                requires_confirmation=True,
            )
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
        if normalized == "self_modify":
            return TaskRoute(
                task_type="self_modify",
                summary="Modify the orchestrator's own source with git checkpoint + test gating.",
                recommended_agent="codex",
                allowed_tools=["list_files", "read_file", "search_text", "replace_text", "write_file", "run_command"],
                requires_supervisor=True,
                requires_confirmation=True,
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
        if normalized in {"workspace_inspection", "inspection"}:
            return TaskRoute(
                task_type="workspace_inspection",
                summary="Read or search the workspace without mutating it.",
                recommended_agent="none",
                allowed_tools=["list_files", "read_file", "search_text"],
            )
        if normalized in {"general", "status", "cad_rnd"}:
            return self.route_for(normalized)
        return self.route_for(normalized)

    def route_for_task_type(self, task_type: str) -> TaskRoute:
        return self._route_for_task_type(task_type)

    def _is_workspace_inspection_request(self, lowered_text: str) -> bool:
        text = (lowered_text or "").strip()
        if not text:
            return False

        owner_prefix = text.split("--- tool results", 1)[0]

        if any(hint in owner_prefix for hint in self._READ_ONLY_INSPECTION_HINTS):
            return True

        # Explicit owner tool constraints should force read-only inspection routing.
        if not any(marker in owner_prefix for marker in ("use", "only", "named", "tools")):
            return False
        for tool_name in self._INSPECTION_TOOL_NAMES:
            if re.search(rf"\b{re.escape(tool_name)}\b", owner_prefix):
                return True
        return False

    def allowed_tools_for(self, task_type: str) -> list[str]:
        route = self._route_for_task_type(task_type)
        return route.allowed_tools

    def effective_allowed_tools(self, task_type: str, trusted: bool = False) -> list[str]:
        route = self._route_for_task_type(task_type)
        forbidden_auto_add = {"delete_file", "rename_path", "launch_executable", "download_file"}
        ordered_names = list(route.allowed_tools)
        ordered_names.extend(self.BASE_TOOLS)
        if trusted:
            ordered_names.extend(self.TRUSTED_LOCAL_TOOLS)

        filtered: list[str] = []
        for name in ordered_names:
            if name not in self.tools:
                continue
            if name in forbidden_auto_add and name not in route.allowed_tools:
                continue
            if name not in filtered:
                filtered.append(name)
        return filtered

    def render_tool_manifest(self, task_type: str, trusted: bool = False) -> str:
        route = self._route_for_task_type(task_type)
        tools = [self.tools[name] for name in self.effective_allowed_tools(task_type, trusted=trusted)]
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

    def filter_tool_calls(self, task_type: str, tool_calls: list[dict] | None, trusted: bool = False) -> list[dict]:
        allowed = set(self.effective_allowed_tools(task_type, trusted=trusted))
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