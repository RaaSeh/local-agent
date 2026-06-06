from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
from local_agent.llm.anthropic_client import AnthropicClient

yaml_text = Path('agents/pwasher_marketing.yaml').read_text(encoding='utf-8')
profile = '''Business profile:
- Company: Rocket Wash
- Current market: Residential in Ladysmith/Saltair
- Current services: windows, power washing, roofs (softwash), gutters, house cleaning
- Growth goals: expand territory, win commercial bids, gain industrial clients for hot-water washing of heavy machinery and grimy industrial structures
- Team size: owner + 1 employee
- Region focus now: Nanaimo-Mill Bay and nearby practical routes
'''

prompt = (
    "Revise this worker-agent YAML for the above profile. Keep it ethical and evidence-first. "
    "Make worker, Claude supervisor, and owner interact in devil's-advocate style with explicit negative feedback and correction loops. "
    "Return two parts: (1) bullet recommendations, (2) full revised YAML only.\n\n"
    + profile + "\nCurrent YAML:\n" + yaml_text
)

client = AnthropicClient(os.getenv('ANTHROPIC_API_KEY'))
model = os.getenv('ANTHROPIC_MODEL')
output = client.chat(model=model, system='You are a strict ops strategist for small service businesses.', user=prompt)
print(output)
