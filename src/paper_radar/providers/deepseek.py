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


def _reader_profile_text(reader_profile: dict[str, list[str]]) -> str:
    primary = "; ".join(reader_profile.get("primary_focus", []))
    secondary = "; ".join(reader_profile.get("secondary_focus", []))
    return f"Primary focus: {primary}. Secondary focus: {secondary}."


def _system_prompt(language: str, reader_profile: dict[str, list[str]]) -> str:
    profile_text = _reader_profile_text(reader_profile)
    if language == "zh":
        return (
            "你是机器人研究者的阅读导读助手。规则引擎已经完成选稿；你不参与筛选、排序或论文质量判定。"
            f"读者画像：{profile_text} "
            "只能依据每篇论文提供的 title、abstract 和 metadata。不得补充摘要没有给出的实验数字，不得猜测数据集、"
            "模型规模、训练资源或硬件，不得把作者 claim 写成已经被独立验证的事实，也不得因为论文被选中或内部评分高就断言质量高。"
            "不得使用“经典”“最佳”“奠基性”“最重要”等评价；只有原文明示时才可写“可直接应用”。摘要没有定量结果时，"
            "只准确转述作者公开的 claim。技术名称、model names 和 acronyms 保持论文原文形式。"
            "每篇写一个约 120–220 个汉字的连贯段落，通常 3 句话，最多 4 句话，不要编号或列清单。"
            "method paper 按 problem→key method→摘要明确报告的 evidence/result→通读时应重点检查的技术点组织；"
            "survey/review 按 scope→taxonomy/synthesis→coverage→作为知识地图的用途组织，绝不能写成提出新方法；"
            "benchmark/dataset 按 measured target→evaluation/data novelty→enabled research question 组织。"
            "避免“具有重要意义”“具有很高参考价值”“未来可广泛扩展”等空泛措辞。"
            "只输出一个 JSON 对象，不要输出任何其他文字："
            '{"analyses":[{"paper_id":"...","takeaway":"..."}]}。'
        )
    return (
        "You are a reading-guide assistant for a robotics researcher. A rule engine has already "
        "selected these papers; you do not screen, rank, or judge paper quality. "
        f"Reader profile: {profile_text} "
        "Use only the supplied title, abstract, and metadata. Never add experimental numbers that "
        "the abstract does not report; never guess datasets, model scale, training resources, or "
        "hardware; and never present an author claim as independently validated fact. Do not infer "
        "quality from selection or an internal score. Never use classic, best, seminal, or most "
        "important. Say directly applicable only when the source explicitly supports it. If the "
        "abstract has no quantitative result, accurately describe its stated claim instead. Keep "
        "technical names, model names, method names, and acronyms exactly as written in the paper. "
        "For each paper, write one coherent 80-130-word English paragraph, usually three sentences "
        "and no more than four; do not number points or write a list. For a method paper, cover the "
        "problem, key method, explicitly reported evidence or result, and the technical point to "
        "inspect while reading. For a survey or review, cover scope, taxonomy or synthesis, "
        "coverage, and its use as a knowledge map; never describe it as proposing a new method. For "
        "a benchmark or dataset, cover what is measured, what is new in the evaluation or data, and "
        "the research question it enables. Avoid generic praise such as important significance, "
        "high reference value, broad future extensions, or similar unsupported language. "
        "Output only one JSON object with no other text: "
        '{"analyses":[{"paper_id":"...","takeaway":"..."}]}.'
    )


def _abstract_for(entry: RecommendationEntry, limit: int = 3000) -> str:
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
                    "publication_year": year,
                    "selection_category": entry.category,
                    "core_topics": entry.core_topics,
                    "subtopics": entry.subtopics,
                    "document_type": entry.document_type,
                    "domain_affinity": entry.domain_affinity,
                    "abstract": _abstract_for(
                        entry, limit=self.config.abstract_char_limit
                    ),
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
                {
                    "role": "system",
                    "content": _system_prompt(
                        self.config.language, self.config.reader_profile
                    ),
                },
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
