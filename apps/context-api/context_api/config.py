from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_url: str
    embedding_model: str
    bootstrap_api_key: str
    bootstrap_tenant: str
    bootstrap_principal: str
    bootstrap_projects: tuple[str, ...]
    session_retention_days: int
    classifier_poll_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.getenv("CONTEXT_BOOTSTRAP_API_KEY", "")
        if len(key) < 24:
            raise RuntimeError("CONTEXT_BOOTSTRAP_API_KEY must contain at least 24 characters")
        projects = tuple(
            item.strip()
            for item in os.getenv("CONTEXT_BOOTSTRAP_PROJECTS", "*").split(",")
            if item.strip()
        )
        retention_days = int(os.getenv("CONTEXT_SESSION_RETENTION_DAYS", "90"))
        poll_seconds = float(os.getenv("CONTEXT_CLASSIFIER_POLL_SECONDS", "0.5"))
        if not 1 <= retention_days <= 3650:
            raise RuntimeError("CONTEXT_SESSION_RETENTION_DAYS must be between 1 and 3650")
        if not 0.1 <= poll_seconds <= 60:
            raise RuntimeError("CONTEXT_CLASSIFIER_POLL_SECONDS must be between 0.1 and 60")
        return cls(
            database_url=os.getenv(
                "CONTEXT_DATABASE_URL",
                "postgresql://context:context@localhost:5432/context",
            ),
            ollama_url=os.getenv("CONTEXT_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            embedding_model=os.getenv(
                "CONTEXT_EMBEDDING_MODEL", "qwen3-embedding:0.6b"
            ),
            bootstrap_api_key=key,
            bootstrap_tenant=os.getenv("CONTEXT_BOOTSTRAP_TENANT", "local"),
            bootstrap_principal=os.getenv(
                "CONTEXT_BOOTSTRAP_PRINCIPAL", "local-developer"
            ),
            bootstrap_projects=projects or ("*",),
            session_retention_days=retention_days,
            classifier_poll_seconds=poll_seconds,
        )
