# Implementation Roadmap

## Phase 0 - Freeze And Baseline (Day 0)
Objectives:
- Preserve legacy path for rollback safety.
- Define clear acceptance criteria for replacement pipeline.

Deliverables:
- Legacy branch/tag snapshot
- Baseline run examples captured from current system
- Migration checklist

Exit criteria:
- You can revert to old behavior in under 10 minutes if needed.

## Phase 1 - Replace The Brain (Week 1)
Objectives:
- Keep existing shell (Telegram, logging, runs, settings) where useful.
- Replace local-first reasoning path with deterministic provider pipeline.

Build items:
- Provider wrappers:
  - OpenAI client
  - Anthropic client
  - Perplexity client
- Orchestrator module:
  - Stage runner
  - Policy enforcement
  - Structured schemas
- Canonical workflow:
  - business planning workflow (single entrypoint)

Target stage sequence:
1. Research (Perplexity)
2. Plan draft (GPT)
3. Critique (Claude)
4. Revision (GPT)
5. Final memo (Claude or GPT per policy)

Exit criteria:
- Telegram can trigger one end-to-end business planning run.
- Each run saves all stage artifacts.

## Phase 2 - Reliability Hardening (Week 2)
Objectives:
- Make the workflow resilient and transparent.

Build items:
- Retries and timeouts per provider call
- Run status model (queued, running, blocked, completed, failed)
- Error surfacing to Telegram
- One-click rerun with same input
- Cost estimator per stage and total run

Exit criteria:
- 5/5 test runs complete without manual code edits.
- Failures are recoverable and clearly reported.

## Phase 3 - Policy-Driven Autonomy (Week 3)
Objectives:
- Add controlled autonomy with explicit approvals.

Build items:
- Approval queue over Telegram
- Uncertainty-triggered escalation policy
- Ask-max-3-questions bundling rule
- Budget caps and stop conditions

Exit criteria:
- System auto-continues on low-risk tasks.
- System pauses correctly for restricted actions.

## Phase 4 - Multi-Business Operation (Week 4)
Objectives:
- Support Rocket Wash and Raze Development Studios profiles cleanly.

Build items:
- Business profile registry
- Per-business prompts, constraints, and KPI templates
- Per-business run history filtering and summaries

Exit criteria:
- Same workflow runs in both business modes with distinct outputs.

## Phase 5 - Domain Expansion (After Workflow Validation)
Objectives:
- Add only workflows with clear ROI.

Candidate expansions:
- GTM planner
- Pricing challenge workflow
- 90-day launch planner
- CAD R&D support loop for your two existing projects

Gate condition:
- Add no new workflow until current one scores well on usefulness and low babysitting.

## Evaluation Cadence
Test with at least 5 real prompts:
- Evaluate this niche SaaS idea
- Compare two markets
- Propose GTM for X
- Challenge my pricing model
- Create a 90-day launch plan

Score dimensions:
- Usefulness
- Specificity
- Realism
- Evidence quality
- Babysitting required

## Cost Planning Baseline (Starter Assumptions)
For one deterministic 5-stage run:
- 1 Perplexity call
- 2 GPT calls
- 2 Claude calls

Practical monthly budgeting template:
- Runs per day x days per month x average run cost
- Add 20% buffer for retries and longer prompts

Note:
Use real observed token usage in run artifacts to replace assumptions after first week.

## Risk Register
Top migration risks:
- API key/config drift across providers
- Inconsistent output schemas
- Prompt bloat causing cost creep
- Approval fatigue if escalation is too sensitive

Mitigations:
- Strict schema validation
- Fixed stage count
- Cost caps and explicit stop conditions
- Tunable escalation thresholds

## Definition Of Done For This Program
- Deterministic business-planning workflow is default path in Telegram.
- Legacy local-first reasoning loop is removed from primary execution path.
- Runs persist full artifacts with cost/confidence metadata.
- Docs and tests cover happy path and blocked/escalation path.