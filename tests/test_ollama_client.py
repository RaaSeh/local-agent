from __future__ import annotations

import httpx

from local_agent.llm.ollama_client import OllamaClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("POST", "http://localhost")

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=httpx.Response(self.status_code)
            )


def test_ollama_client_uses_chat_endpoint(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"message": {"content": "chat_ok"}})

    monkeypatch.setattr("local_agent.llm.ollama_client.httpx.post", fake_post)

    client = OllamaClient("http://localhost:11434")
    out = client.chat(model="mistral:latest", system="sys", user="hello")

    assert out == "chat_ok"
    assert calls[0][0].endswith("/api/chat")


def test_ollama_client_falls_back_to_generate(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        if url.endswith("/api/chat"):
            return _FakeResponse(404, {"error": "not found"})
        if url.endswith("/api/generate"):
            return _FakeResponse(200, {"response": "generate_ok"})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("local_agent.llm.ollama_client.httpx.post", fake_post)

    client = OllamaClient("http://localhost:11434")
    out = client.chat(model="mistral:latest", system="sys", user="hello")

    assert out == "generate_ok"
    assert calls[0][0].endswith("/api/chat")
    assert calls[1][0].endswith("/api/generate")


def test_ollama_client_falls_back_to_v1_chat(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        if url.endswith("/api/chat"):
            return _FakeResponse(404, {"error": "not found"})
        if url.endswith("/api/generate"):
            return _FakeResponse(404, {"error": "not found"})
        if url.endswith("/v1/chat/completions"):
            return _FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "v1_ok",
                            }
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("local_agent.llm.ollama_client.httpx.post", fake_post)

    client = OllamaClient("http://localhost:11434")
    out = client.chat(model="mistral:latest", system="sys", user="hello")

    assert out == "v1_ok"
    assert calls[0][0].endswith("/api/chat")
    assert calls[1][0].endswith("/api/generate")
    assert calls[2][0].endswith("/v1/chat/completions")