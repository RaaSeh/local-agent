from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
from local_agent.llm.anthropic_client import AnthropicClient

pwasher = Path('agents/pwasher_marketing.yaml').read_text(encoding='utf-8')
supervisor = Path('agents/supervisor.yaml').read_text(encoding='utf-8')

context = '''
Owner update:
- Travel bounds are strict: Mill Bay is the south bound, Nanaimo is the north bound.
- Rocket Wash completed 4 commercial/industrial bids in the last year.
- Need the worker and supervisor loop to use devil's-advocate feedback with realistic execution for a 2-person team.
'''

prompt = f'''Research-style follow-up: identify what should be corrected in these YAML prompts for better lead quality and execution realism.

Requirements:
1) Tight geographic filtering to Nanaimo-Mill Bay corridor only.
2) Use prior proof (4 completed commercial/industrial bids) to improve qualification and website positioning.
3) Improve call-list quality for industrial/commercial opportunities (especially large white PVC/tent structures).
4) Add explicit negative feedback loop between worker output and supervisor correction.
5) Keep outputs concise and actionable.

Return:
- Section A: Top corrections (bullet points)
- Section B: Revised lines for pwasher prompt text
- Section C: Revised lines for supervisor prompt text

Current pwasher YAML:\n{pwasher}\n\nCurrent supervisor YAML:\n{supervisor}\n\n{context}
'''

client = AnthropicClient(os.getenv('ANTHROPIC_API_KEY'))
model = os.getenv('ANTHROPIC_MODEL')
out = client.chat(model=model, system='You are an exacting operations reviewer for local service-business agents.', user=prompt)
Path('.tmp_claude_followup_review.md').write_text(out, encoding='utf-8')
print('wrote .tmp_claude_followup_review.md')
