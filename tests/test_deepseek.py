from __future__ import annotations

import httpx
import pytest

from paper_radar.models import Paper
from paper_radar.providers.deepseek import DeepSeekClient, LLMAnalysisError
from paper_radar.reader_models import RecommendationEntry


def paper(index: int = 1) -> Paper:
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
        research_fit=71,
        video_potential=30,
        matched_topics=["legged_humanoid", "optimal_control"],
        matched_keywords=["humanoid robots", "whole-body control"],
    )


def entry(index: int = 1) -> RecommendationEntry:
    return RecommendationEntry(
        category="frontier_recent",
        paper=paper(index),
        reasons=["research_fit 71 >= 40"],
    )


def deepseek_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_no_api_key_makes_no_request(profile) -> None:
    client = DeepSeekClient(profile.llm_analysis, environment={})
    assert client.analyze_recommendations([entry()]) == []
    assert client.run_request_count == 0


def test_disabled_config_makes_no_request(profile) -> None:
    config = profile.llm_analysis
    disabled = type(config)(
        enabled=False,
        provider=config.provider,
        endpoint=config.endpoint,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        retries=config.retries,
        retry_delay_seconds=config.retry_delay_seconds,
        max_tokens=config.max_tokens,
        language=config.language,
    )
    client = DeepSeekClient(
        disabled,
        environment={"DEEPSEEK_API_KEY": "secret"},
    )
    assert client.analyze_recommendations([entry()]) == []
    assert client.run_request_count == 0


def test_parses_chinese_guide_and_sends_bearer_token(profile) -> None:
    secret = "sk-test-123"
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = request.content
        return httpx.Response(
            200,
            json=deepseek_response(
                '{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"summary":"提出一种面向人形机器人的全身控制方法。",'
                '"why_relevant":"与腿部人形和最优控制方向直接相关。",'
                '"one_line_verdict":"方法新颖，值得一读。"}]}'
            ),
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": secret},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = client.analyze_recommendations([entry()])

    assert captured["authorization"] == f"Bearer {secret}"
    assert secret.encode() not in captured["body"]
    assert len(results) == 1
    analysis = results[0]
    assert analysis.canonical_paper_id == "arxiv:2608.00001"
    assert "全身控制" in analysis.summary
    assert analysis.why_relevant
    assert analysis.one_line_verdict
    assert analysis.model == profile.llm_analysis.model
    assert analysis.generated_at


def test_parses_code_fenced_json(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=deepseek_response(
                '```json\n{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"summary":"导读内容。","why_relevant":"相关。","one_line_verdict":"可读。"}]}\n```'
            ),
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = client.analyze_recommendations([entry()])
    assert len(results) == 1
    assert results[0].summary == "导读内容。"


def test_malformed_json_returns_empty(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=deepseek_response("I am not JSON"),
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.analyze_recommendations([entry()]) == []


def test_unknown_paper_id_is_ignored(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=deepseek_response(
                '{"analyses":[{"paper_id":"arxiv:9999.99999",'
                '"summary":"不存在的论文。","why_relevant":"","one_line_verdict":""}]}'
            ),
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.analyze_recommendations([entry()]) == []


def test_http_error_raises_after_retries(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(LLMAnalysisError):
        client.analyze_recommendations([entry()])
