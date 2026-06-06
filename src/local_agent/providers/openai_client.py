from __future__ import annotations

import time
import httpx

MAX_RETRIES = 4
BASE_DELAY = 5.0


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4.1-mini",
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    def chat(self, model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1800) -> dict:
        if not self.api_key:
            raise RuntimeError("Missing OPENAI_API_KEY")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                with httpx.Client(timeout=180) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
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

        choices = data.get("choices", [])
        text = ""
        if choices:
            message = choices[0].get("message", {})
            text = str(message.get("content", "")).strip()

        return {
            "text": text,
            "usage": data.get("usage", {}),
            "raw": data,
            "provider": "openai",
            "model": model,
        }
