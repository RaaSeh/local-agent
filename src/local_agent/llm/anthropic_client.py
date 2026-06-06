import httpx

class AnthropicClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def chat(self, model: str, system: str, user: str, options: dict | None = None) -> str:
        """
        Minimal Anthropic Messages API call.
        Adjust max_tokens / headers as needed.
        """
        options = options or {}
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": int(options.get("max_tokens", 800)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        r = httpx.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        # content is a list of blocks; we join text blocks
        parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()