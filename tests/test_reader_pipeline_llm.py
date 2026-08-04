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
                summary="提出一种面向人形机器人的全身控制方法。",
                why_relevant="与腿部人形和最优控制方向直接相关。",
                one_line_verdict="方法新颖，值得一读。",
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
    assert recommendation["llm_analysis"][0]["summary"].startswith("提出")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "今日导读" in index
    assert "DeepSeek 生成" in index
    assert "Whole-Body Control for Agile Humanoid Robots" in index


def test_reader_run_without_provider_has_no_guide(tmp_path: Path, profile) -> None:
    result = _run(tmp_path, profile, llm_provider=None)

    assert result.recommendation_count == 1
    assert result.llm_analysis_count == 0
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "今日导读" not in index


def test_failing_llm_provider_never_blocks_the_page(tmp_path: Path, profile) -> None:
    provider = FakeLLMProvider([_paper()], fail=True)
    result = _run(tmp_path, profile, llm_provider=provider)

    assert result.recommendation_count == 1
    assert result.llm_analysis_count == 0
    assert provider.calls == 1
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "今日精选论文" in index
    assert "今日导读" not in index


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
                                '"summary":"提出一种全身控制方法。","why_relevant":"与方向相关。",'
                                '"one_line_verdict":"值得一读。"}]}'
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
        summary="导读。",
        why_relevant="相关。",
        one_line_verdict="可读。",
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
    assert restored.llm_analysis[0].summary == "导读。"
    assert restored.llm_analysis[0].canonical_paper_id == "arxiv:2608.00001"
    assert restored.schema_version == 3
