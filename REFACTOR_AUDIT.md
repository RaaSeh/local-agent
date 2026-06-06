# Refactor Audit

## Summary

This repository contained useful orchestration and integration scaffolding, but portions were still overly biased toward a deterministic business-planning flow. The refactor baseline keeps the useful foundation, adds platform-level capabilities, and isolates planner-specific behavior.

## Preserve

Preserved as-is or with minor adaptation:
- `src/local_agent/orchestration/admin.py` core plan/act/delegate/summarize loop
- `src/local_agent/orchestration/planner.py` structured JSON planning pattern
- `src/local_agent/orchestration/task_router.py` model routing override mechanism
- `src/local_agent/orchestration/tools.py` local tool execution handlers
- `src/local_agent/integrations/telegram_bot.py` remote entrypoint
- `src/local_agent/storage/runs.py` run artifact persistence

## Refactor

Refactored to support the platform direction:
- Hosted provider support added in `src/local_agent/llm/router.py`.
- Orchestrator default policy shifted toward hosted primary model for admin orchestration.
- Admin orchestration now includes:
  - workspace context loading
  - retrieval context injection
  - centralized risky-action approval queue handling
  - Telegram-accessible workspace and approval commands

## Added

New platform modules:
- `src/local_agent/policy/approvals.py`
- `src/local_agent/context/workspaces.py`
- `src/local_agent/retrieval/simple_index.py`
- `src/local_agent/llm/openai_client.py`
- `src/local_agent/llm/perplexity_client.py`

New configuration and workspace data:
- `config/policy.yaml`
- `config/retrieval.yaml`
- `config/workspaces.yaml`
- `workspaces/rocket-wash/primer.md`
- `workspaces/raze-development-studios/primer.md`
- `workspaces/shared/primer.md`

## Isolate / Deprecate Direction

Planner-specific components are preserved for continuity but treated as legacy workflow paths:
- `src/local_agent/workflows/business_plan.py`
- `src/local_agent/workflows/intake.py`
- planner-focused prompt set in `prompts/*.txt`

They are no longer the architectural center of the platform.

## Remove (Recommended Next Step)

No destructive removals were applied in this baseline refactor. Recommended next cleanup:
- Move legacy planner workflow into `src/local_agent/legacy/`.
- Mark planner scripts as optional/deprecated in command help text.
- Add a migration note for old `runs/business_plan-*` artifact consumers.

## Validation Notes

Targeted regression tests passed for orchestrator and Telegram modules after refactor updates:
- `tests/test_admin_orchestrator.py`
- `tests/test_telegram_bot.py`

The test environment emitted an end-of-run `KeyboardInterrupt` after successful pass output, but test pass counts were reported before interruption.
