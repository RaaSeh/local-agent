# Local Agent AI Operations Platform

A Windows-first AI operations assistant platform for managing real business work across multiple workspaces.

This repository now targets a general-purpose orchestration system, not a narrow business-planner bot.

Primary businesses:
- Rocket Wash
- Raze Development Studios

## What This Platform Does

- Runs a hosted high-capability orchestrator model for primary planning and synthesis.
- Delegates specialist work to configurable agents and model providers.
- Uses local models (Ollama) when appropriate without enforcing local-first quality tradeoffs.
- Supports workspace-aware context priming per chat/session.
- Supports retrieval with source citations over repo files, prompts, and workspace docs.
- Supports guarded local execution (filesystem, scripts, package installs, commands).
- Uses a centralized approval flow for risky actions.
- Supports remote operation through Telegram in phase 1.

## Current Architecture

Core modules:
- `src/local_agent/orchestration/admin.py`: central orchestration loop for admin/work requests.
- `src/local_agent/orchestration/planner.py`: LLM planner that returns structured routing/tool decisions.
- `src/local_agent/orchestration/task_router.py`: config-driven routing and model policy resolution.
- `src/local_agent/policy/approvals.py`: centralized policy classification and approval store.
- `src/local_agent/context/workspaces.py`: active workspace selection and primer loading.
- `src/local_agent/retrieval/simple_index.py`: simple retrieval index and citation rendering.
- `src/local_agent/orchestration/tools.py`: guarded tool executor for local operations.
- `src/local_agent/integrations/telegram_bot.py`: Telegram interface over the same backend logic.

Legacy deterministic planning flow remains available:
- `src/local_agent/workflows/business_plan.py`
- `prompts/*.txt`

## Workspace Model

Configured in `config/workspaces.yaml`.

Default workspaces:
- `rocket-wash`
- `raze-development-studios`
- `shared`

Primer files:
- `workspaces/rocket-wash/primer.md`
- `workspaces/raze-development-studios/primer.md`
- `workspaces/shared/primer.md`

Telegram commands:
- `/workspace` list available workspaces
- `/workspace set <id>` set active workspace for current chat
- `/workspace show` show active workspace summary

## Routing And Model Configuration

The planner + task router determine:
- task type
- selected agent (or direct tool path)
- provider/model
- supervisor requirement
- confirmation requirement

Default strategy:
- Orchestrator (`admin`) defaults to hosted provider via `ORCHESTRATOR_PRIMARY_PROVIDER` and `ORCHESTRATOR_PRIMARY_MODEL`.
- Specialist coding/CAD tasks can route to local or hosted models via env overrides.

Useful overrides:
- `ORCHESTRATOR_PRIMARY_PROVIDER=openai`
- `ORCHESTRATOR_PRIMARY_MODEL=gpt-4.1-mini`
- `TASK_ROUTER_<AGENT>_PROVIDER=...`
- `TASK_ROUTER_<AGENT>_MODEL=...`

Examples:
- `TASK_ROUTER_SOFTWARE_DEV_PROVIDER=ollama`
- `TASK_ROUTER_SOFTWARE_DEV_MODEL=qwen2.5-coder:14b`
- `TASK_ROUTER_CAD_RND_PROVIDER=anthropic`
- `TASK_ROUTER_CAD_RND_MODEL=claude-sonnet-4-20250514`

## Retrieval And Citations

Retrieval config:
- `config/retrieval.yaml`

Behavior:
- Builds a simple local index in `state/retrieval/index.json`.
- Searches configured source paths and file types.
- Returns snippets with source references.
- Citations are enabled by default in phase 1.

Telegram commands:
- `/reindex` rebuild retrieval index
- `/sources <query>` inspect retrieval snippets/citations directly

## Memory Strategy

This platform does not rely on raw transcript accumulation as the main memory mechanism.

Memory components:
- Workspace primers (editable files under `workspaces/*/primer.md`)
- Rolling owner/work memory (`state/owner_profile.json`, `state/lessons.json`)
- Interaction history (`state/interactions.jsonl`)
- Retrieval-based context rehydration

## Approval Framework

Centralized policy and queue:
- Policy config: `config/policy.yaml`
- Store: `state/pending_approvals.json`

Risky tool calls are queued for approval before execution.

Telegram commands:
- `/approvals`
- `/approve <approval_id>`
- `/reject <approval_id> [reason]`

Default gated classes:
- destructive file operations
- shell execution
- executable launch
- package installation
- generated tool/scaffold writes
- downloads

## Desktop Executable Workflow

Repeatable runbook for tasks like "open the exe on the desktop":

1. Discovery: locate the target executable with tool calls such as `list_files` / `search_text`.
2. Capability check: run `check_capability` for any required command/package support.
3. Setup if missing: use `install_python_packages`, `download_file`, or `scaffold_tool` as needed.
4. Launch: use `launch_executable` (or `run_command` when appropriate) with the explicit desktop `.exe` path.
5. Verify: require launch evidence in tool output (pid/running status or equivalent process result).
6. Record: persist interaction evidence/status so the run is auditable in `state/interactions.jsonl`.

See [DESKTOP_EXECUTION_WORKFLOW.md](DESKTOP_EXECUTION_WORKFLOW.md) for the detailed procedure.

## Artifacts And Storage Layout

Runtime storage:
- `runs/` structured run outputs and delegation briefs
- `runs/delegations/` coding-agent handoff artifacts
- `state/` memory, approval queue, retrieval index, workspace session state

Recommended artifact pattern:
- workspace -> project/task -> run id/date -> artifact type

## Telegram Usage (Phase 1)

Run:

```bash
python scripts/run_telegram_bot.py
```

Primary commands:
- `/work <request>` operations mode
- `/plan <profile>|<goal>` guided multi-stage planning flow
- `/workspace`, `/workspace set <id>`, `/workspace show`
- `/autonomy`, `/autonomy set trusted|manual`
- `/approvals`, `/approve`, `/reject`
- `/sources <query>`, `/reindex`
- `/runs`

Autonomy modes:
- `manual` (default): risky local tools queue for explicit `/approve`.
- `trusted`: safe local risky tools auto-run (for example `run_command`, `execute_python`, `install_python_packages`, `scaffold_tool`) while destructive, external, and production-risk requests still require approval.

## Setup (Windows)

1. Create and activate venv.
2. Install dependencies:

```bash
pip install -e .
```

3. Add `.env` with provider keys and runtime settings:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-20250514
PERPLEXITY_API_KEY=...
PERPLEXITY_MODEL=sonar-pro
OLLAMA_BASE_URL=http://localhost:11434
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=12345,67890
WORKSPACE_ROOT=.
ORCHESTRATOR_PRIMARY_PROVIDER=openai
ORCHESTRATOR_PRIMARY_MODEL=gpt-4.1-mini
```

## Extending The System

Add or extend:
- Agent configs in `agents/*.yaml`
- Policy in `config/policy.yaml`
- Retrieval scope in `config/retrieval.yaml`
- Workspace definitions in `config/workspaces.yaml`
- New integrations under `src/local_agent/integrations/`
- New tool handlers in `src/local_agent/orchestration/tools.py`

Planned extension path:
- Discord connector as a phase 2 integration that calls the same orchestration backend.

## Tradeoffs

- Retrieval is intentionally simple and file-based for quick reliability in phase 1.
- Legacy business-planning workflow is preserved for continuity but no longer defines core architecture.
- Approval execution currently targets risky local tool calls first; broader external action classes are modeled for expansion.

## Related Docs

- `SPECIFICATION.md` full system spec
- `REFACTOR_AUDIT.md` preserve/refactor/deprecate analysis
- `IMPLEMENTATION_ROADMAP.md` implementation planning history
