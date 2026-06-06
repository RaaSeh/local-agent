"""
Single-pass daily job with mistral worker and claude supervisor.
No iteration loop - mistral produces excellent output on first attempt.
"""
import argparse
from pathlib import Path
from datetime import datetime

from rich import print

from local_agent.config import load_env, get_env
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.router import LLMRouter
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.core.supervise import supervise
from local_agent.core.io import write_json, write_digest_markdown
from local_agent.integrations.telegram_notify import send_status_message
from local_agent.leads import build_external_research_context
from local_agent.orchestration.memory import MemoryStore


def _record_automation_run(job_name: str, worker: dict, supervisor_record: dict) -> None:
    memory = MemoryStore("state")
    supervisor_output = str(supervisor_record.get("output", "")).strip()
    if supervisor_output:
        memory.apply_updates(
            [{"kind": "lesson", "value": supervisor_output[:300], "source": f"{job_name}:supervisor"}]
        )
    memory.append_interaction(
        {
            "chat_id": "automation",
            "user_message": f"Automation run for {job_name}",
            "selected_agent": worker.get("agent_id", "unknown"),
            "status": "completed",
            "summary": supervisor_output[:220] or str(worker.get("output", ""))[:220],
        }
    )


def main() -> int:
    load_env()
    send_status_message("Daily lead-generation run started.")

    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY")),
    )

    supervisor_cfg = load_yaml("agents/supervisor.yaml")
    agent_cfg = load_yaml("agents/pwasher_marketing.yaml")
    
    task_prompt = agent_cfg["tasks"][0]["prompt"]
    external_context = build_external_research_context()
    task_prompt = f"{task_prompt}\n\n{external_context}"

    print("[cyan]Running daily lead generation...[/cyan]")
    print(f"[yellow]Worker model:[/yellow] {agent_cfg['llm']['model']}")
    
    # Single pass - no iteration loop
    worker = run_agent_task(router, agent_cfg, task_prompt)
    print("[green][DONE] Worker generated leads[/green]")

    # Get supervisor review
    supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
    print("[green][DONE] Supervisor reviewed output[/green]")

    # Save outputs
    worker_path = write_json(worker["agent_id"], worker)
    supervisor_path = write_json("supervisor", supervisor_record)
    digest_path = write_digest_markdown(datetime.today().strftime("%Y-%m-%d"), digest_md)
    _record_automation_run("daily", worker, supervisor_record)
    send_status_message(
        "Daily lead-generation run finished. "
        f"Worker: {worker['agent_id']}. Supervisor review saved to {supervisor_path}."
    )

    print(f"\n[cyan]Output saved:[/cyan]")
    print(f"  Worker:     {worker_path}")
    print(f"  Supervisor: {supervisor_path}")
    print(f"  Digest:     {digest_path}")
    
    # Print supervisor summary
    supervisor_output = supervisor_record.get("output", "")
    if "Summary" in supervisor_output:
        # Extract first 500 chars of summary
        summary_start = supervisor_output.find("## 1) Summary") or supervisor_output.find("Summary")
        if summary_start >= 0:
            summary_section = supervisor_output[summary_start:summary_start+500]
            print(f"\n[yellow]Supervisor Summary (excerpted):[/yellow]")
            print(f"[white]{summary_section}...[/white]")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
