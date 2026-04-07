from dataclasses import dataclass

from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient

@dataclass
class LLMRouter:
    ollama: OllamaClient
    anthropic: AnthropicClient

    def chat(self, provider: str, model: str, system: str, user: str) -> str:
        if provider == "ollama":
            return self.ollama.chat(model=model, system=system, user=user)
        if provider == "anthropic":
            return self.anthropic.chat(model=model, system=system, user=user)
        raise ValueError(f"Unknown provider: {provider}")