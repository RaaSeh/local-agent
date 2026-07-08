from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


_DEFAULT_HTTP_ALLOWLIST = {"httpbin.org"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PolicyDecision:
    requires_approval: bool
    reason: str
    category: str


class PolicyEngine:
    """Centralized policy classifier for local and outward actions."""

    DEFAULT_RISKY_TOOLS = {
        "delete_file",
        "rename_path",
    }

    _SAFE_RENAME_EXTENSIONS = {".txt", ".md", ".rst", ".log", ".csv"}

    _DELETE_APPROVAL_CUTOFF = datetime(2026, 6, 1, tzinfo=timezone.utc)
    _DANGEROUS_COMMAND_TOKENS = (
        "rm ",
        "del ",
        "rmdir",
        "format ",
        "mkfs",
        "shutdown",
        "reboot",
        "git reset --hard",
        "git clean -fd",
    )

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else None
        if self.config_path:
            self.workspace_root = self.config_path.parent.parent.resolve()
        else:
            self.workspace_root = Path.cwd().resolve()
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        if self.config_path and self.config_path.exists():
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        return {
            "approval": {
                "require_for_tool_names": sorted(self.DEFAULT_RISKY_TOOLS),
                "require_for_categories": [
                    "external_action",
                    "destructive_action",
                    "production_change",
                    "spend_money",
                ],
            }
        }

    def classify_tool_call(self, tool_call: dict[str, Any]) -> PolicyDecision:
        tool_name = str(tool_call.get("tool", "")).strip().lower()
        if tool_name in {"delete_file"}:
            target_path = str(tool_call.get("path", "")).strip()
            if self._delete_targets_pre_june_2026(target_path):
                return PolicyDecision(
                    True,
                    "Deleting files older than June 2026 requires approval.",
                    "destructive_action",
                )
            return PolicyDecision(False, "Deletion target is recent; no approval required.", "safe_internal_action")

        if tool_name in {"rename_path"}:
            if self._is_low_risk_rename(tool_call):
                return PolicyDecision(
                    False,
                    "Plain text file rename is auto-allowed.",
                    "safe_internal_action",
                )
            return PolicyDecision(True, "Path rename/move is workflow-changing and requires approval.", "workflow_change")

        if tool_name == "run_command":
            command = str(tool_call.get("command", "")).strip().lower()
            if any(token in command for token in self._DANGEROUS_COMMAND_TOKENS):
                return PolicyDecision(
                    True,
                    "Destructive shell command requires approval.",
                    "destructive_action",
                )
            return PolicyDecision(False, "Non-destructive local command is auto-allowed.", "safe_internal_action")

        if tool_name == "http_request":
            target_url = str(tool_call.get("url", "")).strip()
            target_host = (urlparse(target_url).hostname or "").strip().lower()
            method = str(tool_call.get("method", "GET")).strip().upper()
            allowlist_raw = os.getenv("HTTP_TOOL_ALLOWLIST", "")
            allowlist = {item.strip().lower() for item in allowlist_raw.split(",") if item.strip()}
            if method == "GET":
                allowlist |= _DEFAULT_HTTP_ALLOWLIST
            paid_hosts_raw = os.getenv("HTTP_TOOL_PAID_HOSTS", "apps.emaillistverified.com")
            paid_hosts = {item.strip().lower() for item in paid_hosts_raw.split(",") if item.strip()}
            if target_host and target_host in paid_hosts:
                return PolicyDecision(
                    True,
                    "External HTTP request to a paid API requires approval.",
                    "spend_money",
                )
            if method == "GET" and target_host and target_host in allowlist:
                return PolicyDecision(
                    False,
                    "Allowlisted HTTPS GET request is auto-allowed.",
                    "safe_internal_action",
                )
            return PolicyDecision(
                True,
                "External HTTP request requires approval.",
                "external_action",
            )

        if tool_name in {
            "launch_executable",
            "install_python_packages",
            "download_file",
            "execute_python",
            "scaffold_tool",
            "write_file",
            "replace_text",
            "append_file",
            "make_directory",
            "check_capability",
            "list_files",
            "read_file",
            "search_text",
        }:
            return PolicyDecision(False, "Helper/local action is auto-allowed.", "safe_internal_action")

        return PolicyDecision(False, "Safe local action.", "safe_internal_action")

    def _delete_targets_pre_june_2026(self, raw_path: str) -> bool:
        if not raw_path:
            return True

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        candidate = candidate.resolve()

        if not candidate.exists():
            return True

        try:
            mtime = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return True
        return mtime < self._DELETE_APPROVAL_CUTOFF

    def _is_low_risk_rename(self, tool_call: dict[str, Any]) -> bool:
        source_raw = str(tool_call.get("path", "") or tool_call.get("source", "")).strip()
        target_raw = str(tool_call.get("target", "") or tool_call.get("new_path", "")).strip()
        if not source_raw or not target_raw:
            return False

        source = Path(source_raw)
        target = Path(target_raw)
        if source.suffix.lower() not in self._SAFE_RENAME_EXTENSIONS:
            return False
        if target.suffix.lower() not in self._SAFE_RENAME_EXTENSIONS:
            return False

        source_parent = str(source.parent).replace("\\", "/")
        target_parent = str(target.parent).replace("\\", "/")
        return source_parent == target_parent

    def classify_owner_request(self, owner_message: str) -> PolicyDecision:
        text = (owner_message or "").strip().lower()
        if not text:
            return PolicyDecision(False, "Empty request.", "safe_internal_action")

        if self._is_customer_facing_email_request(text):
            return PolicyDecision(
                True,
                "Customer-facing email requests require explicit owner approval.",
                "customer_facing_email",
            )

        if self._is_production_code_change_request(text):
            return PolicyDecision(
                True,
                "Functional production code change requests require explicit owner approval.",
                "production_change",
            )

        if self._is_external_contractual_or_reputational_action(text):
            return PolicyDecision(
                True,
                "External contractual or reputational risk actions require explicit owner approval.",
                "external_action",
            )

        return PolicyDecision(
            False,
            "Low-risk analysis/planning request can proceed without approval.",
            "safe_internal_action",
        )

    def _contains_any(self, text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    def _is_customer_facing_email_request(self, text: str) -> bool:
        email_tokens = ("email", "e-mail", "mail")
        recipient_tokens = ("customer", "client", "prospect", "buyer", "user")
        action_tokens = ("send", "draft", "write", "compose", "reply", "forward")
        return (
            self._contains_any(text, email_tokens)
            and self._contains_any(text, recipient_tokens)
            and self._contains_any(text, action_tokens)
        )

    def _is_production_code_change_request(self, text: str) -> bool:
        production_tokens = ("production", "prod", "live system", "live api", "live site")
        code_tokens = ("code", "api", "service", "backend", "frontend", "feature", "functionality")
        change_tokens = (
            "change",
            "modify",
            "patch",
            "hotfix",
            "deploy",
            "release",
            "push",
            "ship",
            "update",
            "refactor",
            "functional change",
        )
        return (
            self._contains_any(text, production_tokens)
            and self._contains_any(text, code_tokens)
            and self._contains_any(text, change_tokens)
        )

    def _is_external_contractual_or_reputational_action(self, text: str) -> bool:
        action_tokens = ("send", "publish", "post", "announce", "sign", "submit", "contact", "reach out")
        risk_tokens = (
            "contract",
            "agreement",
            "msa",
            "sow",
            "legal",
            "quote",
            "public statement",
            "press release",
            "linkedin",
            "tweet",
            "x.com",
            "brand",
            "reputation",
        )
        return self._contains_any(text, action_tokens) and self._contains_any(text, risk_tokens)

    def tool_calls_require_approval(self, tool_calls: list[dict[str, Any]]) -> PolicyDecision:
        risky_tools = set(self._config.get("approval", {}).get("require_for_tool_names", []))
        for call in tool_calls:
            tool_name = str(call.get("tool", "")).strip().lower()
            decision = self.classify_tool_call(call)
            if decision.requires_approval:
                return decision
            if tool_name in risky_tools:
                return PolicyDecision(True, f"Configured risky tool requires approval: {tool_name}", "local_risky_action")
        return PolicyDecision(False, "No approval needed for selected tool calls.", "safe_internal_action")


class ApprovalStore:
    """Persistent approval queue for risky tool/action execution."""

    def __init__(self, state_dir: str | Path = "state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "pending_approvals.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"approvals": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"approvals": []}
        payload.setdefault("approvals", [])
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add_pending(
        self,
        chat_id: int,
        owner_message: str,
        tool_calls: list[dict[str, Any]],
        rationale: str,
        workspace: str,
        execution_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._load()
        approval_id = f"apr-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}"
        record = {
            "approval_id": approval_id,
            "chat_id": chat_id,
            "workspace": workspace,
            "owner_message": owner_message,
            "tool_calls": tool_calls,
            "execution_plan": execution_plan or {},
            "rationale": rationale,
            "status": "pending",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "decision_reason": "",
        }
        payload["approvals"].append(record)
        self._save(payload)
        return record

    def list_pending(self, chat_id: int | None = None) -> list[dict[str, Any]]:
        payload = self._load()
        items = [record for record in payload["approvals"] if record.get("status") == "pending"]
        if chat_id is not None:
            items = [record for record in items if int(record.get("chat_id", -1)) == chat_id]
        return items

    def get(self, approval_id: str) -> dict[str, Any] | None:
        payload = self._load()
        for record in payload["approvals"]:
            if str(record.get("approval_id")) == approval_id:
                return record
        return None

    def resolve(self, approval_id: str, approved: bool, reason: str = "") -> dict[str, Any] | None:
        payload = self._load()
        for record in payload["approvals"]:
            if str(record.get("approval_id")) != approval_id:
                continue
            record["status"] = "approved" if approved else "rejected"
            record["decision_reason"] = reason
            record["updated_at"] = _utc_now()
            self._save(payload)
            return record
        return None

    def mark_executed(self, approval_id: str) -> dict[str, Any] | None:
        payload = self._load()
        for record in payload["approvals"]:
            if str(record.get("approval_id")) != approval_id:
                continue
            record["status"] = "executed"
            record["updated_at"] = _utc_now()
            self._save(payload)
            return record
        return None


class AutonomyProfileStore:
    """Per-chat autonomy settings for approval behavior."""

    VALID_MODES = {"manual", "trusted"}

    def __init__(self, state_dir: str | Path = "state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "autonomy_profiles.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"profiles": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"profiles": {}}
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            payload["profiles"] = {}
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_mode(self, chat_id: int) -> str:
        payload = self._load()
        profiles = payload.get("profiles") or {}
        mode = str((profiles.get(str(chat_id)) or {}).get("mode", "manual")).strip().lower()
        if mode not in self.VALID_MODES:
            return "manual"
        return mode

    def set_mode(self, chat_id: int, mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized not in self.VALID_MODES:
            raise ValueError("Invalid autonomy mode")
        payload = self._load()
        profiles = payload.setdefault("profiles", {})
        profiles[str(chat_id)] = {
            "mode": normalized,
            "updated_at": _utc_now(),
        }
        self._save(payload)
        return normalized
