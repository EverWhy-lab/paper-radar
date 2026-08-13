from dataclasses import replace

from paper_radar.fetchers.arxiv import parse_atom
from paper_radar.scoring import robotics_context_gate, score_paper


def test_research_and_video_scores_are_explainable(atom_xml: str, profile) -> None:
    paper = score_paper(parse_atom(atom_xml)[0], profile)

    assert 50 <= paper.research_fit <= 100
    assert 40 <= paper.video_potential <= 100
    assert "legged_humanoid" in paper.matched_topics
    assert "optimal_control" in paper.matched_topics
    assert "whole-body control" in [keyword.casefold() for keyword in paper.matched_keywords]
    assert any(reason["label"] == "cs.RO" for reason in paper.research_reasons)
    assert all("points" in reason and "location" in reason for reason in paper.research_reasons)
    assert paper.video_reasons


def test_exclusion_penalty_and_score_bounds(atom_xml: str, profile) -> None:
    source = parse_atom(atom_xml)[0]
    excluded = replace(
        source,
        title="Reinforcement Learning for Autonomous Driving",
        summary="An autonomous driving policy and recommendation system.",
        categories=["cs.LG"],
        primary_category="cs.LG",
    )

    score_paper(excluded, profile)

    assert 0 <= excluded.research_fit <= 100
    penalties = [reason for reason in excluded.research_reasons if reason["kind"] == "exclusion"]
    assert len(penalties) == 2
    assert all(reason["points"] < 0 for reason in penalties)


def test_short_acronym_uses_word_boundaries(atom_xml: str, profile) -> None:
    source = parse_atom(atom_xml)[0]
    no_match = replace(source, title="A New MPCity Dataset", summary="Generic robotics.", categories=[])
    yes_match = replace(source, title="MPC-Based Robot Control", summary="Generic robotics.", categories=[])

    score_paper(no_match, profile)
    score_paper(yes_match, profile)

    assert "optimal_control" not in no_match.matched_topics
    assert "optimal_control" in yes_match.matched_topics


def test_generic_control_terms_do_not_establish_robotics_context(
    atom_xml: str, profile
) -> None:
    source = parse_atom(atom_xml)[0]
    paper = replace(
        source,
        title="RL-MPC Integration for Linear Systems",
        summary=(
            "A review of reinforcement learning and model predictive control "
            "for general linear systems."
        ),
    )

    score_paper(paper, profile)
    context = robotics_context_gate(paper.title, paper.summary, "", profile)

    assert context.eligible is False
    assert context.positive_matches == []
    assert any(
        reason["kind"] == "robotics_context" and reason["eligible"] is False
        for reason in paper.research_reasons
    )


def test_explicit_robotics_context_survives_negative_domain_terms(
    atom_xml: str, profile
) -> None:
    source = parse_atom(atom_xml)[0]
    paper = replace(
        source,
        title="Model Predictive Control for a Humanoid Robot",
        summary=(
            "A whole-body control system lets the humanoid robot inspect HVAC "
            "equipment in a smart building."
        ),
    )

    score_paper(paper, profile)
    context = robotics_context_gate(paper.title, paper.summary, "", profile)

    assert context.eligible is True
    assert "humanoid" in [term.casefold() for term in context.positive_matches]
    assert "HVAC" in context.negative_matches
