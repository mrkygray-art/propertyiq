"""Add any missing settings to .env, generating random secrets.

Existing values (including POSTGRES_PASSWORD) are never read aloud, changed, or printed.
Only the names of keys that were added are printed.
"""
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

DEFAULTS = {
    "PIQ_DB_HOST": "127.0.0.1",
    "PIQ_DB_PORT": "5432",
    "PIQ_DB_NAME": "propertyiq_db",
    "PIQ_OWNER_PASSWORD": None,
    "PIQ_READONLY_PASSWORD": None,
    "PIQ_SESSION_SECRET": None,
    "PIQ_DEMO_PASSWORD": None,
    "PIQ_OLLAMA_URL": "http://127.0.0.1:11434",
    "PIQ_CHAT_MODEL": "qwen3:4b-instruct-2507-q4_K_M",
    "PIQ_EMBED_MODEL": "nomic-embed-text",
    "PIQ_ALLOWED_MODELS": "qwen3:4b-instruct-2507-q4_K_M,qwen3:4b,qwen3:8b,nomic-embed-text",
}


def main() -> None:
    text = ENV.read_text(encoding="utf-8-sig") if ENV.exists() else ""
    present = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    added = []
    lines = []
    for key, default in DEFAULTS.items():
        if key in present:
            continue
        value = default if default is not None else secrets.token_urlsafe(24)
        lines.append(f"{key}={value}")
        added.append(key)
    if "POSTGRES_PASSWORD" not in present:
        print("WARNING: POSTGRES_PASSWORD is missing from .env; add it by hand.")
    if lines:
        if text and not text.endswith("\n"):
            text += "\n"
        ENV.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    print("Added: " + (", ".join(added) if added else "nothing (all keys present)"))


if __name__ == "__main__":
    main()
