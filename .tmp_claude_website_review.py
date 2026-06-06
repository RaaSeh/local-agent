from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path
from local_agent.llm.anthropic_client import AnthropicClient

pwasher = Path('agents/pwasher_marketing.yaml').read_text(encoding='utf-8')
supervisor = Path('agents/supervisor.yaml').read_text(encoding='utf-8')

prompt = f"""
Business context:
- Brand: Rocket Wash
- Website: rocketwashbc.com (Wix)
- Current focus: Residential (Ladysmith/Saltair)
- Expansion goals: commercial bids + industrial hot-water washing in Nanaimo-Mill Bay
- Team size: 2 (owner + 1 employee)

Task:
Review and improve these YAML agent prompts so:
1) pwasher agent produces verifiable industrial/commercial call lists
2) pwasher agent also outputs weekly website update actions for rocketwashbc.com (copy, service pages, proof, CTA, forms)
3) Claude supervisor applies devil's-advocate critique against both lead list and website recommendations
4) output stays concise and executable for a small team

Return sections:
- Recommendations (bullets)
- Revised pwasher_marketing.yaml
- Revised supervisor.yaml (only prompt text if possible)

Current pwasher YAML:\n{pwasher}

Current supervisor YAML:\n{supervisor}
""".strip()

client = AnthropicClient(os.getenv('ANTHROPIC_API_KEY'))
model = os.getenv('ANTHROPIC_MODEL')
out = client.chat(model=model, system='You are a strict agent workflow reviewer.', user=prompt)
Path('.tmp_claude_website_review.md').write_text(out, encoding='utf-8')
print('WROTE .tmp_claude_website_review.md')
