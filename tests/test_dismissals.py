from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.history_models import HistoricalPaper
from paper_radar.models import Paper
from paper_radar.reader_models import DismissalEntry
from paper_radar.reader_storage import DismissalStorage


def historical(
    index: int,
    *,
    title: str,
    abstract: str,
    topic: str = "pose_rescue",
    keyword: str = "rescue robotics",
) -> HistoricalPaper:
    return HistoricalPaper(
        canonical_paper_id=f"openalex:W{900 + index}",
        openalex_id=f"W{900 + index}",
        base_arxiv_id=None,
        doi=f"10.5555/robot{index}",
        title=title,
        authors=["Fixture Author"],
        abstract=abstract,
        publication_date="2020-01-01",
        publication_year=2020,
        source_name="Fixture Venue",
        source_type="article",
        topics=[],
        cited_by_count=100,
        counts_by_year=[{"year": 2026, "cited_by_count": 10}],
        fwci=2.0,
        citation_normalized_percentile=0.9,
        referenced_works=[],
        related_works=[],
        discovery_source=["fixture"],
        seed_paper_id=[],
        discovered_at="2026-08-01T10:15:00+08:00",
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status="unread",
        dismissed=False,
        metadata_updated_at="2026-08-03T09:00:00+08:00",
        openalex_url=f"https://openalex.org/W{900 + index}",
        landing_page_url=f"https://doi.org/10.5555/robot{index}",
        open_access_url=None,
        pdf_url=None,
        is_knowledge_map=False,
    )


def recent(index: int, *, topic: str, keyword: str) -> Paper:
    base = f"2608.{index:05d}"
    return Paper(
        arxiv_id=f"{base}v1",
        base_id=base,
        version=1,
        title=f"Recent Paper {index}: {keyword}",
        authors=["Fixture Author"],
        published="2026-08-03T01:00:00Z",
        updated="2026-08-03T01:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=f"A physical robot study of {keyword}.",
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
        research_fit=80,
        video_potential=50,
        matched_topics=[topic],
        matched_keywords=[keyword],
    )


def select(profile, *, histories=None, recents=None, dismissals=None):
    return CuratedRecommendationEngine(profile).select(
        recent_new=recents or [],
        historical_papers=histories or [],
        reading_pool=[],
        history={},
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
        dismissals=dismissals,
    )


def dismissal(paper_id: str, topics: list[str], days_ago: int = 1) -> DismissalEntry:
    day = (date(2026, 8, 3) - timedelta(days=days_ago)).isoformat()
    return DismissalEntry(
        canonical_paper_id=paper_id,
        title="Dismissed",
        topics=topics,
        reason="not_interested",
        dismissed_at=f"{day}T10:00:00+08:00",
    )


def test_dismissal_storage_round_trip(tmp_path: Path) -> None:
    storage = DismissalStorage(tmp_path / "data")
    storage.add(
        DismissalEntry(
            canonical_paper_id="arxiv:2608.00001",
            title="Paper A",
            topics=["pose_rescue"],
            reason="not_interested",
            dismissed_at="2026-08-03T10:00:00+08:00",
        )
    )
    entries = storage.load()
    assert len(entries) == 1
    assert entries[0].canonical_paper_id == "arxiv:2608.00001"
    removed = storage.remove("arxiv:2608.00001")
    assert removed.title == "Paper A"
    assert storage.load() == []


def test_dismissed_recent_paper_is_excluded(profile) -> None:
    papers = [
        recent(1, topic="pose_rescue", keyword="rescue robotics"),
        recent(2, topic="vla_foundation", keyword="vision-language-action"),
    ]
    dismissals = [dismissal("arxiv:2608.00001", ["pose_rescue"])]

    result = select(profile, recents=papers, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert "arxiv:2608.00001" not in ids
    assert "arxiv:2608.00002" in ids


def test_dismissed_historical_paper_is_excluded(profile) -> None:
    histories = [
        historical(1, title="Rescue Robotics System", abstract="rescue robotics search and rescue"),
        historical(
            2,
            title="Humanoid Robot Locomotion System",
            abstract="humanoid robot legged locomotion",
        ),
    ]
    dismissals = [dismissal("openalex:W901", ["pose_rescue"])]

    result = select(profile, histories=histories, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert "openalex:W901" not in ids
    assert "openalex:W902" in ids


def test_topic_cooldown_reduces_related_recommendations(profile) -> None:
    dismissals = [
        dismissal("openalex:W901", ["pose_rescue"]),
        dismissal("openalex:W902", ["pose_rescue"]),
    ]
    recents = [
        recent(1, topic="pose_rescue", keyword="rescue robotics"),
        recent(2, topic="vla_foundation", keyword="vision-language-action"),
    ]

    result = select(profile, recents=recents, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert "arxiv:2608.00001" not in ids  # pose_rescue is in cooldown
    assert "arxiv:2608.00002" in ids  # unrelated topic still selected


def test_one_dismissal_is_not_enough_to_trigger_topic_cooldown(profile) -> None:
    dismissals = [dismissal("openalex:W901", ["vla_foundation"])]
    papers = [recent(1, topic="vla_foundation", keyword="vision-language-action")]

    result = select(profile, recents=papers, dismissals=dismissals)

    assert [entry.canonical_paper_id for entry in result.recommendations] == [
        "arxiv:2608.00001"
    ]


@pytest.mark.parametrize(
    ("days_ago", "is_cooling"),
    [
        (13, True),
        (14, False),
        (15, False),
        (30, False),
        (31, False),
        (60, False),
    ],
)
def test_topic_cooldown_boundary(profile, days_ago: int, is_cooling: bool) -> None:
    dismissals = [
        dismissal("openalex:W901", ["vla_foundation"], days_ago=days_ago),
        dismissal("openalex:W902", ["vla_foundation"], days_ago=days_ago),
    ]
    papers = [recent(1, topic="vla_foundation", keyword="vision-language-action")]

    result = select(profile, recents=papers, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert ("arxiv:2608.00001" not in ids) is is_cooling


def test_latest_triggering_feedback_starts_topic_cooldown(profile) -> None:
    dismissals = [
        dismissal("openalex:W901", ["vla_foundation"], days_ago=20),
        dismissal("openalex:W902", ["vla_foundation"], days_ago=13),
    ]
    papers = [recent(1, topic="vla_foundation", keyword="vision-language-action")]

    result = select(profile, recents=papers, dismissals=dismissals)

    assert result.recommendations == []


@pytest.mark.parametrize(
    ("cooldown_days", "days_ago", "is_cooling"),
    [(3, 2, True), (3, 3, False), (21, 14, True)],
)
def test_topic_cooldown_uses_configured_duration(
    profile, cooldown_days: int, days_ago: int, is_cooling: bool
) -> None:
    custom_profile = replace(
        profile,
        dismissals={
            **profile.dismissals,
            "topic_cooldown_days": cooldown_days,
        },
    )
    dismissals = [
        dismissal("openalex:W901", ["vla_foundation"], days_ago=days_ago),
        dismissal("openalex:W902", ["vla_foundation"], days_ago=days_ago),
    ]
    papers = [recent(1, topic="vla_foundation", keyword="vision-language-action")]

    result = select(custom_profile, recents=papers, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert ("arxiv:2608.00001" not in ids) is is_cooling


@pytest.mark.parametrize(
    ("days_ago", "is_cooling"),
    [(29, True), (30, False), (31, False)],
)
def test_topic_feedback_window_excludes_day_30(
    profile, days_ago: int, is_cooling: bool
) -> None:
    custom_profile = replace(
        profile,
        dismissals={
            **profile.dismissals,
            "topic_cooldown_days": 60,
        },
    )
    dismissals = [
        dismissal("openalex:W901", ["vla_foundation"], days_ago=days_ago),
        dismissal("openalex:W902", ["vla_foundation"], days_ago=days_ago),
    ]
    papers = [recent(1, topic="vla_foundation", keyword="vision-language-action")]

    result = select(custom_profile, recents=papers, dismissals=dismissals)

    ids = {entry.canonical_paper_id for entry in result.recommendations}
    assert ("arxiv:2608.00001" not in ids) is is_cooling
