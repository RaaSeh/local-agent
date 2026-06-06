"""
Compare multiple Ollama models by running the daily job with each and getting
a single Claude supervisor review comparing all outcomes.
"""
import argparse
import json
import tempfile
from pathlib import Path
from datetime import datetime

from rich import print
from rich.table import Table

from local_agent.config import load_env, get_env
from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.router import LLMRouter
from local_agent.core.run_once import load_yaml, run_agent_task
from local_agent.core.supervise import supervise
from local_agent.core.io import write_json
from local_agent.leads import build_external_research_context


def run_job_with_model(router: LLMRouter, model_name: str, task_prompt: str, agent_config: dict) -> dict:
    """Run the daily job with a specific model."""
    print(f"\n[yellow]Testing model: {model_name}[/yellow]")
    
    # Temporarily update the router's ollama model
    original_model = agent_config.get("llm", {}).get("model", "llama3.1:8b")
    
    # Create a modified config for this run
    modified_config = agent_config.copy()
    modified_config["llm"] = agent_config.get("llm", {}).copy()
    modified_config["llm"]["model"] = model_name
    
    try:
        worker = run_agent_task(router, modified_config, task_prompt)
        print(f"[green]  [OK] Worker completed with {model_name}[/green]")
        return worker
    except Exception as e:
        print(f"[red]  [ERROR] Failed with {model_name}: {e}[/red]")
        return None


def build_comparison_prompt(results: dict) -> str:
    """Build a supervisor prompt comparing all three model outputs."""
    comparison = """
# COMPARATIVE MODEL EVALUATION

You are reviewing lead generation outputs from THREE different Ollama models:
1. neural-chat
2. mistral
3. llama2

Each model was given the SAME task: generate call-ready pressure-washing leads for Rocket Wash
using the same external research data (Google Places candidates).

Your responsibility:
- Compare lead quality across models
- Identify strengths and weaknesses of each model's output
- Recommend which model is best for this lead-generation task
- Base recommendation on: accuracy, instruction adherence, business judgment, confidence calibration

---

## MODEL 1: neural-chat OUTPUT

"""
    for model_name, worker_output in results.items():
        comparison += f"\n## MODEL: {model_name.upper()} OUTPUT\n\n"
        comparison += f"```\n{worker_output['output']}\n```\n\n"
    
    comparison += """
---

## EVALUATION CRITERIA

1. **Instruction Adherence**
   - Did the model follow the system prompt rules?
   - Did it apply supervisor feedback correctly?
   - Did it avoid fabrication?

2. **Lead Quality**
   - Are leads appropriate for a 2-person crew?
   - Are confidence scores justified?
   - Are geographic decisions sound?
   - Are there business understanding issues? (e.g., including inappropriate leads like major pulp mills)

3. **Analysis Depth**
   - Generic boilerplate vs. specific business analysis?
   - Evidence-backed reasoning or fabricated justifications?
   - Clear why each lead matters?

4. **Error Handling**
   - Self-correction when given conflicting info?
   - Proper backlog vs. call-ready distinction?
   - Honest about confidence limitations?

5. **Iteration Behavior**
   - For models that ran multiple iterations, how quickly did they improve?
   - Did supervisor feedback stick?

---

## YOUR RECOMMENDATION

Provide:
1. **Overall comparison table** (3-5 key metrics across the 3 models)
2. **Winning model** with justification (1-2 paragraphs)
3. **Key differences** between models (what neural-chat does differently than mistral, etc.)
4. **Implementation recommendation** (which model to use for production daily runs)

Format your response as:

# COMPARATIVE ANALYSIS

## Model Comparison Table
[Table with metrics]

## Winning Model: [NAME]
[Justification]

## Key Differences
- neural-chat: [distinctive traits]
- mistral: [distinctive traits]  
- llama2: [distinctive traits]

## Production Recommendation
[Clear recommendation with confidence]
"""
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Ollama models for Rocket Wash lead generation")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["neural-chat", "mistral", "llama2"],
        help="List of models to compare (default: neural-chat mistral llama2)"
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Run only single iteration (no supervisor feedback loop)"
    )
    args = parser.parse_args()

    load_env()

    router = LLMRouter(
        ollama=OllamaClient(get_env("OLLAMA_BASE_URL", "http://localhost:11434")),
        anthropic=AnthropicClient(get_env("ANTHROPIC_API_KEY")),
    )

    # Load base configuration
    agent_cfg = load_yaml("agents/pwasher_marketing.yaml")
    task_prompt = agent_cfg["tasks"][0]["prompt"]
    external_context = build_external_research_context()
    task_prompt = f"{task_prompt}\n\n{external_context}"

    print(f"\n[bold cyan]=== Ollama Model Comparison ===[/bold cyan]")
    print(f"[cyan]Testing models: {', '.join(args.models)}[/cyan]")
    print(f"[cyan]Same task, same external data, different reasoning[/cyan]\n")

    # Run each model
    model_results = {}
    for model_name in args.models:
        worker = run_job_with_model(router, model_name, task_prompt, agent_cfg)
        if worker:
            model_results[model_name] = worker
            # Save individual model result
            file_path = write_json(f"model_test_{model_name}", worker)
            print(f"  Saved to: {file_path}")
        else:
            print(f"  [red]Skipping {model_name} due to error[/red]")

    if not model_results:
        print("[red]No models completed successfully[/red]")
        return 1

    print(f"\n[green]Completed {len(model_results)} models[/green]")

    # Build comparative supervisor prompt
    print(f"\n[yellow]Getting Claude supervisor review of all models...[/yellow]")
    
    comparison_prompt = build_comparison_prompt(model_results)
    
    # Get supervisor review
    supervisor_cfg = load_yaml("agents/supervisor.yaml")
    supervisor_system = supervisor_cfg["behavior"]["system_prompt"]
    
    supervisor_client = AnthropicClient(get_env("ANTHROPIC_API_KEY"))
    comparison_review = supervisor_client.chat(
        model=get_env("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        system=supervisor_system,
        user=comparison_prompt,
        options={"max_tokens": 2000}
    )

    # Save comparison review
    comparison_result = {
        "timestamp": datetime.now().isoformat(),
        "models_tested": list(model_results.keys()),
        "comparison_prompt": comparison_prompt,
        "supervisor_review": comparison_review,
    }
    
    comparison_file = Path("runs") / f"model_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    comparison_file.parent.mkdir(exist_ok=True)
    comparison_file.write_text(json.dumps(comparison_result, indent=2), encoding="utf-8")

    print(f"[cyan]Comparison saved to: {comparison_file}[/cyan]")

    # Display the supervisor review
    print(f"\n[bold green]=== SUPERVISOR COMPARATIVE REVIEW ===[/bold green]\n")
    print(comparison_review)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
