# Final System Product Spec

## 1) Product Intent
Build a semi-autonomous business operating system that helps you grow multiple businesses while you are in the field. The system should produce decision-ready outputs, critique itself, and only interrupt you for high-value approvals.

## 2) Primary Users And Jobs
- Owner (you): receive concrete strategic recommendations, approve high-impact actions, and monitor progress from Telegram.
- Future collaborator (optional): consume approved plans and execution artifacts.

## 3) Business Contexts
The system must support multiple business modes with separate strategy behavior:
- Rocket Wash mode
- Raze Development Studios mode

Each mode should maintain:
- Distinct master prompt and policy profile
- Distinct assumptions and constraints
- Distinct KPI tracking and run history tags

## 4) Core Capabilities (Final System)
- Multi-stage reasoning pipeline with explicit critique and revision.
- Internet-grounded research for market and competitor signals.
- Structured memo generation for strategic decisions.
- Scheduled recurring market-research workflows.
- Cost and confidence reporting on every run.
- Approval workflow over Telegram for high-impact actions.
- Artifact persistence for every stage (inputs, prompts, outputs, timings, cost estimates).

## 5) Required Workflows
Initial required workflows:
- Business planning memo generation (canonical workflow)
- Recurring market research cron workflow

Target follow-on workflows:
- GTM planning and channel prioritization
- Pricing challenge and risk analysis
- 90-day execution plan generation
- CAD R&D iteration support for existing two projects

## 6) Autonomy Model
Target autonomy level: semi-autonomous.

System behavior:
- Executes low-risk analysis and planning tasks without asking.
- Bundles uncertainty questions and asks only when needed.
- Requires explicit owner approval for restricted action categories.

## 7) Mandatory Approval Boundaries
Always require owner approval (via Telegram) for:
- Customer-facing emails
- Functional changes to production codebases
- Any external action with contractual or reputational risk

## 8) Quality And Output Contract
Every stage output should be structured and include:
- Summary
- Key assumptions
- Evidence and sources
- Risks and counterarguments
- Recommendations
- Confidence score
- Next steps

Final memo must separate facts from assumptions and state confidence clearly.

## 9) Escalation Policy
Escalate to owner when any of the following occurs:
- Material assumption uncertainty that changes recommendations
- Conflicting evidence across sources
- Budget guardrail exceeded
- Confidence below configured threshold

Escalation UX:
- Telegram message with concise context
- Up to 3 bundled questions
- Suggested options to approve or redirect

## 10) Knowledge And Data Scope
Allowed:
- Local workspace files relevant to task
- Internet research sources
- Stored run artifacts and historical outputs

Operational guardrails:
- Respect explicit denylist paths/secrets
- Log all external source URLs used in recommendations

## 11) Cost, Latency, Reliability Targets
- Cost visibility: per-run estimated cost and monthly rollup.
- Latency: async-friendly; slower response acceptable for better quality.
- Reliability: dependable during business-day operating window.

## 12) 90-Day Success Metrics
- Detailed growth plan produced for Rocket Wash.
- Detailed growth plan produced for Raze Development Studios.
- Measurable reduction in owner babysitting per strategic task.
- Market-research cron runs delivering useful decision input weekly.
- CAD R&D support producing iterative, actionable outputs.

## 13) Non-Goals For Early Versions
- Multi-user collaboration platform
- Voice interfaces
- Distributed multi-machine workers
- Advanced RAG/vector architecture before core workflows are stable

## 14) Product Principle
Do not optimize for prompt complexity. Optimize for deterministic workflow quality, policy clarity, and decision usefulness.