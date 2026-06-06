# V1 Execution Scope

## V1 Goal
Deliver one high-quality, low-babysitting business planning workflow through Telegram.

## In Scope
- One canonical workflow:
  - Input: business goal/question
  - Output: final memo plus intermediate artifacts
- Deterministic stage flow (no open-ended loops):
  - Research
  - Draft plan
  - Critique
  - Revise
  - Final memo
- Provider specialization:
  - Perplexity: research
  - GPT: planning and revision
  - Claude: critique and final memo
- Structured outputs at every stage
- Full artifact persistence and run history
- Escalation for uncertainty and restricted actions

## Out Of Scope For V1
- Vector databases and advanced RAG
- Autonomous browser agents
- Multi-user support
- Voice interaction
- Distributed workers across multiple machines
- Local GPU inference optimization as primary path

## Functional Contract
Workflow function:
- run_business_plan(goal, business_profile) -> run_result

Required artifacts per run:
- research_packet
- draft_plan
- critique_packet
- revised_plan
- final_memo
- escalation_questions (when applicable)

Required metadata per run:
- run_id
- timestamps per stage
- prompt identifiers
- provider/model used per stage
- token/cost estimate per stage and total
- confidence per stage
- run status

## Telegram Experience (V1)
- Submit a goal/question
- Receive status updates by stage
- Receive final memo and confidence summary
- If blocked, receive bundled approval/escalation questions

## Approval Rules (V1)
Always require explicit approval for:
- Customer-facing emails
- Functional code changes

Default behavior:
- Continue automatically for low-risk analysis tasks
- Pause only on uncertainty or restricted actions

## Quality Bar For Accepting V1
A run is considered acceptable if it:
- Produces specific and actionable recommendations
- Uses cited evidence from research stage
- Clearly separates facts vs assumptions
- Includes realistic risks and mitigation
- Requires minimal back-and-forth to be useful

## Test Plan (V1)
Run at least 5 real prompts and score each on:
- Usefulness
- Specificity
- Realism
- Evidence quality
- Babysitting required

Pass target:
- Clear improvement over current baseline on at least 4 of 5 dimensions.

## Cost Visibility (V1)
- Show estimated cost in run summary.
- Keep a simple monthly projection dashboard value:
  - monthly_estimate = average_run_cost x planned_runs_per_month

## V1 Completion Checklist
- Deterministic workflow wired to Telegram default route
- Provider wrappers live and configured
- Run artifacts persisted reliably
- Escalation/approval path verified
- Tests for happy path and escalation path
- Operator documentation updated