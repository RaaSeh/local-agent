from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from local_agent.context.workspaces import WorkspaceContextStore
from local_agent.core.run_once import load_yaml, resolve_agent_llm, run_agent_task
from local_agent.orchestration.planner import ToolPlanner
from local_agent.orchestration.project_scanner import (
    detect_project_path_in_text,
    get_external_project_dir,
    scan_external_project,
)
from local_agent.orchestration.registry import TaskRegistry
from local_agent.orchestration.memory import MemoryStore
from local_agent.orchestration.supervisor import ToolSupervisor
from local_agent.orchestration.tools import ToolExecutor
from local_agent.policy.approvals import ApprovalStore, AutonomyProfileStore, PolicyEngine
from local_agent.retrieval.simple_index import RetrievalIndex
from local_agent.storage.runs import RunStore


class AdminOrchestrator:
    _MAX_DELEGATE_REPAIR_ATTEMPTS = 1
    _MAX_DELEGATE_RECOVERY_ATTEMPTS = 2

    def __init__(
        self,
        router,
        workspace_root: str | Path = ".",
        agents_dir: str | Path = "agents",
        state_dir: str | Path = "state",
        runs_dir: str | Path = "runs",
    ):
        self.router = router
        self.workspace_root = Path(workspace_root).resolve()
        self.agents_dir = self.workspace_root / Path(agents_dir)
        self.memory = MemoryStore(self.workspace_root / Path(state_dir))
        self.runs = RunStore(self.workspace_root / Path(runs_dir))
        self.tools = ToolExecutor(self.workspace_root)
        self.registry = TaskRegistry()
        self.planner = ToolPlanner(router=self.router, agent_cfg=self._load_agents().get("admin", {}), registry=self.registry)
        self.policy = PolicyEngine(self.workspace_root / "config" / "policy.yaml")
        self.approvals = ApprovalStore(self.workspace_root / Path(state_dir))
        self.autonomy = AutonomyProfileStore(self.workspace_root / Path(state_dir))
        self.workspace_context = WorkspaceContextStore(
            workspace_root=self.workspace_root,
            config_path="config/workspaces.yaml",
            state_dir=state_dir,
        )
        self.retrieval = RetrievalIndex(
            workspace_root=self.workspace_root,
            config_path="config/retrieval.yaml",
            state_dir=Path(state_dir) / "retrieval",
        )
        self.supervisor = None

    def handle_message(self, chat_id: int, text: str) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return ["Send a message, /agents, /memory, or /status."]

        approval_messages = self._handle_natural_language_approval(chat_id=chat_id, text=cleaned)
        if approval_messages:
            return approval_messages

        if cleaned.lower() == "/agents":
            return [self._format_agent_list()]
        if cleaned.lower() == "/memory":
            return [self.memory.render_memory_summary()]
        if cleaned.lower() == "/runs":
            return [self.runs.render_recent_runs()]
        if cleaned.lower() == "/status":
            return [self.memory.render_status()]
        if cleaned.lower() in {"test status", "status test", "ping", "health"}:
            return [
                "System operational.",
                self.workspace_context.render_workspace_summary(chat_id),
                self.memory.render_status(),
            ]
        if cleaned.lower().startswith("/workspace"):
            return [self._handle_workspace_command(chat_id=chat_id, command_text=cleaned)]
        if cleaned.lower().startswith("/autonomy"):
            return [self._handle_autonomy_command(chat_id=chat_id, command_text=cleaned)]
        if cleaned.lower() == "/approvals":
            return [self._render_pending_approvals(chat_id=chat_id)]
        if cleaned.lower().startswith("/approve"):
            return self._handle_approve_command(chat_id=chat_id, command_text=cleaned)
        if cleaned.lower().startswith("/reject"):
            return [self._handle_reject_command(chat_id=chat_id, command_text=cleaned)]
        if cleaned.lower() == "/reindex":
            count = self.retrieval.rebuild()
            return [f"Retrieval index rebuilt with {count} documents."]
        if cleaned.lower().startswith("/sources"):
            query = cleaned[8:].strip()
            if not query:
                return ["Usage: /sources <query>"]
            return [self.retrieval.render_hits(query_text=query, limit=5, include_citations=True)]

        explicit_agent, explicit_prompt = self._parse_direct_request(cleaned)
        if explicit_agent:
            return self._run_direct_agent(chat_id=chat_id, agent_id=explicit_agent, prompt=explicit_prompt)

        initial_route = self.registry.route_for(cleaned)
        route_task_type = initial_route.task_type
        plan = self._plan_request(chat_id=chat_id, owner_message=cleaned, route_task_type=route_task_type)
        # Apply any direct memory updates from plan, plus always record the
        # owner message so we can extract facts from it later via memory_kind.
        memory_updates = list(plan.get("memory_updates") or [])
        memory_kind = str(plan.get("memory_kind", "none")).strip().lower()
        if memory_kind not in ("none", "") and cleaned:
            memory_updates.append({"kind": memory_kind, "value": cleaned[:400], "source": "owner"})
        self.memory.apply_updates(memory_updates)

        # If no delegate_prompt was provided, use the owner's full message.
        if not plan.get("delegate_prompt"):
            plan["delegate_prompt"] = cleaned

        messages: list[str] = []
        status_update = str(plan.get("status_update", "")).strip()
        if status_update:
            messages.append(status_update)

        if plan.get("rationale"):
            messages.append(f"Planner choice: {plan.get('task_type', 'general')} | {plan.get('rationale')}")

        message_policy = self.policy.classify_owner_request(cleaned)
        if message_policy.requires_approval:
            selected_agent = str(plan.get("selected_agent", "none")).strip()
            approval_tool_calls: list[dict] = [
                call for call in (plan.get("tool_calls") or []) if isinstance(call, dict)
            ]
            approval_execution_plan: dict = self._build_approval_execution_plan(
                owner_message=cleaned,
                plan=plan,
                include_tool_calls=True,
            )
            record = self.approvals.add_pending(
                chat_id=chat_id,
                owner_message=cleaned,
                tool_calls=approval_tool_calls,
                rationale=message_policy.reason,
                workspace=self.workspace_context.get_active_workspace(chat_id),
                execution_plan=approval_execution_plan,
            )
            plan["requires_user_input"] = True
            messages.append(
                f"Approval required ({message_policy.category}): {message_policy.reason} "
                f"Use /approve {record['approval_id']} or /reject {record['approval_id']}"
            )
            self.memory.append_interaction(
                {
                    "chat_id": chat_id,
                    "user_message": cleaned,
                    "selected_agent": selected_agent or "admin",
                    "status": "needs-input",
                    "summary": message_policy.reason[:220],
                }
            )
            return messages

        plan, tool_results = self._run_tool_loop(
            cleaned,
            plan,
            chat_id=chat_id,
            route_task_type=route_task_type,
        )
        if tool_results:
            messages.append(self._format_tool_feedback(tool_results))
        delegate_output = ""
        supervisor_output = ""
        coding_brief_path = ""
        delegate_iterations = 0
        agent_map = self._load_agents()
        selected_agent = self._resolve_selected_agent(str(plan.get("selected_agent", "none")).strip(), agent_map)
        plan["selected_agent"] = selected_agent

        # When tools ran but failed and the planner didn't escalate on its own,
        # route to a repair delegate (codex preferred) for diagnosis and a concrete fix.
        if selected_agent in ("none", "") and tool_results:
            _failed_results = [r for r in tool_results if not r.get("ok")]
            _succeeded_results = [r for r in tool_results if r.get("ok")]
            if _failed_results and not _succeeded_results:
                repair_agent_id, repair_output = self._delegate_tool_failure_repair(
                    owner_message=cleaned,
                    plan=plan,
                    tool_results=tool_results,
                    agent_map=agent_map,
                )
                if repair_output:
                    selected_agent = repair_agent_id
                    plan["selected_agent"] = repair_agent_id
                    delegate_output = repair_output
                    delegate_iterations = 1
                    messages.append(f"[Tool failures detected — escalated to {repair_agent_id} for diagnosis]")

        if selected_agent and selected_agent != "none":
            agent_cfg = agent_map.get(selected_agent)
            if not agent_cfg:
                messages.append(f"Admin routing failed: unknown agent '{selected_agent}'.")
            else:
                delegate_output = self._run_delegated_agent(agent_cfg, cleaned, plan, tool_results)
                delegate_iterations = 1

                if self._is_fabricated_delegation(
                    delegate_output,
                    tool_results,
                    task_type=str(plan.get("task_type", "")).strip(),
                ):
                    delegate_output = ""
                    selected_agent = "none"
                    plan["selected_agent"] = "none"
                    plan["delegate_prompt"] = ""
                    correction_note = "previous delegation produced no tool results; make direct tool calls"
                    replanned_context = f"{cleaned}\n\nDelegation integrity correction: {correction_note}"
                    repaired_plan = self._plan_request(
                        chat_id=chat_id,
                        owner_message=replanned_context,
                        route_task_type=route_task_type,
                    )
                    repaired_plan["selected_agent"] = "none"
                    repaired_plan["delegate_prompt"] = ""
                    repaired_plan, repaired_results = self._run_tool_loop(
                        replanned_context,
                        repaired_plan,
                        chat_id=chat_id,
                        route_task_type=route_task_type,
                    )
                    if repaired_results:
                        tool_results.extend(repaired_results)
                        messages.append(self._format_tool_feedback(repaired_results))
                    plan = repaired_plan
                else:
                    gate_missing = self._validate_delegate_output(cleaned, plan, delegate_output)
                    repair_attempt = 0
                    while gate_missing and repair_attempt < self._MAX_DELEGATE_REPAIR_ATTEMPTS:
                        repair_attempt += 1
                        repaired_plan = dict(plan)
                        repaired_plan["delegate_prompt"] = self._build_delegate_repair_prompt(
                            base_delegate_prompt=str(plan.get("delegate_prompt", "")).strip() or cleaned,
                            prior_output=delegate_output,
                            missing_sections=gate_missing,
                        )
                        delegate_output = self._run_delegated_agent(agent_cfg, cleaned, repaired_plan, tool_results)
                        delegate_iterations += 1
                        gate_missing = self._validate_delegate_output(cleaned, plan, delegate_output)

                    if gate_missing:
                        gate_feedback = self._format_delegate_gate_failure(plan, gate_missing)
                        plan["requires_user_input"] = True
                        messages.append(gate_feedback)

                        self.memory.append_interaction(
                            {
                                "chat_id": chat_id,
                                "user_message": cleaned,
                                "selected_agent": selected_agent or "admin",
                                "status": "needs-input",
                                "summary": gate_feedback[:220],
                            }
                        )
                        return messages

                    recovery_attempt = 0
                    while self._delegate_output_needs_recovery(delegate_output) and recovery_attempt < self._MAX_DELEGATE_RECOVERY_ATTEMPTS:
                        recovery_attempt += 1
                        repaired_plan = dict(plan)
                        repaired_plan["delegate_prompt"] = self._build_delegate_failure_repair_prompt(
                            base_delegate_prompt=str(plan.get("delegate_prompt", "")).strip() or cleaned,
                            prior_output=delegate_output,
                        )
                        delegate_output = self._run_delegated_agent(agent_cfg, cleaned, repaired_plan, tool_results)
                        delegate_iterations += 1

                    if self._delegate_output_needs_recovery(delegate_output):
                        plan["requires_user_input"] = True
                        failure_text = (
                            "Delegate recovery exhausted. Output is still non-executable or deflecting. "
                            "Reply with /work and specify whether to continue with hosted codex/software_dev routing."
                        )
                        messages.append(failure_text)
                        self.memory.append_interaction(
                            {
                                "chat_id": chat_id,
                                "user_message": cleaned,
                                "selected_agent": selected_agent or "admin",
                                "status": "needs-input",
                                "summary": failure_text[:220],
                            }
                        )
                        return messages

                    coding_brief_path = self._maybe_save_coding_agent_brief(
                        selected_agent=selected_agent,
                        owner_message=cleaned,
                        plan=plan,
                        tool_results=tool_results,
                        delegate_output=delegate_output,
                    )
                    if bool(plan.get("needs_supervisor")):
                        supervisor_output = self._review_with_supervisor(agent_cfg, cleaned, delegate_output)

        if coding_brief_path:
            messages.append(f"Coding-agent brief: {coding_brief_path}")
        if selected_agent and selected_agent != "none":
            messages.append(f"Delegate iterations: {delegate_iterations}")

        if tool_results or delegate_output or supervisor_output:
            supervisor_cfg = self._load_agents().get("supervisor")
            if supervisor_cfg:
                review_record, digest_md = ToolSupervisor(self.router, supervisor_cfg).review(
                    owner_message=cleaned,
                    plan=plan,
                    tool_results=tool_results,
                    delegate_output=delegate_output,
                    memory_context=(
                        f"{self.memory.render_context(max_chars=2000)}\n\n"
                        f"{self.memory.render_conversation_thread(limit=8)}"
                    ),
                )
                supervisor_output = str(review_record.get("summary", "")).strip()
                if review_record.get("corrections"):
                    supervisor_output = self._format_supervisor_feedback(review_record)
                messages.append(digest_md)

        final_reply = self._synthesize_reply(
            owner_message=cleaned,
            plan=plan,
            tool_results=tool_results,
            delegate_output=delegate_output,
            supervisor_output=supervisor_output,
        )
        messages.append(final_reply)

        evaluation = self._evaluate_completion(
            owner_message=cleaned,
            task_type=str(plan.get("task_type", "general")),
            selected_agent=selected_agent or "admin",
            tool_results=tool_results,
            delegate_output=delegate_output,
            supervisor_output=supervisor_output,
            plan=plan,
        )

        self.memory.append_interaction(
            {
                "chat_id": chat_id,
                "user_message": cleaned,
                "selected_agent": selected_agent or "admin",
                "status": evaluation["status"],
                "completion_reason": evaluation["completion_reason"],
                "evidence": evaluation["evidence"],
                "execution_intent": evaluation["execution_intent"],
                "summary": final_reply[:220],
            }
        )
        return messages

    def _run_direct_agent(self, chat_id: int, agent_id: str, prompt: str) -> list[str]:
        agent_cfg = self._load_agents().get(agent_id)
        if not agent_cfg:
            return [f"Unknown agent '{agent_id}'. Use /agents to see valid IDs."]

        task_prompt = prompt or str(agent_cfg.get("tasks", [{}])[0].get("prompt", "")).strip()
        output = self._run_delegated_agent(agent_cfg, task_prompt, {"delegate_prompt": task_prompt}, [])
        route = self.registry.route_for(task_prompt)
        evaluation = self._evaluate_completion(
            owner_message=task_prompt,
            task_type=route.task_type,
            selected_agent=agent_id,
            tool_results=[],
            delegate_output=output,
            supervisor_output="",
            plan={"requires_user_input": False, "requires_confirmation": route.requires_confirmation},
        )
        self.memory.append_interaction(
            {
                "chat_id": chat_id,
                "user_message": task_prompt,
                "selected_agent": agent_id,
                "status": evaluation["status"],
                "completion_reason": evaluation["completion_reason"],
                "evidence": evaluation["evidence"],
                "execution_intent": evaluation["execution_intent"],
                "summary": output[:220],
            }
        )
        return [output]

    def _load_agents(self) -> dict[str, dict]:
        agent_map: dict[str, dict] = {}
        for path in sorted(self.agents_dir.glob("*.yaml")):
            cfg = load_yaml(str(path))
            agent_id = str(cfg.get("id", "")).strip()
            if not bool(cfg.get("enabled", True)):
                continue
            if agent_id:
                agent_map[agent_id] = cfg
        return agent_map

    def _resolve_selected_agent(self, requested_agent: str, agent_map: dict[str, dict]) -> str:
        agent_id = str(requested_agent or "none").strip()
        if not agent_id:
            return "none"
        if agent_id in {"none", "admin"}:
            return agent_id
        if agent_id in agent_map:
            return agent_id
        if agent_id == "cad_rnd":
            for fallback in ("codex", "software_dev"):
                if fallback in agent_map:
                    return fallback
            return "none"
        return "none"

    def _format_agent_list(self) -> str:
        lines = ["Available agents:"]
        for agent_id, cfg in sorted(self._load_agents().items()):
            model = cfg.get("llm", {}).get("model") or cfg.get("llm", {}).get("model_env", "")
            lines.append(f"- {agent_id}: {cfg.get('name', '')} [{model}]")
        return "\n".join(lines)

    def _parse_direct_request(self, text: str) -> tuple[str | None, str]:
        cleaned = text.strip()
        if cleaned.lower().startswith("/ask "):
            body = cleaned[5:].strip()
            first_space = body.find(" ")
            if first_space > 0:
                return body[:first_space].strip(), body[first_space + 1 :].strip()
        if ":" in cleaned:
            maybe_agent, maybe_prompt = cleaned.split(":", 1)
            maybe_agent = maybe_agent.strip()
            if maybe_agent in self._load_agents():
                return maybe_agent, maybe_prompt.strip()
        return None, ""

    # ------------------------------------------------------------------
    # Iterative tool execution loop
    # ------------------------------------------------------------------

    _MAX_TOOL_ITERATIONS = 4
    _MAX_EXECUTION_RECOVERY_ATTEMPTS = 1

    _EXECUTION_INTENT_TASK_TYPES = {
        "document_export",
        "desktop_execution",
        "environment",
        "tool_acquisition",
        "workspace_edit",
    }

    _CODE_PATCH_INTENT_TOKENS = (
        "patch",
        "code change",
        "production code",
        "implement",
        "fix",
        "refactor",
        "update ",
        "modify",
        "changed files",
        "diff",
        "pytest",
        "tests",
    )

    _CODE_PATCH_CONTEXT_TOKENS = (
        ".py",
        "changed files",
        "diff",
        "apply_patch",
        "unified diff",
        "src/",
        "test_",
    )

    _EXECUTION_INTENT_TOKENS = (
        "open ",
        "launch ",
        "start ",
        "open app",
        ".exe",
        "executable",
        "desktop app",
        "run command",
        "execute command",
        "terminal",
        "screenshot",
        "screen shot",
        "install",
        "diagnose",
        "troubleshoot",
        "fix my system",
    )

    _KNOWN_PACKAGE_HINTS = {
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

    def _run_tool_loop(
        self,
        owner_message: str,
        initial_plan: dict,
        chat_id: int = 0,
        route_task_type: str | None = None,
    ) -> tuple[dict, list[dict]]:
        """Execute tool calls from the plan, then re-plan with results up to
        MAX_TOOL_ITERATIONS times.  Stops early when the plan emits no more
        tool_calls (i.e. the LLM signals it is done acting and wants to
        delegate or reply).

        Returns the final plan dict and the *accumulated* tool results list.
        """
        plan = initial_plan
        all_tool_results: list[dict] = []
        accumulated_context = owner_message
        recovery_attempts = 0
        self_modify_guard_applied = False

        for _iteration in range(self._MAX_TOOL_ITERATIONS):
            calls = plan.get("tool_calls") or []
            if not calls:
                break  # Nothing more to run

            if str(plan.get("task_type", "")).strip().lower() == "self_modify" and not self_modify_guard_applied:
                calls = [*self._self_modify_guard_calls(), *calls]
                self_modify_guard_applied = True

            needs_approval = self.policy.tool_calls_require_approval(calls)
            if needs_approval.requires_approval:
                if self._should_auto_approve(chat_id=chat_id, owner_message=owner_message, tool_calls=calls):
                    iteration_results = self.tools.execute(calls)
                    all_tool_results.extend(iteration_results)

                    result_summary = self._format_tool_results_verbose(iteration_results)
                    accumulated_context = (
                        f"{accumulated_context}\n\n"
                        f"--- Tool results (iteration {_iteration + 1}, auto-approved) ---\n"
                        f"{result_summary}"
                        "\n\nBased on the above results, produce an updated plan JSON. "
                        "If no further tools are needed, set tool_calls to [] and choose a selected_agent or provide a direct reply."
                    )
                    plan = self._plan_request(
                        chat_id=chat_id,
                        owner_message=accumulated_context,
                        route_task_type=route_task_type,
                    )
                    continue

                record = self.approvals.add_pending(
                    chat_id=chat_id,
                    owner_message=owner_message,
                    tool_calls=calls,
                    rationale=str(plan.get("rationale", "Approval required for risky tool call")),
                    workspace=self.workspace_context.get_active_workspace(chat_id),
                    execution_plan=self._build_approval_execution_plan(
                        owner_message=owner_message,
                        plan=plan,
                        include_tool_calls=True,
                    ),
                )
                plan = dict(plan)
                plan["tool_calls"] = []
                plan["requires_user_input"] = True
                plan["reply"] = (
                    f"Approval required before running risky tool calls. "
                    f"Use /approve {record['approval_id']} or /reject {record['approval_id']}"
                )
                break

            iteration_results = self.tools.execute(calls)
            all_tool_results.extend(iteration_results)
            has_failed_tools = any(not bool(result.get("ok")) for result in iteration_results)
            execution_intent = self._detect_execution_intent(
                owner_message=owner_message,
                task_type=str(plan.get("task_type", "")),
            )

            # Summarise results so the LLM can see what happened
            result_summary = self._format_tool_results_verbose(iteration_results)
            accumulated_context = (
                f"{accumulated_context}\n\n"
                f"--- Tool results (iteration {_iteration + 1}) ---\n"
                f"{result_summary}"
                "\n\nBased on the above results, produce an updated plan JSON. "
                "If no further tools are needed, set tool_calls to [] and choose a selected_agent or provide a direct reply."
            )

            if execution_intent and has_failed_tools:
                accumulated_context += (
                    "\n\nRecovery policy: this is an execution-intent task and at least one tool failed. "
                    "Attempt one concrete corrective tool_call before asking the user for additional input. "
                    "Do not delegate. Keep selected_agent as none."
                )
            elif has_failed_tools:
                accumulated_context += (
                    "\n\nTool failure detected. Review the errors above and either:\n"
                    "a) Retry with corrected tool call parameters (fix the path, args, or approach), or\n"
                    "b) Set selected_agent=codex with a concrete repair delegate_prompt if this needs code analysis.\n"
                    "Do NOT set requires_user_input=true unless the task is genuinely impossible to complete."
                )

            # Re-plan with the enriched context
            plan = self._plan_request(
                chat_id=chat_id,
                owner_message=accumulated_context,
                route_task_type=route_task_type,
            )

            if execution_intent and has_failed_tools:
                plan["selected_agent"] = "none"
                if recovery_attempts < self._MAX_EXECUTION_RECOVERY_ATTEMPTS:
                    if not (plan.get("tool_calls") or []):
                        recovery_attempts += 1
                        fallback_calls = self._infer_execution_recovery_tool_calls(
                            owner_message=owner_message,
                            task_type=str(plan.get("task_type", "")).strip(),
                            failed_results=iteration_results,
                        )
                        if fallback_calls:
                            plan["tool_calls"] = fallback_calls
                            plan["requires_user_input"] = False
                            plan["requires_confirmation"] = False
                            plan["selected_agent"] = "none"
                            plan["reply"] = "Attempting automatic recovery action after failed execution."
                        else:
                            plan["requires_user_input"] = False

        return plan, all_tool_results

    def _should_auto_approve(self, chat_id: int, owner_message: str, tool_calls: list[dict]) -> bool:
        mode = self.autonomy.get_mode(chat_id)
        if mode != "trusted":
            return False

        message_policy = self.policy.classify_owner_request(owner_message)
        if message_policy.requires_approval:
            return False

        always_blocked = {"delete_file", "rename_path"}
        allowed_launch_basenames = {"alibredesign.exe", "alibre design.exe"}
        for call in tool_calls:
            tool_name = str(call.get("tool", "")).strip().lower()
            if tool_name in always_blocked:
                return False
            if tool_name == "launch_executable":
                resolved_name = Path(str(call.get("path", "")).strip()).name.strip().lower()
                if resolved_name not in allowed_launch_basenames:
                    return False
            if tool_name == "run_command" and self.policy.classify_tool_call(call).requires_approval:
                return False
        return True

    def _self_modify_guard_calls(self) -> list[dict]:
        # Self-modify flows start with a deterministic git checkpoint before edits.
        # The execution loop should run `pytest -q` after edits and revert via git if tests fail.
        return [
            {
                "tool": "run_command",
                "command": 'git add -A && git commit -m "chad-self-modify checkpoint" --allow-empty',
                "timeout": 60,
            }
        ]

    def _is_code_patch_request(self, owner_message: str) -> bool:
        lowered = (owner_message or "").strip().lower()
        if not lowered:
            return False
        has_patch_intent = any(token in lowered for token in self._CODE_PATCH_INTENT_TOKENS)
        has_code_context = any(token in lowered for token in self._CODE_PATCH_CONTEXT_TOKENS)
        return has_patch_intent and has_code_context

    def _infer_code_patch_recovery_tool_calls(self, owner_message: str) -> list[dict]:
        """Generate a code-first recovery chain for patch requests."""
        candidate_paths: list[str] = [
            "src/local_agent/orchestration/admin.py",
            "src/local_agent/orchestration/planner.py",
            "src/local_agent/orchestration/tools.py",
            "tests/test_admin_orchestrator.py",
        ]

        for match in re.findall(r"[A-Za-z0-9_./\\-]+\.py", owner_message or ""):
            normalized = match.replace("\\", "/").lstrip("./")
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)

        existing_paths: list[str] = []
        for rel_path in candidate_paths:
            if (self.workspace_root / rel_path).exists() and rel_path not in existing_paths:
                existing_paths.append(rel_path)

        calls: list[dict] = [{"tool": "list_files", "path": "src/local_agent/orchestration", "limit": 120}]
        for rel_path in existing_paths[:3]:
            calls.append(
                {
                    "tool": "read_file",
                    "path": rel_path,
                    "start_line": 1,
                    "end_line": 240,
                }
            )
        calls.append(
            {
                "tool": "run_command",
                "command": ".\\.venv\\Scripts\\python.exe -m pytest tests/test_admin_orchestrator.py -q",
                "timeout": 120,
            }
        )
        return calls[:5]

    def _resolve_alibre_executable(self) -> dict:
        """Deterministically locate AlibreDesign.exe and cache the result.

        Returns a dict with keys:
          found (bool), path (str|None), discovery_method (str), attempted_paths (list[str])
        """
        cache_file = self.memory.state_dir / "alibre_executable_path.json"
        # Check persisted cache first
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_path = cached.get("path", "")
                if cached_path and Path(cached_path).exists():
                    return {
                        "found": True,
                        "path": cached_path,
                        "discovery_method": "cache",
                        "attempted_paths": [cached_path],
                    }
            except Exception:
                pass

        exe_names = ["AlibreDesign.exe", "Alibre Design.exe"]
        search_roots = [
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
        ]
        attempted: list[str] = []

        for root in search_roots:
            if not root.exists():
                continue
            # Look for any folder matching "Alibre*" at the top level of the root
            try:
                alibre_dirs = [d for d in root.iterdir() if d.is_dir() and d.name.lower().startswith("alibre")]
            except PermissionError:
                alibre_dirs = []
            for alibre_dir in alibre_dirs:
                # Search both the folder itself and a "Program" subfolder
                search_dirs = [alibre_dir, alibre_dir / "Program"]
                for search_dir in search_dirs:
                    for exe_name in exe_names:
                        candidate = search_dir / exe_name
                        attempted.append(str(candidate))
                        if candidate.exists():
                            result = {
                                "found": True,
                                "path": str(candidate),
                                "discovery_method": "filesystem_search",
                                "attempted_paths": attempted,
                            }
                            # Persist for future calls
                            try:
                                cache_file.parent.mkdir(parents=True, exist_ok=True)
                                cache_file.write_text(
                                    json.dumps({"path": str(candidate)}), encoding="utf-8"
                                )
                            except Exception:
                                pass
                            return result

        return {
            "found": False,
            "path": None,
            "discovery_method": "filesystem_search",
            "attempted_paths": attempted,
        }

    def _infer_execution_recovery_tool_calls(
        self,
        owner_message: str,
        task_type: str,
        failed_results: list[dict],
    ) -> list[dict]:
        lowered = (owner_message or "").strip().lower()
        failed_output = "\n".join(
            str(result.get("output", "")) for result in failed_results if not result.get("ok")
        ).lower()

        if self._is_code_patch_request(owner_message):
            return self._infer_code_patch_recovery_tool_calls(owner_message)

        if task_type == "desktop_execution" and "alibre" in lowered:
            resolved = self._resolve_alibre_executable()
            if resolved["found"]:
                return [
                    {
                        "tool": "launch_executable",
                        "path": resolved["path"],
                        "wait_seconds": 3,
                    }
                ]
            return [
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
            ]

        install_intent = any(token in lowered for token in ("install", "dependency", "package", "pip"))
        if task_type in {"environment", "tool_acquisition"} and install_intent:
            package_names = self._infer_known_packages(owner_message)
            if package_names:
                calls: list[dict] = []
                for name in package_names:
                    calls.append({"tool": "check_capability", "kind": "package", "name": name})
                calls.append({"tool": "install_python_packages", "packages": package_names})
                return calls

            if task_type == "tool_acquisition" and any(token in lowered for token in ("helper", "script", "tool")):
                return [
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
                ]

        if "not found" in failed_output and task_type == "desktop_execution":
            return [{"tool": "list_files", "path": "C:/Program Files", "limit": 120}]
        return []

    def _infer_known_packages(self, owner_message: str) -> list[str]:
        lowered = (owner_message or "").lower()
        found: list[str] = []
        for token, package_name in self._KNOWN_PACKAGE_HINTS.items():
            if re.search(rf"\b{re.escape(token)}\b", lowered) and package_name not in found:
                found.append(package_name)
        return found[:3]

    def _plan_request(
        self,
        chat_id: int,
        owner_message: str,
        route_task_type: str | None = None,
    ) -> dict:
        admin_cfg = self._load_agents().get("admin")
        if not admin_cfg:
            raise RuntimeError("Missing agents/admin.yaml")

        worker_ids = [agent_id for agent_id in self._load_agents() if agent_id not in {"admin", "supervisor"}]
        retrieval_context = self.retrieval.render_hits(query_text=owner_message, limit=4, include_citations=True)
        workspace_context = self.workspace_context.context_block(chat_id=chat_id)
        enriched_memory_context = (
            f"{self.memory.render_context(max_chars=2000)}\n\n"
            f"Workspace context:\n{workspace_context}\n\n"
            f"Retrieval context:\n{retrieval_context}\n\n"
            f"{self.memory.render_conversation_thread(limit=8)}"
        )
        trusted = self.autonomy.get_mode(chat_id) == "trusted"
        return self.planner.plan(
            owner_message=owner_message,
            memory_context=enriched_memory_context,
            allowed_agents=worker_ids,
            trusted=trusted,
            route_task_type=route_task_type,
        )

    def _render_pending_approvals(self, chat_id: int) -> str:
        pending = self.approvals.list_pending(chat_id=chat_id)
        if not pending:
            return "No pending approvals."
        lines = ["Pending approvals:"]
        for record in pending:
            tools = ", ".join(str(call.get("tool", "")) for call in record.get("tool_calls", []))
            lines.append(
                f"- {record.get('approval_id')} | workspace={record.get('workspace')} | tools={tools}"
            )
        return "\n".join(lines)

    def _handle_natural_language_approval(self, chat_id: int, text: str) -> list[str]:
        lowered = (text or "").strip().lower()
        if not lowered:
            return []
        approval_tokens = ("approve", "approved", "proceed", "go ahead", "continue", "yes proceed")
        if not any(token in lowered for token in approval_tokens):
            return []

        pending = self.approvals.list_pending(chat_id=chat_id)
        if not pending:
            return []

        # Approve and execute the most recent pending request.
        record = pending[-1]
        approval_id = str(record.get("approval_id", "")).strip()
        if not approval_id:
            return []
        return self._handle_approve_command(chat_id=chat_id, command_text=f"/approve {approval_id}")

    def _handle_approve_command(self, chat_id: int, command_text: str) -> list[str]:
        parts = command_text.split(maxsplit=2)
        if len(parts) < 2:
            return ["Usage: /approve <approval_id>"]
        approval_id = parts[1].strip()
        record = self.approvals.get(approval_id)
        if not record:
            return [f"Approval not found: {approval_id}"]
        if str(record.get("status")) != "pending":
            return [f"Approval {approval_id} is already {record.get('status')}."]
        if int(record.get("chat_id", -1)) != chat_id:
            return ["Approval belongs to a different chat session."]

        self.approvals.resolve(approval_id=approval_id, approved=True, reason="Approved by owner")
        execution_plan = record.get("execution_plan") if isinstance(record.get("execution_plan"), dict) else {}
        tool_calls = record.get("tool_calls") or execution_plan.get("tool_calls") or []
        tool_results = self.tools.execute(tool_calls)

        delegate_output = ""
        supervisor_output = ""
        delegate_requires_input = False
        selected_agent = str(execution_plan.get("selected_agent", "none")).strip()
        if selected_agent in {"", "none", "admin"}:
            selected_agent = "none"

        if selected_agent != "none":
            agent_map = self._load_agents()
            resolved_agent = self._resolve_selected_agent(selected_agent, agent_map)
            agent_cfg = agent_map.get(resolved_agent)
            if agent_cfg:
                delegate_plan = {
                    "selected_agent": resolved_agent,
                    "task_type": str(execution_plan.get("task_type", "code_support") or "code_support"),
                    "delegate_prompt": str(execution_plan.get("delegate_prompt", "")).strip()
                    or str(record.get("owner_message", "")).strip(),
                    "needs_supervisor": bool(execution_plan.get("needs_supervisor", True)),
                    "rationale": str(execution_plan.get("rationale", "")).strip(),
                }
                delegate_output = self._run_delegated_agent(
                    agent_cfg=agent_cfg,
                    owner_message=str(record.get("owner_message", "")).strip(),
                    plan=delegate_plan,
                    tool_results=tool_results,
                )

                gate_missing = self._validate_delegate_output(
                    str(record.get("owner_message", "")).strip(),
                    delegate_plan,
                    delegate_output,
                )
                repair_attempt = 0
                while gate_missing and repair_attempt < self._MAX_DELEGATE_REPAIR_ATTEMPTS:
                    repair_attempt += 1
                    repaired_plan = dict(delegate_plan)
                    repaired_plan["delegate_prompt"] = self._build_delegate_repair_prompt(
                        base_delegate_prompt=str(delegate_plan.get("delegate_prompt", "")).strip()
                        or str(record.get("owner_message", "")).strip(),
                        prior_output=delegate_output,
                        missing_sections=gate_missing,
                    )
                    delegate_output = self._run_delegated_agent(
                        agent_cfg=agent_cfg,
                        owner_message=str(record.get("owner_message", "")).strip(),
                        plan=repaired_plan,
                        tool_results=tool_results,
                    )
                    gate_missing = self._validate_delegate_output(
                        str(record.get("owner_message", "")).strip(),
                        delegate_plan,
                        delegate_output,
                    )

                if gate_missing:
                    delegate_requires_input = True
                    delegate_output = self._format_delegate_gate_failure(delegate_plan, gate_missing)
                elif delegate_plan.get("needs_supervisor"):
                    supervisor_output = self._review_with_supervisor(
                        agent_cfg=agent_cfg,
                        owner_message=str(record.get("owner_message", "")).strip(),
                        delegate_output=delegate_output,
                    )
        self.approvals.mark_executed(approval_id=approval_id)
        owner_message = str(record.get("owner_message", "")).strip()
        route = self.registry.route_for(owner_message)
        evaluation = self._evaluate_completion(
            owner_message=owner_message,
            task_type=route.task_type,
            selected_agent=selected_agent or "admin",
            tool_results=tool_results,
            delegate_output=delegate_output,
            supervisor_output=supervisor_output,
            plan={
                "requires_user_input": delegate_requires_input,
                "requires_confirmation": False,
                "execution_intent_override": execution_plan.get("execution_intent_override"),
                "require_mutating_tool_evidence": execution_plan.get("require_mutating_tool_evidence"),
                "tool_calls": tool_calls,
            },
        )
        summary = f"Approval {approval_id} executed. {evaluation['completion_reason']}"
        self.memory.append_interaction(
            {
                "chat_id": chat_id,
                "approval_id": approval_id,
                "user_message": owner_message,
                "selected_agent": "admin",
                "status": evaluation["status"],
                "completion_reason": evaluation["completion_reason"],
                "evidence": evaluation["evidence"],
                "execution_intent": evaluation["execution_intent"],
                "summary": summary[:220],
            }
        )
        return [
            f"Approved and executed {approval_id}.",
            self._format_tool_feedback(tool_results),
            self._format_tool_results(tool_results),
            delegate_output or "No delegate execution.",
            supervisor_output or "No supervisor review.",
        ]

    def _build_approval_execution_plan(self, owner_message: str, plan: dict, include_tool_calls: bool = True) -> dict:
        agent_map = self._load_agents()
        selected_agent = self._resolve_selected_agent(
            str(plan.get("selected_agent", "none")).strip(),
            agent_map,
        )
        looks_like_code_patch = self._is_code_patch_request(owner_message)
        # Production code-change approvals should prefer hosted codex over local software_dev.
        if selected_agent == "software_dev" and looks_like_code_patch:
            selected_agent = "codex" if "codex" in agent_map else "none"
        if selected_agent in {"none", "admin", ""} and looks_like_code_patch:
            if "codex" in agent_map:
                selected_agent = "codex"
            else:
                selected_agent = "none"

        task_type = str(plan.get("task_type", "code_support") or "code_support")
        if looks_like_code_patch:
            task_type = "code_support"

        planned_tool_calls = (
            [call for call in (plan.get("tool_calls") or []) if isinstance(call, dict)]
            if include_tool_calls
            else []
        )

        require_mutating_tool_evidence = task_type in {"code_support", "workspace_edit"}
        execution_intent_override = task_type in self._EXECUTION_INTENT_TASK_TYPES or require_mutating_tool_evidence

        delegate_prompt = str(plan.get("delegate_prompt", "")).strip() or owner_message
        if selected_agent == "codex":
            delegate_prompt = self._build_codex_patch_delegate_prompt(
                owner_message=owner_message,
                base_prompt=delegate_prompt,
            )

        return {
            "selected_agent": selected_agent,
            "delegate_prompt": delegate_prompt,
            "task_type": task_type,
            "needs_supervisor": bool(plan.get("needs_supervisor", True)),
            "rationale": str(plan.get("rationale", "")).strip(),
            "execution_intent_override": execution_intent_override,
            "require_mutating_tool_evidence": require_mutating_tool_evidence,
            "tool_calls": planned_tool_calls,
        }

    def _build_codex_patch_delegate_prompt(self, owner_message: str, base_prompt: str) -> str:
        return (
            f"{base_prompt}\n\n"
            "Codex patch hardening requirements:\n"
            "- You must write the patch, not just describe it.\n"
            "- Return a concrete changed-files list with exact paths.\n"
            "- Return patch-ready edits in unified diff or apply_patch format.\n"
            "- Include the exact validation command you ran and the observed result.\n"
            "- If blocked, state the blocker and what exact input is missing.\n"
            "- Keep changes minimal and scoped to owner intent.\n\n"
            f"Owner request context:\n{owner_message}"
        )

    def _handle_reject_command(self, chat_id: int, command_text: str) -> str:
        parts = command_text.split(maxsplit=2)
        if len(parts) < 2:
            return "Usage: /reject <approval_id> [reason]"
        approval_id = parts[1].strip()
        reason = parts[2].strip() if len(parts) > 2 else "Rejected by owner"
        record = self.approvals.get(approval_id)
        if not record:
            return f"Approval not found: {approval_id}"
        if int(record.get("chat_id", -1)) != chat_id:
            return "Approval belongs to a different chat session."
        self.approvals.resolve(approval_id=approval_id, approved=False, reason=reason)
        return f"Rejected approval {approval_id}. Reason: {reason}"

    def _detect_execution_intent(self, owner_message: str, task_type: str) -> bool:
        lowered = (owner_message or "").strip().lower()
        if str(task_type or "").strip().lower() == "code_support" and self._is_code_patch_request(owner_message):
            return False
        if str(task_type or "").strip().lower() in self._EXECUTION_INTENT_TASK_TYPES:
            return True
        if not lowered:
            return False
        return any(token in lowered for token in self._EXECUTION_INTENT_TOKENS)

    def _extract_artifact_paths(self, chunks: list[str]) -> list[str]:
        seen: list[str] = []
        pattern = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,8}")
        for chunk in chunks:
            for match in pattern.findall(chunk or ""):
                if len(match) < 4:
                    continue
                if match not in seen:
                    seen.append(match)
                if len(seen) >= 8:
                    return seen
        return seen

    def _evaluate_completion(
        self,
        owner_message: str,
        task_type: str,
        selected_agent: str,
        tool_results: list[dict],
        delegate_output: str,
        supervisor_output: str,
        plan: dict,
    ) -> dict:
        _ = selected_agent
        executed_tool_calls = [str(result.get("tool", "")).strip() for result in tool_results if result.get("tool")]
        successful_tools = [
            str(result.get("tool", "")).strip()
            for result in tool_results
            if result.get("tool") and bool(result.get("ok"))
        ]

        verification_signals: list[str] = []
        if successful_tools:
            verification_signals.append(f"successful_tools:{len(successful_tools)}")

        evidence_chunks = [
            *(str(result.get("output", "")) for result in tool_results),
            delegate_output or "",
            supervisor_output or "",
        ]
        combined_text = "\n".join(evidence_chunks).lower()
        if any(token in combined_text for token in ("completed", "created", "saved", "installed", "executed")):
            verification_signals.append("completion_language_detected")

        evidence = {
            "executed_tool_calls": executed_tool_calls,
            "successful_tools": successful_tools,
            "verification_signals": verification_signals,
            "artifact_paths": self._extract_artifact_paths(evidence_chunks),
            "diagnostics": {
                "planned_tool_count": len(plan.get("tool_calls") or []),
                "executed_tool_count": len(executed_tool_calls),
                "failed_tool_count": sum(1 for result in tool_results if not bool(result.get("ok"))),
                "requires_confirmation": bool(plan.get("requires_confirmation")),
            },
            "notes": [],
        }

        execution_intent_override = plan.get("execution_intent_override")
        if isinstance(execution_intent_override, bool):
            execution_intent = execution_intent_override
        else:
            execution_intent = self._detect_execution_intent(owner_message=owner_message, task_type=task_type)
        requires_user_input = bool(plan.get("requires_user_input"))
        requires_confirmation = bool(plan.get("requires_confirmation"))
        failed_tools = any(not bool(result.get("ok")) for result in tool_results)
        require_mutating_tool_evidence = bool(plan.get("require_mutating_tool_evidence"))

        if requires_user_input:
            if execution_intent and failed_tools:
                evidence["notes"].append("Execution failed and planner requested input; classify as failed_execution.")
                return {
                    "status": "failed",
                    "completion_reason": "failed_execution",
                    "evidence": evidence,
                    "execution_intent": True,
                }
            evidence["notes"].append("Planner marked requires_user_input=true.")
            return {
                "status": "needs-input",
                "completion_reason": "waiting_user_input",
                "evidence": evidence,
                "execution_intent": execution_intent,
            }

        if execution_intent:
            if failed_tools:
                evidence["notes"].append("One or more execution tools failed.")
                return {
                    "status": "failed",
                    "completion_reason": "failed_execution",
                    "evidence": evidence,
                    "execution_intent": True,
                }

            if requires_confirmation and not executed_tool_calls:
                evidence["notes"].append("Execution request requires approval/confirmation before action.")
                evidence["diagnostics"]["blocked_reason"] = "approval_required"
                return {
                    "status": "needs-input",
                    "completion_reason": "blocked_by_approval",
                    "evidence": evidence,
                    "execution_intent": True,
                }

            if not executed_tool_calls:
                evidence["notes"].append("No executed tools recorded for execution-intent request.")
                evidence["diagnostics"]["blocked_reason"] = "no_tool_calls_executed"
                return {
                    "status": "needs-input",
                    "completion_reason": "missing_execution_evidence",
                    "evidence": evidence,
                    "execution_intent": True,
                }

        if require_mutating_tool_evidence:
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
            if not any(tool in mutating_tools for tool in executed_tool_calls):
                evidence["notes"].append(
                    "Coding/workspace change request missing mutating-tool execution evidence."
                )
                evidence["diagnostics"]["blocked_reason"] = "missing_mutating_tool_evidence"
                return {
                    "status": "needs-input",
                    "completion_reason": "missing_execution_evidence",
                    "evidence": evidence,
                    "execution_intent": execution_intent,
                }

        return {
            "status": "completed",
            "completion_reason": "criteria_met",
            "evidence": evidence,
            "execution_intent": execution_intent,
        }

    def _handle_workspace_command(self, chat_id: int, command_text: str) -> str:
        parts = command_text.split(maxsplit=2)
        if len(parts) == 1:
            return self.workspace_context.list_workspaces_text(chat_id)
        if len(parts) >= 3 and parts[1].lower() == "set":
            workspace_id = parts[2].strip()
            if self.workspace_context.set_active_workspace(chat_id=chat_id, workspace_id=workspace_id):
                return f"Active workspace set to {workspace_id}."
            return f"Unknown workspace '{workspace_id}'."
        if len(parts) >= 2 and parts[1].lower() in {"show", "status"}:
            return self.workspace_context.render_workspace_summary(chat_id)
        return "Usage: /workspace | /workspace set <id> | /workspace show"

    def _handle_autonomy_command(self, chat_id: int, command_text: str) -> str:
        parts = command_text.split(maxsplit=2)
        if len(parts) == 1:
            mode = self.autonomy.get_mode(chat_id)
            return (
                "Autonomy mode: "
                f"{mode}. Use /autonomy set trusted for auto-approval of safe local edits/commands, "
                "or /autonomy set manual for explicit approvals."
            )

        if len(parts) >= 3 and parts[1].lower() == "set":
            mode = parts[2].strip().lower()
            try:
                stored = self.autonomy.set_mode(chat_id=chat_id, mode=mode)
            except ValueError:
                return "Invalid autonomy mode. Use: /autonomy set manual | /autonomy set trusted"
            if stored == "trusted":
                return (
                    "Autonomy mode set to trusted. Safe local risky tools will auto-run without /approve. "
                    "Destructive, external, and production-risk actions still require approval."
                )
            return "Autonomy mode set to manual. Risky local tools require /approve."

        return "Usage: /autonomy | /autonomy set manual | /autonomy set trusted"

    def _select_repair_delegate(self, task_type: str, agent_map: dict) -> str | None:
        """Pick the best available delegate to audit and repair tool failures."""
        preference_order = ["codex", "software_dev"]
        for candidate in preference_order:
            if candidate in agent_map:
                return candidate
        return None

    def _build_tool_failure_audit_prompt(
        self,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
    ) -> str:
        """Build a structured prompt for the repair delegate describing what failed and why."""
        failed = [r for r in tool_results if not r.get("ok")]
        succeeded = [r for r in tool_results if r.get("ok")]
        task_type = str(plan.get("task_type", "")).strip()
        lines = [
            f"Original request: {owner_message}",
            f"Task type: {task_type}",
            "",
            "## Tool Execution Failures",
        ]
        for r in failed:
            lines.append(f"- Tool: {r.get('tool')} | Error: {r.get('output', 'unknown error')}")
        if succeeded:
            lines.append("")
            lines.append("## Successful Tool Calls (context only)")
            for r in succeeded:
                out = str(r.get("output", ""))[:200]
                lines.append(f"- Tool: {r.get('tool')} | Output: {out}")
        lines += [
            "",
            "## Your Task",
            "1. **Root Cause**: Diagnose exactly why each failed tool call failed "
            "(wrong path, bad args, missing file, permission issue, resolver bug, etc.).",
            "2. **Corrective Action**: Provide the exact corrected tool call parameters, "
            "or the concrete code patch if the failure is in a source file "
            "(tools.py, planner.py, registry.py, etc.).",
            "3. **Verification**: State the exact command or tool call to confirm the fix works.",
            "",
            "Return implementation-ready output only. No meta-commentary.",
        ]
        return "\n".join(lines)

    def _delegate_tool_failure_repair(
        self,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
        agent_map: dict,
    ) -> tuple[str, str]:
        """Escalate unresolved tool failures to a delegate for diagnosis and repair.

        Returns (agent_id, output). Returns ("none", "") if no suitable delegate exists.
        """
        repair_agent_id = self._select_repair_delegate(
            task_type=str(plan.get("task_type", "")),
            agent_map=agent_map,
        )
        if not repair_agent_id:
            return "none", ""
        agent_cfg = agent_map.get(repair_agent_id)
        if not agent_cfg:
            return "none", ""

        audit_prompt = self._build_tool_failure_audit_prompt(owner_message, plan, tool_results)
        repair_plan = dict(plan)
        repair_plan["delegate_prompt"] = audit_prompt
        repair_plan["selected_agent"] = repair_agent_id
        repair_plan["task_type"] = "code_support"

        output = self._run_delegated_agent(agent_cfg, owner_message, repair_plan, tool_results)

        # One retry if the delegate deflects rather than fixing.
        if self._delegate_output_needs_recovery(output):
            retry_prompt = self._build_delegate_failure_repair_prompt(
                base_delegate_prompt=audit_prompt,
                prior_output=output,
            )
            repair_plan["delegate_prompt"] = retry_prompt
            output = self._run_delegated_agent(agent_cfg, owner_message, repair_plan, tool_results)

        return repair_agent_id, output

    def _run_delegated_agent(
        self,
        agent_cfg: dict,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
    ) -> str:
        delegate_prompt = str(plan.get("delegate_prompt", "")).strip() or owner_message
        selected_agent = str(plan.get("selected_agent", "")).strip()
        task_type = str(plan.get("task_type", "")).strip()
        tool_text = self._format_tool_results(tool_results)

        detected_project_path = detect_project_path_in_text(f"{owner_message}\n{delegate_prompt}")
        project_snapshot = ""
        if detected_project_path:
            project_snapshot = scan_external_project(detected_project_path)

        task_prompt = (
            f"Owner request:\n{owner_message}\n\n"
            f"Admin route:\n{delegate_prompt}\n\n"
            f"Delegate contract:\n{self._build_delegate_contract(selected_agent, task_type)}\n\n"
            f"Known memory:\n{self.memory.render_context()}\n\n"
            f"Workspace brief:\n{self._workspace_brief()}\n\n"
            f"Detected external project snapshot:\n{project_snapshot or 'None detected from request text.'}\n\n"
            f"Tool results:\n{tool_text}"
        )
        result = run_agent_task(self.router, agent_cfg, task_prompt, task_context=owner_message)
        return str(result.get("output", "")).strip()

    def _build_delegate_contract(self, selected_agent: str, task_type: str) -> str:
        if selected_agent == "codex":
            return (
                "You are the implementation agent. Write the patch directly.\n"
                "If you receive a task but have no tools available in your manifest, you MUST respond exactly with a failure: 'CANNOT_EXECUTE: no tools available for this task.' You must NEVER describe, summarize, or claim actions (file reads, searches, inspections) that you did not actually perform via tool calls.\n"
                "Required sections:\n"
                "1) Concrete Deliverables (what is implemented)\n"
                "2) Changed Files (exact file paths)\n"
                "3) Patch (unified diff or apply_patch-ready blocks)\n"
                "4) Validation Command(s) and observed result\n"
                "5) Blockers with exact missing inputs, if any"
            )

        if selected_agent == "software_dev" or task_type == "code_support":
            return (
                "You must return implementation-ready output, not meta commentary.\n"
                "Required sections:\n"
                "1) Concrete Deliverables (exact wrappers/features/tests to implement)\n"
                "2) Target Files/Modules (or explicit new files to add)\n"
                "3) Code Snippets or Patch-Ready Blocks\n"
                "4) Validation Command(s) and expected results\n"
                "5) Blockers with exact missing inputs, if any"
            )

        if selected_agent == "cad_rnd" or task_type == "cad_rnd":
            return (
                "You must return a concrete experiment handoff, not generic framework text.\n"
                "Required sections:\n"
                "1) Hypothesis\n"
                "2) Minimal Executable Experiment\n"
                "3) Copilot/Codex Prompt (paste-ready)\n"
                "4) Acceptance Checks\n"
                "5) Next Iteration"
            )

        return (
            "Return concrete, actionable output with explicit deliverables and validation steps."
        )

    def _is_fabricated_delegation(self, delegate_output: str, tool_results: list[dict], task_type: str = "") -> bool:
        normalized_task_type = str(task_type or "").strip().lower()
        if normalized_task_type not in {"workspace_inspection", "inspection"}:
            return False

        text = (delegate_output or "").strip()
        if not text:
            return False
        if tool_results:
            return False
        lowered = text.lower()
        if "cannot_execute: no tools available for this task." in lowered:
            return False

        # Narrative claims of inspection without execution evidence are treated as fabricated output.
        if len(lowered.split()) < 8:
            return False
        action_tokens = (
            "inspected",
            "inspection",
            "read",
            "searched",
            "scanned",
            "reviewed",
            "analyzed",
            "file",
            "files",
            "workspace",
            "directory",
            "line",
        )
        return any(token in lowered for token in action_tokens)

    def _validate_delegate_output(self, owner_message: str, plan: dict, delegate_output: str) -> list[str]:
        selected_agent = str(plan.get("selected_agent", "")).strip().lower()
        task_type = str(plan.get("task_type", "")).strip().lower()
        delegate_prompt = str(plan.get("delegate_prompt", "")).strip().lower()
        combined = f"{owner_message}\n{delegate_prompt}".lower()

        coding_tokens = (
            "implement",
            "build",
            "create",
            "write",
            "develop",
            "api",
            "wrapper",
            "library",
            "mate",
            "assembly",
            "test",
            "alibre",
            "agentic cad",
            "agenticcad",
        )
        has_coding_intent = any(token in combined for token in coding_tokens)
        needs_code_gate = (
            task_type == "code_support"
            or (selected_agent == "codex" and has_coding_intent)
            or (selected_agent == "software_dev" and has_coding_intent)
        )
        if not needs_code_gate:
            return []

        text = (delegate_output or "").strip().lower()
        if not text:
            return [
                "concrete deliverables",
                "target files/modules",
                "code snippets or patch-ready blocks",
                "validation command(s)",
                "blockers (or explicit none)",
            ]

        required_checks = [
            ("concrete deliverables", ("concrete deliverables", "deliverables", "implementation slice", "objective")),
            ("target files/modules", ("target files", "files/modules", "modules", "file:")),
            ("code snippets or patch-ready blocks", ("code snippet", "patch-ready", "```", "diff", "python")),
            ("validation command(s)", ("validation", "pytest", "run:", "command")),
            ("blockers (or explicit none)", ("blocker", "missing input", "none")),
        ]

        if selected_agent == "codex":
            required_checks = [
                ("concrete deliverables", ("concrete deliverables", "deliverables", "implemented")),
                ("changed files", ("changed files", "files changed", "target files", "file:")),
                ("patch", ("```diff", "*** begin patch", "diff --git", "patch-ready")),
                ("validation command(s) and observed result", ("validation", "pytest", "result", "passed", "failed")),
                ("blockers (or explicit none)", ("blocker", "missing input", "none")),
            ]

        missing: list[str] = []
        for label, tokens in required_checks:
            if not any(token in text for token in tokens):
                missing.append(label)
        return missing

    def _format_delegate_gate_failure(self, plan: dict, missing_sections: list[str]) -> str:
        selected_agent = str(plan.get("selected_agent", "none"))
        task_type = str(plan.get("task_type", "general"))
        lines = [
            "Delegate quality gate failed. The delegated output did not meet implementation handoff requirements.",
            f"Route: selected_agent={selected_agent}, task_type={task_type}",
            "Missing required sections:",
        ]
        for idx, section in enumerate(missing_sections, start=1):
            lines.append(f"{idx}. {section}")
        lines.append("Please resubmit with explicit implementation deliverables, file targets, code blocks, validation command(s), and blockers.")
        return "\n".join(lines)

    def _build_delegate_repair_prompt(
        self,
        base_delegate_prompt: str,
        prior_output: str,
        missing_sections: list[str],
    ) -> str:
        missing_text = "\n".join(f"- {item}" for item in missing_sections)
        return (
            f"{base_delegate_prompt}\n\n"
            "Quality gate repair request:\n"
            "Your previous output was close but missing required sections.\n"
            "Regenerate the full response and include ALL required sections.\n"
            "Missing sections from prior output:\n"
            f"{missing_text}\n\n"
            "Prior output (for reference only):\n"
            f"{prior_output[:2500]}"
        )

    def _build_delegate_failure_repair_prompt(self, base_delegate_prompt: str, prior_output: str) -> str:
        return (
            f"{base_delegate_prompt}\n\n"
            "Recovery repair request:\n"
            "Your prior response was non-executable or deflected the task.\n"
            "Regenerate with implementation-ready, directly executable output only.\n"
            "Do not apologize. Do not ask for clarification unless absolutely blocking.\n"
            "Return concrete files, steps, validation commands, and blockers.\n\n"
            "Prior output (for reference only):\n"
            f"{prior_output[:2500]}"
        )

    def _delegate_output_needs_recovery(self, delegate_output: str) -> bool:
        text = (delegate_output or "").strip().lower()
        if not text:
            return True
        failure_tokens = (
            "i'm sorry",
            "i apologize",
            "unable to",
            "cannot",
            "can't",
            "awaiting",
            "please confirm",
            "need your confirmation",
            "i need to understand",
            "i need more information",
            "no result",
        )
        return any(token in text for token in failure_tokens)

    def _review_with_supervisor(self, agent_cfg: dict, owner_message: str, delegate_output: str) -> str:
        supervisor_cfg = self._load_agents().get("supervisor")
        if not supervisor_cfg:
            return ""
        worker_record = {
            "agent_id": agent_cfg["id"],
            "agent_name": agent_cfg["name"],
            "provider": agent_cfg["llm"]["provider"],
            "model": resolve_agent_llm(agent_cfg)["model"],
            "task_prompt": owner_message,
            "output": delegate_output,
        }
        review_record, _ = ToolSupervisor(self.router, supervisor_cfg).review(
            owner_message=owner_message,
            plan={"task_type": "delegated_work"},
            tool_results=[],
            delegate_output=delegate_output,
            memory_context=self.memory.render_context(),
        )
        supervisor_output = str(review_record.get("summary", "")).strip()
        if supervisor_output:
            self.memory.apply_updates(
                [{"kind": "lesson", "value": supervisor_output[:300], "source": "supervisor"}]
            )
        return supervisor_output

    def _synthesize_reply(
        self,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
        delegate_output: str,
        supervisor_output: str,
    ) -> str:
        admin_cfg = self._load_agents().get("admin")
        if not admin_cfg:
            raise RuntimeError("Missing agents/admin.yaml")

        if not tool_results and not delegate_output and not supervisor_output:
            reply = str(plan.get("reply", "")).strip()
            return reply or "No result produced."

        system = (
            "You are writing the final Telegram response to the owner. "
            "Be concise, concrete, and action-oriented. "
            "If supervisor output exists, prefer its conclusions but keep the reply readable on a phone."
        )
        user = (
            f"Owner message:\n{owner_message}\n\n"
            f"Planned reply intent:\n{plan.get('reply', '')}\n\n"
            f"Tool results:\n{self._format_tool_results(tool_results)}\n\n"
            f"Delegate output:\n{delegate_output or 'None'}\n\n"
            f"Supervisor output:\n{supervisor_output or 'None'}"
        )
        return self._chat_with_agent(admin_cfg, system=system, user=user).strip()

    def _chat_with_agent(self, agent_cfg: dict, system: str, user: str) -> str:
        resolved = resolve_agent_llm(agent_cfg)
        return self.router.chat(
            provider=resolved["provider"],
            model=resolved["model"],
            system=system,
            user=user,
            options=resolved["options"],
        )

    def _workspace_brief(self) -> str:
        docs = [
            self.workspace_root / "README.md",
            self.workspace_root / "WORK_COMPLETED_TODAY.md",
            self.workspace_root / "agentPersonaGuid.md",
        ]
        parts: list[str] = []
        for path in docs:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")[:1200]
            parts.append(f"[{path.name}]\n{text}")
        recent_runs = self.runs.render_recent_runs(limit=5, max_chars=2200)
        if recent_runs.strip():
            parts.append(f"[recent_runs]\n{recent_runs}")
        # Include external project snapshot (e.g. AgenticCAD) when env var is set
        external_project_dir = get_external_project_dir()
        if external_project_dir:
            snapshot = scan_external_project(external_project_dir)
            if snapshot:
                parts.append(snapshot)
        return "\n\n".join(parts)[:5000]

    def _maybe_save_coding_agent_brief(
        self,
        selected_agent: str,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
        delegate_output: str,
    ) -> str:
        if selected_agent not in {"software_dev", "cad_rnd", "codex"}:
            return ""

        combined = "\n".join(
            [
                owner_message,
                str(plan.get("delegate_prompt", "")),
                str(plan.get("task_type", "")),
                str(plan.get("rationale", "")),
            ]
        ).lower()
        trigger_tokens = (
            "copilot",
            "codex",
            "coding agent",
            "agentic cad",
            "alibre",
            "cad",
            "library",
            "experiment",
        )
        if not any(token in combined for token in trigger_tokens):
            return ""

        brief_dir = self.runs.runs_dir / "delegations"
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief_path = brief_dir / f"{self._utc_stamp()}-{selected_agent}.md"
        brief_path.write_text(
            self._build_coding_agent_brief(
                selected_agent=selected_agent,
                owner_message=owner_message,
                plan=plan,
                tool_results=tool_results,
                delegate_output=delegate_output,
            ),
            encoding="utf-8",
        )
        return str(brief_path)

    def _build_coding_agent_brief(
        self,
        selected_agent: str,
        owner_message: str,
        plan: dict,
        tool_results: list[dict],
        delegate_output: str,
    ) -> str:
        delegate_prompt = str(plan.get("delegate_prompt", "")).strip() or owner_message
        rationale = str(plan.get("rationale", "")).strip() or "No planner rationale captured."
        task_type = str(plan.get("task_type", "general")).strip() or "general"
        tool_summary = self._format_tool_results(tool_results)
        workspace_focus = self._candidate_files_for_agent(selected_agent)
        validation_command = "c:/Users/platt/Desktop/local-agent/.venv/Scripts/python.exe -m pytest tests/test_admin_orchestrator.py"

        prompt = (
            "You are the coding agent working inside the local-agent workspace.\n"
            f"Objective: {delegate_prompt}\n\n"
            "Operating requirements:\n"
            "- Preserve the dual-use nature of the workspace: business planning plus remote admin/R&D delegation.\n"
            "- Improve the system toward a functional Agentic CAD library and the prompt-driven workflow that manages it.\n"
            "- Start from the nearest implementation anchor, make the smallest defensible change, and validate before widening scope.\n"
            "- Prefer edits that improve orchestration, prompt quality, experiment tracking, or validation for Alibre/CAD work.\n\n"
            "Priority files to inspect first:\n"
            + "\n".join(f"- {path}" for path in workspace_focus)
            + "\n\n"
            "Acceptance checks:\n"
            "- The request routes through the correct agent path for CAD or coding-agent work.\n"
            "- The workspace produces a reusable delegation artifact or prompt pack for Copilot/Codex.\n"
            "- Any touched logic has focused test coverage or a narrow validation command.\n"
            f"- Run this validation if tests are touched: {validation_command}\n\n"
            "Relevant local context:\n"
            f"- Selected agent: {selected_agent}\n"
            f"- Task type: {task_type}\n"
            f"- Planner rationale: {rationale}\n\n"
            "Tool findings:\n"
            f"{tool_summary}\n\n"
            "Delegate output from the local worker:\n"
            f"{delegate_output or 'No delegate output captured.'}\n"
        )

        lines = [
            "# Coding Agent Brief",
            "",
            "## Purpose",
            "This artifact is generated for remote execution by Copilot or Codex when the owner delegates R&D or coding work through the admin orchestrator.",
            "",
            "## Owner Request",
            owner_message,
            "",
            "## Planner Route",
            f"- selected_agent: {selected_agent}",
            f"- task_type: {task_type}",
            f"- rationale: {rationale}",
            "",
            "## Recommended Experiment Loop",
            "1. Inspect the priority files and identify the controlling abstraction.",
            "2. Make one narrow change that advances Agentic CAD delegation or library functionality.",
            "3. Run the narrowest validation available.",
            "4. Record any remaining blockers as the next experiment rather than broadening the patch.",
            "",
            "## Priority Files",
            *[f"- {path}" for path in workspace_focus],
            "",
            "## Suggested Validation",
            f"- {validation_command}",
            "",
            "## Paste Into Copilot Or Codex",
            "```text",
            prompt.rstrip(),
            "```",
        ]
        return "\n".join(lines)

    def _candidate_files_for_agent(self, selected_agent: str) -> list[str]:
        shared = [
            "src/local_agent/orchestration/admin.py",
            "src/local_agent/orchestration/registry.py",
            "src/local_agent/orchestration/planner.py",
            "tests/test_admin_orchestrator.py",
            "README.md",
        ]
        if selected_agent == "cad_rnd":
            return ["agents/cad_rnd.yaml", "agents/admin.yaml", *shared]
        if selected_agent == "codex":
            return ["agents/codex.yaml", "agents/admin.yaml", *shared]
        return ["agents/software_dev.yaml", "agents/admin.yaml", *shared]

    @staticmethod
    def _utc_stamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def _format_agent_catalog(self, worker_ids: list[str]) -> str:
        agent_map = self._load_agents()
        lines = []
        for agent_id in worker_ids:
            cfg = agent_map[agent_id]
            lines.append(
                f"- {agent_id}: {cfg.get('name', '')} | {cfg.get('description', '')} | model={cfg.get('llm', {}).get('model', cfg.get('llm', {}).get('model_env', ''))}"
            )
        return "\n".join(lines)

    def _format_tool_results(self, tool_results: list[dict]) -> str:
        if not tool_results:
            return "No tool calls."
        lines = []
        for result in tool_results:
            status = "ok" if result.get("ok") else "error"
            lines.append(f"[{status}] {result.get('tool')}:\n{str(result.get('output', ''))[:1200]}")
        return "\n\n".join(lines)

    def _format_tool_results_verbose(self, tool_results: list[dict]) -> str:
        if not tool_results:
            return "No tool calls."
        lines = []
        for result in tool_results:
            status = "ok" if result.get("ok") else "error"
            lines.append(f"[{status}] {result.get('tool')}:\n{str(result.get('output', ''))[:8000]}")
        return "\n\n".join(lines)

    def _format_tool_feedback(self, tool_results: list[dict]) -> str:
        lines = ["Tool execution:"]
        for result in tool_results:
            status = "ok" if result.get("ok") else "error"
            lines.append(f"- {result.get('tool')} [{status}]")
        return "\n".join(lines)

    def _format_supervisor_feedback(self, review_record: dict) -> str:
        lines = [f"Supervisor: {review_record.get('summary', '')}".strip()]
        corrections = review_record.get("corrections") or []
        if corrections:
            lines.append("Corrections:")
            for item in corrections[:5]:
                lines.append(f"- {item}")
        risks = review_record.get("risks") or []
        if risks:
            lines.append("Risks:")
            for item in risks[:5]:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _parse_json(self, raw: str) -> dict:
        candidate = raw.strip()
        if "```" in candidate:
            start = candidate.find("```")
            end = candidate.rfind("```")
            if start != -1 and end > start:
                candidate = candidate[start + 3 : end].strip()
                if candidate.lower().startswith("json"):
                    candidate = candidate[4:].strip()

        start = candidate.find("{")
        end = candidate.rfind("}")

        parsed: dict = {}
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                # Best-effort field extraction when JSON is malformed.
                # Pull out the value of selected_agent by scanning the text.
                import re
                m = re.search(r'"selected_agent"\s*:\s*"([^"]+)"', candidate)
                if m:
                    parsed["selected_agent"] = m.group(1)
                for bool_key in ("needs_supervisor", "requires_user_input"):
                    mb = re.search(rf'"{bool_key}"\s*:\s*(true|false)', candidate, re.IGNORECASE)
                    if mb:
                        parsed[bool_key] = mb.group(1).lower() == "true"

        parsed.setdefault("status_update", "")
        parsed.setdefault("selected_agent", "none")
        parsed.setdefault("delegate_prompt", "")
        parsed.setdefault("reply", raw.strip() if not parsed.get("selected_agent") or parsed["selected_agent"] == "none" else "")
        parsed.setdefault("tool_calls", [])
        parsed.setdefault("memory_updates", [])
        parsed.setdefault("memory_kind", "none")
        parsed.setdefault("needs_supervisor", False)
        parsed.setdefault("requires_user_input", False)

        if not isinstance(parsed.get("tool_calls"), list):
            parsed["tool_calls"] = []
        else:
            parsed["tool_calls"] = [call for call in parsed["tool_calls"] if isinstance(call, dict)]

        if not isinstance(parsed.get("memory_updates"), list):
            parsed["memory_updates"] = []
        else:
            parsed["memory_updates"] = [item for item in parsed["memory_updates"] if isinstance(item, dict)]

        return parsed