from __future__ import annotations

from datetime import datetime
from pathlib import Path

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.history_models import HistoricalPaper
from paper_radar.models import Paper
from paper_radar.reader_models import DailyRecommendations
from paper_radar.reader_rendering import RecommendationSiteRenderer
from paper_radar.reader_storage import RecommendationStorage


def historical(
    index: int,
    *,
    title: str,
    abstract: str,
    arxiv_id: str | None = None,
    knowledge_map: bool = False,
    status: str = "unread",
    dismissed: bool = False,
) -> HistoricalPaper:
    return HistoricalPaper(
        canonical_paper_id=f"openalex:W{900 + index}",
        openalex_id=f"W{900 + index}",
        base_arxiv_id=arxiv_id,
        doi=f"10.5555/robot{index}",
        title=title,
        authors=["Historical Fixture Author"],
        abstract=abstract,
        publication_date=f"20{15 + index:02d}-01-01",
        publication_year=2015 + index,
        source_name="Fixture Robotics Venue",
        source_type="review" if knowledge_map else "article",
        topics=[],
        cited_by_count=100 + index * 10,
        counts_by_year=[
            {"year": 2026, "cited_by_count": 30},
            {"year": 2025, "cited_by_count": 25},
            {"year": 2024, "cited_by_count": 15},
            {"year": 2023, "cited_by_count": 12},
        ],
        fwci=2.5,
        citation_normalized_percentile=0.95,
        referenced_works=[],
        related_works=[],
        discovery_source=["fixture_topic_search"],
        seed_paper_id=[],
        discovered_at="2026-08-01T10:15:00+08:00",
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status=status,
        dismissed=dismissed,
        metadata_updated_at="2026-08-03T09:00:00+08:00",
        openalex_url=f"https://openalex.org/W{900 + index}",
        landing_page_url=f"https://doi.org/10.5555/robot{index}",
        open_access_url=None,
        pdf_url=(f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None),
        is_knowledge_map=knowledge_map,
    )


def recent(index: int, *, arxiv_id: str | None = None, topic="pose_rescue", keyword="rescue robotics") -> Paper:
    base = arxiv_id or f"2608.{index:05d}"
    return Paper(
        arxiv_id=f"{base}v1",
        base_id=base,
        version=1,
        title=f"Rescue Robotics System {index}: {keyword}",
        authors=["Recent Fixture Author"],
        published="2026-08-03T01:00:00Z",
        updated="2026-08-03T01:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary=f"A physical robot study of {keyword}.",
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
        research_fit=80,
        video_potential=60,
        matched_topics=[topic],
        matched_keywords=[keyword],
    )


def select(profile, *, histories, recents=None, history=None):
    return CuratedRecommendationEngine(profile).select(
        recent_new=recents or [],
        historical_papers=histories,
        reading_pool=[],
        history=history or {},
        target_date="2026-08-03",
        considered_at="2026-08-03T10:15:00+08:00",
    )


def test_v013_category_caps_total_limit_and_exclusivity(profile) -> None:
    histories = [
        historical(1, title="Whole-Body Control Method for Humanoid Robots", abstract="whole-body control humanoid robot"),
        historical(2, title="Mobile Manipulation Framework", abstract="mobile manipulation loco-manipulation"),
        historical(3, title="Quadruped Robot Locomotion System", abstract="quadruped robot legged locomotion"),
        historical(4, title="Diffusion Policy for Visuomotor Control", abstract="diffusion policy visuomotor policy"),
        historical(5, title="Mobile Manipulation Survey", abstract="survey mobile manipulation", knowledge_map=True),
        historical(6, title="Legged Robot Tutorial and Survey", abstract="tutorial survey legged robot", knowledge_map=True),
    ]
    recents = [
        recent(1),
        recent(2, topic="vla_foundation", keyword="vision-language-action"),
        recent(3, topic="mobile_manipulation", keyword="mobile manipulation"),
    ]
    result = select(profile, histories=histories, recents=recents)
    counts = {
        category: sum(entry.category == category for entry in result.recommendations)
        for category in (
            "frontier_recent",
            "high_impact_historical",
            "review_knowledge_map",
        )
    }

    assert len(result.recommendations) <= 5
    assert counts["frontier_recent"] <= 2
    assert counts["high_impact_historical"] <= 3
    assert counts["review_knowledge_map"] <= 1
    assert len({entry.canonical_paper_id for entry in result.recommendations}) == len(result.recommendations)


def test_frontier_selection_preserves_topic_diversity(profile) -> None:
    recents = [
        recent(
            1,
            topic="vla_robot_foundation",
            keyword="vision-language-action",
        ),
        recent(
            2,
            topic="vla_robot_foundation",
            keyword="robot foundation model",
        ),
        recent(
            3,
            topic="dexterous_multimodal_manipulation",
            keyword="dexterous manipulation",
        ),
    ]

    result = select(profile, histories=[], recents=recents)

    frontier = [
        entry for entry in result.recommendations
        if entry.category == "frontier_recent"
    ]
    assert len(frontier) == 2
    assert {entry.paper.matched_topics[0] for entry in frontier} == {
        "vla_robot_foundation",
        "dexterous_multimodal_manipulation",
    }


def test_same_paper_cannot_enter_recent_and_historical_categories(profile) -> None:
    shared = "2401.00004"
    old = historical(
        1,
        title="Humanoid Whole-Body Loco-Manipulation System",
        abstract="humanoid robot whole-body manipulation and loco-manipulation",
        arxiv_id=shared,
    )
    result = select(
        profile,
        histories=[old],
        recents=[
            recent(
                1,
                arxiv_id=shared,
                topic="humanoid_loco_manipulation",
                keyword="loco-manipulation",
            )
        ],
    )

    matching = [entry for entry in result.recommendations if shared in entry.aliases or f"arxiv:{shared}" in entry.aliases]
    assert len(matching) == 1
    assert matching[0].category == "frontier_recent"


def test_review_alias_enters_review_category_not_frontier(profile) -> None:
    shared = "2401.00005"
    review = historical(
        5,
        title="A Survey of MPC for Humanoid Robots",
        abstract="survey of model predictive control and whole-body control for humanoid robot locomotion",
        arxiv_id=shared,
        knowledge_map=True,
    )
    recent_review = recent(
        5,
        arxiv_id=shared,
        topic="optimal_control",
        keyword="whole-body control",
    )
    recent_review.title = review.title
    recent_review.summary = review.abstract or ""

    result = select(profile, histories=[review], recents=[recent_review])

    matching = [entry for entry in result.recommendations if f"arxiv:{shared}" in entry.aliases]
    assert len(matching) == 1
    assert matching[0].category == "review_knowledge_map"


def test_robotics_review_enters_knowledge_map_not_frontier(profile) -> None:
    review = recent(
        6,
        topic="humanoid_loco_manipulation",
        keyword="humanoid robot",
    )
    review.title = "A Systematic Review of MPC for Humanoid Robots"
    review.summary = "A survey and taxonomy of whole-body control for humanoid robot locomotion."

    result = select(profile, histories=[], recents=[review])

    assert len(result.recommendations) == 1
    assert result.recommendations[0].category == "review_knowledge_map"


def test_generic_rl_mpc_review_cannot_enter_any_robotics_category(profile) -> None:
    false_positive = recent(
        7,
        topic="optimal_control",
        keyword="model predictive control",
    )
    false_positive.title = "RL-MPC Integration for Linear Systems"
    false_positive.summary = (
        "A systematic review and taxonomy of reinforcement learning and model "
        "predictive control for general linear systems."
    )
    false_positive.research_fit = 100
    false_positive.matched_topics = ["optimal_control", "robot_learning"]
    false_positive.matched_keywords = [
        "model predictive control",
        "MPC",
        "reinforcement learning",
    ]

    result = select(profile, histories=[], recents=[false_positive])

    assert result.recommendations == []


def test_historical_dismissed_read_and_cooldown_rules(profile) -> None:
    dismissed = historical(
        1, title="Whole-Body Control Method", abstract="whole-body control humanoid robot", dismissed=True
    )
    read = historical(
        2, title="Mobile Manipulation Framework", abstract="mobile manipulation loco-manipulation", status="read"
    )
    cooling = historical(
        3, title="Quadruped Robot System", abstract="quadruped robot legged locomotion"
    )
    available = historical(
        4,
        title="Diffusion Policy for Mobile Manipulation",
        abstract="diffusion policy visuomotor policy mobile manipulation",
    )
    history = {
        cooling.canonical_paper_id.casefold(): [
            {"date": "2026-07-20", "category": "high_impact_historical"}
        ]
    }
    result = select(
        profile,
        histories=[dismissed, read, cooling, available],
        history=history,
    )

    assert [entry.canonical_paper_id for entry in result.recommendations] == [
        available.canonical_paper_id
    ]


def test_unselected_history_never_appears_in_homepage_or_archive(
    tmp_path: Path, profile
) -> None:
    selected = historical(
        1,
        title="SELECTED Whole-Body Control Method",
        abstract="whole-body control humanoid robot",
    )
    hidden = historical(
        2,
        title="HIDDEN HISTORICAL CANDIDATE",
        abstract="unrelated medical image segmentation",
    )
    result = select(profile, histories=[selected, hidden])
    storage = RecommendationStorage(tmp_path / "data")
    storage.save(
        DailyRecommendations(
            date="2026-08-03",
            generated_at="2026-08-03T10:15:00+08:00",
            recommendations=result.recommendations,
            candidate_count=100,
            historical_candidate_count=2,
            mode="fixture",
            selection_config=profile.recommendations["daily_mix"],
        )
    )
    index, archive = RecommendationSiteRenderer(
        tmp_path / "site", storage, profile
    ).render("2026-08-03")

    for page in (index, archive):
        html = page.read_text(encoding="utf-8")
        assert "SELECTED Whole-Body Control Method" in html
        assert "HIDDEN HISTORICAL CANDIDATE" not in html
        assert "OpenAlex citations" in html
