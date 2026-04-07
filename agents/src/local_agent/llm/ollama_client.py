import httpx

class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def chat(self, model: str, system: str, user: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        return data["message"]["content"]