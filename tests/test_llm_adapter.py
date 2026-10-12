"""Local-only guardrails in the inference adapter. No model needed except the marked test."""
import pytest

from propertyiq.llm import OllamaProvider, check_model_allowed, strip_reasoning, validate_local_url

ALLOWED = ("qwen3:4b", "nomic-embed-text")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434",
    "http://host.docker.internal:11434", "http://10.0.0.5:11434", "http://192.168.1.20:8000",
])
def test_local_urls_accepted(url):
    assert validate_local_url(url)


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "https://api.anthropic.com", "https://ollama.com",
    "http://8.8.8.8:11434", "http://169.254.169.254/latest", "http://0.0.0.0:11434",
    "ftp://127.0.0.1", "http://example.internal:11434",
])
def test_remote_urls_refused(url):
    with pytest.raises(ValueError):
        validate_local_url(url)


def test_model_allowlist():
    assert check_model_allowed("qwen3:4b", ALLOWED)
    assert check_model_allowed("nomic-embed-text:latest", ALLOWED)
    for bad in ("llama3:70b", "gpt-oss:120b-cloud", "qwen3:4b-cloud", "qwen3-coder:480b-cloud"):
        with pytest.raises(ValueError):
            check_model_allowed(bad, ALLOWED + (bad,) if "cloud" in bad else ALLOWED)


@pytest.mark.parametrize("raw,clean", [
    ("<think>secret plan</think>\n\nAnswer.", "Answer."),
    ("leaked reasoning without opening tag\n</think>\n\nAnswer.", "Answer."),
    ("Plain answer.", "Plain answer."),
])
def test_strip_reasoning(raw, clean):
    assert strip_reasoning(raw) == clean


@pytest.mark.llm
def test_live_local_model_round_trip():
    provider = OllamaProvider("http://127.0.0.1:11434", "qwen3:4b", "nomic-embed-text", ALLOWED)
    if not provider.available():
        pytest.skip("Ollama or models not available")
    result = provider.chat("You are terse.", "Reply with exactly: OK")
    assert "</think>" not in result.text and "OK" in result.text
    assert len(provider.embed(["Westlake Office Center"])[0]) == 768
