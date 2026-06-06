from __future__ import annotations

import httpx


class PerplexityClient:
    def __init__(self, api_key: str, base_url: str = "https://api.perplexity.ai"):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def chat(self, model: str, system: str, user: str, options: dict | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("Missing PERPLEXITY_API_KEY")

        options = options or {}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(options.get("temperature", 0.1)),
            "max_tokens": int(options.get("max_tokens", 900)),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = httpx.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "")).strip()
