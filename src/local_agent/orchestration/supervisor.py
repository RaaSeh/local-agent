from __future__ import annotations

import json
from dataclasses import dataclass

from local_agent.core.run_once import resolve_agent_llm


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
        parsed = _parse_json(raw)
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