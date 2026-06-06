from __future__ import annotations

import time
import httpx

MAX_RETRIES = 4
BASE_DELAY = 5.0


class AnthropicClient:
    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key.strip()
        self.default_model = default_model

    def chat(self, model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1800) -> dict:
        if not self.api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                with httpx.Client(timeout=180) as client:
                    resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                if resp.status_code in (429, 500, 502, 503, 504):
                    retry_after = float(resp.headers.get("retry-after", BASE_DELAY * (2 ** attempt)))
                    time.sleep(min(retry_after, 60))
                    last_exc = httpx.HTTPStatusError(resp.text, request=resp.request, response=resp)
                    continue
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504):
                    raise
                last_exc = exc
                time.sleep(BASE_DELAY * (2 ** attempt))
            except httpx.TimeoutException as exc:
                last_exc = exc
                time.sleep(BASE_DELAY * (2 ** attempt))
        else:
            raise last_exc
        data = resp.json()

        parts: list[str] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))

        usage = data.get("usage", {})
        return {
            "text": "\n".join(parts).strip(),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
            "raw": data,
            "provider": "anthropic",
            "model": model,
        }
