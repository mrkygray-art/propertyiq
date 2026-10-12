"""Local inference adapter.

LLMProvider is the seam for swapping Ollama for vLLM later. Guardrails enforced here,
not just in configuration:
  * the endpoint must be loopback or a private-network address (no hosted inference APIs);
  * only allowlisted model tags may be called, and Ollama cloud models are always refused;
  * HTTP proxy environment variables are ignored and redirects are not followed;
  * leaked reasoning text (qwen3 "<think>...</think>") is stripped before anything is shown.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

LOCAL_HOSTNAMES = {"localhost", "host.docker.internal", "ollama"}


class LLMError(RuntimeError):
    """The local model is unavailable or returned something unusable."""


def validate_local_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Inference URL must be http(s) with a host: {url!r}")
    host = parts.hostname.lower()
    if host in LOCAL_HOSTNAMES:
        return url.rstrip("/")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"Inference host {host!r} is not a local name or private IP address") from None
    # Link-local is refused too: 169.254.169.254 is the cloud metadata endpoint.
    if not (ip.is_loopback or ip.is_private) or ip.is_unspecified or ip.is_link_local:
        raise ValueError(f"Inference host {host!r} is not loopback or private")
    return url.rstrip("/")


def check_model_allowed(model: str, allowed: tuple[str, ...]) -> str:
    tag = model.strip().lower()
    if "cloud" in tag.split(":")[-1] or tag.endswith("-cloud"):
        raise ValueError(f"Cloud-hosted model {model!r} is not permitted")
    normalized = {a.lower() for a in allowed} | {f"{a.lower()}:latest" for a in allowed if ":" not in a}
    if tag not in normalized:
        raise ValueError(f"Model {model!r} is not on the allowlist")
    return model


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove model reasoning. qwen3 sometimes emits reasoning with only a closing tag."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text.strip()


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None


class LLMProvider(Protocol):
    name: str

    def available(self) -> bool: ...
    def chat(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 600) -> LLMResult: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, chat_model: str, embed_model: str, allowed: tuple[str, ...],
                 timeout: float = 240.0):
        self._thinks: bool | None = None
        self.base_url = validate_local_url(base_url)
        self.chat_model = check_model_allowed(chat_model, allowed)
        self.embed_model = check_model_allowed(embed_model, allowed)
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False, follow_redirects=False)

    def installed_models(self) -> set[str]:
        resp = self._client.get("/api/tags", timeout=3.0)
        resp.raise_for_status()
        return {m["name"] for m in resp.json().get("models", [])}

    def available(self) -> bool:
        try:
            installed = self.installed_models()
        except httpx.HTTPError:
            return False
        want = {self.chat_model, self.embed_model}
        return all(m in installed or f"{m}:latest" in installed for m in want)

    def thinks(self) -> bool:
        """Whether the chat model is a reasoning model. Cached after the first successful check.

        qwen3:4b on Ollama is Qwen3-4B-Thinking-2507: it always reasons, and with think=False
        (Ollama 0.40.3) the reasoning lands in the answer. So for reasoning models we send
        think=True, which moves the reasoning to message.thinking, and discard that field.
        """
        if self._thinks is None:
            try:
                resp = self._client.post("/api/show", json={"model": self.chat_model}, timeout=5.0)
                resp.raise_for_status()
                self._thinks = "thinking" in resp.json().get("capabilities", [])
            except (httpx.HTTPError, ValueError):
                return False
        return self._thinks

    def chat(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 600) -> LLMResult:
        thinks = self.thinks()
        body = {
            "model": self.chat_model,
            "stream": False,
            # Reasoning models need room for the reasoning as well as the answer.
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens + (2500 if thinks else 0),
                "num_ctx": 6144 if thinks else 4096,
            },
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if thinks:
            body["think"] = True
        try:
            resp = self._client.post("/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError("The local model did not respond. Verified figures are still shown.") from exc
        if data.get("done_reason") == "length":
            raise LLMError("The local model's answer was cut off. Verified figures are shown instead.")
        text = strip_reasoning(data.get("message", {}).get("content", ""))
        if not text:
            raise LLMError("The local model returned an empty answer.")
        return LLMResult(
            text=text, model=self.chat_model,
            prompt_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
            duration_ms=int(data["total_duration"] / 1e6) if data.get("total_duration") else None,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.post("/api/embed", json={"model": self.embed_model, "input": texts})
            resp.raise_for_status()
            return resp.json()["embeddings"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMError("The local embedding model did not respond.") from exc
