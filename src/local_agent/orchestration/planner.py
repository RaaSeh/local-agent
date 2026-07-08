from __future__ import annotations

import json
import logging
import re
import os
from dataclasses import dataclass
from pathlib import Path

from local_agent.core.run_once import resolve_agent_llm
from local_agent.orchestration.parse_utils import PlanParseError, extract_json_candidate
from local_agent.orchestration.registry import TaskRegistry


logger = logging.getLogger(__name__)

_FAULT_INJECT_PARSE_FAILURE_FIRED = False


def _maybe_force_parse_failure() -> None:
    global _FAULT_INJECT_PARSE_FAILURE_FIRED
    if _FAULT_INJECT_PARSE_FAILURE_FIRED:
        return
    if os.getenv("CHAD_FORCE_PARSE_FAILURE") != "1":
        return
    _FAULT_INJECT_PARSE_FAILURE_FIRED = True
    logger.warning("[FAULT-INJECT] CHAD_FORCE_PARSE_FAILURE=1 forcing planner parse failure")
    raise PlanParseError(raw="<fault-injected malformed output>", candidate="<fault-injected malformed output>", source="fault-inject")


def _parse_json(raw: str) -> dict:
    _maybe_force_parse_failure()
    candidate = extract_json_candidate(raw)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError as exc:
        raise PlanParseError(raw=raw or "", candidate=candidate) from exc
    raise PlanParseError(raw=raw or "", candidate=candidate)


_KNOWN_PACKAGE_HINTS: dict[str, str] = {
    "pytest": "pytest",
    "ruff": "ruff",
    "requests": "requests",
    "numpy": "numpy",
    "pandas": "pandas",
    "playwright": "playwright",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pillow": "pillow",
    "opencv": "opencv-python",
}


def _infer_known_packages(owner_message: str) -> list[str]:
    lowered = (owner_message or "").lower()
    found: list[str] = []
    for token, package_name in _KNOWN_PACKAGE_HINTS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered) and package_name not in found:
            found.append(package_name)
    return found[:3]


def _extract_completed_write_targets(owner_message: str) -> set[str]:
    completed: set[str] = set()
    for raw_path in re.findall(r"Wrote\s+([^\r\n]+)", owner_message or "", flags=re.IGNORECASE):
        name = Path(raw_path.strip()).name.strip()
        if name:
            completed.add(name.lower())
    return completed


def _infer_numbered_file_write_calls(owner_message: str) -> list[dict]:
    text = owner_message or ""
    lowered = text.lower()
    if not any(token in lowered for token in ("create", "make", "add", "build", "write")):
        return []

    range_match = re.search(
        r"\b([A-Za-z0-9_-]*?)(\d+)\.(txt|md|json|yaml|yml|csv)\s*(?:\.\.\.?|…|through|thru|to|-)\s*([A-Za-z0-9_-]*?)(\d+)\.(txt|md|json|yaml|yml|csv)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not range_match:
        return []

    start_prefix, start_num, start_ext, end_prefix, end_num, end_ext = range_match.groups()
    resolved_end_prefix = end_prefix or start_prefix
    if resolved_end_prefix.lower() != start_prefix.lower() or end_ext.lower() != start_ext.lower():
        return []

    start_value = int(start_num)
    end_value = int(end_num)
    if end_value < start_value:
        return []

    width = max(len(start_num), len(end_num))
    completed = _extract_completed_write_targets(text)
    calls: list[dict] = []
    for value in range(start_value, end_value + 1):
        filename = f"{start_prefix}{value:0{width}d}.{start_ext.lower()}"
        if filename.lower() in completed:
            continue
        calls.append({"tool": "write_file", "path": filename, "content": ""})
    return calls[:5]


def _infer_http_request_call(owner_message: str) -> dict | None:
    text = (owner_message or "").strip()
    lowered = text.lower()
    url_match = re.search(r"https://[^\s)\]>'\"]+", text)
    if not url_match:
        return None

    method_match = re.search(r"\b(get|post)\b", lowered)
    if "http_request" not in lowered and not method_match:
        return None

    method = method_match.group(1).upper() if method_match else "GET"
    return {
        "tool": "http_request",
        "method": method,
        "url": url_match.group(0),
        "timeout": 30,
    }


@dataclass
class ToolPlanner:
    router: object
    agent_cfg: dict
    registry: TaskRegistry

    def plan(
        self,
        owner_message: str,
        memory_context: str,
        allowed_agents: list[str],
        trusted: bool = False,
        route_task_type: str | None = None,
    ) -> dict:
        resolved = resolve_agent_llm(self.agent_cfg, task_context=owner_message)
        route_hint = (
            self.registry.route_for_task_type(route_task_type)
            if route_task_type
            else self.registry.route_for(owner_message)
        )
        allowed_tools = self.registry.render_tool_manifest(route_hint.task_type, trusted=trusted)

        system = (
            f"{resolved['system']}\n\n"
            "You are the task planner. Your job is to determine whether the request should be handled\n"
            "by tools, a delegated worker, or a direct response. Use the registry below.\n"
            "Return ONLY a JSON object. No markdown, no prose.\n"
        )
        user = (
            "Reply with ONLY a JSON object containing exactly these keys:\n"
            "status_update, selected_agent, delegate_prompt, reply, tool_calls,\n"
            "memory_kind, memory_updates, needs_supervisor, requires_user_input, rationale,\n"
            "tool_research, requires_confirmation\n\n"
            f"Owner message: {owner_message}\n\n"
            f"Registry route hint: {registry_to_json(route_hint)}\n\n"
            f"Allowed agents: none, {', '.join(allowed_agents)}\n\n"
            f"Tool manifest:\n{allowed_tools}\n\n"
            f"Memory context:\n{memory_context}\n\n"
            "Routing rules:\n"
            "- When the owner names specific tools to use (e.g. list_files, read_file, search_text), populate the tool_calls array directly with those calls. NEVER delegate owner-specified tool operations to another agent. Delegation is only for tasks that require an agent's reasoning, not for direct file or inspection operations.\n"
            "- If the request is best served by direct tools, keep selected_agent as none and add concrete tool_calls.\n"
            "- For desktop_execution, environment, tool_acquisition, and workspace_edit tasks, prefer selected_agent=none with direct tool_calls unless the tool manifest cannot satisfy the request.\n"
            "- For workspace_inspection tasks, selected_agent must be none, delegate_prompt must be empty, and tool_calls must contain direct list_files/read_file/search_text calls.\n"
            "- Make all network/HTTP/API calls with http_request. This is required whenever external URLs are involved.\n"
            "- Do NOT use run_command with curl/wget/Invoke-WebRequest for HTTP; run_command is only for local shell/file operations.\n"
            "- If a required capability may be missing, prefer check_capability first and then install/scaffold/download the missing capability in the same plan when the target is clear.\n"
            "- If the request needs a specialist worker, choose exactly one selected_agent and keep tool_calls empty unless the tools are strictly necessary.\n"
            "- For delegated coding/CAD tasks, delegate_prompt must be concrete and implementation-ready (specific deliverables, file or module targets, and acceptance checks).\n"
            "- Never use vague delegate_prompt wording such as 'review and experimentation' for implementation requests.\n"
            "- Use the registry to avoid inventing tools.\n"
            "- If you need clarification before acting, set requires_user_input to true.\n"
            "- tool_research should briefly explain why the chosen tool(s) fit the task.\n"
            "- requires_confirmation should be true for file-deletion, move, install, or shell-execution work.\n"
        )

        raw = self.router.chat(
            provider=resolved["provider"],
            model=resolved["model"],
            system=system,
            user=user,
            options=resolved["options"],
        )
        try:
            parsed = _parse_json(raw)
        except PlanParseError as exc:
            logger.warning("Planner returned malformed JSON: %s", exc.preview())
            return {
                "status_update": "Planner output could not be parsed.",
                "selected_agent": "none",
                "delegate_prompt": "",
                "reply": (
                    "Run blocked: planner returned malformed JSON. "
                    f"Raw snippet: {exc.preview()}"
                ),
                "tool_calls": [],
                "memory_kind": "none",
                "memory_updates": [],
                "needs_supervisor": False,
                "requires_user_input": False,
                "rationale": "Planner response could not be parsed.",
                "tool_research": "",
                "requires_confirmation": False,
                "task_type": route_hint.task_type,
                "status": "blocked",
                "completion_reason": "planner_parse_failure",
                "parse_error": True,
                "parse_error_source": exc.source,
                "parse_error_raw": exc.raw,
                "parse_error_snippet": exc.preview(),
            }

        parsed.setdefault("status_update", "")
        parsed["task_type"] = route_hint.task_type
        parsed.setdefault("selected_agent", route_hint.recommended_agent)
        parsed.setdefault("delegate_prompt", "")
        parsed.setdefault("reply", raw.strip())
        parsed.setdefault("tool_calls", [])
        parsed.setdefault("memory_kind", "none")
        parsed.setdefault("memory_updates", [])
        parsed.setdefault("needs_supervisor", route_hint.requires_supervisor)
        parsed.setdefault("requires_user_input", False)
        parsed.setdefault("rationale", "")
        parsed.setdefault("tool_research", "")
        parsed["requires_confirmation"] = bool(route_hint.requires_confirmation or parsed.get("requires_confirmation"))

        if not isinstance(parsed.get("tool_calls"), list):
            parsed["tool_calls"] = []
        if not isinstance(parsed.get("memory_updates"), list):
            parsed["memory_updates"] = []

        parsed["tool_calls"] = self.registry.filter_tool_calls(
            parsed["task_type"],
            parsed["tool_calls"],
            trusted=trusted,
        )

        if parsed.get("task_type") == "workspace_inspection":
            if str(parsed.get("selected_agent", "")).strip().lower() not in {"", "none", "admin"}:
                logger.warning(
                    "Planner emitted delegated selected_agent for no-delegate workspace_inspection route; stripping delegation"
                )
            if str(parsed.get("delegate_prompt", "")).strip():
                logger.warning(
                    "Planner emitted delegate_prompt for no-delegate workspace_inspection route; stripping delegation"
                )
            parsed["selected_agent"] = "none"
            parsed["delegate_prompt"] = ""
            if not parsed["tool_calls"]:
                parsed["tool_calls"] = self.registry.filter_tool_calls(
                    parsed["task_type"],
                    self._infer_workspace_inspection_tool_calls(owner_message),
                    trusted=trusted,
                )

        fallback_route = {"desktop_execution", "environment", "tool_acquisition", "workspace_edit"}
        if parsed.get("task_type") in fallback_route:
            parsed["selected_agent"] = "none"
            if not parsed["tool_calls"]:
                parsed["tool_calls"] = self._infer_fallback_tool_calls(
                    owner_message=owner_message,
                    task_type=str(parsed.get("task_type", "")),
                )
                parsed["tool_calls"] = self.registry.filter_tool_calls(
                    parsed["task_type"],
                    parsed["tool_calls"],
                    trusted=trusted,
                )

        # For inspection/general tasks the LLM sometimes skips tool_calls entirely.
        # Infer list_files directly so desktop-relative paths are always resolved.
        if parsed.get("task_type") in {"inspection", "workspace_inspection", "general"} and not parsed.get("tool_calls"):
            inferred = self._infer_fallback_tool_calls(
                owner_message=owner_message,
                task_type=str(parsed.get("task_type", "")),
            )
            if inferred:
                parsed["tool_calls"] = self.registry.filter_tool_calls(
                    parsed["task_type"],
                    inferred,
                    trusted=trusted,
                )

        if not parsed.get("selected_agent"):
            parsed["selected_agent"] = route_hint.recommended_agent

        if self._requires_verification_first_route(parsed["task_type"]) and not self._is_replan_context(owner_message):
            if not self._starts_with_verification_tool(parsed["tool_calls"]):
                if not self._should_skip_verification_first(owner_message, str(parsed["task_type"])):
                    parsed["tool_calls"] = self._verification_first_tool_calls(
                        owner_message=owner_message,
                        task_type=str(parsed["task_type"]),
                    )

        if self.registry.confirmation_required(parsed["task_type"], parsed["tool_calls"]):
            parsed["requires_confirmation"] = True

        if self._is_low_risk_http_plan(parsed):
            parsed["requires_confirmation"] = False
            parsed["needs_supervisor"] = False
            rationale = str(parsed.get("rationale", "")).strip().lower()
            if "approval" in rationale or "risky" in rationale or "supervisor" in rationale:
                parsed["rationale"] = (
                    "Direct low-risk HTTP GET to allowlisted host via http_request; no approval, delegation, or supervisor review required."
                )
            for key in ("status_update", "reply"):
                text = str(parsed.get(key, "")).strip()
                if text and any(token in text.lower() for token in ("supervisor approval", "requires approval", "risky tool")):
                    parsed[key] = "Executing direct low-risk HTTP GET request."

        if (
            parsed.get("task_type") == "tool_acquisition"
            and self._is_replan_context(owner_message)
            and "[ok] http_request" in (owner_message or "").lower()
            and all(str((call or {}).get("tool", "")).strip().lower() == "http_request" for call in (parsed.get("tool_calls") or []))
        ):
            # The HTTP request already succeeded in a previous iteration; avoid duplicate identical calls.
            parsed["tool_calls"] = []
            parsed["requires_confirmation"] = False
            parsed["reply"] = str(parsed.get("reply") or "HTTP request already completed successfully.")

        parsed = self._normalize_delegate_contract(
            parsed=parsed,
            owner_message=owner_message,
            allowed_agents=allowed_agents,
        )

        return parsed

    def _infer_fallback_tool_calls(self, owner_message: str, task_type: str) -> list[dict]:
        text = (owner_message or "").strip()
        lowered = text.lower()
        calls: list[dict] = []

        if task_type == "workspace_inspection":
            return self._infer_workspace_inspection_tool_calls(owner_message)

        if task_type == "workspace_edit":
            file_create_calls = _infer_numbered_file_write_calls(owner_message)
            if file_create_calls:
                return file_create_calls

        if task_type == "tool_acquisition":
            http_call = _infer_http_request_call(owner_message)
            if http_call:
                return [http_call]

        if task_type == "desktop_execution" and "alibre" in lowered:
            calls.append(
                {
                    "tool": "run_command",
                    "command": (
                        "powershell -NoProfile -Command \""
                        "$roots=@('C:\\Program Files','C:\\Program Files (x86)');"
                        "$names=@('AlibreDesign.exe','Alibre Design.exe');"
                        "$hit=$null;"
                        "foreach($r in $roots){"
                        " Get-ChildItem -Path $r -Directory -Filter 'Alibre*' -ErrorAction SilentlyContinue | ForEach-Object {"
                        "  foreach($sub in @($_.FullName,(Join-Path $_.FullName 'Program'))){"
                        "   foreach($n in $names){"
                        "    $p=Join-Path $sub $n;"
                        "    if(Test-Path $p){$hit=$p; break}"
                        "   } if($hit){break}"
                        "  } if($hit){break}"
                        " } if($hit){break}"
                        "};"
                        "if(-not $hit){Write-Output 'alibre_exe_not_found'; exit 1};"
                        "Start-Process -FilePath $hit;"
                        "Write-Output ('launched:'+$hit)"
                        "\""
                    ),
                    "timeout": 30,
                }
            )
            return calls

        install_intent = any(token in lowered for token in ("install", "dependency", "package", "pip"))
        if task_type in {"environment", "tool_acquisition"} and install_intent:
            package_names = _infer_known_packages(owner_message)
            if package_names:
                for name in package_names:
                    calls.append({"tool": "check_capability", "kind": "package", "name": name})
                calls.append({"tool": "install_python_packages", "packages": package_names})
                return calls

        missing_command = re.search(r"\bmissing tool\s+([A-Za-z0-9_.\-]+)", lowered)
        if task_type == "tool_acquisition" and missing_command:
            calls.append({"tool": "check_capability", "kind": "command", "name": missing_command.group(1)})

        if task_type == "tool_acquisition" and not calls and any(
            token in lowered for token in ("helper", "script", "tool")
        ):
            calls.append(
                {
                    "tool": "scaffold_tool",
                    "path": "tools/generated_helper.py",
                    "purpose": "Auto-generated helper tool scaffold for missing local capability.",
                    "code": (
                        "\"\"\"Generated helper scaffold.\"\"\"\n\n"
                        "def run() -> str:\n"
                        "    return \"TODO: implement helper capability.\"\n\n"
                        "if __name__ == '__main__':\n"
                        "    print(run())\n"
                    ),
                }
            )
            return calls

        download_match = re.search(r"https://[^\s)\]>'\"]+", text)
        if task_type == "tool_acquisition" and download_match:
            url = download_match.group(0)
            filename = Path(url).name or "download.bin"
            calls.append({"tool": "download_file", "url": url, "path": f"tools/downloads/{filename}"})
            return calls

        # Infer list_files for inspection/general requests when the LLM didn't emit it.
        # Matches: "list files in desktop/RW_Media", "list desktop/RW_Media", etc.
        if task_type in {"inspection", "general", "workspace_edit"} and not calls:
            list_match = re.search(
                r"\blist(?:\s+files?)?\s+(?:in|of|at|from)?\s*([^\s,.!?]+(?:[/\\\\][^\s,.!?]+)*)",
                lowered,
            )
            if list_match:
                # Preserve original casing from owner_message
                start, end = list_match.span(1)
                raw_target = text[start:end].strip()
                calls.append({"tool": "list_files", "path": raw_target})
        return calls

    def _infer_workspace_inspection_tool_calls(self, owner_message: str) -> list[dict]:
        lowered = (owner_message or "").lower()
        calls: list[dict] = []

        if "list_files" in lowered:
            calls.append({"tool": "list_files", "path": ".", "limit": 120})
        if "search_text" in lowered:
            calls.append({"tool": "search_text", "path": ".", "query": "TODO", "limit": 50})
        if "read_file" in lowered:
            calls.append({"tool": "read_file", "path": "README.md", "start_line": 1, "end_line": 120})

        if not calls:
            calls.append({"tool": "list_files", "path": ".", "limit": 120})
        return calls[:5]

    def _owner_named_inspection_tools(self, owner_message: str) -> bool:
        lowered = (owner_message or "").lower()
        # Re-plan contexts append tool-result blocks that can mention tool names;
        # only inspect the owner-facing prefix when deciding explicit tool intent.
        owner_prefix = lowered.split("--- tool results", 1)[0]
        has_named_tool = any(name in owner_prefix for name in ("list_files", "read_file", "search_text"))
        has_directive = any(marker in owner_prefix for marker in ("use only", "use", "only"))
        return has_named_tool and has_directive

    @staticmethod
    def _requires_verification_first_route(task_type: str) -> bool:
        return str(task_type or "").strip().lower() in {
            "workspace_edit",
            "tool_acquisition",
            "environment",
            "self_modify",
            "code_support",
        }

    @staticmethod
    def _starts_with_verification_tool(tool_calls: list[dict]) -> bool:
        if not tool_calls:
            return False
        first_tool = str((tool_calls[0] or {}).get("tool", "")).strip().lower()
        return first_tool in {"list_files", "read_file", "search_text", "check_capability"}

    @staticmethod
    def _is_replan_context(owner_message: str) -> bool:
        lowered = (owner_message or "").lower()
        if "supervisor retry verification complete" in lowered:
            return True
        if any(
            marker in lowered
            for marker in (
                "supervisor re-plan attempt",
                "supervisor requires:",
            )
        ):
            return False
        return any(
            marker in lowered
            for marker in (
                "--- tool results",
                "previous tool results",
                "previous planner snapshot",
            )
        )

    @staticmethod
    def _should_skip_verification_first(owner_message: str, task_type: str) -> bool:
        normalized = str(task_type or "").strip().lower()
        if normalized == "tool_acquisition" and _infer_http_request_call(owner_message):
            return True
        if normalized == "workspace_edit" and _infer_numbered_file_write_calls(owner_message):
            return True
        return False

    @staticmethod
    def _is_low_risk_http_plan(parsed: dict) -> bool:
        if str(parsed.get("task_type", "")).strip().lower() != "tool_acquisition":
            return False
        calls = parsed.get("tool_calls") or []
        if not isinstance(calls, list) or not calls:
            return False
        for call in calls:
            if not isinstance(call, dict):
                return False
            if str(call.get("tool", "")).strip().lower() != "http_request":
                return False
        return not TaskRegistry().confirmation_required("tool_acquisition", calls)

    def _verification_first_tool_calls(self, owner_message: str, task_type: str) -> list[dict]:
        calls: list[dict] = [{"tool": "list_files", "path": ".", "limit": 120}]
        lowered = (owner_message or "").lower()

        if any(token in lowered for token in ("search", "find", "locate", "agent", "yaml", "toolbox", "directory", "folder")):
            query = "agent yaml"
            if any(token in lowered for token in ("toolbox", "tool box")):
                query = "toolbox"
            elif "yaml" in lowered or "yml" in lowered:
                query = "yaml"
            calls.append({"tool": "search_text", "path": ".", "query": query, "limit": 25})

        if str(task_type or "").strip().lower() in {"workspace_edit", "code_support", "self_modify"} and "agent" in lowered:
            calls.append({"tool": "read_file", "path": "agents/admin.yaml", "start_line": 1, "end_line": 240})

        return calls[:5]

    def _normalize_delegate_contract(self, parsed: dict, owner_message: str, allowed_agents: list[str]) -> dict:
        selected_agent = str(parsed.get("selected_agent", "")).strip().lower()
        delegate_prompt = str(parsed.get("delegate_prompt", "")).strip()
        if not delegate_prompt or selected_agent not in {"", "none", "admin"}:
            return parsed

        task_type = str(parsed.get("task_type", "")).strip().lower()
        if task_type in {
            "workspace_edit",
            "tool_acquisition",
            "environment",
            "desktop_execution",
            "document_export",
            "workspace_inspection",
            "inspection",
            "general",
        }:
            direct_calls = parsed.get("tool_calls") or self._infer_fallback_tool_calls(
                owner_message=owner_message,
                task_type=task_type,
            )
            if direct_calls:
                logger.warning(
                    "Planner emitted delegate_prompt for direct-tool task_type=%s with selected_agent=none; clearing delegate_prompt",
                    task_type,
                )
                if not parsed.get("tool_calls"):
                    parsed["tool_calls"] = self.registry.filter_tool_calls(
                        parsed["task_type"],
                        direct_calls,
                        trusted=False,
                    )
                parsed["delegate_prompt"] = ""
                return parsed

        if self._has_mutating_tool_calls(parsed.get("tool_calls") or []):
            logger.warning(
                "Planner emitted delegate_prompt alongside direct mutating tool calls; clearing delegate_prompt"
            )
            parsed["delegate_prompt"] = ""
            return parsed

        if parsed.get("tool_calls"):
            return parsed

        delegate_agent = self._pick_delegate_agent(allowed_agents)
        if delegate_agent == "none":
            logger.warning(
                "Planner emitted delegate_prompt with selected_agent=none but no delegate agent is available; clearing delegate_prompt"
            )
            parsed["delegate_prompt"] = ""
            return parsed

        logger.warning(
            "Planner emitted delegate_prompt with selected_agent=none and no mutating tool calls; forcing delegation to %s",
            delegate_agent,
        )
        parsed["selected_agent"] = delegate_agent
        parsed["delegate_prompt"] = delegate_prompt or owner_message.strip()
        parsed["tool_calls"] = []
        return parsed

    @staticmethod
    def _has_mutating_tool_calls(tool_calls: list[dict]) -> bool:
        mutating_tools = {
            "write_file",
            "replace_text",
            "append_file",
            "make_directory",
            "delete_file",
            "rename_path",
            "run_command",
            "execute_python",
            "install_python_packages",
            "scaffold_tool",
            "launch_executable",
            "download_file",
        }
        for call in tool_calls:
            if str((call or {}).get("tool", "")).strip().lower() in mutating_tools:
                return True
        return False

    @staticmethod
    def _pick_delegate_agent(allowed_agents: list[str]) -> str:
        normalized = [
            str(agent).strip()
            for agent in allowed_agents
            if str(agent).strip() and str(agent).strip().lower() not in {"none", "admin"}
        ]
        for preferred in ("codex", "software_dev"):
            if preferred in normalized:
                return preferred
        return normalized[0] if normalized else "none"


def registry_to_json(route_hint) -> str:
    return json.dumps(
        {
            "task_type": route_hint.task_type,
            "summary": route_hint.summary,
            "recommended_agent": route_hint.recommended_agent,
            "allowed_tools": route_hint.allowed_tools,
            "requires_supervisor": route_hint.requires_supervisor,
            "requires_confirmation": route_hint.requires_confirmation,
        },
        indent=2,
    )