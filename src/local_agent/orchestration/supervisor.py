from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from local_agent.core.run_once import resolve_agent_llm
from local_agent.orchestration.parse_utils import PlanParseError, extract_json_candidate


logger = logging.getLogger(__name__)

_FAULT_INJECT_PARSE_FAILURE_FIRED = False


def _maybe_force_parse_failure() -> None:
    global _FAULT_INJECT_PARSE_FAILURE_FIRED
    if _FAULT_INJECT_PARSE_FAILURE_FIRED:
        return
    if os.getenv("CHAD_FORCE_PARSE_FAILURE") != "1":
        return
    _FAULT_INJECT_PARSE_FAILURE_FIRED = True
    logger.warning("[FAULT-INJECT] CHAD_FORCE_PARSE_FAILURE=1 forcing supervisor parse failure")
    raise PlanParseError(
        raw="<fault-injected malformed output>",
        candidate="<fault-injected malformed output>",
        source="fault-inject",
    )


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


@dataclass
class ToolSupervisor:
    router: object
    agent_cfg: dict

    def review(
        self,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
        delegate_output: str,
        memory_context: str,
    ) -> tuple[dict, str]:
        resolved = resolve_agent_llm(self.agent_cfg, task_context=owner_message)
        tool_text = self._format_tool_results(tool_results)
        system = (
            f"{resolved['system']}\n\n"
            "You are the supervisor for a task-driven tool workflow. Review whether the planner chose the right\n"
            "task type, tool(s), and next action. Return ONLY a JSON object.\n"
        )
        user = (
            "Reply with ONLY a JSON object containing exactly these keys:\n"
            "summary, risks, corrections, approve, next_actions, tool_feedback, task_feedback\n\n"
            f"Owner message: {owner_message}\n\n"
            f"Planner output:\n{json.dumps(plan, indent=2)}\n\n"
            f"Tool results:\n{tool_text}\n\n"
            f"Delegate output:\n{delegate_output or 'None'}\n\n"
            f"Memory and conversation context:\n{memory_context}\n\n"
            "Rules:\n"
            "- Use the conversation thread in memory_context to understand prior turns. "
            "Check whether the current request is a follow-up or continuation of a previous task.\n"
            "- Be a devil's advocate. Point out weak tool choice, unsafe actions, and missing verification.\n"
            "- If the tool choice was appropriate, say so explicitly.\n"
            "- Include at least 2 concrete corrections or confirmations.\n"
            "- approve should be true only if the plan/tool execution is acceptable.\n"
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
            summary = f"Run blocked: supervisor output could not be parsed. Raw snippet: {exc.preview()}"
            return (
                {
                    "summary": summary,
                    "risks": ["Malformed supervisor JSON output."],
                    "corrections": ["Re-run the supervisor review with valid JSON output."],
                    "approve": False,
                    "next_actions": [],
                    "tool_feedback": "",
                    "task_feedback": "",
                    "status": "blocked",
                    "parse_error": True,
                    "parse_error_source": exc.source,
                    "parse_error_raw": exc.raw,
                    "parse_error_snippet": exc.preview(),
                },
                "\n".join(
                    [
                        "## Supervisor Review",
                        summary,
                        "",
                        "### Risks",
                        "- Malformed supervisor JSON output.",
                    ]
                ),
            )
        parsed.setdefault("summary", raw.strip())
        parsed.setdefault("risks", [])
        parsed.setdefault("corrections", [])
        parsed.setdefault("approve", False)
        parsed.setdefault("next_actions", [])
        parsed.setdefault("tool_feedback", "")
        parsed.setdefault("task_feedback", "")

        digest_lines = [
            "## Supervisor Review",
            str(parsed.get("summary", "")).strip(),
        ]
        risks = parsed.get("risks") or []
        if risks:
            digest_lines.append("\n### Risks")
            for item in risks[:5]:
                digest_lines.append(f"- {item}")
        corrections = parsed.get("corrections") or []
        if corrections:
            digest_lines.append("\n### Corrections")
            for item in corrections[:5]:
                digest_lines.append(f"- {item}")
        next_actions = parsed.get("next_actions") or []
        if next_actions:
            digest_lines.append("\n### Next Actions")
            for item in next_actions[:5]:
                digest_lines.append(f"- {item}")

        digest_md = "\n".join(digest_lines)
        return parsed, digest_md

    def _format_tool_results(self, tool_results: list[dict]) -> str:
        if not tool_results:
            return "No tool results."
        lines = []
        for result in tool_results:
            status = "ok" if result.get("ok") else "error"
            lines.append(f"[{status}] {result.get('tool')}: {str(result.get('output', ''))[:1200]}")
        return "\n".join(lines)