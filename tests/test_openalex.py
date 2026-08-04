from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from paper_radar.providers.base import HistoricalProviderError
from paper_radar.providers.openalex import OpenAlexProvider, parse_openalex_work
from paper_radar.reader_models import DailyRecommendations, RecommendationEntry
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import RecommendationStorage


def test_openalex_work_parser_preserves_identifiers_and_unknowns(openalex_payload) -> None:
    work = openalex_payload["results"][0]
    paper = parse_openalex_work(
        work,
        discovered_at="2026-08-03T10:15:00+08:00",
        discovery_source="fixture",
    )

    assert paper.canonical_paper_id == "openalex:W100"
    assert paper.openalex_id == "W100"
    assert paper.base_arxiv_id == "2201.00001"
    assert paper.doi == "10.1000/seed"
    assert paper.abstract == "mobile manipulation robot system"
    assert paper.citation_normalized_percentile == 0.95
    assert paper.referenced_works == ["W101", "W102"]


def test_get_work_resolves_arxiv_via_http_landing_page(
    tmp_path: Path, profile, openalex_payload
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, request=request, json=deepcopy(openalex_payload)
        )

    provider = OpenAlexProvider(
        profile.openalex,
        tmp_path / "data",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        environment={"OPENALEX_API_KEY": "secret"},
        now=lambda: datetime(2026, 8, 3, 10, 15),
        sleep=lambda _: None,
    )

    paper = provider.get_work("1603.06937")

    assert (
        "locations.landing_page_url:http://arxiv.org/abs/1603.06937"
        in captured["params"]["filter"]
    )
    assert paper.base_arxiv_id == "2201.00001"


def test_openalex_cache_prevents_duplicate_requests_and_never_stores_key(
    tmp_path: Path, profile, openalex_payload
) -> None:
    secret = "fixture-secret-never-persist"
    response_payload = deepcopy(openalex_payload)
    response_payload["request_echo"] = {"api_key": secret, "note": f"key={secret}"}
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, request=request, json=response_payload)

    provider = OpenAlexProvider(
        profile.openalex,
        tmp_path / "data",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        environment={"OPENALEX_API_KEY": secret},
        now=lambda: datetime(2026, 8, 3, 10, 15),
        sleep=lambda _: None,
    )
    first = provider.search_works(
        "mobile manipulation",
        limit=5,
        publication_year_min=2000,
        publication_year_max=2026,
    )
    second = provider.search_works(
        "mobile manipulation",
        limit=5,
        publication_year_min=2000,
        publication_year_max=2026,
    )
    provider.save_stats()

    recommendation_storage = RecommendationStorage(tmp_path / "data")
    recommendation_storage.save(
        DailyRecommendations(
            date="2026-08-03",
            generated_at="2026-08-03T10:15:00+08:00",
            recommendations=[
                RecommendationEntry(
                    category="high_impact_historical",
                    paper=first[0].to_reader_paper(),
                    historical_paper=first[0],
                    reasons=["fixture metadata only"],
                )
            ],
            candidate_count=0,
            historical_candidate_count=len(first),
            mode="fixture",
            selection_config=profile.recommendations["daily_mix"],
        )
    )
    index, _ = RecommendationSiteRenderer(
        tmp_path / "site", recommendation_storage, profile
    ).render("2026-08-03")

    assert len(first) == len(second) == 5
    assert len(calls) == 1
    assert provider.run_request_count == 1
    assert provider.run_cache_hits == 1
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "data" / "history").rglob("*.json")
    )
    assert secret not in persisted
    assert secret not in index.read_text(encoding="utf-8")
    stats = json.loads(
        (tmp_path / "data" / "history" / "provider_stats.json").read_text(
            encoding="utf-8"
        )
    )
    assert stats["request_count"] == 1
    assert stats["cache_hits"] == 1
    assert stats["remaining_call_budget"] == profile.openalex.daily_request_budget - 1


def test_missing_openalex_key_has_friendly_error_without_network(tmp_path: Path, profile) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    provider = OpenAlexProvider(
        profile.openalex,
        tmp_path / "data",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        environment={},
        now=lambda: datetime(2026, 8, 3, 10, 15),
        sleep=lambda _: None,
    )
    with pytest.raises(HistoricalProviderError, match="OPENALEX_API_KEY"):
        provider.search_works(
            "robotics", limit=1, publication_year_min=2000, publication_year_max=2026
        )
    assert calls == 0
