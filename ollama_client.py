"""Minimal Ollama client using only Python's standard library.

Requests are restricted to the local machine. No API key is needed.
Official API: https://docs.ollama.com/api/chat
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

OLLAMA_URL = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, model: str = "qwen3:8b", timeout: int = 180):
        self.model = model
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(OLLAMA_URL + path, timeout=5) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OllamaError(f"اتصال به Ollama برقرار نشد: {exc}") from exc

    def installed_models(self) -> list[str]:
        data = self._get("/api/tags")
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat(self, messages: list[dict[str, Any]], tools: list[dict]) -> dict:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }).encode("utf-8")
        request = urllib.request.Request(
            OLLAMA_URL + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            details = exc.read(1000).decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {exc.code}: {details}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama پاسخ نداد: {exc}") from exc
        message = payload.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise OllamaError("فرمت پاسخ Ollama معتبر نیست.")
        return message
