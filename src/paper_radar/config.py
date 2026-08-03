from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the research profile is missing or invalid."""


@dataclass(frozen=True)
class FetchConfig:
    endpoint: str
    user_agent: str
    page_size: int
    max_pages: int
    page_delay_seconds: float
    timeout_seconds: float
    retries: int
    lookback_days: int = 7


@dataclass(frozen=True)
class ResearchProfile:
    site_name: str
    timezone: str
    fetch: FetchConfig
    categories: dict[str, float]
    scoring: dict[str, Any]
    video_scoring: dict[str, Any]
    sections: dict[str, Any]

    @property
    def topic_labels(self) -> dict[str, str]:
        return {
            str(topic["id"]): str(topic["label"])
            for topic in self.scoring["topics"]
        }


def load_profile(path: Path) -> ResearchProfile:
    if not path.exists():
        raise ConfigError(f"Research profile not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    try:
        site = raw["site"]
        fetch_raw = raw["fetch"]
        categories = {str(key): float(value) for key, value in raw["categories"].items()}
        fetch = FetchConfig(
            endpoint=str(fetch_raw["endpoint"]),
            user_agent=str(fetch_raw["user_agent"]),
            page_size=int(fetch_raw["page_size"]),
            max_pages=int(fetch_raw["max_pages"]),
            page_delay_seconds=float(fetch_raw["page_delay_seconds"]),
            timeout_seconds=float(fetch_raw["timeout_seconds"]),
            retries=int(fetch_raw["retries"]),
            lookback_days=int(fetch_raw.get("lookback_days", 7)),
        )
        profile = ResearchProfile(
            site_name=str(site["name"]),
            timezone=str(site["timezone"]),
            fetch=fetch,
            categories=categories,
            scoring=dict(raw["scoring"]),
            video_scoring=dict(raw["video_scoring"]),
            sections=dict(raw["sections"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Missing or invalid research profile field: {exc}") from exc

    if not profile.categories:
        raise ConfigError("At least one arXiv category is required")
    if not profile.scoring.get("topics"):
        raise ConfigError("At least one scoring topic is required")
    if fetch.page_size < 1 or fetch.max_pages < 1 or fetch.retries < 1 or fetch.lookback_days < 1:
        raise ConfigError("page_size, max_pages, retries, and lookback_days must be positive")
    if fetch.page_delay_seconds < 3:
        raise ConfigError("arXiv API page delay must be at least 3 seconds")
    return profile
