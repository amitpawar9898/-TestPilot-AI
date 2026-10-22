"""Minimal provider-agnostic LLM client (plain HTTPS via requests; no vendor SDK needed)."""
from __future__ import annotations

import json
import re

import requests

from config.settings import PROVIDERS, Settings, api_key_for, redact


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider: str, model: str, base_url: str, api_key: str = "", timeout: int = 90):
        self.provider, self.model, self.base_url, self._key, self.timeout = provider, model, base_url.rstrip("/"), api_key, timeout

    @classmethod
    def from_settings(cls, s: Settings) -> "LLMClient | None":
        ok, _ = s.llm_status()
        if not ok:
            return None
        return cls(s.provider, s.effective_model(), s.effective_base_url(), api_key_for(s.provider))

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        try:
            if self.provider == "anthropic":
                r = requests.post(f"{self.base_url}/v1/messages", timeout=self.timeout,
                                  headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                  json={"model": self.model, "max_tokens": max_tokens, "system": system,
                                        "messages": [{"role": "user", "content": user}]})
                self._check(r)
                return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            if self.provider == "openai":
                headers = {"content-type": "application/json", "authorization": f"Bearer {self._key}"}
                r = requests.post(f"{self.base_url}/responses", timeout=self.timeout, headers=headers,
                                  json={"model": self.model, "instructions": system, "input": user,
                                        "max_output_tokens": max_tokens})
                self._check(r)
                data = r.json()
                text = data.get("output_text")
                if isinstance(text, str) and text:
                    return text
                parts = [block.get("text", "") for item in data.get("output", [])
                         for block in item.get("content", []) if block.get("type") == "output_text"]
                if parts:
                    return "\n".join(parts)
                raise LLMError("OpenAI Responses API returned no text output.")
            headers = {"content-type": "application/json"}
            if self._key:
                headers["authorization"] = f"Bearer {self._key}"
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers, timeout=self.timeout,
                              json={"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            self._check(r)
            return r.json()["choices"][0]["message"]["content"] or ""
        except LLMError:
            raise
        except requests.RequestException as e:
            raise LLMError(redact(f"Network error talking to {self.provider}: {type(e).__name__}")) from None
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Unexpected response format from {self.provider}: {type(e).__name__}") from None

    @staticmethod
    def _check(r: requests.Response) -> None:
        if r.status_code >= 400:
            body = redact(r.text[:200].replace("\n", " "))
            hint = " Check the API key." if r.status_code in (401, 403) else ""
            raise LLMError(f"HTTP {r.status_code} from provider.{hint} {body}")

    def test_connection(self) -> tuple[bool, str]:
        try:
            out = self.complete("Reply with the single word OK.", "ping", max_tokens=10)
            return True, f"Connected to {PROVIDERS[self.provider]['label']} ({self.model}). Reply: {out.strip()[:40]!r}"
        except LLMError as e:
            return False, str(e)

    @staticmethod
    def extract_json(text: str):
        t = (text or "").strip()
        t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
        for start, end in (("[", "]"), ("{", "}")):
            i, j = t.find(start), t.rfind(end)
            if i != -1 and j > i:
                try:
                    return json.loads(t[i:j + 1])
                except ValueError:
                    continue
        raise ValueError("no JSON found in LLM output")


def detect_ollama(base_url: str = "http://localhost:11434/v1", timeout: float = 1.2) -> tuple[bool, list[str]]:
    """Is a local Ollama server running, and which models does it have? Never raises."""
    root = base_url.rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    try:
        r = requests.get(root + "/api/tags", timeout=timeout)
        r.raise_for_status()
        return True, [m["name"] for m in r.json().get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return False, []
