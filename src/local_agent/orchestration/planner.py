from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from local_agent.core.run_once import resolve_agent_llm
from local_agent.orchestration.registry import TaskRegistry


def _parse_json(raw: str) -> dict:
    candidate = (raw or "").strip()
    if "```" in candidate:
        start = candidate.find("```")
        end = candidate.rfind("```")
        if start != -1 and end > start:
            candidate = candidate[start + 3 : end].strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start : end + 1]

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


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
    ) -> dict:
        resolved = resolve_agent_llm(self.agent_cfg, task_context=owner_message)
        route_hint = self.registry.route_for(owner_message)
        allowed_tools = self.registry.render_tool_manifest(route_hint.task_type, trusted=trusted)

        system = (
            f"{resolved['system']}\n\n"
            "You are the task planner. Your job is to determine whether the request should be handled\n"
            "by tools, a delegated worker, or a direct response. Use the registry below.\n"
            "Return ONLY a JSON object. No markdown, no prose.\n"
        )
        user = (
            "Reply with ONLY a JSON object containing exactly these keys:\n"
            "status_update, task_type, selected_agent, delegate_prompt, reply, tool_calls,\n"
            "memory_kind, memory_updates, needs_supervisor, requires_user_input, rationale,\n"
            "tool_research, requires_confirmation\n\n"
            f"Owner message: {owner_message}\n\n"
            f"Registry route hint: {registry_to_json(route_hint)}\n\n"
            f"Allowed agents: none, {', '.join(allowed_agents)}\n\n"
            f"Tool manifest:\n{allowed_tools}\n\n"
            f"Memory context:\n{memory_context}\n\n"
            "Routing rules:\n"
            "- If the request is best served by direct tools, keep selected_agent as none and add concrete tool_calls.\n"
            "- For desktop_execution, environment, tool_acquisition, and workspace_edit tasks, prefer selected_agent=none with direct tool_calls unless the tool manifest cannot satisfy the request.\n"
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
        parsed = _parse_json(raw)

        parsed.setdefault("status_update", "")
        parsed.setdefault("task_type", route_hint.task_type)
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
        parsed.setdefault("requires_confirmation", route_hint.requires_confirmation)

        if not isinstance(parsed.get("tool_calls"), list):
            parsed["tool_calls"] = []
        if not isinstance(parsed.get("memory_updates"), list):
            parsed["memory_updates"] = []

        fallback_route = {"desktop_execution", "environment", "tool_acquisition", "workspace_edit"}
        parsed_task_type = str(parsed.get("task_type", "")).strip()
        if route_hint.task_type in fallback_route and parsed_task_type not in fallback_route:
            parsed["task_type"] = route_hint.task_type

        parsed["tool_calls"] = self.registry.filter_tool_calls(
            parsed["task_type"],
            parsed["tool_calls"],
            trusted=trusted,
        )

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
        if parsed.get("task_type") in {"inspection", "general"} and not parsed.get("tool_calls"):
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

        if not parsed.get("task_type"):
            parsed["task_type"] = route_hint.task_type

        if self.registry.confirmation_required(parsed["task_type"], parsed["tool_calls"]):
            parsed["requires_confirmation"] = True

        return parsed

    def _infer_fallback_tool_calls(self, owner_message: str, task_type: str) -> list[dict]:
        text = (owner_message or "").strip()
        lowered = text.lower()
        calls: list[dict] = []

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