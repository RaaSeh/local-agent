from dotenv import load_dotenv
load_dotenv()
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.router import LLMRouter
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.core.supervise import supervise
from local_agent.core.io import write_json
from local_agent.config import get_env
import traceback

try:
    router = LLMRouter(
        ollama=OllamaClient(get_env('OLLAMA_BASE_URL', 'http://localhost:11434')),
        anthropic=AnthropicClient(get_env('ANTHROPIC_API_KEY')),
    )
    agent_cfg = load_yaml('agents/pwasher_marketing.yaml')
    task_prompt = agent_cfg['tasks'][0]['prompt']
    worker = run_agent_task(router, agent_cfg, task_prompt)
    w = write_json(worker['agent_id'], worker)
    print('worker_ok', w)

    supervisor_cfg = load_yaml('agents/supervisor.yaml')
    supervisor_record, digest_md = supervise(router, supervisor_cfg, worker)
    s = write_json('supervisor', supervisor_record)
    print('supervisor_ok', s)
    print('digest_len', len(digest_md))
except Exception as e:
    print('ERROR', type(e).__name__, str(e))
    traceback.print_exc()
