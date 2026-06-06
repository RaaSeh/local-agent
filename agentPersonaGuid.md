# Agent Persona and YAML Tuning Guide

This document explains how the agent YAML files are designed in this project and how to fine-tune them for specific outcomes.

## YAML Design Pattern

Each agent file in [agents](agents) follows a common structure:

1. Identity and metadata
- id
- name
- description

2. LLM configuration
- llm.provider: backend selector (for example, ollama or anthropic)
- llm.model: explicit model name
- llm.model_env: optional environment variable override for model selection

3. Behavior configuration
- behavior.system_prompt: persistent role, constraints, and style for the agent

4. Task configuration
- tasks: list of task objects
- each task has:
  - id
  - prompt

## Runtime Mapping (What fields are actually used)

Key runtime files:

- YAML loading and task execution: [agents/src/local_agent/core/run_once.py](agents/src/local_agent/core/run_once.py)
- Provider routing: [agents/src/local_agent/llm/router.py](agents/src/local_agent/llm/router.py)
- Job selection and prompt pick: [scripts/cron_run.py](scripts/cron_run.py)

Current behavior:

- The selected job reads only tasks[0].prompt.
- system_prompt is always passed as the system role message.
- provider and model determine which backend and model are called.
- supervisor performs a second-pass review over worker output.

## Most Important Tuning Levers

1. behavior.system_prompt
- Primary control over persona and policy.
- Use it to set guardrails, risk tolerance, and response style.
- Include explicit failure behavior (for example: request missing inputs instead of guessing).

2. tasks[0].prompt
- Primary control over the immediate deliverable.
- Since only the first task is used by current runner logic, this prompt is critical.

3. llm.provider and llm.model
- Strong influence on quality, speed, and cost.
- Local smaller models are faster/cheaper but less reliable on complex structured reasoning.

4. Output format constraints
- Add exact section names, item counts, and field requirements.
- This improves consistency and downstream usability.

## Practical Fine-Tuning Workflow

1. Define the exact outcome
- Example: produce three high-confidence lead hypotheses for today.

2. Tighten persona rules in system_prompt
- Role, allowed assumptions, prohibited behavior, and tone.
- Add an uncertainty rule: ask for constraints instead of inventing details.

3. Constrain output format
- Require exact headers and fixed counts.
- Require short rationale and confidence score where relevant.

4. Make prompt input-aware
- Tell the agent what to do with provided context.
- Define fallback behavior when context is missing.

5. Evaluate outputs in runs
- Compare output JSON and daily digests in [runs](runs).
- Change one variable at a time so improvements are attributable.

## Recommended Prompt Template

Use this shape for stable, high-signal outputs:

- Objective
- Inputs expected
- Rules and constraints
- Output schema (exact sections)
- Missing-input behavior

Example skeleton:

Objective:
- Produce one actionable experiment for improving agentic CAD workflow quality.

Inputs expected:
- Current workflow summary
- Constraints (time, tools, budget)
- Success metric baseline

Rules:
- Do not assume unavailable tools.
- If any required input is missing, list exactly what is needed.

Output schema:
1) Hypothesis
2) Method (step-by-step)
3) Required Tools
4) Success Criteria
5) Risks
6) Next Data to Collect

Missing-input behavior:
- If no baseline metrics are provided, propose the minimum dataset needed before running the experiment.

## Current Project Notes

- There is a filename/path mismatch in job config loading:
  - [scripts/cron_run.py](scripts/cron_run.py) currently references agents/trade_marketing.yaml for daily.
  - The existing file appears to be [agents/pwasher_maketing.yaml](agents/pwasher_maketing.yaml).
- This mismatch should be resolved by either renaming the YAML file or updating the script path.

## Suggested Next Improvements

1. Add YAML-configurable generation controls
- temperature
- max_tokens
- top_p

2. Support task selection by task id
- Instead of always using tasks[0], allow selecting a specific task.

3. Add lightweight schema validation for YAML files
- Fail early with clear errors for missing required fields.

4. Standardize naming and spelling in file IDs and paths
- Prevent runtime path errors and confusion across jobs.
