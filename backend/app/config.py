"""Settings from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5-5"))
    google_maps_api_key: str | None = field(default_factory=lambda: os.getenv("GOOGLE_MAPS_API_KEY"))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "motorcycle_ai.db"))


settings = Settings()
