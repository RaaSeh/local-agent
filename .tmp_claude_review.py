from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
from local_agent.llm.anthropic_client import AnthropicClient

yaml_text = Path('agents/pwasher_marketing.yaml').read_text(encoding='utf-8')
prompt = (
    "Review this YAML for a local pressure-washing lead agent. "
    "Goal: identify industrial businesses in Nanaimo-Mill Bay, especially large white PVC cover structures/tents, "
    "and produce ethical call lists. Add devil's-advocate style critical feedback loops between worker, supervisor, "
    "and owner. Return concise recommendations as bullets plus a revised YAML draft.\n\nYAML:\n" + yaml_text
)

client = AnthropicClient(os.getenv('ANTHROPIC_API_KEY'))
model = os.getenv('ANTHROPIC_MODEL')
output = client.chat(model=model, system='You are a strict prompt and agent-config reviewer.', user=prompt)
print(output)
