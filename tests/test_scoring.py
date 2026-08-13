from dataclasses import replace

from paper_radar.fetchers.arxiv import parse_atom
from paper_radar.models import Paper
from paper_radar.scoring import robotics_context_gate, score_paper


def topical_paper(title: str, summary: str, *, category: str = "cs.AI") -> Paper:
    return Paper(
        arxiv_id="2608.99999v1",
        base_id="2608.99999",
        version=1,
        title=title,
        authors=["Fixture Author"],
        published="2026-08-03T01:00:00Z",
        updated="2026-08-03T01:00:00Z",
        primary_category=category,
        categories=[category],
        summary=summary,
        abs_url="https://arxiv.org/abs/2608.99999",
        pdf_url="https://arxiv.org/pdf/2608.99999",
    )


def test_research_and_video_scores_are_explainable(atom_xml: str, profile) -> None:
    paper = score_paper(parse_atom(atom_xml)[0], profile)

    assert 30 <= paper.research_fit <= 100
    assert 40 <= paper.video_potential <= 100
    assert "robot_control_optimization" in paper.matched_topics
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

    assert "robot_control_optimization" not in no_match.matched_topics
    assert "robot_control_optimization" in yes_match.matched_topics


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


def test_profile_has_six_current_core_topics_and_secondary_control(profile) -> None:
    topic_weights = {
        topic["id"]: topic["weight"] for topic in profile.scoring["topics"]
    }

    assert set(profile.recommendations["core_topic_ids"]) == {
        "vla_robot_foundation",
        "world_model_embodied_reasoning",
        "humanoid_loco_manipulation",
        "robot_learning_posttraining",
        "dexterous_multimodal_manipulation",
        "robot_data_scaling_sim2real",
    }
    assert topic_weights["vla_robot_foundation"] == 24
    assert topic_weights["robot_control_optimization"] == 12
    assert "robot_control_optimization" not in profile.recommendations["core_topic_ids"]
    assert "pose_rescue" not in topic_weights
    assert "llm_agent_robotics" not in topic_weights


def test_old_topic_ids_resolve_without_breaking_archives_or_feedback(profile) -> None:
    assert profile.canonical_topic_id("legged_humanoid") == (
        "humanoid_loco_manipulation"
    )
    assert profile.canonical_topic_id("diffusion_visuomotor") == (
        "robot_learning_posttraining"
    )
    assert profile.canonical_topic_id("vla_foundation") == "vla_robot_foundation"
    assert profile.topic_labels["optimal_control"] == "Robot Control & Optimization"
    assert profile.topic_labels["pose_rescue"].endswith("(legacy)")


def test_current_robot_ai_frontier_signals_match_expected_topics(profile) -> None:
    examples = [
        (
            "Vision-Language-Action Foundation Model for General-Purpose Robot Manipulation",
            "A generalist robot policy uses cross-embodiment data.",
            "vla_robot_foundation",
        ),
        (
            "World Action Models for Robot Learning",
            "An embodied world model enables long-horizon manipulation and hierarchical robot planning.",
            "world_model_embodied_reasoning",
        ),
        (
            "Hierarchical VLA Planning for Long-Horizon Manipulation",
            "A vision-language-action robot policy performs embodied planning.",
            "world_model_embodied_reasoning",
        ),
        (
            "Humanoid Whole-Body Loco-Manipulation",
            "Whole-body reinforcement learning enables humanoid manipulation.",
            "humanoid_loco_manipulation",
        ),
        (
            "Reinforcement Fine-Tuning for VLA Post-Training",
            "A robot policy learns from failures using online reinforcement learning.",
            "robot_learning_posttraining",
        ),
        (
            "Whole-Body Imitation and Reinforcement Learning for Humanoids",
            "A humanoid robot learns whole-body manipulation and locomotion.",
            "humanoid_loco_manipulation",
        ),
        (
            "Vision-Tactile Robot Policies for Dexterous Manipulation",
            "A tactile policy enables contact-rich bimanual manipulation.",
            "dexterous_multimodal_manipulation",
        ),
        (
            "Tactile-Reactive Manipulation with a Robot Hand",
            "A tactile manipulation policy detects slip during in-hand manipulation.",
            "dexterous_multimodal_manipulation",
        ),
        (
            "Bimanual Manipulation with Multimodal Robot Policies",
            "A robot policy combines vision tactile input and force feedback.",
            "dexterous_multimodal_manipulation",
        ),
        (
            "Large-Scale Real-World Robot Data Scaling",
            "Cross-embodiment data and sim-to-real train a robot foundation model.",
            "robot_data_scaling_sim2real",
        ),
        (
            "Continual VLA Learning Across Robot Embodiments",
            "A continual VLA prevents catastrophic forgetting during robot skill acquisition.",
            "robot_learning_posttraining",
        ),
        (
            "Sim-to-Real Foundation Policies for Robot Manipulation",
            "A robot foundation model uses domain randomization and synthetic robot data.",
            "robot_data_scaling_sim2real",
        ),
    ]

    for title, summary, expected_topic in examples:
        paper = score_paper(topical_paper(title, summary), profile)
        context = robotics_context_gate(title, summary, "", profile)
        assert expected_topic in paper.matched_topics
        assert paper.research_fit >= 30
        assert context.eligible is True


def test_generic_ai_and_non_robot_domains_cannot_become_core_recommendations(
    profile,
) -> None:
    examples = [
        ("Large Language Model Agents for Planning", "Agentic planning for software tasks."),
        ("Diffusion Models for Image Generation", "A generative image diffusion model."),
        ("Reinforcement Learning with Better Exploration", "A generic game-playing method."),
        ("Video World Models for Future Prediction", "A generative video model."),
        ("World Models for Autonomous Driving", "A self-driving vehicle predicts future traffic."),
        ("A Neural Recommender System", "Recommendation system ranking at scale."),
        ("MPC for Power Systems", "Control of a smart grid and microgrid."),
        ("Diffusion for Medical Images", "Medical image segmentation."),
        ("Human Pose Estimation with Transformers", "Human keypoint detection in videos."),
    ]

    core_topics = set(profile.recommendations["core_topic_ids"])
    for title, summary in examples:
        paper = score_paper(topical_paper(title, summary), profile)
        context = robotics_context_gate(title, summary, "", profile)
        assert context.eligible is False
        assert not (set(paper.matched_topics) & core_topics) or paper.research_fit < 40
