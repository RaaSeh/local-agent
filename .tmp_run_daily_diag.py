import json
import traceback
from pathlib import Path

from local_agent.config import load_env, get_env
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.router import LLMRouter
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.core.supervise import supervise
from local_agent.core.io import write_json, write_digest_markdown
from datetime import date

result = {"ok": False}

try:
    load_env()
    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY")),
    )
    supervisor_cfg = load_yaml("agents/supervisor.yaml")
    agent_cfg = load_yaml("agents/pwasher_marketing.yaml")
    task_prompt = agent_cfg["tasks"][0]["prompt"]

    worker = run_agent_task(router, agent_cfg, task_prompt)
    worker_path = str(write_json(worker["agent_id"], worker))

    supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
    supervisor_path = str(write_json("supervisor", supervisor_record))
    digest_path = str(write_digest_markdown(date.today().isoformat(), digest_md))

    result = {
        "ok": True,
        "worker_path": worker_path,
        "supervisor_path": supervisor_path,
        "digest_path": digest_path,
    }
except Exception as e:
    result = {
        "ok": False,
        "error_type": type(e).__name__,
        "error": str(e),
        "traceback": traceback.format_exc(),
    }

Path('.tmp_run_daily_diag_result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
