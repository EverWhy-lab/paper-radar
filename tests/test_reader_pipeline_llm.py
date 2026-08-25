from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from paper_radar.models import Paper
from paper_radar.providers.deepseek import DeepSeekClient, LLMAnalysisError
from paper_radar.reader_models import DailyRecommendations, LLMAnalysis, RecommendationEntry
from paper_radar.reader_pipeline import execute_reader_historical_run


def _paper(index: int = 1) -> Paper:
    base = f"2608.{index:05d}"
    return Paper(
        arxiv_id=f"{base}v1",
        base_id=base,
        version=1,
        title=f"Whole-Body Control for Agile Humanoid Robots {index}",
        authors=["Ada Researcher"],
        published="2026-08-04T01:00:00Z",
        updated="2026-08-04T01:00:00Z",
        primary_category="cs.RO",
        categories=["cs.RO"],
        summary="A model predictive control method for humanoid locomotion and mobile manipulation.",
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
    )


class FakeFetcher:
    def __init__(self, papers: list[Paper]) -> None:
        self.papers = papers

    def fetch_for_date(self, target_date, timezone_name, categories):
        return self.papers


class FakeLLMProvider:
    def __init__(self, papers: list[Paper], *, fail: bool = False) -> None:
        self.papers = papers
        self.fail = fail
        self.calls = 0

    def analyze_recommendations(self, recommendations: list[RecommendationEntry]) -> list[LLMAnalysis]:
        self.calls += 1
        if self.fail:
            raise LLMAnalysisError("fixture failure")
        return [
            LLMAnalysis(
                canonical_paper_id=entry.canonical_paper_id,
                title=entry.paper.title,
                takeaway=(
                    "Introduces a whole-body control method for humanoid robots. "
                    "Solves offset-free tracking and is directly usable in whole-body "
                    "control research."
                ),
                generated_at="2026-08-04T10:30:00+08:00",
                model="deepseek-chat",
            )
            for entry in recommendations
        ]


def _run(tmp_path: Path, profile, *, llm_provider=None, papers=None):
    now = datetime(2026, 8, 4, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    return execute_reader_historical_run(
        tmp_path,
        date(2026, 8, 4),
        profile=profile,
        fetcher=FakeFetcher(papers or [_paper()]),
        now=now,
        llm_provider=llm_provider,
    )


def test_reader_run_generates_guide_and_renders_section(tmp_path: Path, profile) -> None:
    result = _run(tmp_path, profile, llm_provider=FakeLLMProvider([_paper()]))

    assert result.recommendation_count == 1
    assert result.llm_analysis_count == 1
    recommendation = json.loads(
        (tmp_path / "data" / "recommendations" / "2026-08-04.json").read_text(encoding="utf-8")
    )
    assert recommendation["llm_analysis"][0]["takeaway"].startswith("Introduces")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Today&#39;s Guide" not in index
    assert "Takeaway" in index
    assert "Importance" not in index
    assert "<summary>Why selected</summary>" in index
    assert "<details class=\"selection-diagnostics\">" in index
    assert "DeepSeek" not in index
    assert "Why it matters" not in index
    assert index.count("Whole-Body Control for Agile Humanoid Robots") >= 1

    metadata_start = index.index('<div class="metadata">')
    metadata_end = index.index("</div>", metadata_start)
    assert "Utility" not in index[metadata_start:metadata_end]
    diagnostics_start = index.index('<details class="selection-diagnostics">')
    diagnostics_end = index.index("</details>", diagnostics_start)
    diagnostics = index[diagnostics_start:diagnostics_end]
    assert "Utility" in diagnostics
    assert "Domain affinity" in diagnostics
    assert "Core topics" in diagnostics
    assert "Original abstract" in index
    assert "Save" in index
    assert "Not Relevant" in index

    css = (tmp_path / "site" / "assets" / "reader.css").read_text(encoding="utf-8")
    assert ".navigation-row { display: flex; min-height: 72px;" in css
    assert "margin: 0; padding-block: 18px;" in css
    assert ".category--model_based_recent" in css
    assert ".category--rising_recent" in css


def test_reader_run_without_provider_has_no_guide(tmp_path: Path, profile) -> None:
    result = _run(tmp_path, profile, llm_provider=None)

    assert result.recommendation_count == 1
    assert result.llm_analysis_count == 0
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Takeaway" not in index


def test_failing_llm_provider_never_blocks_the_page(tmp_path: Path, profile) -> None:
    provider = FakeLLMProvider([_paper()], fail=True)
    result = _run(tmp_path, profile, llm_provider=provider)

    assert result.recommendation_count == 1
    assert result.llm_analysis_count == 0
    assert provider.calls == 1
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'class="paper-card"' in index
    assert ">Papers</h2>" not in index
    assert "Takeaway" not in index


def test_real_client_key_never_lands_in_data_files(tmp_path: Path, profile) -> None:
    secret = "sk-secret-abc-123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                                "content": (
                                    '{"analyses":[{"paper_id":"arxiv:2608.00001",'
                                    '"takeaway":"Introduces a whole-body control method. '
                                    'Solves offset-free tracking for legged platforms."}]}'
                                )
                        }
                    }
                ]
            },
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": secret},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = _run(tmp_path, profile, llm_provider=client)

    assert result.llm_analysis_count == 1
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file() and not path.name.endswith((".pyc", ".py")):
            assert secret not in path.read_text(encoding="utf-8", errors="ignore")


def test_daily_recommendations_round_trip_preserves_guide(profile) -> None:
    entry = RecommendationEntry(
        category="frontier_recent",
        paper=_paper(),
        reasons=["fixture"],
    )
    analysis = LLMAnalysis(
        canonical_paper_id="arxiv:2608.00001",
        title="Whole-Body Control for Agile Humanoid Robots 1",
        takeaway="Guide text for the paper.",
        generated_at="2026-08-04T10:30:00+08:00",
        model="deepseek-chat",
    )
    daily = DailyRecommendations(
        date="2026-08-04",
        generated_at="2026-08-04T10:30:00+08:00",
        recommendations=[entry],
        candidate_count=1,
        mode="fixture",
        selection_config=profile.recommendations["daily_mix"],
        llm_analysis=[analysis],
    )
    restored = DailyRecommendations.from_dict(daily.to_dict())

    assert restored.llm_analysis is not None
    assert restored.llm_analysis[0].takeaway == "Guide text for the paper."
    assert restored.llm_analysis[0].canonical_paper_id == "arxiv:2608.00001"
    assert restored.schema_version == 4


def test_old_three_part_guide_is_merged_into_takeaway() -> None:
    restored = LLMAnalysis.from_dict(
        {
            "canonical_paper_id": "arxiv:2608.00001",
            "title": "Paper",
            "summary": "Does X.",
            "why_relevant": "Relevant to Y.",
            "one_line_verdict": "Worth reading.",
            "generated_at": "2026-08-04T10:30:00+08:00",
            "model": "deepseek-chat",
        }
    )

    assert restored.takeaway == "Does X. Relevant to Y. Worth reading."


def test_history_page_links_all_dates_after_rerender(tmp_path: Path, profile) -> None:
    from paper_radar.reader_rendering import RecommendationSiteRenderer
    from paper_radar.reader_storage import RecommendationStorage

    storage = RecommendationStorage(tmp_path / "data")
    config = profile.recommendations["daily_mix"]
    storage.save(
        DailyRecommendations(
            date="2026-08-03",
            generated_at="2026-08-03T10:30:00+08:00",
            recommendations=[
                RecommendationEntry(category="frontier_recent", paper=_paper(3))
            ],
            candidate_count=1,
            mode="fixture",
            selection_config=config,
        )
    )
    storage.save(
        DailyRecommendations(
            date="2026-08-04",
            generated_at="2026-08-04T10:30:00+08:00",
            recommendations=[
                RecommendationEntry(category="frontier_recent", paper=_paper(4))
            ],
            candidate_count=1,
            mode="fixture",
            selection_config=config,
        )
    )
    RecommendationSiteRenderer(tmp_path / "site", storage, profile).render("2026-08-04")
    old_page = (tmp_path / "site" / "recommendations" / "2026-08-03.html").read_text(
        encoding="utf-8"
    )
    # Per-day pages no longer embed the date list; the Archive page links all dates.
    assert 'href="2026-08-04.html"' not in old_page
    archive = (tmp_path / "site" / "history.html").read_text(encoding="utf-8")
    assert 'href="recommendations/2026-08-04.html"' in archive
    assert 'href="recommendations/2026-08-03.html"' in archive


def test_history_page_lists_dates_newest_first(tmp_path: Path, profile) -> None:
    from paper_radar.reader_rendering import RecommendationSiteRenderer
    from paper_radar.reader_storage import RecommendationStorage

    storage = RecommendationStorage(tmp_path / "data")
    config = profile.recommendations["daily_mix"]
    for day, index in (("2026-08-03", 3), ("2026-08-05", 5)):
        storage.save(
            DailyRecommendations(
                date=day,
                generated_at=f"{day}T10:30:00+08:00",
                recommendations=[
                    RecommendationEntry(
                        category="frontier_recent", paper=_paper(index)
                    )
                ],
                candidate_count=1,
                mode="fixture",
                selection_config=config,
            )
        )
    RecommendationSiteRenderer(tmp_path / "site", storage, profile).render(
        "2026-08-05"
    )

    archive = (tmp_path / "site" / "history.html").read_text(encoding="utf-8")
    assert archive.index("2026-08-05") < archive.index("2026-08-03")
    assert "Whole-Body Control for Agile Humanoid Robots 5" in archive
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'href="history.html"' in index
    assert "history-menu" not in index
