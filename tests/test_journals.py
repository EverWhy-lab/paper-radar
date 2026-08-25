from __future__ import annotations

from datetime import date, datetime

from paper_radar.curation import CuratedRecommendationEngine
from paper_radar.history_models import HistoricalPaper
from paper_radar.reader_models import RecommendationEntry


def journal_paper(
    index: int,
    *,
    title: str,
    abstract: str,
    publication_date: str,
    journal: str = "Automatica",
) -> HistoricalPaper:
    return HistoricalPaper(
        canonical_paper_id=f"openalex:W{200 + index}",
        openalex_id=f"W{200 + index}",
        base_arxiv_id=None,
        doi=f"10.5555/journal{index}",
        title=title,
        authors=["Journal Author"],
        abstract=abstract,
        publication_date=publication_date,
        publication_year=int(publication_date[:4]),
        source_name=journal,
        source_type="article",
        topics=[],
        cited_by_count=0,
        counts_by_year=[],
        fwci=None,
        citation_normalized_percentile=None,
        referenced_works=[],
        related_works=[],
        discovery_source=[f"journal_search:{journal}"],
        seed_paper_id=[],
        discovered_at="2026-08-03T10:15:00+08:00",
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status="unread",
        dismissed=False,
        metadata_updated_at="2026-08-03T09:00:00+08:00",
        openalex_url=f"https://openalex.org/W{200 + index}",
        landing_page_url=f"https://doi.org/10.5555/journal{index}",
        open_access_url=None,
        pdf_url=None,
        is_knowledge_map=False,
    )


def select(profile, *, historical_papers):
    return CuratedRecommendationEngine(profile).select(
        recent_new=[],
        historical_papers=historical_papers,
        reading_pool=[],
        history={},
        target_date="2026-08-05",
        considered_at="2026-08-05T10:15:00+08:00",
    )


def test_robotics_core_journal_sources_and_limits(profile) -> None:
    sources = {
        source["name"]: source for source in profile.journals["sources"]
    }

    assert sources["IEEE Transactions on Robotics"] == {
        "name": "IEEE Transactions on Robotics",
        "source_id": "S144620930",
        "group": "robotics_core",
        "fetch_limit": 100,
        "rising_scan_limit": 600,
    }
    assert sources["The International Journal of Robotics Research"] == {
        "name": "The International Journal of Robotics Research",
        "source_id": "S73484101",
        "group": "robotics_core",
        "fetch_limit": 60,
        "rising_scan_limit": 300,
    }
    assert sources["IEEE Robotics and Automation Letters"] == {
        "name": "IEEE Robotics and Automation Letters",
        "source_id": "S4210169774",
        "group": "robotics_core",
        "fetch_limit": 600,
        "rising_scan_limit": 3000,
    }
    control_names = {
        "IEEE Transactions on Automatic Control",
        "Automatica",
        "IEEE Transactions on Industrial Electronics",
        "IEEE Transactions on Control Systems Technology",
        "IEEE Transactions on Industrial Informatics",
        "Control Engineering Practice",
        "Journal of Process Control",
    }
    assert {
        source["name"]
        for source in profile.journals["sources"]
        if source["group"] == "control_supplement"
    } == control_names


def test_journal_recent_selects_only_fresh_relevant_journal_papers(profile) -> None:
    fresh = journal_paper(
        1,
        title="Whole-Body Control for Humanoid Robots",
        abstract=(
            "whole-body control model predictive control for humanoid robot "
            "locomotion with mobile manipulation and robot learning"
        ),
        publication_date="2026-07-20",
    )
    old = journal_paper(
        2,
        title="Older Control Paper",
        abstract="whole-body control model predictive control for legged robots",
        publication_date="2025-01-01",
    )
    dismissed = journal_paper(
        3,
        title="Dismissed Journal Paper",
        abstract="whole-body control model predictive control for humanoid robots",
        publication_date="2026-07-25",
    )
    dismissed.dismissed = True

    result = select(profile, historical_papers=[fresh, old, dismissed])

    journal_entries = [
        entry for entry in result.recommendations
        if entry.category == "journal_recent"
    ]
    assert [entry.canonical_paper_id for entry in journal_entries] == ["openalex:W201"]
    assert len(journal_entries) <= int(
        profile.recommendations["daily_mix"]["journal_recent"]["max_count"]
    )


def test_journal_recent_respects_category_cap(profile) -> None:
    papers = [
        journal_paper(
            index,
            title=f"Control Paper {index}",
            abstract=(
                (
                    "whole-body control model predictive control for humanoid robot locomotion "
                    "robot learning diffusion policy"
                    if index % 2
                    else "mobile manipulation loco-manipulation whole-body manipulation "
                    "model predictive control robot learning vision-language-action"
                )
            ),
            publication_date="2026-07-20",
            journal=("Automatica" if index % 2 else "IEEE Transactions on Automatic Control"),
        )
        for index in range(1, 5)
    ]

    result = select(profile, historical_papers=papers)

    journal_entries = [
        entry for entry in result.recommendations
        if entry.category == "journal_recent"
    ]
    assert len(journal_entries) == 2


def test_recent_method_only_journal_paper_can_use_model_based_lane(profile) -> None:
    method = journal_paper(
        10,
        title="Ultrafast Kinodynamic Motion Planning for Manipulators",
        abstract=(
            "A sampling-based motion planning algorithm produces dynamically feasible "
            "trajectories for robotic manipulators."
        ),
        publication_date="2026-08-04",
    )

    result = select(profile, historical_papers=[method])

    assert [entry.category for entry in result.recommendations] == [
        "model_based_recent"
    ]
    assert result.recommendations[0].historical_paper is method
