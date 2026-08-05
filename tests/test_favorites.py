from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar import cli
from paper_radar.feedback import apply_feedback_lines
from paper_radar.history_storage import HistoricalPaperStorage
from paper_radar.providers.openalex import parse_openalex_work
from paper_radar.reader_models import DailyRecommendations, FavoriteEntry
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import (
    DismissalStorage,
    FavoriteStorage,
    RecommendationStorage,
)


def test_favorite_storage_round_trip(tmp_path: Path) -> None:
    storage = FavoriteStorage(tmp_path / "data")
    entry = FavoriteEntry(
        canonical_paper_id="arxiv:2608.00001",
        title="Paper A",
        authors=["Ada"],
        publication_year=2026,
        source_name="arXiv",
        abstract="Abstract.",
        landing_page_url="https://arxiv.org/abs/2608.00001",
        pdf_url=None,
        openalex_url=None,
        doi=None,
        saved_at="2026-08-05T10:00:00+08:00",
    )
    storage.add(entry)
    assert len(storage.load()) == 1
    removed = storage.remove("arxiv:2608.00001")
    assert removed.title == "Paper A"
    assert storage.load() == []


def test_apply_feedback_batch_writes_both_stores(
    tmp_path: Path, openalex_payload
) -> None:
    HistoricalPaperStorage(tmp_path / "data").save(
        [
            parse_openalex_work(
                deepcopy(openalex_payload["results"][0]),
                discovered_at="2026-08-03T10:15:00+08:00",
                discovery_source="fixture",
            )
        ]
    )
    lines = [
        "not-interested openalex:W100 — Whole-Body Control Paper",
        "favorite openalex:W100 — Whole-Body Control Paper",
        "not-interested openalex:W100 — duplicate",
        "garbage line",
    ]

    result = apply_feedback_lines(lines, tmp_path / "data", "2026-08-05T10:00:00+08:00")

    assert result.applied_dismissals == 1
    assert result.applied_favorites == 1
    assert result.skipped == 1
    assert result.failed == ["garbage line"]
    dismissals = DismissalStorage(tmp_path / "data").load()
    assert len(dismissals) == 1
    assert dismissals[0].canonical_paper_id == "openalex:w100"
    favorites = FavoriteStorage(tmp_path / "data").load()
    assert len(favorites) == 1
    assert favorites[0].canonical_paper_id == "openalex:w100"
    assert favorites[0].title  # metadata resolved from the pool


def test_candidates_prune_removes_only_old_files(tmp_path: Path) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    candidate_dir = tmp_path / "data" / "candidates"
    candidate_dir.mkdir(parents=True)
    old = today - timedelta(days=45)
    recent = today - timedelta(days=10)
    (candidate_dir / f"{old.isoformat()}.json").write_text("{}", encoding="utf-8")
    (candidate_dir / f"{recent.isoformat()}.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "not-a-date.json").write_text("{}", encoding="utf-8")

    args = argparse.Namespace(candidates_command="prune", older_than=30)
    exit_code = cli._candidates(tmp_path, args)

    assert exit_code == 0
    assert not (candidate_dir / f"{old.isoformat()}.json").exists()
    assert (candidate_dir / f"{recent.isoformat()}.json").exists()
    assert (candidate_dir / "not-a-date.json").exists()


def test_favorites_page_renders_from_favorite_store(
    tmp_path: Path, profile, openalex_payload
) -> None:
    storage = RecommendationStorage(tmp_path / "data")
    storage.save(
        DailyRecommendations(
            date="2026-08-05",
            generated_at="2026-08-05T12:00:00+08:00",
            recommendations=[],
            candidate_count=0,
            mode="fixture",
            selection_config=profile.recommendations["daily_mix"],
        )
    )
    paper = parse_openalex_work(
        deepcopy(openalex_payload["results"][0]),
        discovered_at="2026-08-03T10:15:00+08:00",
        discovery_source="fixture",
    )
    favorites = [
        FavoriteEntry(
            canonical_paper_id=paper.canonical_paper_id,
            title=paper.title,
            authors=paper.authors,
            publication_year=paper.publication_year,
            source_name=paper.source_name or "OpenAlex",
            abstract=paper.abstract or "",
            landing_page_url=paper.landing_page_url,
            pdf_url=paper.pdf_url,
            openalex_url=paper.openalex_url,
            doi=paper.doi,
            saved_at="2026-08-05T12:00:00+08:00",
        )
    ]

    RecommendationSiteRenderer(tmp_path / "site", storage, profile).render(
        "2026-08-05", favorites=favorites
    )

    page = (tmp_path / "site" / "favorites.html").read_text(encoding="utf-8")
    assert "Favorites" in page
    assert paper.title in page
    assert 'href="assets/reader.css"' in page
