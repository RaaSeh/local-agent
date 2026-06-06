from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, state_dir: str | Path = "state"):
        self.state_dir = Path(state_dir)
        self.owner_path = self.state_dir / "owner_profile.json"
        self.lessons_path = self.state_dir / "lessons.json"
        self.interactions_path = self.state_dir / "interactions.jsonl"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return json.loads(json.dumps(default))
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_owner_profile(self) -> dict:
        default = {
            "identity": [],
            "businesses": [],
            "preferences": [],
            "active_projects": [],
            "updated_at": None,
        }
        return self._read_json(self.owner_path, default)

    def load_lessons(self) -> dict:
        default = {"lessons": [], "updated_at": None}
        return self._read_json(self.lessons_path, default)

    def apply_updates(self, updates: list[dict] | None) -> None:
        if not updates:
            return

        owner = self.load_owner_profile()
        lessons = self.load_lessons()

        buckets = {
            "identity": "identity",
            "business": "businesses",
            "preference": "preferences",
            "project": "active_projects",
        }

        changed_owner = False
        changed_lessons = False
        for update in updates:
            kind = str(update.get("kind", "")).strip().lower()
            value = str(update.get("value", "")).strip()
            source = str(update.get("source", "admin")).strip() or "admin"
            if not value:
                continue

            if kind in buckets:
                bucket = owner[buckets[kind]]
                if value not in bucket:
                    bucket.append(value)
                    changed_owner = True
                continue

            if kind == "lesson":
                lesson_record = {
                    "value": value,
                    "source": source,
                    "recorded_at": _utc_now(),
                }
                if lesson_record["value"] not in [entry.get("value") for entry in lessons["lessons"]]:
                    lessons["lessons"].append(lesson_record)
                    changed_lessons = True

        if changed_owner:
            owner["updated_at"] = _utc_now()
            self._write_json(self.owner_path, owner)
        if changed_lessons:
            lessons["updated_at"] = _utc_now()
            self._write_json(self.lessons_path, lessons)

    def append_interaction(self, record: dict) -> None:
        payload = dict(record)
        payload.setdefault("recorded_at", _utc_now())
        with self.interactions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def recent_interactions(self, limit: int = 8) -> list[dict]:
        if not self.interactions_path.exists():
            return []
        lines = self.interactions_path.read_text(encoding="utf-8").splitlines()
        output: list[dict] = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            output.append(json.loads(line))
        return output

    def render_conversation_thread(self, limit: int = 8, max_summary_chars: int = 300) -> str:
        """Return the recent exchange as an ordered dialogue thread.

        Each entry is rendered as::

            [N] User: <user_message>
                → [status] <summary>

        This gives planners and the supervisor a clear view of what has been
        discussed and what was (or was not) accomplished.
        """
        entries = self.recent_interactions(limit=limit)
        if not entries:
            return "No conversation history yet."
        lines = ["Conversation thread (oldest → newest):"]
        for idx, entry in enumerate(entries, start=1):
            user_msg = str(entry.get("user_message", "")).strip()[:200]
            status = str(entry.get("status", "completed")).strip()
            summary = str(entry.get("summary", "")).strip()[:max_summary_chars]
            agent = str(entry.get("selected_agent", "admin")).strip()
            tools = entry.get("evidence", {}).get("executed_tool_calls", [])
            tool_note = f" | tools={','.join(tools)}" if tools else ""
            lines.append(f"[{idx}] User: {user_msg}")
            lines.append(f"     → [{status}] {agent}{tool_note}: {summary}")
        return "\n".join(lines)

    def render_context(self, max_chars: int = 4000) -> str:
        owner = self.load_owner_profile()
        lessons = self.load_lessons()
        recent = self.recent_interactions()

        parts = ["Owner Memory"]
        parts.append("Identity: " + ("; ".join(owner["identity"]) or "Unknown"))
        parts.append("Businesses: " + ("; ".join(owner["businesses"]) or "Unknown"))
        parts.append("Preferences: " + ("; ".join(owner["preferences"]) or "None recorded"))
        parts.append(
            "Active Projects: " + ("; ".join(owner["active_projects"]) or "None recorded")
        )

        if lessons["lessons"]:
            parts.append("Lessons:")
            for entry in lessons["lessons"][-5:]:
                parts.append(f"- {entry.get('value')} ({entry.get('source', 'unknown')})")

        if recent:
            parts.append("Recent Interactions:")
            for entry in recent[-5:]:
                user_msg = str(entry.get("user_message", "")).strip()[:120]
                summary = str(entry.get("summary", "")).strip() or user_msg
                status = str(entry.get("status", "")).strip()
                status_tag = f" [{status}]" if status else ""
                if summary:
                    parts.append(f"-{status_tag} {summary[:300]}")

        text = "\n".join(parts)
        return text[:max_chars]

    def render_status(self) -> str:
        recent = self.recent_interactions(limit=5)
        if not recent:
            return "No tracked activity yet."

        lines = ["Recent activity:"]
        for entry in reversed(recent):
            agent = entry.get("selected_agent") or "admin"
            status = entry.get("status") or "completed"
            summary = str(entry.get("summary", "")).strip() or str(entry.get("user_message", "")).strip()
            evidence_marker = self._compact_evidence_marker(entry)
            lines.append(f"- [{status}] {agent}{evidence_marker}: {summary[:140]}")
        return "\n".join(lines)

    def _compact_evidence_marker(self, entry: dict) -> str:
        if str(entry.get("status", "")).strip().lower() != "completed":
            return ""
        if not bool(entry.get("execution_intent")):
            return ""
        evidence = entry.get("evidence")
        if not isinstance(evidence, dict):
            return ""
        successful_tools = evidence.get("successful_tools")
        if not isinstance(successful_tools, list) or not successful_tools:
            return ""
        return f" [proof:{len(successful_tools)}t]"

    def render_memory_summary(self) -> str:
        return self.render_context(max_chars=2500)