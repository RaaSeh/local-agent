from __future__ import annotations

import atexit
import os
import random
import time
from pathlib import Path
import re

import httpx

from local_agent.config import load_env, get_env
from local_agent.llm.anthropic_client import AnthropicClient as LLMAnthropicClient
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.openai_client import OpenAIClient as LLMOpenAIClient
from local_agent.llm.perplexity_client import PerplexityClient as LLMPerplexityClient
from local_agent.llm.router import LLMRouter
from local_agent.orchestration.admin import AdminOrchestrator
from local_agent.orchestrator.runner import OrchestratorRunner
from local_agent.providers.anthropic_client import AnthropicClient
from local_agent.providers.openai_client import OpenAIClient
from local_agent.providers.perplexity_client import PerplexityClient
from local_agent.workflows.intake import IntakeConductor, IntakeSession


class _TelegramBotInstanceLock:
    """Best-effort single-instance lock for Telegram long-poll workers."""

    _PID_LOCK_STALE_SECONDS = 30
    _EMPTY_LOCK_STALE_SECONDS = 30

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        existing_pid = self._read_pid()
        if existing_pid and self._pid_exists(existing_pid):
            raise RuntimeError(
                f"Telegram bot is already running (pid={existing_pid}). Stop the other instance before starting a new one."
            )
        if existing_pid:
            if self._is_stale_pid_lock():
                self._remove_stale_lock()
            else:
                raise RuntimeError(
                    f"Telegram bot lock is currently held (pid={existing_pid}). Stop the other instance before starting a new one."
                )
        elif self.lock_path.exists():
            if self._is_stale_empty_lock():
                self._remove_stale_lock()
            else:
                raise RuntimeError(
                    "Telegram bot startup already in progress. Stop the other instance before starting a new one."
                )

        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            self.acquired = True
            return
        except FileExistsError:
            # Another process raced us; read lock again for a helpful error.
            existing_pid = self._read_pid()
            if existing_pid and self._pid_exists(existing_pid):
                raise RuntimeError(
                    f"Telegram bot is already running (pid={existing_pid}). Stop the other instance before starting a new one."
                )
            if existing_pid:
                if self._is_stale_pid_lock():
                    self._remove_stale_lock()
                else:
                    raise RuntimeError(
                        f"Telegram bot lock is currently held (pid={existing_pid}). Stop the other instance before starting a new one."
                    )
            elif self._is_stale_empty_lock():
                self._remove_stale_lock()
            else:
                raise RuntimeError(
                    "Telegram bot startup already in progress. Stop the other instance before starting a new one."
                )
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.lock_path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def _read_pid(self) -> int:
        if not self.lock_path.exists():
            return 0
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip().splitlines()
            if not raw:
                return 0
            return int(raw[0].strip())
        except (OSError, ValueError):
            return 0

    def _remove_stale_lock(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _lock_age_seconds(self) -> float:
        if not self.lock_path.exists():
            return 0.0
        try:
            return max(0.0, time.time() - self.lock_path.stat().st_mtime)
        except OSError:
            return 0.0

    def _is_stale_pid_lock(self) -> bool:
        return self._lock_age_seconds() >= float(self._PID_LOCK_STALE_SECONDS)

    def _is_stale_empty_lock(self) -> bool:
        if not self.lock_path.exists():
            return False
        return self._lock_age_seconds() >= float(self._EMPTY_LOCK_STALE_SECONDS)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                # Access denied can still indicate a live process.
                last_error = ctypes.windll.kernel32.GetLastError()
                if int(last_error) == 5:
                    return True
                return False
            except Exception:
                pass
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def _build_runner() -> OrchestratorRunner:
    load_env()
    return OrchestratorRunner(
        openai_client=OpenAIClient(
            api_key=get_env("OPENAI_API_KEY", ""),
            default_model=get_env("OPENAI_MODEL", "gpt-4.1-mini"),
        ),
        anthropic_client=AnthropicClient(
            api_key=get_env("ANTHROPIC_API_KEY", ""),
            default_model=get_env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        ),
        perplexity_client=PerplexityClient(
            api_key=get_env("PERPLEXITY_API_KEY", ""),
            default_model=get_env("PERPLEXITY_MODEL", "sonar-pro"),
        ),
        prompt_dir=get_env("PROMPTS_DIR", "prompts"),
        runs_dir=get_env("RUNS_DIR", "runs"),
        confidence_threshold=float(get_env("ESCALATION_CONFIDENCE_THRESHOLD", "0.7")),
        budget_cap_usd=float(get_env("RUN_BUDGET_CAP_USD", "3.0")),
    )


def _build_admin_orchestrator() -> AdminOrchestrator:
    load_env()
    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=LLMAnthropicClient(get_env("ANTHROPIC_API_KEY", "")),
        openai=LLMOpenAIClient(get_env("OPENAI_API_KEY", "")),
        perplexity=LLMPerplexityClient(get_env("PERPLEXITY_API_KEY", "")),
    )
    return AdminOrchestrator(
        router=router,
        workspace_root=Path(get_env("WORKSPACE_ROOT", ".")),
        agents_dir=get_env("AGENTS_DIR", "agents"),
        state_dir=get_env("STATE_DIR", "state"),
        runs_dir=get_env("RUNS_DIR", "runs"),
    )


def _parse_allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed: set[int] = set()
    for value in raw.split(","):
        v = value.strip()
        if not v:
            continue
        try:
            allowed.add(int(v))
        except ValueError:
            continue
    return allowed


class TelegramBotRunner:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.offset = 0
        self._conflict_recovery_attempted = False
        self.runner = _build_runner()
        self.admin = _build_admin_orchestrator()
        self._intake_sessions: dict[int, IntakeSession] = {}
        self._chat_modes: dict[int, str] = {}
        self._intake_conductor = IntakeConductor(
            anthropic_client=self.runner.anthropic_client,
            prompt_dir=Path(get_env("PROMPTS_DIR", "prompts")),
        )

    def _bool_env(self, name: str, default: bool) -> bool:
        raw = str(os.getenv(name, str(default))).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _attempt_conflict_recovery(self) -> str:
        try:
            with httpx.Client(timeout=30) as client:
                info_response = client.get(f"{self.base_url}/getWebhookInfo")
                info_response.raise_for_status()
                info_payload = info_response.json()
                webhook_info = (info_payload.get("result") or {}) if isinstance(info_payload, dict) else {}
                webhook_url = str(webhook_info.get("url", "")).strip()

                if not webhook_url:
                    return "No active webhook found; another getUpdates consumer may still be running elsewhere."

                if not self._bool_env("TELEGRAM_AUTO_DELETE_WEBHOOK", True):
                    return (
                        f"Webhook is active ({webhook_url}). Disable webhook or set TELEGRAM_AUTO_DELETE_WEBHOOK=true "
                        "for polling mode auto-recovery."
                    )

                delete_response = client.post(
                    f"{self.base_url}/deleteWebhook",
                    json={"drop_pending_updates": False},
                )
                delete_response.raise_for_status()
                return f"Deleted active webhook ({webhook_url}); retrying getUpdates polling."
        except Exception as exc:
            return f"Conflict recovery probe failed: {exc}"

    def _send_message(self, chat_id: int, text: str) -> None:
        payload = {"chat_id": chat_id, "text": text}
        with httpx.Client(timeout=45) as client:
            r = client.post(f"{self.base_url}/sendMessage", json=payload)
            r.raise_for_status()

    def _parse_business_profile(self, text: str) -> tuple[str, str]:
        cleaned = (text or "").strip()
        command, payload = self._parse_command(cleaned)
        if command == "/plan":
            cleaned = payload

        if "|" in cleaned:
            profile, goal = cleaned.split("|", 1)
            profile = profile.strip().lower().replace(" ", "_")
            goal = goal.strip()
            if profile and goal:
                return profile, goal

        return "rocket_wash", cleaned

    def _parse_command(self, text: str) -> tuple[str, str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return "", ""

        # Support Telegram deep-link command text rendered as markdown in some clients,
        # e.g. "[/work](tg://bot_command?command=work) list files".
        markdown_link = re.match(
            r"^\[(/[^\]\s]+)\]\(tg://bot_command\?command=([^\)\s]+)\)\s*(.*)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if markdown_link:
            deep_link_command = "/" + markdown_link.group(2).lstrip("/").split("@", 1)[0].lower()
            payload = markdown_link.group(3).strip()
            return deep_link_command, payload

        plain_deep_link = re.match(
            r"^tg://bot_command\?command=([^\s]+)\s*(.*)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if plain_deep_link:
            deep_link_command = "/" + plain_deep_link.group(1).lstrip("/").split("@", 1)[0].lower()
            payload = plain_deep_link.group(2).strip()
            return deep_link_command, payload

        if not cleaned.startswith("/"):
            return "", cleaned

        parts = cleaned.split(maxsplit=1)
        head = parts[0]
        tail = parts[1] if len(parts) > 1 else ""
        command = head.split("@", 1)[0].lower()
        return command, tail.strip()

    def _send_messages(self, chat_id: int, messages: list[str]) -> None:
        for message in messages:
            cleaned = str(message).strip()
            if cleaned:
                self._send_message(chat_id, cleaned)

    def _handle_workspace_message(self, chat_id: int, text: str) -> None:
        replies = self.admin.handle_message(chat_id=chat_id, text=text)
        self._send_messages(chat_id, replies)

    def _handle_file_upload(self, chat_id: int, message: dict) -> None:
        try:
            file_id = ""
            file_name = ""
            ext = ".bin"

            document = message.get("document") or {}
            photo = message.get("photo") or []
            audio = message.get("audio") or {}

            if isinstance(document, dict) and document.get("file_id"):
                file_id = str(document.get("file_id"))
                file_name = str(document.get("file_name") or "").strip()
                if file_name:
                    ext = Path(file_name).suffix or ".bin"
            elif isinstance(photo, list) and photo:
                largest = photo[-1] if isinstance(photo[-1], dict) else {}
                file_id = str(largest.get("file_id") or "")
                ext = ".jpg"
            elif isinstance(audio, dict) and audio.get("file_id"):
                file_id = str(audio.get("file_id"))
                ext = ".mp3"
            else:
                self._send_message(chat_id, "File save failed: unsupported upload type")
                return

            if not file_id:
                self._send_message(chat_id, "File save failed: missing file_id")
                return

            if not file_name:
                file_name = f"{file_id}{ext}"

            # Prevent path traversal by keeping only the basename.
            file_name = Path(file_name).name.strip() or f"{file_id}{ext}"

            media_dir = Path(os.getenv("MEDIA_SAVE_DIR", "state/media/"))
            media_dir.mkdir(parents=True, exist_ok=True)

            candidate = media_dir / file_name
            stem = candidate.stem
            suffix = candidate.suffix
            index = 1
            while candidate.exists():
                candidate = media_dir / f"{stem}_{index}{suffix}"
                index += 1

            with httpx.Client(timeout=45) as client:
                meta_response = client.get(f"{self.base_url}/getFile", params={"file_id": file_id})
                meta_response.raise_for_status()
                meta_payload = meta_response.json()
                if not isinstance(meta_payload, dict):
                    raise RuntimeError("unexpected Telegram getFile response")
                file_path = (meta_payload.get("result") or {}).get("file_path")
                if not meta_payload.get("ok") or not file_path:
                    raise RuntimeError("unable to resolve Telegram file path")

                download_response = client.get(f"https://api.telegram.org/file/bot{self.token}/{file_path}")
                download_response.raise_for_status()
                candidate.write_bytes(download_response.content)

            size_kb = candidate.stat().st_size / 1024
            self._send_message(chat_id, f"Saved: {candidate.name} ({size_kb:.1f} KB) -> {candidate.as_posix()}")
        except Exception as exc:
            self._send_message(chat_id, f"File save failed: {exc}")

    def _set_mode(self, chat_id: int, mode: str) -> None:
        self._chat_modes[chat_id] = mode

    def _mode(self, chat_id: int) -> str:
        return self._chat_modes.get(chat_id, "work")

    def _looks_like_workspace_request(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        if lowered.startswith(("/work", "/admin", "/runs", "/memory", "/status", "/agents", "/autonomy")):
            return True
        if lowered.startswith("/plan"):
            return False

        workspace_patterns = [
            r"\bwrite\b",
            r"\bsave\b",
            r"\bexport\b",
            r"\bconvert\b",
            r"\bpdf\b",
            r"\.pdf\b",
            r"\bfile\b",
            r"\bfolder\b",
            r"\bdirectory\b",
            r"\brename\b",
            r"\bdelete\b",
            r"\bmove\b",
            r"\bcopy\b",
            r"\binstall\b",
            r"\bpip\b",
            r"\bpackage\b",
            r"\bread\b",
            r"\blist\b",
            r"\bsearch\b",
            r"\bfind\b",
            r"\bedit\b",
            r"\bappend\b",
            r"\brun\b.*\bcommand\b",
            r"\bdesktop\b",
            r"\bworkspace\b",
            r"\bmedia\b",
            r"\bimage\b",
            # Coding / R&D / Copilot delegation
            r"\bcode\b",
            r"\bscript\b",
            r"\bpython\b",
            r"\bimplement\b",
            r"\bdebug\b",
            r"\brefactor\b",
            r"\btest\b",
            r"\bapi\b",
            r"\bmodule\b",
            r"\bclass\b",
            r"\bfunction\b",
            r"\bcad\b",
            r"\balibre\b",
            r"\bcopilot\b",
            r"\bcodex\b",
            r"\bagent\b",
            r"\brepository\b",
            r"\brepo\b",
            r"\bgit\b",
            r"\bdevelop\b",
            r"\bbuild\b",
        ]
        return any(re.search(pattern, lowered) for pattern in workspace_patterns)

    def _reply_with_agent(self, chat_id: int, text: str) -> None:
        allowed = _parse_allowed_chat_ids()
        if allowed and chat_id not in allowed:
            self._send_message(chat_id, "You are not authorized to use this bot.")
            return

        cleaned = (text or "").strip()
        command, payload = self._parse_command(cleaned)
        if not cleaned:
            self._send_message(
                chat_id,
                "Send /work <request> for operations tasks, /workspace to manage workspace context, or /plan <business_profile>|<goal> for the guided planning pipeline.",
            )
            return

        if cleaned.lower() in {"help"} or command == "/help":
            self._send_message(
                chat_id,
                "Usage:\n/work <request> — default mode for admin, research, coding delegation, and file/tool execution\n/workspace — list available workspaces\n/workspace set <id> — change active workspace context\n/autonomy — show current autonomy mode\n/autonomy set trusted|manual — control approval strictness\n/approvals — list pending approvals\n/approve <id> or /reject <id> [reason] — resolve risky action requests\n/sources <query> — inspect retrieval citations\n/reindex — rebuild retrieval index\n/plan <profile>|<goal> — run the guided planning pipeline\n/runs — show recent run outputs\n/cancel — cancel current planning session\n\nProfiles: rocket_wash, raze_development_studios",
            )
            return

        if command in {"/work", "/admin"}:
            self._set_mode(chat_id, "work")
            # Cancel any in-progress intake session so /work always reaches admin.
            self._intake_sessions.pop(chat_id, None)
            if payload:
                self._handle_workspace_message(chat_id, payload)
            else:
                self._send_message(chat_id, "Workspace mode enabled. Send an operations request, or use /workspace to switch context.")
            return

        if command == "/runs":
            self._set_mode(chat_id, "work")
            self._send_messages(chat_id, self.admin.handle_message(chat_id=chat_id, text="/runs"))
            return

        if command == "/plan":
            self._set_mode(chat_id, "plan")

        if cleaned.lower() in {"cancel"} or command == "/cancel":
            if chat_id in self._intake_sessions:
                del self._intake_sessions[chat_id]
                self._send_message(chat_id, "Session cancelled. Send /plan or a new message to start fresh.")
            else:
                self._send_message(chat_id, "No active session to cancel.")
            return

        if self._mode(chat_id) == "work":
            self._handle_workspace_message(chat_id, cleaned)
            return

        # Even when plan mode is active, route clearly operational/file tasks to workspace mode.
        if chat_id not in self._intake_sessions and self._looks_like_workspace_request(cleaned):
            self._set_mode(chat_id, "work")
            self._handle_workspace_message(chat_id, cleaned)
            return

        # If already in an active intake session, continue the conversation
        if chat_id in self._intake_sessions:
            session = self._intake_sessions[chat_id]
            if not session.ready:
                try:
                    reply = self._intake_conductor.reply(session, cleaned)
                except Exception as exc:
                    self._send_message(chat_id, f"Intake error: {exc}")
                    return
                if session.ready:
                    self._send_message(chat_id, reply)
                    self._send_message(chat_id, "Reply *yes* to confirm and run, or correct anything above.")
                else:
                    self._send_message(chat_id, reply)
                return
            else:
                # Session is ready, waiting for user confirmation
                lowered = cleaned.lower()
                if any(word in lowered for word in ("yes", "go", "proceed", "confirm", "run", "looks good", "correct", "ok", "yep", "sure")):
                    # Launch the pipeline
                    self._run_pipeline(chat_id, session)
                    del self._intake_sessions[chat_id]
                else:
                    # Treat as a correction — feed back into intake
                    session.ready = False
                    session.refined_goal = ""
                    try:
                        reply = self._intake_conductor.reply(session, cleaned)
                    except Exception as exc:
                        self._send_message(chat_id, f"Intake error: {exc}")
                        return
                    if session.ready:
                        self._send_message(chat_id, reply)
                        self._send_message(chat_id, "Reply *yes* to confirm and run, or correct anything above.")
                    else:
                        self._send_message(chat_id, reply)
                return

        # Start a new intake session
        business_profile, initial_goal = self._parse_business_profile(cleaned)
        session = IntakeSession(business_profile=business_profile)
        self._intake_sessions[chat_id] = session
        self._set_mode(chat_id, "plan")

        self._send_message(chat_id, f"Starting intake for *{business_profile}*. I'll ask a few questions to sharpen the brief before we run.")
        try:
            reply = self._intake_conductor.start(session, initial_goal)
        except Exception as exc:
            del self._intake_sessions[chat_id]
            self._send_message(chat_id, f"Intake error: {exc}")
            return

        if session.ready:
            self._send_message(chat_id, reply)
            self._send_message(chat_id, "Reply *yes* to confirm and run, or correct anything above.")
        else:
            self._send_message(chat_id, reply)

    def _run_pipeline(self, chat_id: int, session: IntakeSession) -> None:
        self._send_message(chat_id, f"Scope confirmed. Launching 5-stage pipeline for *{session.business_profile}*...")

        stage_map = {
            "research": "Stage 1/5: Research",
            "plan": "Stage 2/5: Draft Plan",
            "critique": "Stage 3/5: Critique",
            "revise": "Stage 4/5: Revise",
            "final_memo": "Stage 5/5: Final Memo",
        }

        def on_stage(stage_name: str) -> None:
            label = stage_map.get(stage_name, stage_name)
            self._send_message(chat_id, label)

        try:
            result = self.runner.run_business_plan(
                goal=session.refined_goal,
                business_profile=session.business_profile,
                on_stage=on_stage,
            )
        except Exception as exc:
            self._send_message(chat_id, f"Run failed: {exc}")
            return

        summary = result.get("final_memo", {}).get("summary") or "No final summary generated."
        confidence = result.get("confidence", 0)
        cost = result.get("total_estimated_cost_usd", 0)
        status = result.get("status", "completed")
        run_path = result.get("run_path", "")
        escalation_questions = result.get("escalation_questions", [])

        final_lines = [
            f"Status: {status}",
            f"Confidence: {confidence}",
            f"Estimated cost: ${cost:.4f}",
            f"Run artifact: {run_path}",
            "",
            "Final memo:",
            str(summary),
        ]
        if escalation_questions:
            final_lines.append("")
            final_lines.append("Questions requiring your input:")
            for idx, question in enumerate(escalation_questions, start=1):
                final_lines.append(f"{idx}. {question}")

        final_payload = "\n".join(final_lines)
        max_chars = int(os.getenv("TELEGRAM_MAX_RESPONSE_CHARS", "3500"))
        if len(final_payload) > max_chars:
            final_payload = final_payload[: max_chars - 15] + "\n\n[truncated]"
        self._send_message(chat_id, final_payload)

    def run_forever(self) -> None:
        print(f"Telegram bot started. Press Ctrl+C to stop. (pid={os.getpid()})")
        conflict_count = 0
        while True:
            try:
                with httpx.Client(timeout=70) as client:
                    r = client.get(
                        f"{self.base_url}/getUpdates",
                        params={"timeout": 60, "offset": self.offset + 1},
                    )
                    r.raise_for_status()
                    data = r.json()

                if not data.get("ok"):
                    time.sleep(2)
                    continue

                for update in data.get("result", []):
                    self.offset = max(self.offset, int(update.get("update_id", 0)))
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    text = message.get("text", "")
                    has_file_upload = any(message.get(key) for key in ("document", "photo", "audio"))
                    if not isinstance(chat_id, int):
                        continue
                    if has_file_upload:
                        self._handle_file_upload(chat_id, message)
                    if text:
                        self._reply_with_agent(chat_id, text)
                conflict_count = 0
            except KeyboardInterrupt:
                raise
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 409:
                    conflict_count += 1
                    if not self._conflict_recovery_attempted:
                        self._conflict_recovery_attempted = True
                        recovery_message = self._attempt_conflict_recovery()
                        print(f"Telegram conflict recovery: {recovery_message}")
                    backoff = min(30.0, (2 ** min(conflict_count, 5)) + random.uniform(0.0, 1.5))
                    print(
                        "Telegram polling conflict (409): another getUpdates consumer is active. "
                        f"pid={os.getpid()} retrying in {backoff:.1f}s"
                    )
                    time.sleep(backoff)
                    continue
                print(f"Telegram polling HTTP error: {exc}")
                time.sleep(3)
            except Exception as exc:
                print(f"Telegram polling error: {exc}")
                time.sleep(3)


def run_telegram_bot() -> int:
    load_env()
    token = get_env("TELEGRAM_BOT_TOKEN")
    lock_path = Path(get_env("STATE_DIR", "state")) / "telegram_bot.lock"
    lock = _TelegramBotInstanceLock(lock_path)
    try:
        lock.acquire()
    except RuntimeError as exc:
        print(str(exc))
        return 1

    print(f"Telegram lock acquired: {lock_path} (pid={os.getpid()})")

    atexit.register(lock.release)
    runner = TelegramBotRunner(token=token)
    try:
        runner.run_forever()
    finally:
        lock.release()
    return 0
