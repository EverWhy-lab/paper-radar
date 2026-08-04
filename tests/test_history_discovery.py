from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from paper_radar.history_discovery import HistoricalDiscoveryService
from paper_radar.history_models import HistoricalPaper
from paper_radar.history_storage import (
    HistoricalPaperStorage,
    HistoryStorageError,
    SeedStorage,
    deduplicate_historical,
)
from paper_radar.providers.base import HistoricalProviderError, ProviderStats
from paper_radar.providers.openalex import parse_openalex_work


class FixtureProvider:
    def __init__(self, payload, *, fail_after: int | None = None) -> None:
        self.raw = {item["id"].rsplit("/", 1)[-1]: item for item in payload["results"]}
        self.calls: list[tuple[str, str]] = []
        self.fail_after = fail_after
        self.saved_stats = False

    def _check(self, kind: str, value: str) -> None:
        self.calls.append((kind, value))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise HistoricalProviderError("fixture OpenAlex failure")

    def _paper(self, work_id: str, source: str, seed: str | None = None) -> HistoricalPaper:
        return parse_openalex_work(
            deepcopy(self.raw[work_id]),
            discovered_at="2026-08-03T10:15:00+08:00",
            discovery_source=source,
            seed_paper_id=seed,
        )

    @property
    def stats(self) -> ProviderStats:
        return ProviderStats("2026-08-03", len(self.calls), 0, 100 - len(self.calls))

    @property
    def run_request_count(self) -> int:
        return len(self.calls)

    @property
    def run_cache_hits(self) -> int:
        return 0

    def save_stats(self) -> None:
        self.saved_stats = True

    def search_works(self, query, *, limit, publication_year_min, publication_year_max):
        self._check("search", query)
        work_id = "W103" if any(term in query for term in ("survey", "review")) else "W104"
        return [self._paper(work_id, f"topic_search:{query}")][:limit]

    def get_work(self, identifier):
        self._check("resolve", identifier)
        return self._paper("W100", "seed_resolution")

    def get_works_by_ids(
        self, openalex_ids, *, limit, discovery_source="seed_graph", seed_paper_id=None
    ):
        self._check(discovery_source, "|".join(openalex_ids))
        return [
            self._paper(work_id, discovery_source, seed_paper_id)
            for work_id in openalex_ids
            if work_id in self.raw
        ][:limit]

    def get_citing_works(self, openalex_id, *, limit, seed_paper_id=None):
        self._check("citing", openalex_id)
        return [self._paper("W105", "seed_graph:citing_works", seed_paper_id)][:limit]


class FailingSeedProvider(FixtureProvider):
    def __init__(self, payload, *, fail_ids):
        super().__init__(payload)
        self.fail_ids = set(fail_ids)

    def get_work(self, identifier):
        if identifier in self.fail_ids:
            raise HistoricalProviderError(
                f"OpenAlex did not resolve identifier: {identifier}"
            )
        return super().get_work(identifier)


def test_arxiv_doi_openalex_aliases_deduplicate(openalex_payload) -> None:
    first = parse_openalex_work(
        deepcopy(openalex_payload["results"][0]),
        discovered_at="2026-08-03T10:15:00+08:00",
        discovery_source="openalex",
    )
    doi_copy = deepcopy(first)
    doi_copy.canonical_paper_id = "doi:10.1000/seed"
    doi_copy.openalex_id = None
    arxiv_copy = deepcopy(first)
    arxiv_copy.canonical_paper_id = "arxiv:2201.00001"
    arxiv_copy.openalex_id = None
    arxiv_copy.doi = None

    unique = deduplicate_historical([first, doi_copy, arxiv_copy])

    assert len(unique) == 1
    assert unique[0].openalex_id == "W100"
    assert unique[0].doi == "10.1000/seed"
    assert unique[0].base_arxiv_id == "2201.00001"


def test_seed_crud_accepts_three_identifier_types(tmp_path: Path) -> None:
    storage = SeedStorage(tmp_path / "data")
    storage.add("2201.00001v2", "2026-08-03T10:15:00+08:00")
    storage.add("10.1000/robot", "2026-08-03T10:15:00+08:00")
    storage.add("https://openalex.org/W123", "2026-08-03T10:15:00+08:00")
    assert len(storage.load()) == 3
    with pytest.raises(HistoryStorageError, match="already"):
        storage.add("https://arxiv.org/abs/2201.00001", "2026-08-03T10:15:00+08:00")
    storage.remove("doi:10.1000/robot")
    assert len(storage.load()) == 2
    with pytest.raises(HistoryStorageError, match="not found"):
        storage.remove("10.9999/missing")


def test_seed_graph_expands_exactly_one_layer_and_respects_seed_cap(
    tmp_path: Path, profile, openalex_payload
) -> None:
    discovery = dict(profile.historical_discovery)
    discovery["per_seed_limit"] = 2
    configured = replace(profile, historical_discovery=discovery)
    SeedStorage(tmp_path / "data").add("W100", "2026-08-03T10:15:00+08:00")
    provider = FixtureProvider(openalex_payload)
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        configured,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )

    seed = SeedStorage(tmp_path / "data").load()[0]
    candidates, _ = service._seed_candidates(seed, 2)

    assert len(candidates) <= 3  # seed metadata plus at most two graph candidates
    assert sum(kind == "resolve" for kind, _ in provider.calls) == 1
    assert all(value not in {"W101", "W102", "W103", "W105"} for kind, value in provider.calls if kind == "resolve")


def test_global_discovery_limit_and_sources_are_enforced(
    tmp_path: Path, profile, openalex_payload
) -> None:
    SeedStorage(tmp_path / "data").add("W100", "2026-08-03T10:15:00+08:00")
    provider = FixtureProvider(openalex_payload)
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        profile,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )
    result = service.discover(limit=3)
    stored = HistoricalPaperStorage(tmp_path / "data").load()

    assert result.discovered_count <= 3
    assert result.pool_count <= 3
    assert len(stored) <= 3
    assert any(kind == "search" for kind, _ in provider.calls)
    assert any(kind == "citing" for kind, _ in provider.calls)
    assert provider.saved_stats is True


def test_dry_run_estimates_requests_without_any_write(tmp_path: Path, profile) -> None:
    data_dir = tmp_path / "data"
    SeedStorage(data_dir).add("W100", "2026-08-03T10:15:00+08:00")
    before = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    plan = HistoricalDiscoveryService(data_dir, profile, None).plan(limit=20)
    after = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }

    assert plan.candidate_limit == 20
    assert plan.expansion_depth == 1
    assert plan.estimated_request_count == (
        len(profile.historical_discovery["topic_queries"])
        + len(profile.historical_discovery["knowledge_map_queries"])
        + 4
    )
    assert before == after


def test_openalex_failure_preserves_pool_stats_and_page(
    tmp_path: Path, profile, openalex_payload
) -> None:
    paths = {
        tmp_path / "data" / "history" / "discovery_pool.json": "stable historical pool",
        tmp_path / "data" / "history" / "provider_stats.json": "stable provider stats",
        tmp_path / "site" / "index.html": "stable page",
    }
    for path, value in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    provider = FixtureProvider(openalex_payload, fail_after=1)
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        profile,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )

    with pytest.raises(HistoricalProviderError, match="fixture"):
        service.discover(limit=20)

    for path, value in paths.items():
        assert path.read_text(encoding="utf-8") == value


def test_refresh_all_refreshes_every_pool_paper(tmp_path: Path, profile, openalex_payload) -> None:
    storage = HistoricalPaperStorage(tmp_path / "data")
    storage.save(
        [
            parse_openalex_work(
                deepcopy(openalex_payload["results"][0]),
                discovered_at="2026-08-03T10:15:00+08:00",
                discovery_source="fixture",
            )
        ]
    )
    provider = FixtureProvider(openalex_payload)
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        profile,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )

    result = service.refresh_all()

    assert result.refreshed_count == 1
    assert result.failed_count == 0
    assert result.pool_count == 1
    assert provider.calls == [("resolve", "openalex:W100")]
    assert provider.saved_stats


def test_refresh_all_counts_failures_and_preserves_pool(
    tmp_path: Path, profile, openalex_payload
) -> None:
    storage = HistoricalPaperStorage(tmp_path / "data")
    storage.save(
        [
            parse_openalex_work(
                deepcopy(openalex_payload["results"][0]),
                discovered_at="2026-08-03T10:15:00+08:00",
                discovery_source="fixture",
            ),
            parse_openalex_work(
                deepcopy(openalex_payload["results"][1]),
                discovered_at="2026-08-03T10:15:00+08:00",
                discovery_source="fixture",
            ),
        ]
    )
    provider = FixtureProvider(openalex_payload, fail_after=0)
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        profile,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )

    result = service.refresh_all()

    assert result.refreshed_count == 0
    assert result.failed_count == 2
    assert result.pool_count == 2
    assert provider.saved_stats


def test_discover_skips_unresolvable_seed_and_continues(
    tmp_path: Path, profile, openalex_payload
) -> None:
    SeedStorage(tmp_path / "data").add("1603.06937", "2026-08-03T10:15:00+08:00")
    SeedStorage(tmp_path / "data").add("W100", "2026-08-03T10:15:00+08:00")
    provider = FailingSeedProvider(openalex_payload, fail_ids={"1603.06937"})
    service = HistoricalDiscoveryService(
        tmp_path / "data",
        profile,
        provider,
        now=datetime(2026, 8, 3, 10, 15),
    )

    result = service.discover(limit=20)

    assert result.failed_seed_ids == ["1603.06937"]
    assert result.discovered_count > 0
    assert result.pool_count > 0
    assert provider.saved_stats
