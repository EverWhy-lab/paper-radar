from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchProfile
from paper_radar.historical_scoring import score_historical_papers
from paper_radar.history_models import HistoricalPaper, SeedPaper
from paper_radar.history_storage import HistoricalPaperStorage, SeedStorage, deduplicate_historical
from paper_radar.providers.base import HistoricalProvider


@dataclass(frozen=True)
class DiscoveryPlan:
    topic_query_count: int
    knowledge_map_query_count: int
    seed_count: int
    expansion_depth: int
    candidate_limit: int
    estimated_request_count: int


@dataclass(frozen=True)
class DiscoveryResult:
    discovered_count: int
    pool_count: int
    request_count: int
    cache_hits: int
    remaining_call_budget: int
    pool_path: Path


def build_discovery_plan(
    profile: ResearchProfile,
    seeds: list[SeedPaper],
    *,
    limit: int | None = None,
) -> DiscoveryPlan:
    config = profile.historical_discovery
    candidate_limit = min(
        int(config["global_candidate_limit"]),
        int(limit if limit is not None else config["default_cli_limit"]),
    )
    expansion_sources = list(config["seed_expansion_sources"])
    topic_queries = list(config["topic_queries"])
    map_queries = list(config["knowledge_map_queries"])
    estimated = len(topic_queries) + len(map_queries) + len(seeds) * (
        1 + len(expansion_sources)
    )
    return DiscoveryPlan(
        topic_query_count=len(topic_queries),
        knowledge_map_query_count=len(map_queries),
        seed_count=len(seeds),
        expansion_depth=int(config["expansion_depth"]),
        candidate_limit=max(0, candidate_limit),
        estimated_request_count=estimated,
    )


class HistoricalDiscoveryService:
    def __init__(
        self,
        data_dir: Path,
        profile: ResearchProfile,
        provider: HistoricalProvider | None,
        *,
        now: datetime | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.profile = profile
        self.provider = provider
        self.zone = ZoneInfo(profile.timezone)
        self.now = (now or datetime.now(self.zone)).astimezone(self.zone)
        self.paper_storage = HistoricalPaperStorage(data_dir)
        self.seed_storage = SeedStorage(data_dir)

    def plan(self, *, limit: int | None = None) -> DiscoveryPlan:
        return build_discovery_plan(
            self.profile, self.seed_storage.load(), limit=limit
        )

    @staticmethod
    def _retag(
        papers: list[HistoricalPaper], source: str, seed_id: str | None = None
    ) -> list[HistoricalPaper]:
        for paper in papers:
            paper.discovery_source = sorted(set(paper.discovery_source) | {source})
            if seed_id:
                paper.seed_paper_id = sorted(set(paper.seed_paper_id) | {seed_id})
        return papers

    def _seed_candidates(
        self, seed: SeedPaper, per_seed_limit: int
    ) -> tuple[list[HistoricalPaper], SeedPaper]:
        assert self.provider is not None
        resolved = self.provider.get_work(seed.identifier)
        seed.openalex_id = resolved.openalex_id
        seed.base_arxiv_id = resolved.base_arxiv_id
        seed.doi = resolved.doi
        seed.title = resolved.title
        seed_id = resolved.canonical_paper_id
        results = self._retag([resolved], "manual_seed", seed_id)
        sources = list(self.profile.historical_discovery["seed_expansion_sources"])
        allocation = max(1, math.ceil(per_seed_limit / max(1, len(sources))))
        for source in sources:
            if source == "referenced_works":
                batch = self.provider.get_works_by_ids(
                    resolved.referenced_works,
                    limit=allocation,
                    discovery_source="seed_graph:referenced_works",
                    seed_paper_id=seed_id,
                )
            elif source == "related_works":
                batch = self.provider.get_works_by_ids(
                    resolved.related_works,
                    limit=allocation,
                    discovery_source="seed_graph:related_works",
                    seed_paper_id=seed_id,
                )
            elif source == "citing_works" and resolved.openalex_id:
                batch = self.provider.get_citing_works(
                    resolved.openalex_id,
                    limit=allocation,
                    seed_paper_id=seed_id,
                )
            else:
                batch = []
            results.extend(self._retag(batch, f"seed_graph:{source}", seed_id))
        # The seed itself is useful metadata, but the expansion cap applies to graph candidates.
        expanded = [
            paper for paper in deduplicate_historical(results) if paper.canonical_paper_id != seed_id
        ][:per_seed_limit]
        return [resolved] + expanded, seed

    def discover(self, *, limit: int | None = None) -> DiscoveryResult:
        if self.provider is None:
            raise ValueError("A provider is required for a non-dry-run discovery")
        plan = self.plan(limit=limit)
        config = self.profile.historical_discovery
        per_query = int(config["per_query_limit"])
        year_min = int(config["publication_year_min"])
        year_max = int(config["publication_year_max"])
        candidates: list[HistoricalPaper] = []

        try:
            for query in config["topic_queries"]:
                batch = self.provider.search_works(
                    str(query),
                    limit=per_query,
                    publication_year_min=year_min,
                    publication_year_max=year_max,
                )
                candidates.extend(self._retag(batch, f"topic_search:{query}"))

            for query in config["knowledge_map_queries"]:
                batch = self.provider.search_works(
                    str(query),
                    limit=per_query,
                    publication_year_min=year_min,
                    publication_year_max=year_max,
                )
                candidates.extend(self._retag(batch, f"knowledge_map_search:{query}"))

            updated_seeds: list[SeedPaper] = []
            for seed in self.seed_storage.load():
                seed_batch, updated = self._seed_candidates(
                    seed, int(config["per_seed_limit"])
                )
                candidates.extend(seed_batch)
                updated_seeds.append(updated)
        except Exception:
            # Failed calls still consume provider budget; persist only aggregate counters.
            self.provider.save_stats()
            raise

        unique = deduplicate_historical(candidates)
        scored = score_historical_papers(
            unique, self.profile, as_of_year=self.now.year
        )[: plan.candidate_limit]
        merged = self.paper_storage.merge(scored)

        # Nothing persistent is replaced until every provider request and score completes.
        pool_path = self.paper_storage.save(merged)
        if updated_seeds:
            self.seed_storage.save(updated_seeds)
        self.provider.save_stats()
        stats = self.provider.stats
        return DiscoveryResult(
            discovered_count=len(scored),
            pool_count=len(merged),
            request_count=self.provider.run_request_count,
            cache_hits=self.provider.run_cache_hits,
            remaining_call_budget=stats.remaining_call_budget,
            pool_path=pool_path,
        )

    def refresh(self, identifier: str) -> HistoricalPaper:
        if self.provider is None:
            raise ValueError("A provider is required for refresh")
        try:
            refreshed = self.provider.get_work(identifier)
        except Exception:
            self.provider.save_stats()
            raise
        scored = score_historical_papers(
            [refreshed], self.profile, as_of_year=self.now.year
        )[0]
        merged = self.paper_storage.merge([scored])
        self.paper_storage.save(merged)
        self.provider.save_stats()
        return next(
            paper
            for paper in merged
            if paper.aliases & scored.aliases
        )
