from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IntakeSession:
    business_profile: str
    history: list[dict] = field(default_factory=list)
    refined_goal: str = ""
    ready: bool = False


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if end > start:
            text = text[start + 3 : end].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"status": "asking", "message": text}


class IntakeConductor:
    """Drives a clarification conversation using an Anthropic client."""

    def __init__(self, anthropic_client, prompt_dir: Path):
        self.client = anthropic_client
        self.system_prompt = (prompt_dir / "intake.txt").read_text(encoding="utf-8")

    def start(self, session: IntakeSession, initial_text: str) -> str:
        """Process the first user message and return the bot's opening question."""
        session.history.append({"role": "user", "content": initial_text})
        return self._advance(session)

    def reply(self, session: IntakeSession, user_message: str) -> str:
        """Process a follow-up user message and return the bot's next message."""
        session.history.append({"role": "user", "content": user_message})
        return self._advance(session)

    def _advance(self, session: IntakeSession) -> str:
        # Build the user turn from full history as a dialogue block
        dialogue_lines = []
        for turn in session.history:
            role_label = "User" if turn["role"] == "user" else "Assistant"
            dialogue_lines.append(f"{role_label}: {turn['content']}")
        user_block = "\n".join(dialogue_lines)

        response = self.client.chat(
            model=self.client.default_model,
            system=self.system_prompt,
            user=f"Business profile: {session.business_profile}\n\nConversation so far:\n{user_block}",
            temperature=0.3,
            max_tokens=600,
        )

        parsed = _extract_json(response.get("text", ""))
        status = parsed.get("status", "asking")
        message = parsed.get("message", "Could you tell me more about what you're trying to achieve?")

        session.history.append({"role": "assistant", "content": message})

        if status == "ready":
            session.refined_goal = parsed.get("refined_goal", message)
            session.ready = True

        return message
