from dataclasses import replace
from datetime import date

import httpx
import pytest

from paper_radar.config import FetchConfig
from paper_radar.fetchers.arxiv import (
    ArxivClient,
    ArxivFetchError,
    deduplicate_papers,
    parse_atom,
    split_arxiv_id,
)


EMPTY_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"""


def test_parse_atom_metadata(atom_xml: str) -> None:
    papers = parse_atom(atom_xml)

    assert len(papers) == 2
    first = papers[0]
    assert first.arxiv_id == "2608.00001v1"
    assert first.base_id == "2608.00001"
    assert first.version == 1
    assert first.title == "Whole-Body Control for Agile Quadruped Robots"
    assert first.authors == ["Ada Researcher", "Lin Roboticist"]
    assert first.primary_category == "cs.RO"
    assert first.categories == ["cs.RO", "eess.SY"]
    assert "model predictive control" in first.summary
    assert first.abs_url.endswith("2608.00001v1")
    assert first.pdf_url.endswith("2608.00001v1")


def test_parse_invalid_atom_has_friendly_error() -> None:
    with pytest.raises(ArxivFetchError, match="invalid Atom XML"):
        parse_atom("<not-closed>")


def test_split_modern_and_legacy_ids() -> None:
    assert split_arxiv_id("https://arxiv.org/abs/2608.00001v3") == ("2608.00001", 3)
    assert split_arxiv_id("hep-th/9901001v2") == ("hep-th/9901001", 2)


def test_deduplicate_keeps_latest_version(atom_xml: str) -> None:
    first = parse_atom(atom_xml)[0]
    version_two = replace(first, arxiv_id="2608.00001v2", version=2, updated="2026-08-03T11:00:00Z")

    result = deduplicate_papers([version_two, first, replace(first)])

    assert len(result) == 1
    assert result[0].version == 2


def test_client_retries_without_live_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectTimeout("fixture timeout", request=request)
        return httpx.Response(200, text=EMPTY_FEED, request=request)

    sleeps: list[float] = []
    config = FetchConfig(
        endpoint="https://example.invalid/api",
        user_agent="Test/1.0",
        page_size=100,
        max_pages=1,
        page_delay_seconds=3.1,
        timeout_seconds=1,
        retries=3,
    )
    client = ArxivClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    assert client._get({"search_query": "cat:cs.RO"}) == EMPTY_FEED
    assert calls == 3
    assert sleeps and all(delay >= 3 for delay in sleeps)


def test_client_reports_exhausted_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable", request=request)

    config = FetchConfig(
        endpoint="https://example.invalid/api",
        user_agent="Test/1.0",
        page_size=100,
        max_pages=1,
        page_delay_seconds=3,
        timeout_seconds=1,
        retries=2,
    )
    client = ArxivClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(ArxivFetchError, match="after 2 attempts"):
        client._get({"search_query": "cat:cs.RO"})


def test_fetch_for_shanghai_date_filters_updates(atom_xml: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=atom_xml if len(requests) == 1 else EMPTY_FEED, request=request)

    config = FetchConfig(
        endpoint="https://example.invalid/api",
        user_agent="Test/1.0",
        page_size=100,
        max_pages=1,
        page_delay_seconds=3,
        timeout_seconds=1,
        retries=1,
    )
    client = ArxivClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    papers = client.fetch_for_date(date(2026, 8, 3), "Asia/Shanghai", ["cs.RO", "cs.AI"])

    assert {paper.base_id for paper in papers} == {"2608.00001", "2608.00002"}
    first_query = requests[0].url.params["search_query"]
    assert "submittedDate:[202608021600 TO 202608031559]" in first_query
    assert "cat:cs.RO OR cat:cs.AI" in first_query
