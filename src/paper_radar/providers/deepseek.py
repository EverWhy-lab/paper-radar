from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable, Mapping

import httpx

from paper_radar.config import LLMAnalysisConfig
from paper_radar.reader_models import LLMAnalysis, RecommendationEntry


class LLMAnalysisError(RuntimeError):
    """Raised when the LLM provider cannot safely complete a request."""


def _system_prompt(language: str) -> str:
    if language == "zh":
        return (
            "你是一位机器人研究方向（腿部机器人、移动操作、最优控制、机器人学习等）的个人阅读助手。"
            "你只会收到规则引擎已经选出的 0-5 篇论文。对每篇论文用简体中文写一段 3-5 句话的 Takeaway，"
            "分析这篇论文对用户的价值，必须覆盖两个方面：(1) 论文本身：在什么研究背景下解决什么问题、"
            "取得了什么关键成果；(2) 对用户的意义：这篇论文和用户的研究方向（腿部机器人、移动操作、"
            "最优控制、机器人学习等）有什么关系、用户能拿它来做什么或在它基础上做什么。"
            "两方面的内容都要有，但写成一段连贯文字，不要机械罗列条目。"
            "只输出一个 JSON 对象，不要输出任何其他文字："
            '{"analyses":[{"paper_id":"...","takeaway":"..."}]}。'
            "要求客观中立，不要使用“经典”“最佳”“最重要”等绝对化评价；引用数和评分只是筛选信号，不是质量标签。"
        )
    return (
        "You are a personal reading assistant for robotics research (legged robots, "
        "mobile manipulation, optimal control, robot learning, and related fields). "
        "You only receive 0-5 papers already selected by a rule engine. For each paper, "
        "write one coherent Takeaway in English (3-5 sentences) analyzing the paper's value to "
        "the user, covering two aspects: (1) the paper itself: the research background, the "
        "problem it solves, and the key results it reports; (2) its meaning to the user: how it "
        "relates to the user's robotics research (legged robots, mobile manipulation, optimal "
        "control, robot learning) and what the user can do with it or build on it. "
        "Cover both aspects in one flowing paragraph; do not write a mechanical list. "
        "Use professional terminology exactly as it appears in the paper: keep model names, "
        "method names, acronyms, and technical vocabulary unchanged. "
        "Output only one JSON object with no other text: "
        '{"analyses":[{"paper_id":"...","takeaway":"..."}]}. '
        "Be objective and neutral; never use absolute labels such as \"classic\", \"best\", or "
        "\"most important\"; citation counts and scores are screening signals, not quality labels."
    )


def _abstract_for(entry: RecommendationEntry, limit: int = 1200) -> str:
    if entry.historical_paper is not None:
        text = entry.historical_paper.abstract or ""
    else:
        text = entry.paper.summary or ""
    return text[:limit]


def _strip_code_fences(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    return stripped


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = _strip_code_fences(text)
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        value = json.loads(stripped[start : end + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class DeepSeekClient:
    def __init__(
        self,
        config: LLMAnalysisConfig,
        *,
        client: httpx.Client | None = None,
        environment: Mapping[str, str] | None = None,
        now: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._environment = environment if environment is not None else os.environ
        self._api_key = self._environment.get("DEEPSEEK_API_KEY")
        self._now = now
        self._sleep = sleep
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds),
            headers=headers,
        )
        self._request_count = 0

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def run_request_count(self) -> int:
        return self._request_count

    def _build_payload(self, recommendations: list[RecommendationEntry]) -> dict[str, Any]:
        papers: list[dict[str, Any]] = []
        for entry in recommendations:
            historical = entry.historical_paper
            year = None
            if historical is not None:
                year = historical.publication_year
            elif entry.paper.published:
                year = int(entry.paper.published[:4])
            papers.append(
                {
                    "paper_id": entry.canonical_paper_id,
                    "title": entry.paper.title,
                    "authors": entry.paper.authors[:8],
                    "year": year,
                    "topics": entry.paper.matched_topics,
                    "research_fit": entry.paper.research_fit,
                    "historical_value_score": (
                        round(historical.historical_value_score, 1)
                        if historical is not None
                        and historical.historical_value_score is not None
                        else None
                    ),
                    "abstract": _abstract_for(entry),
                }
            )
        user_content = (
            "以下是今天规则引擎选出的论文（最多 5 篇）：\n"
            + json.dumps(papers, ensure_ascii=False, indent=2)
            + "\n请按系统要求输出导读 JSON。"
        )
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _system_prompt(self.config.language)},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": self.config.max_tokens,
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.config.retries):
            self._request_count += 1
            try:
                headers = {"Content-Type": "application/json"}
                if self._api_key:
                    headers["Authorization"] = f"Bearer {self._api_key}"
                response = self.client.post(
                    self.config.endpoint, json=payload, headers=headers
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"DeepSeek API returned HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                if attempt + 1 < self.config.retries:
                    self._sleep(self.config.retry_delay_seconds * (2**attempt))
        raise LLMAnalysisError(
            f"DeepSeek API unavailable after {self.config.retries} attempts: {last_error}"
        ) from last_error

    def _parse(
        self,
        payload: dict[str, Any],
        recommendations: list[RecommendationEntry],
    ) -> list[LLMAnalysis]:
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                )
            data = _extract_json_object(str(content or ""))
            if data is None:
                return []
            by_id = {entry.canonical_paper_id: entry for entry in recommendations}
            by_id_folded = {key.casefold(): entry for key, entry in by_id.items()}
            analyses = data.get("analyses")
            if analyses is None and isinstance(data, dict):
                return []
            results: list[LLMAnalysis] = []
            for item in analyses:
                paper_id = str(item.get("paper_id", ""))
                entry = by_id.get(paper_id) or by_id_folded.get(paper_id.casefold())
                if entry is None:
                    continue
                takeaway = str(item.get("takeaway", "")).strip()
                if not takeaway:
                    parts = [
                        str(item.get("summary", "")).strip(),
                        str(item.get("why_relevant", "")).strip(),
                        str(item.get("one_line_verdict", "")).strip(),
                    ]
                    takeaway = " ".join(part for part in parts if part)
                if not takeaway:
                    continue
                results.append(
                    LLMAnalysis(
                        canonical_paper_id=paper_id,
                        title=entry.paper.title,
                        takeaway=takeaway,
                        generated_at=self._now().isoformat(timespec="seconds"),
                        model=self.config.model,
                    )
                )
            return results
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def analyze_recommendations(
        self, recommendations: list[RecommendationEntry]
    ) -> list[LLMAnalysis]:
        if not self.config.enabled or not self._api_key or not recommendations:
            return []
        payload = self._build_payload(recommendations)
        response = self._post(payload)
        results = self._parse(response, recommendations)
        if not results:
            message = (response.get("choices") or [{}])[0].get("message") or {}
            content = message.get("content") or ""
            print(
                "DeepSeek guide: API call succeeded but no analyses were parsed; "
                f"raw response head: {str(content)[:400]!r}",
                file=sys.stderr,
            )
        return results
