"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from functools import lru_cache
import os


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with local-development defaults."""

    database_url: str
    sql_echo: bool


def _as_bool(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./farelocker.db"),
        sql_echo=_as_bool(os.getenv("SQL_ECHO")),
    )
