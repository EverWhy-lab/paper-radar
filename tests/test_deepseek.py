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


def test_parses_english_guide_and_sends_bearer_token(profile) -> None:
    secret = "sk-test-123"
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = request.content
        return httpx.Response(
            200,
            json=deepseek_response(
                '{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"takeaway":"Introduces a whole-body control method for humanoid robots. '
                'Solves offset-free tracking on compliant arms and is directly usable in '
                'whole-body control research."}]}'
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
    assert "whole-body control" in analysis.takeaway
    assert analysis.model == profile.llm_analysis.model
    assert analysis.generated_at


def test_english_system_prompt_keeps_paper_terms(profile) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200,
            json=deepseek_response(
                '{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"takeaway":"Takeaway text."}]}'
            ),
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.analyze_recommendations([entry()])
    body = captured["body"].decode("utf-8")
    assert "Use professional terminology exactly as it appears in the paper" in body
    assert "model names" in body
    assert "classic" in body
    assert "write one coherent Takeaway in English" in body
    assert "the research background" in body
    assert "what the user can do with it" in body
    assert "do not write a mechanical list" in body


def test_parses_code_fenced_json(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=deepseek_response(
                '```json\n{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"takeaway":"Guide text."}]}\n```'
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
    assert results[0].takeaway == "Guide text."


def test_parses_json_embedded_in_prose(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=deepseek_response(
                'Sure, here is the guide:\n'
                '{"analyses":[{"paper_id":"arxiv:2608.00001",'
                '"takeaway":"Prose-wrapped takeaway."}]}\nHope that helps.'
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
    assert results[0].takeaway == "Prose-wrapped takeaway."


def test_parses_content_as_list_of_text_parts(profile) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                                "content": [
                                    {"type": "text", "text": '{"analyses":[{"paper_id":"arxiv:2608.00001",'},
                                    {"type": "text", "text": '"takeaway":"Parted takeaway."}]}'},
                                ]
                        }
                    }
                ]
            },
            request=request,
        )

    client = DeepSeekClient(
        profile.llm_analysis,
        environment={"DEEPSEEK_API_KEY": "secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = client.analyze_recommendations([entry()])
    assert len(results) == 1
    assert results[0].takeaway == "Parted takeaway."


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
                '"takeaway":"Unknown paper."}]}'
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
