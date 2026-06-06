from dataclasses import dataclass

from local_agent.llm.ollama_client import OllamaClient
from local_agent.llm.anthropic_client import AnthropicClient
from local_agent.llm.openai_client import OpenAIClient
from local_agent.llm.perplexity_client import PerplexityClient

@dataclass
class LLMRouter:
    ollama: OllamaClient
    anthropic: AnthropicClient
    openai: OpenAIClient | None = None
    perplexity: PerplexityClient | None = None

    def chat(
        self,
        provider: str,
        model: str,
        system: str,
        user: str,
        options: dict | None = None,
    ) -> str:
        if provider == "ollama":
            return self.ollama.chat(model=model, system=system, user=user, options=options)
        if provider == "anthropic":
            return self.anthropic.chat(model=model, system=system, user=user, options=options)
        if provider == "openai":
            if self.openai is None:
                raise ValueError("OpenAI provider requested but no OpenAI client is configured")
            return self.openai.chat(model=model, system=system, user=user, options=options)
        if provider == "perplexity":
            if self.perplexity is None:
                raise ValueError("Perplexity provider requested but no Perplexity client is configured")
            return self.perplexity.chat(model=model, system=system, user=user, options=options)
        raise ValueError(f"Unknown provider: {provider}")