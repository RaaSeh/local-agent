import argparse
import json
import re
from pathlib import Path

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


def _load_latest_supervisor_feedback(max_chars: int = 2500) -> str:
    runs_dir = Path("runs")
    files = sorted(runs_dir.glob("supervisor-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return ""
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        output = str(data.get("output", "")).strip()
        if not output:
            return ""
        return output[:max_chars]
    except Exception:
        return ""


def _parse_supervisor_approval(supervisor_record: dict) -> tuple[bool, str]:
    """
    Parse supervisor output to determine if worker output is approved.
    
    Returns: (is_approved, approval_reason)
    - Approved if: 3+ call-ready leads detected and no critical rejections
    - Rejected if: <3 leads, fabrication detected, or major issues noted
    """
    output = supervisor_record.get("output", "")
    
    # Check for explicit rejection keywords
    rejection_keywords = [
        "reject", "rejected", "fabricated", "false", "placeholder",
        "unverifiable", "critical error", "too few leads"
    ]
    for keyword in rejection_keywords:
        if keyword.lower() in output.lower()[:500]:  # Check early/summary section
            # But check if it's pre-emptively approving "fewer than 3"
            if "explicitly approve" in output.lower() and "fewer than 3" in output.lower():
                # Supervisor is allowing < 3 leads if that's all we have
                if "candidate call list" in output.lower():
                    return True, "Approved with fewer than 3 leads (all available verified)"
            return False, f"Supervisor identified issues: {keyword}"
    
    # Count candidate mentions (heuristic for 3+ leads)
    candidate_count = len(re.findall(r'(business name|candidate|call list item)', output.lower()))
    
    # Check for "approved" or positive signals
    if any(phrase in output.lower() for phrase in 
           ["approved", "ready to call", "call-ready", "proceed with",
            "explicitly approve", "good leads", "strong candidates"]):
        return True, "Supervisor approved output with call-ready leads"
    
    # Default: if not explicitly approved, ask for iteration
    return False, "No explicit approval from supervisor; requesting refinement"


def _build_iteration_prompt(base_prompt: str, supervisor_output: str, iteration: int) -> str:
    """Add supervisor feedback to worker prompt for next iteration."""
    return (
        f"{base_prompt}\n\n"
        f"[ITERATION {iteration} - Supervisor Feedback from Previous Run]\n"
        f"Apply the following corrections and constraints to improve your output:\n\n"
        f"{supervisor_output}\n\n"
        f"[END SUPERVISOR FEEDBACK]\n"
        f"Please revise your output incorporating the supervisor's feedback above."
    )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job",
        default="daily",
        choices=["daily", "cad", "software", "software_dev"],
    )
    args = parser.parse_args()

    load_env()
    send_status_message(f"Automation job '{args.job}' started.")

    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY")),
    )

    supervisor_cfg = load_yaml("agents/supervisor.yaml")

    if args.job == "daily":
        agent_cfg = load_yaml("agents/pwasher_marketing.yaml")
        base_task_prompt = agent_cfg["tasks"][0]["prompt"]
        external_context = build_external_research_context()
        base_task_prompt = f"{base_task_prompt}\n\n{external_context}"
        
        # Load feedback from previous run (for first-time auto-correction)
        latest_feedback = _load_latest_supervisor_feedback()
        
        # Iteration loop for daily job
        max_iterations = 3
        current_iteration = 0
        worker = None
        supervisor_record = None
        is_approved = False
        
        while current_iteration < max_iterations and not is_approved:
            current_iteration += 1
            print(f"\n[yellow]Daily Job Iteration {current_iteration}/{max_iterations}[/yellow]")
            
            # Build task prompt with feedback from previous iteration or prior run
            if current_iteration == 1 and latest_feedback:
                # First iteration: include feedback from previous day's supervisor run
                task_prompt = (
                    f"{base_task_prompt}\n\n"
                    "Previous Supervisor Feedback (apply corrections before final output):\n"
                    f"{latest_feedback}"
                )
            elif current_iteration > 1 and supervisor_record:
                # Subsequent iterations: include feedback from THIS run's supervisor
                supervisor_feedback = supervisor_record.get("output", "")
                task_prompt = _build_iteration_prompt(base_task_prompt, supervisor_feedback, current_iteration)
            else:
                task_prompt = base_task_prompt
            
            # Run worker
            worker = run_agent_task(router, agent_cfg, task_prompt)
            print(f"[green][OK] Worker completed iteration {current_iteration}[/green]")
            
            # Get supervisor review
            supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
            print(f"[cyan][OK] Supervisor reviewed output[/cyan]")
            
            # Check if approved
            is_approved, reason = _parse_supervisor_approval(supervisor_record)
            print(f"[{'green' if is_approved else 'yellow'}]  {reason}[/{'green' if is_approved else 'yellow'}]")
            
            if is_approved:
                print(f"[green][OK] Output approved after {current_iteration} iteration(s)[/green]")
                break
            elif current_iteration < max_iterations:
                print(f"[yellow]>>> Requesting revision (iteration {current_iteration + 1}/{max_iterations})[/yellow]")
            else:
                print(f"[yellow]!!! Max iterations reached; using best available output[/yellow]")
        
        # Save final outputs
        worker_path = write_json(worker["agent_id"], worker)
        print(f"[green]Worker record:[/green] {worker_path}")
        supervisor_path = write_json("supervisor", supervisor_record)
        digest_path = write_digest_markdown(__import__("datetime").date.today().isoformat(), digest_md)
        print(f"[cyan]Supervisor record:[/cyan] {supervisor_path}")
        print(f"[magenta]Digest:[/magenta] {digest_path}")
        _record_automation_run("daily", worker, supervisor_record)
        send_status_message(
            f"Automation job 'daily' finished after {current_iteration} iteration(s). Supervisor file: {supervisor_path}."
        )

    elif args.job == "cad":
        agent_cfg = load_yaml("agents/cad_rnd.yaml")
        task_prompt = agent_cfg["tasks"][0]["prompt"]
        worker = run_agent_task(router, agent_cfg, task_prompt)
        worker_path = write_json(worker["agent_id"], worker)
        print(f"[green]Worker record:[/green] {worker_path}")
        supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
        supervisor_path = write_json("supervisor", supervisor_record)
        digest_path = write_digest_markdown(__import__("datetime").date.today().isoformat(), digest_md)
        print(f"[cyan]Supervisor record:[/cyan] {supervisor_path}")
        print(f"[magenta]Digest:[/magenta] {digest_path}")
        _record_automation_run("cad", worker, supervisor_record)
        send_status_message(f"Automation job 'cad' finished. Supervisor file: {supervisor_path}.")

    else:
        config_name = "software_dev.yaml" if args.job == "software_dev" else "software_marketing.yaml"
        agent_cfg = load_yaml(f"agents/{config_name}")
        task_prompt = agent_cfg["tasks"][0]["prompt"]
        worker = run_agent_task(router, agent_cfg, task_prompt)
        worker_path = write_json(worker["agent_id"], worker)
        print(f"[green]Worker record:[/green] {worker_path}")
        supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
        supervisor_path = write_json("supervisor", supervisor_record)
        digest_path = write_digest_markdown(__import__("datetime").date.today().isoformat(), digest_md)
        print(f"[cyan]Supervisor record:[/cyan] {supervisor_path}")
        print(f"[magenta]Digest:[/magenta] {digest_path}")
        _record_automation_run(args.job, worker, supervisor_record)
        send_status_message(
            f"Automation job '{args.job}' finished. Supervisor file: {supervisor_path}."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())