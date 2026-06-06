# SPECIFICATION

## 1. Project Overview

This project is a Windows-first AI operations platform for orchestrating real business work across two primary workspaces:
- Rocket Wash
- Raze Development Studios

It is designed as a reusable orchestration foundation for research, planning, coding, troubleshooting, writing, administrative automation, and remote task handling.

## 2. Goals and Non-Goals

Goals:
- Deliver practical value quickly with a hosted high-capability orchestrator.
- Delegate specialist tasks to provider/model-appropriate workers.
- Support safe semi-autonomous execution with explicit approval gates.
- Keep behavior configuration-driven and inspectable.
- Support workspace-separated operation with shared platform infrastructure.

Non-goals:
- Hardcoded business-planner-only behavior.
- Opaque routing decisions.
- Unlimited uncontrolled persistent transcript memory.
- Provider-specific logic spread across unrelated modules.

## 3. Users and Roles

Primary users:
- Owner
- Business partner Josh

System roles:
- Orchestrator (primary conversation + planning)
- Specialist workers (coding, CAD R&D, marketing, critique)
- Supervisor/critic (quality and disagreement surfacing)
- Human approver (owner/authorized user)

## 4. Business Workspace Model

Required workspaces:
- rocket-wash
- raze-development-studios
- shared

Behavior:
- User can set workspace explicitly.
- System can default to workspace and include context from workspace primer files.
- Shared mode is intentional and explicit.

Implementation:
- `config/workspaces.yaml`
- `workspaces/<workspace-id>/primer.md`
- Session-state mapping in `state/workspace_sessions.json`

## 5. System Architecture

Layered architecture:
- API/Integration layer (Telegram phase 1, web UI locally)
- Orchestration core (plan -> act -> delegate -> synthesize)
- Model provider abstraction
- Retrieval and context loader
- Policy and approvals
- Tool/execution layer
- Artifact and state storage

Core orchestrator path:
1. Receive request
2. Load workspace context + retrieval context + memory context
3. Plan with structured JSON output
4. Execute safe tools or queue risky actions for approval
5. Delegate specialist tasks when needed
6. Run critique/supervisor review when needed
7. Return concise response + persist state/artifacts

## 6. Preferred Stack and Rationale

Preferred stack:
- Backend: Python
- API framework: FastAPI
- Remote interface: Telegram (phase 1)
- Local model runtime: Ollama
- Hosted providers: OpenAI, Anthropic, Perplexity
- Config: YAML/TOML
- Persistence: file-based local storage for phase 1 reliability

Rationale:
- Fast iteration on Windows
- Easy self-hosting
- Clear configuration ergonomics
- Simple deploy and maintenance path

## 7. Model Orchestration and Routing

Requirements:
- Hosted orchestrator as primary high-capability model.
- Configurable specialist routing by task type and agent.
- Inspectable provider/model selection.

Implementation baseline:
- `src/local_agent/orchestration/task_router.py`
- `src/local_agent/llm/router.py`
- `agents/*.yaml`

Current defaults:
- `admin` orchestrator defaults to hosted via env (`ORCHESTRATOR_PRIMARY_PROVIDER`, `ORCHESTRATOR_PRIMARY_MODEL`)
- Specialists can route to Ollama or hosted providers by policy/env overrides

## 8. Critique / Devil’s Advocate Mode

Structured critique pattern:
- proposer (delegate output)
- critic/supervisor review
- synthesis response to user

Configurable behavior:
- enabled by task type and route policy
- optional rounds for high-stakes tasks
- disagreement preserved in final summary where meaningful

## 9. Retrieval and Knowledge Management

Scope (phase 1):
- repository files
- prompts
- workspace docs
- business docs added to configured paths

Required capabilities:
- indexing pipeline
- retrieval query with source references
- configurable citation behavior
- workspace-aware usage in planning context

Implementation baseline:
- `src/local_agent/retrieval/simple_index.py`
- `config/retrieval.yaml`
- index storage in `state/retrieval/index.json`

## 10. Filesystem and Local Tool Execution

Supported actions:
- read/write/search files
- create directories
- append/replace text
- install dependencies
- run scripts and commands
- scaffold helper tools

Guarding:
- command allow/deny checks in tool executor
- centralized approval checks before risky execution
- action logs persisted in state/run artifacts

## 11. Approval and Safety Framework

Centralized policy engine behavior:
- classify actions
- require approvals for risky/destructive/outward-facing categories
- persist pending approvals
- support approve/reject resolution
- store rationale and timestamps

Implementation baseline:
- `src/local_agent/policy/approvals.py`
- `config/policy.yaml`
- `state/pending_approvals.json`
- Telegram commands: `/approvals`, `/approve`, `/reject`

## 12. Memory and Session Priming Strategy

Memory model:
- editable workspace primers
- rolling owner and lesson memory
- interaction history references
- retrieval-based context injection
- approved/rejected action persistence

Principles:
- no uncontrolled transcript growth as primary memory strategy
- lightweight, explicit, editable memory artifacts

## 13. Data Sources and Indexing Plan

Phase 1 sources:
- root docs (`README.md`, `SPECIFICATION.md`)
- prompts directory
- source code (`src/`)
- agent configs
- workspace primers/docs
- selected run artifacts

Phase 2 source expansion:
- CAD/API manuals and SDK docs
- external synced business docs
- ticketing/CRM exports

## 14. Configuration Model

Configuration domains:
- agent model/provider behavior (`agents/*.yaml`)
- routing overrides (env + router policies)
- approval policy (`config/policy.yaml`)
- retrieval scope (`config/retrieval.yaml`)
- workspace boundaries (`config/workspaces.yaml`)

Requirements:
- editable without code changes
- safe defaults
- inspectable behavior in logs/responses

## 15. Security and Secrets Handling

Secrets:
- use `.env` for local secrets
- never commit keys
- keep provider keys scoped minimally

Operational controls:
- allowed chat ID filtering for Telegram
- approval gating for high-risk actions
- command guardrails in executor

## 16. UX / Interface Requirements

Local/web UI (phase 1 baseline or minimal view):
- active workspace
- running jobs/status
- approval queue
- routing/provider visibility
- artifacts and sources visibility

Telegram UX:
- quick kickoff for tasks
- concise progress and completion updates
- clear approval actions
- links or references to generated artifacts

## 17. Remote Interfaces (Telegram phase 1, Discord phase 2)

Telegram phase 1:
- fully integrated with shared backend orchestration
- supports task submission, approval flow, retrieval inspection, and results

Discord phase 2:
- integration module planned as extension point
- no phase 1 dependency or blocking implementation requirement

## 18. Logging, Auditability, and Observability

Required logs:
- tool execution results
- approval requests and resolutions
- routing decisions (task type, selected agent, provider/model)
- run summaries and artifacts

Storage baseline:
- `runs/`
- `state/`

## 19. Artifact Storage Strategy

Artifact classes:
- plans
- research packets
- prompts/handoffs
- summaries
- execution logs
- troubleshooting notes

Naming/path strategy:
- workspace + run_id + artifact_type
- preserve machine-readable outputs (JSON) with human-readable summaries (MD/TXT)

## 20. Suggested Repository Structure

Target separation:
- `src/local_agent/integrations`
- `src/local_agent/orchestration`
- `src/local_agent/policy`
- `src/local_agent/context`
- `src/local_agent/retrieval`
- `src/local_agent/providers` and `src/local_agent/llm`
- `config/`
- `workspaces/`
- `runs/`
- `state/`
- `docs/` (future consolidated docs)

## 21. Phase 1 Scope

Priority implementation scope:
1. isolate narrow planner assumptions
2. hosted orchestrator + delegation baseline
3. config-driven routing
4. workspace context loading
5. simple reliable retrieval with citations
6. centralized approval queue/policy
7. Telegram integration via shared backend
8. structured artifact persistence
9. guarded local execution
10. documentation and operator clarity

## 22. Future Scope

- Discord integration module
- stronger queue/job workers with retries and schedules
- richer retrieval ranking and chunk-level citations
- web UI dashboard with routing and approvals timeline
- deeper CAD/API tool adapters
- external connectors (email/calendar/CRM) with strict gating

## 23. Initial Implementation Roadmap

Stage A (done in this refactor baseline):
- Add policy + approval engine
- Add workspace context and primers
- Add retrieval index and query with citations
- Extend router for hosted providers
- Integrate approvals/workspace/retrieval into admin orchestrator
- Update Telegram commands and docs

Stage B:
- Add job queue abstraction and job-state API
- Add first local web UI panel for jobs/approvals/artifacts
- Improve artifact taxonomy and per-workspace routing explainability

Stage C:
- Discord connector
- advanced critique modes with multi-round compare
- expanded connectors and policy classes

## 24. Risks and Tradeoffs

- Simple retrieval index is intentionally lightweight; quality may lag dense vector systems on complex semantic queries.
- Legacy planning path remains for continuity and may require explicit deprecation over time.
- Approval flow currently focuses on risky local actions first; outward action adapters need full policy binding as they are added.
- File-based persistence is pragmatic for phase 1 but may need migration for multi-user scaling.

## 25. Open Questions / Assumptions

Open questions:
- Preferred default orchestrator model tier for cost vs quality under daily usage.
- Expected throughput and whether to introduce background workers immediately.
- Exact citation verbosity defaults for Telegram responses.
- Priority order for Discord vs web UI expansion.

Assumptions:
- Windows host remains the primary runtime environment.
- Telegram is the default remote interface in phase 1.
- Hosted orchestration quality is preferred when local model quality is insufficient.
