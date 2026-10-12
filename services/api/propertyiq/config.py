"""Settings loaded from the environment, with .env at the repo root as a fallback."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from psycopg.conninfo import make_conninfo

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env reader. Real environment variables always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    readonly_password: str
    owner_password: str
    session_secret: str
    ollama_url: str
    chat_model: str
    embed_model: str
    allowed_models: tuple[str, ...]
    rules_path: Path

    def dsn(self, role: str) -> str:
        password = {"piq_readonly": self.readonly_password, "piq_owner": self.owner_password}[role]
        return make_conninfo(
            host=self.db_host, port=self.db_port, dbname=self.db_name,
            user=role, password=password, connect_timeout=5,
        )


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value == "change-me":
        raise RuntimeError(f"{name} is not set. Run: python scripts/init_env.py")
    return value


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        db_host=os.environ.get("PIQ_DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("PIQ_DB_PORT", "5432")),
        db_name=os.environ.get("PIQ_DB_NAME", "propertyiq_db"),
        readonly_password=_require("PIQ_READONLY_PASSWORD"),
        owner_password=os.environ.get("PIQ_OWNER_PASSWORD", ""),
        session_secret=_require("PIQ_SESSION_SECRET"),
        ollama_url=os.environ.get("PIQ_OLLAMA_URL", "http://127.0.0.1:11434"),
        chat_model=os.environ.get("PIQ_CHAT_MODEL", "qwen3:4b"),
        embed_model=os.environ.get("PIQ_EMBED_MODEL", "nomic-embed-text"),
        allowed_models=tuple(
            m.strip()
            for m in os.environ.get("PIQ_ALLOWED_MODELS", "qwen3:4b,nomic-embed-text").split(",")
            if m.strip()
        ),
        rules_path=Path(os.environ.get("PIQ_RULES_PATH", Path(__file__).with_name("rules.json"))),
    )
