from __future__ import annotations

from copy import deepcopy

from paper_radar.historical_scoring import (
    historical_relevance_eligible,
    score_historical_paper,
)
from paper_radar.providers.openalex import parse_openalex_work


def make_historical(openalex_payload, index: int):
    return parse_openalex_work(
        deepcopy(openalex_payload["results"][index]),
        discovered_at="2026-08-03T10:15:00+08:00",
        discovery_source="fixture",
    )


def test_highly_cited_but_irrelevant_work_is_ineligible(profile, openalex_payload) -> None:
    paper = score_historical_paper(
        make_historical(openalex_payload, 2), profile, as_of_year=2026
    )
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert paper.cited_by_count == 5000
    assert eligible is False
    assert paper.research_fit == 0
    assert reasons


def test_relevant_moderate_citation_work_survives_impact_fallback(
    profile, openalex_payload
) -> None:
    paper = score_historical_paper(
        make_historical(openalex_payload, 4), profile, as_of_year=2026
    )
    eligible, _ = historical_relevance_eligible(paper, profile)

    assert eligible is True
    assert paper.fwci is None
    assert paper.citation_normalized_percentile is None
    assert paper.historical_value_score >= profile.recommendations["daily_mix"][
        "high_impact_historical"
    ]["min_historical_value_score"]


def test_different_years_use_age_adjusted_signal_not_raw_citations(
    profile, openalex_payload
) -> None:
    old = make_historical(openalex_payload, 1)
    newer = make_historical(openalex_payload, 1)
    old.publication_year = 2008
    old.publication_date = "2008-01-01"
    old.cited_by_count = 200
    old.citation_normalized_percentile = None
    old.fwci = None
    old.counts_by_year = []
    newer.publication_year = 2025
    newer.publication_date = "2025-01-01"
    newer.cited_by_count = 100
    newer.citation_normalized_percentile = None
    newer.fwci = None
    newer.counts_by_year = []

    score_historical_paper(old, profile, as_of_year=2026)
    score_historical_paper(newer, profile, as_of_year=2026)

    assert old.cited_by_count > newer.cited_by_count
    assert old.historical_score_components["normalized_citation"] < newer.historical_score_components["normalized_citation"]
    assert old.historical_value_score < newer.historical_value_score


def test_missing_percentile_and_fwci_use_available_components(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 4)
    score_historical_paper(paper, profile, as_of_year=2026)

    assert paper.historical_score_components["fwci"] is None
    assert paper.historical_score_components["normalized_citation"] is not None
    assert paper.historical_value_score is not None
    assert any("降级" in reason for reason in paper.historical_score_reasons)


def test_missing_citation_data_remains_unknown_not_zero(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 4)
    paper.cited_by_count = None
    paper.counts_by_year = []
    paper.fwci = None
    paper.citation_normalized_percentile = None
    score_historical_paper(paper, profile, as_of_year=2026)

    assert paper.cited_by_count is None
    assert paper.historical_score_components["normalized_citation"] is None
    assert paper.historical_score_components["citation_momentum"] is None
    assert any("未知处理" in reason for reason in paper.historical_score_reasons)


def test_high_impact_building_mpc_cannot_bypass_robotics_context_gate(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "RL-MPC for building energy management"
    paper.abstract = (
        "Reinforcement learning and model predictive control reduce HVAC "
        "energy use in a smart grid."
    )
    paper.topics = [{"display_name": "Building energy systems"}]
    paper.cited_by_count = 50_000
    paper.fwci = 100.0
    paper.citation_normalized_percentile = 0.9999

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is False
    assert any("机器人语境门槛" in reason for reason in reasons)
    assert any(
        reason["kind"] == "robotics_context" and reason["eligible"] is False
        for reason in paper.research_reasons
    )


def test_robot_mpc_remains_eligible(profile, openalex_payload) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "Model Predictive Control for a Humanoid Robot"
    paper.abstract = (
        "A whole-body control method coordinates humanoid robot locomotion "
        "and mobile manipulation."
    )
    paper.topics = [{"display_name": "Humanoid robotics"}]

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is True
    assert any("机器人语境" in reason for reason in reasons)


def test_historical_topic_metadata_can_establish_robotics_context(
    profile, openalex_payload
) -> None:
    paper = make_historical(openalex_payload, 1)
    paper.title = "Model Predictive Control for Constrained Systems"
    paper.abstract = "A model predictive control method with stability guarantees."
    paper.topics = [{"display_name": "Humanoid robotics"}]

    score_historical_paper(paper, profile, as_of_year=2026)
    eligible, reasons = historical_relevance_eligible(paper, profile)

    assert eligible is True
    assert any("robotics" in reason.casefold() for reason in reasons)
