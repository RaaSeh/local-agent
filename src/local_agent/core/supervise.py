from datetime import date
from local_agent.core.run_once import run_agent_task

def supervise(router, supervisor_cfg: dict, worker_record: dict) -> tuple[dict, str]:
    prompt = f"""
Review this worker output for correctness, business alignment, and risks.

WORKER AGENT: {worker_record.get('agent_name')} ({worker_record.get('agent_id')})
MODEL: {worker_record.get('provider')}:{worker_record.get('model')}

TASK PROMPT:
{worker_record.get('task_prompt')}

WORKER OUTPUT:
{worker_record.get('output')}
""".strip()

    supervisor_record = run_agent_task(router, supervisor_cfg, prompt)

    today = date.today().isoformat()
    digest_md = f"""# Daily Digest ({today})

## Worker Output
{worker_record.get('output')}

---

## Supervisor Review
{supervisor_record.get('output')}
"""
    return supervisor_record, digest_md