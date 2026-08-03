from __future__ import annotations

from datetime import date
from typing import Any

from paper_radar.config import ResearchProfile
from paper_radar.history_models import HistoricalPaper
from paper_radar.scoring import score_paper


def _normalise(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _contains(text: str, term: str) -> bool:
    return _normalise(term) in _normalise(text)


def _metadata_completeness(paper: HistoricalPaper) -> float:
    checks = [
        bool(paper.title),
        bool(paper.authors),
        bool(paper.publication_date or paper.publication_year),
        bool(paper.source_name),
        bool(paper.abstract),
        bool(paper.topics),
        bool(paper.openalex_id or paper.doi or paper.base_arxiv_id),
        paper.cited_by_count is not None
        or paper.fwci is not None
        or paper.citation_normalized_percentile is not None,
    ]
    return 100 * sum(checks) / len(checks)


def _citation_momentum(
    paper: HistoricalPaper, config: dict[str, Any]
) -> tuple[float | None, str | None]:
    values = {
        int(item["year"]): int(item["cited_by_count"])
        for item in paper.counts_by_year
        if item.get("year") is not None and item.get("cited_by_count") is not None
    }
    if not values:
        return None, None
    years = sorted(values, reverse=True)
    recent_count = int(config["recent_growth_years"])
    prior_count = int(config["prior_growth_years"])
    recent_values = [values[year] for year in years[:recent_count]]
    prior_values = [
        values[year] for year in years[recent_count : recent_count + prior_count]
    ]
    recent_average = sum(recent_values) / len(recent_values)
    if not prior_values:
        reference = float(config["annual_citation_reference"])
        score = min(100.0, 100 * recent_average / max(1.0, reference))
        return score, f"近年年均 OpenAlex 引用 {recent_average:.1f}（无更早窗口可比较）"
    prior_average = sum(prior_values) / len(prior_values)
    if prior_average == 0:
        score = 100.0 if recent_average > 0 else 50.0
    else:
        score = min(100.0, 50.0 * recent_average / prior_average)
    return score, (
        f"近 {len(recent_values)} 年年均引用 {recent_average:.1f}；"
        f"此前 {len(prior_values)} 年年均 {prior_average:.1f}"
    )


def _normalized_citation(
    paper: HistoricalPaper, config: dict[str, Any], as_of_year: int
) -> tuple[float | None, str]:
    if paper.citation_normalized_percentile is not None:
        percentile = max(0.0, min(1.0, paper.citation_normalized_percentile))
        top_percent = max(0.1, (1.0 - percentile) * 100)
        return percentile * 100, f"同领域同年份引用标准化位于前 {top_percent:.1f}%"
    if paper.cited_by_count is None:
        return None, "OpenAlex 未提供引用量；按未知处理，不按 0 次处理"
    if paper.publication_year is None:
        return None, f"OpenAlex 收录引用 {paper.cited_by_count} 次；缺少年份，未作年限归一化"
    age = max(1, as_of_year - paper.publication_year + 1)
    annualized = paper.cited_by_count / age
    reference = float(config["annual_citation_reference"])
    score = min(100.0, 100 * annualized / max(1.0, reference))
    return score, (
        f"OpenAlex 收录引用 {paper.cited_by_count} 次；按发表年限折算年均 "
        f"{annualized:.1f}（缺少领域百分位时的降级信号）"
    )


def score_historical_paper(
    paper: HistoricalPaper,
    profile: ResearchProfile,
    *,
    as_of_year: int | None = None,
) -> HistoricalPaper:
    config = profile.historical_scoring
    topic_text = " ".join(
        str(topic.get("display_name") or "") for topic in paper.topics
    )
    scoring_paper = paper.to_reader_paper()
    scoring_paper.summary = " ".join(
        value for value in (paper.abstract or "", topic_text) if value
    )
    score_paper(scoring_paper, profile)
    paper.research_fit = scoring_paper.research_fit
    paper.video_potential = scoring_paper.video_potential
    paper.matched_topics = scoring_paper.matched_topics
    paper.matched_keywords = scoring_paper.matched_keywords
    paper.research_reasons = scoring_paper.research_reasons

    text = f"{paper.title} {paper.abstract or ''} {topic_text} {paper.source_type or ''}"
    review_terms = [str(term) for term in config["review_terms"]]
    method_terms = [str(term) for term in config["method_terms"]]
    paper.is_knowledge_map = any(_contains(text, term) for term in review_terms)

    current_year = as_of_year or date.today().year
    normalized, normalized_reason = _normalized_citation(paper, config, current_year)
    fwci_score = (
        min(100.0, 100 * paper.fwci / max(0.01, float(config["fwci_reference"])))
        if paper.fwci is not None
        else None
    )
    momentum, momentum_reason = _citation_momentum(paper, config)
    seeds = len(set(paper.seed_paper_id))
    seed_score = (
        min(
            100.0,
            60.0
            + 40.0
            * max(0, seeds - 1)
            / max(1, int(config["independent_seed_reference"]) - 1),
        )
        if seeds
        else None
    )
    if paper.is_knowledge_map:
        work_signal = 100.0
    elif any(_contains(text, term) for term in method_terms):
        work_signal = 60.0
    else:
        work_signal = 0.0
    completeness = _metadata_completeness(paper)

    components: dict[str, float | None] = {
        "research_relevance": float(paper.research_fit),
        "normalized_citation": normalized,
        "fwci": fwci_score,
        "citation_momentum": momentum,
        "seed_provenance": seed_score,
        "work_signal": work_signal,
        "metadata_completeness": completeness,
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
    coverage_factor = minimum_factor + (1 - minimum_factor) * (
        available_weight / max(1.0, total_weight)
    )
    paper.historical_value_score = round(
        max(0.0, min(100.0, weighted * coverage_factor)), 1
    )
    paper.historical_score_components = {
        key: (round(value, 1) if value is not None else None)
        for key, value in components.items()
    }

    reasons = [
        f"发表于 {paper.publication_year} 年" if paper.publication_year else "OpenAlex 未提供发表年份",
        normalized_reason,
    ]
    if paper.fwci is not None:
        reasons.append(f"OpenAlex FWCI {paper.fwci:.2f}")
    else:
        reasons.append("OpenAlex 未提供 FWCI，评分按可用指标降级")
    if momentum_reason:
        reasons.append(momentum_reason)
    if seeds:
        reasons.append(f"由 {seeds} 个独立种子论文的单层引用图发现")
    if paper.is_knowledge_map:
        reasons.append("标题、类型或主题元数据命中综述/教程/知识地图规则")
    elif work_signal:
        reasons.append("标题或摘要命中可配置的方法类信号")
    reasons.append(f"元数据完整度 {completeness:.0f}%")
    paper.historical_score_reasons = reasons
    return paper


def historical_relevance_eligible(
    paper: HistoricalPaper, profile: ResearchProfile
) -> tuple[bool, list[str]]:
    config = profile.historical_scoring
    recommendation = profile.recommendations
    core_topics = set(recommendation["core_topic_ids"])
    generic = {_normalise(term) for term in recommendation["generic_keywords"]}
    excluded = [_normalise(term) for term in recommendation["excluded_terms"]]
    strong_keywords = [
        keyword
        for keyword in paper.matched_keywords
        if _normalise(keyword) not in generic
    ]
    core_matches = [topic for topic in paper.matched_topics if topic in core_topics]
    text = _normalise(f"{paper.title} {paper.abstract or ''}")
    if any(term in text for term in excluded):
        return False, ["命中配置的非机器人主线排除词"]
    if paper.research_fit < int(config["min_research_fit"]):
        return False, [
            f"research_fit {paper.research_fit} < {int(config['min_research_fit'])}"
        ]
    if len(core_matches) < int(config["min_core_topic_matches"]):
        return False, ["未达到核心研究方向命中阈值"]
    if len(strong_keywords) < int(config["min_non_generic_keyword_matches"]):
        return False, ["仅有泛化关键词，未命中特定机器人方向词"]
    return True, [
        f"research_fit {paper.research_fit} ≥ {int(config['min_research_fit'])}",
        f"核心方向：{', '.join(core_matches)}",
        f"特定关键词：{', '.join(strong_keywords)}",
    ]


def score_historical_papers(
    papers: list[HistoricalPaper],
    profile: ResearchProfile,
    *,
    as_of_year: int | None = None,
) -> list[HistoricalPaper]:
    return sorted(
        [
            score_historical_paper(paper, profile, as_of_year=as_of_year)
            for paper in papers
        ],
        key=lambda paper: (
            paper.historical_value_score or 0,
            paper.research_fit,
            paper.publication_date or "",
        ),
        reverse=True,
    )
