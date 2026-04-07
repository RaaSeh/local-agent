import argparse

from rich import print

from local_agent.config import load_env, get_env
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.router import LLMRouter
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.core.supervise import supervise
from local_agent.core.io import write_json, write_digest_markdown

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", default="daily", choices=["daily", "cad", "software"])
    args = parser.parse_args()

    load_env()

    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY")),
    )

    supervisor_cfg = load_yaml("agents/supervisor.yaml")

    if args.job == "daily":
        agent_cfg = load_yaml("agents/trade_marketing.yaml")
        task_prompt = agent_cfg["tasks"][0]["prompt"]
        worker = run_agent_task(router, agent_cfg, task_prompt)

    elif args.job == "cad":
        agent_cfg = load_yaml("agents/cad_rnd.yaml")
        task_prompt = agent_cfg["tasks"][0]["prompt"]
        worker = run_agent_task(router, agent_cfg, task_prompt)

    else:
        agent_cfg = load_yaml("agents/software_marketing.yaml")
        task_prompt = agent_cfg["tasks"][0]["prompt"]
        worker = run_agent_task(router, agent_cfg, task_prompt)

    worker_path = write_json(worker["agent_id"], worker)
    print(f"[green]Worker record:[/green] {worker_path}")

    supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
    supervisor_path = write_json("supervisor", supervisor_record)
    digest_path = write_digest_markdown(__import__("datetime").date.today().isoformat(), digest_md)

    print(f"[cyan]Supervisor record:[/cyan] {supervisor_path}")
    print(f"[magenta]Digest:[/magenta] {digest_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())