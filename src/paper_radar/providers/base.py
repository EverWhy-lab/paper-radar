from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from paper_radar.history_models import HistoricalPaper


class HistoricalProviderError(RuntimeError):
    """Raised when a historical metadata provider cannot safely complete a request."""


@dataclass
class ProviderStats:
    date: str
    request_count: int
    cache_hits: int
    remaining_call_budget: int
    last_updated_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HistoricalProvider(Protocol):
    @property
    def stats(self) -> ProviderStats: ...

    @property
    def run_request_count(self) -> int: ...

    @property
    def run_cache_hits(self) -> int: ...

    def search_works(
        self,
        query: str,
        *,
        limit: int,
        publication_year_min: int,
        publication_year_max: int,
    ) -> list[HistoricalPaper]: ...

    def get_work(self, identifier: str) -> HistoricalPaper: ...

    def get_works_by_ids(
        self,
        openalex_ids: list[str],
        *,
        limit: int,
        discovery_source: str = "seed_graph",
        seed_paper_id: str | None = None,
    ) -> list[HistoricalPaper]: ...

    def get_citing_works(
        self,
        openalex_id: str,
        *,
        limit: int,
        seed_paper_id: str | None = None,
    ) -> list[HistoricalPaper]: ...

    def save_stats(self) -> None: ...


class LLMAnalysisProvider(Protocol):
    """Writes optional Chinese commentary for already-selected papers only."""

    def analyze_recommendations(self, recommendations: list[Any]) -> list[Any]: ...
