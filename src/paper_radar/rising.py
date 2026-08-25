from __future__ import annotations

from datetime import date, datetime
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.history_models import HistoricalPaper
from paper_radar.recommendation_utility import detect_document_type
from paper_radar.rising_storage import CitationSnapshot
from paper_radar.scoring import robotics_context_gate


def _normalise(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _captured_at(snapshot: CitationSnapshot) -> datetime | None:
    try:
        return datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def _closest_growth(
    snapshots: list[CitationSnapshot],
    *,
    current_count: int | None,
    captured_at: datetime,
    minimum_elapsed_days: int,
    target_elapsed_days: int,
) -> dict[str, float | int | str] | None:
    if current_count is None:
        return None
    candidates: list[tuple[float, CitationSnapshot]] = []
    for snapshot in snapshots:
        prior_at = _captured_at(snapshot)
        if prior_at is None or snapshot.cited_by_count is None:
            continue
        try:
            elapsed = (captured_at - prior_at).total_seconds() / 86400
        except TypeError:
            continue
        if elapsed >= minimum_elapsed_days:
            candidates.append((elapsed, snapshot))
    if not candidates:
        return None
    elapsed, prior = min(
        candidates,
        key=lambda item: (abs(item[0] - target_elapsed_days), -item[0]),
    )
    delta = current_count - int(prior.cited_by_count or 0)
    return {
        "prior_captured_at": prior.captured_at,
        "elapsed_days": round(elapsed, 2),
        "citation_delta": delta,
        "citations_per_day": round(delta / elapsed, 4),
    }


def observed_citation_growth(
    snapshots: list[CitationSnapshot],
    *,
    current_count: int | None,
    captured_at: datetime,
    config: dict[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    recent = _closest_growth(
        snapshots,
        current_count=current_count,
        captured_at=captured_at,
        minimum_elapsed_days=int(config["min_observed_elapsed_days"]),
        target_elapsed_days=7,
    )
    longer = _closest_growth(
        snapshots,
        current_count=current_count,
        captured_at=captured_at,
        minimum_elapsed_days=int(config["long_growth_min_elapsed_days"]),
        target_elapsed_days=28,
    )
    rates = []
    if recent is not None:
        rates.append((0.65, max(0.0, float(recent["citations_per_day"]))))
    if longer is not None:
        rates.append((0.35 if recent is not None else 1.0, max(0.0, float(longer["citations_per_day"]))))
    if not rates:
        return None, {}
    weight = sum(item[0] for item in rates)
    daily_rate = sum(item_weight * rate for item_weight, rate in rates) / weight
    reference = max(0.0001, float(config["observed_growth_reference_per_day"]))
    score = min(100.0, 100.0 * daily_rate / reference)
    return score, {
        "recent": recent,
        "approximately_28_day": longer,
        "blended_citations_per_day": round(daily_rate, 4),
    }


def score_rising_paper(
    paper: HistoricalPaper,
    profile: ResearchProfile,
    *,
    captured_at: datetime,
    snapshots: list[CitationSnapshot] | None = None,
) -> HistoricalPaper:
    config = profile.rising_discovery
    published = date.fromisoformat(str(paper.publication_date)[:10])
    age_days = (captured_at.date() - published).days
    effective_age_days = max(age_days, int(config["age_smoothing_days"]))
    effective_age_months = effective_age_days / 30.4375
    citation_velocity = (
        paper.cited_by_count / effective_age_months
        if paper.cited_by_count is not None
        else None
    )
    velocity_score = (
        min(
            100.0,
            100.0
            * citation_velocity
            / max(0.01, float(config["citation_velocity_reference_per_month"])),
        )
        if citation_velocity is not None
        else None
    )
    normalized_score = (
        max(0.0, min(100.0, 100 * paper.citation_normalized_percentile))
        if paper.citation_normalized_percentile is not None
        else None
    )
    fwci_score = (
        min(
            100.0,
            100.0 * paper.fwci / max(0.01, float(config["fwci_reference"])),
        )
        if paper.fwci is not None
        else None
    )
    observed_score, observed_details = observed_citation_growth(
        snapshots or [],
        current_count=paper.cited_by_count,
        captured_at=captured_at,
        config=config,
    )
    components: dict[str, float | None] = {
        "research_relevance": float(paper.research_fit),
        "citation_velocity": velocity_score,
        "normalized_citation": normalized_score,
        "fwci": fwci_score,
        "observed_growth": observed_score,
    }
    weights = {key: float(value) for key, value in config["weights"].items()}
    available_weight = sum(
        weights[key] for key, value in components.items() if value is not None
    )
    weighted = sum(
        float(value) * weights[key]
        for key, value in components.items()
        if value is not None
    ) / max(1.0, available_weight)
    total_weight = sum(weights.values())
    minimum_factor = float(config["minimum_metadata_coverage_factor"])
    coverage = minimum_factor + (1 - minimum_factor) * (
        available_weight / max(1.0, total_weight)
    )
    paper.rising_score = round(max(0.0, min(100.0, weighted * coverage)), 1)
    paper.rising_score_components = {
        key: (round(value, 1) if value is not None else None)
        for key, value in components.items()
    }
    paper.rising_updated_at = captured_at.isoformat(timespec="seconds")
    paper.rising_age_days = age_days
    paper.rising_citation_velocity = (
        round(citation_velocity, 3) if citation_velocity is not None else None
    )
    paper.rising_observed_growth = observed_details
    reasons = [
        f"Paper age {age_days} days; citation velocity uses a {int(config['age_smoothing_days'])}-day floor",
        (
            f"Smoothed citation velocity {citation_velocity:.2f} citations/month"
            if citation_velocity is not None
            else "OpenAlex citation count unavailable; velocity omitted rather than treated as zero"
        ),
        (
            f"Field/year normalized citation percentile {paper.citation_normalized_percentile:.3f}"
            if paper.citation_normalized_percentile is not None
            else "Field/year normalized citation percentile unavailable"
        ),
        (
            f"OpenAlex FWCI {paper.fwci:.2f}"
            if paper.fwci is not None
            else "OpenAlex FWCI unavailable; available-component normalization applied"
        ),
    ]
    if observed_details:
        reasons.append(
            "Observed snapshot growth "
            f"{float(observed_details['blended_citations_per_day']):.3f} citations/day"
        )
    else:
        reasons.append(
            f"No valid prior snapshot at least {int(config['min_observed_elapsed_days'])} days old; cold-start score used"
        )
    paper.rising_score_reasons = reasons
    return paper


def rising_eligibility(
    paper: HistoricalPaper,
    profile: ResearchProfile,
    *,
    as_of: date,
) -> tuple[bool, list[str]]:
    config = profile.rising_discovery
    source_ids = {
        str(source["source_id"]).upper()
        for source in profile.journals.get("sources", [])
        if source.get("group") == "robotics_core"
    }
    if not paper.source_id or paper.source_id.upper() not in source_ids:
        return False, ["Source is not one of the configured robotics_core journals"]
    if paper.is_retracted:
        return False, ["OpenAlex marks the work as retracted"]
    if not paper.publication_date:
        return False, ["Publication date unavailable"]
    try:
        published = date.fromisoformat(paper.publication_date[:10])
    except ValueError:
        return False, ["Publication date is invalid"]
    age_days = (as_of - published).days
    if age_days < 0 or age_days > int(config["lookback_days"]):
        return False, [
            f"Paper age {age_days} days is outside 0–{int(config['lookback_days'])} days"
        ]
    reader_paper = paper.to_reader_paper()
    document_type = detect_document_type(reader_paper, profile)
    source_type = _normalise(paper.source_type or "")
    if document_type == "survey" or source_type in {
        "review",
        "survey",
        "tutorial",
        "taxonomy",
    }:
        return False, ["Survey/review/tutorial/taxonomy belongs in the knowledge-map lane"]
    text = _normalise(f"{paper.title} {paper.abstract or ''}")
    excluded = [
        _normalise(term) for term in profile.recommendations.get("excluded_terms", [])
    ]
    if any(term in text for term in excluded):
        return False, ["Matched a configured off-topic exclusion"]
    topic_text = " ".join(
        str(topic.get("display_name") or "") for topic in paper.topics
    )
    context = robotics_context_gate(
        paper.title, paper.abstract or "", topic_text, profile
    )
    if not context.eligible:
        return False, [f"Robotics context failed: {context.reason}"]
    minimum_fit = int(config["min_research_fit"])
    if paper.research_fit < minimum_fit:
        return False, [f"research_fit {paper.research_fit} < {minimum_fit}"]
    return True, [
        f"Robotics context: {', '.join(context.positive_matches)}",
        f"research_fit {paper.research_fit} ≥ {minimum_fit}",
        f"Source restricted to robotics_core ({paper.source_id})",
        f"Publication age {age_days} days",
    ]


def score_quantiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    if not ordered:
        return {key: None for key in ("p50", "p75", "p90", "p95")}

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        weight = position - lower
        return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 1)

    return {
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
    }
