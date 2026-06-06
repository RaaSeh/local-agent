from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class WorkspaceContextStore:
    """Maintains per-chat active workspace and loads workspace primers."""

    def __init__(
        self,
        workspace_root: str | Path,
        config_path: str | Path = "config/workspaces.yaml",
        state_dir: str | Path = "state",
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.config_path = self.workspace_root / Path(config_path)
        self.state_dir = self.workspace_root / Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.session_path = self.state_dir / "workspace_sessions.json"
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        return {
            "default_workspace": "shared",
            "workspaces": {
                "shared": {
                    "name": "Shared",
                    "primer": "workspaces/shared/primer.md",
                }
            },
        }

    def _load_sessions(self) -> dict[str, Any]:
        if not self.session_path.exists():
            return {"by_chat_id": {}}
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"by_chat_id": {}}
        payload.setdefault("by_chat_id", {})
        return payload

    def _save_sessions(self, payload: dict[str, Any]) -> None:
        self.session_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def available_workspaces(self) -> dict[str, dict[str, Any]]:
        workspaces = self._config.get("workspaces", {})
        if not isinstance(workspaces, dict):
            return {}
        return workspaces

    def get_active_workspace(self, chat_id: int | None = None) -> str:
        default_workspace = str(self._config.get("default_workspace", "shared")).strip() or "shared"
        if chat_id is None:
            return default_workspace
        sessions = self._load_sessions()
        value = str(sessions["by_chat_id"].get(str(chat_id), default_workspace)).strip()
        if value in self.available_workspaces():
            return value
        return default_workspace

    def set_active_workspace(self, chat_id: int, workspace_id: str) -> bool:
        workspace_id = workspace_id.strip()
        if workspace_id not in self.available_workspaces():
            return False
        sessions = self._load_sessions()
        sessions["by_chat_id"][str(chat_id)] = workspace_id
        self._save_sessions(sessions)
        return True

    def render_workspace_summary(self, chat_id: int) -> str:
        active = self.get_active_workspace(chat_id)
        info = self.available_workspaces().get(active, {})
        return (
            f"Active workspace: {active}\n"
            f"Name: {info.get('name', 'Unknown')}\n"
            f"Description: {info.get('description', 'No description')}"
        )

    def list_workspaces_text(self, chat_id: int) -> str:
        active = self.get_active_workspace(chat_id)
        lines = ["Available workspaces:"]
        for workspace_id, cfg in self.available_workspaces().items():
            marker = "*" if workspace_id == active else " "
            lines.append(f"{marker} {workspace_id}: {cfg.get('name', workspace_id)}")
        return "\n".join(lines)

    def primer_text(self, workspace_id: str, max_chars: int = 3500) -> str:
        cfg = self.available_workspaces().get(workspace_id, {})
        primer_path = cfg.get("primer")
        if not primer_path:
            return ""
        path = self.workspace_root / Path(str(primer_path))
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")[:max_chars]

    def context_block(self, chat_id: int, max_chars: int = 4000) -> str:
        workspace_id = self.get_active_workspace(chat_id)
        cfg = self.available_workspaces().get(workspace_id, {})
        primer = self.primer_text(workspace_id=workspace_id, max_chars=max_chars)
        return (
            f"Workspace ID: {workspace_id}\n"
            f"Workspace Name: {cfg.get('name', workspace_id)}\n"
            f"Workspace Description: {cfg.get('description', '')}\n"
            f"Workspace Primer:\n{primer}"
        )[:max_chars]
