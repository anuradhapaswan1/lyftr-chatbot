"""Environment-driven configuration (read once per app instance)."""
import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...]
    model: str
    database_url: str
    allowed_origins: list[str]
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_type: str  # "disk" | "local"
    cache_dir: str
    llm_timeout: float
    max_history_messages: int


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        api_keys=tuple(_list(env("LLM_API_KEYS"))),
        model=env("LLM_MODEL", "gemini/gemini-2.5-flash"),
        database_url=env("DATABASE_URL", "sqlite:///./app.db"),
        allowed_origins=_list(env("ALLOWED_ORIGINS", "http://localhost:3000")),
        enable_failure_simulation=_bool(env("ENABLE_FAILURE_SIMULATION")),
        default_system_prompt=env("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        cache_type=env("CACHE_TYPE", "disk").lower(),
        cache_dir=env("CACHE_DIR", "/data/llm_cache"),
        llm_timeout=float(env("LLM_TIMEOUT_SECONDS", "30")),
        max_history_messages=int(env("MAX_HISTORY_MESSAGES", "50")),
    )
