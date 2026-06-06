import httpx


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def chat(self, model: str, system: str, user: str, options: dict | None = None) -> str:
        chat_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if options:
            chat_payload["options"] = options

        chat_resp = httpx.post(f"{self.base_url}/api/chat", json=chat_payload, timeout=180)
        if chat_resp.status_code != 404:
            chat_resp.raise_for_status()
            chat_data = chat_resp.json()
            return chat_data["message"]["content"]

        # Compatibility fallback for Ollama variants that expose /api/generate but not /api/chat.
        generate_payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
        }
        if options:
            generate_payload["options"] = options

        gen_resp = httpx.post(f"{self.base_url}/api/generate", json=generate_payload, timeout=180)
        if gen_resp.status_code != 404:
            gen_resp.raise_for_status()
            gen_data = gen_resp.json()
            return str(gen_data.get("response", "")).strip()

        # OpenAI-compatible fallback used by some Ollama-compatible runtimes.
        v1_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        v1_resp = httpx.post(f"{self.base_url}/v1/chat/completions", json=v1_payload, timeout=180)
        v1_resp.raise_for_status()
        v1_data = v1_resp.json()
        choices = v1_data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return str(message.get("content", "")).strip()