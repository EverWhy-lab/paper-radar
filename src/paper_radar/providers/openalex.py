from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

import httpx

from paper_radar.config import OpenAlexConfig
from paper_radar.history_models import (
    HistoricalPaper,
    canonical_paper_id,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
)
from paper_radar.providers.base import HistoricalProviderError, ProviderStats
from paper_radar.storage import atomic_write_text


def _abstract_from_inverted_index(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned.append((position, str(word)))
    if not positioned:
        return None
    return " ".join(word for _, word in sorted(positioned))


def _percentile_value(value: Any) -> float | None:
    raw = value.get("value") if isinstance(value, dict) else value
    if raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number > 1:
        number /= 100
    return min(1.0, max(0.0, number))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_secret(item, secret)
            for key, item in value.items()
            if str(key).casefold() not in {"api_key", "apikey"}
        }
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def _extract_arxiv_id(work: dict[str, Any]) -> str | None:
    ids = work.get("ids") or {}
    value = ids.get("arxiv")
    if value:
        return normalize_arxiv_id(str(value))
    locations = list(work.get("locations") or [])
    for location in locations:
        for field in ("landing_page_url", "pdf_url"):
            url = str((location or {}).get(field) or "")
            if "arxiv.org/" in url:
                return normalize_arxiv_id(url)
    return None


def parse_openalex_work(
    work: dict[str, Any],
    *,
    discovered_at: str,
    discovery_source: str,
    seed_paper_id: str | None = None,
) -> HistoricalPaper:
    openalex_id = normalize_openalex_id(work.get("id"))
    doi = normalize_doi(work.get("doi") or (work.get("ids") or {}).get("doi"))
    base_arxiv_id = _extract_arxiv_id(work)
    canonical_id = canonical_paper_id(
        openalex_id=openalex_id, doi=doi, base_arxiv_id=base_arxiv_id
    )
    primary_location = work.get("primary_location") or {}
    best_oa = work.get("best_oa_location") or {}
    source = primary_location.get("source") or {}
    authors = [
        str((authorship.get("author") or {}).get("display_name"))
        for authorship in work.get("authorships") or []
        if (authorship.get("author") or {}).get("display_name")
    ]
    topics = [
        {
            "id": normalize_openalex_id(topic.get("id")) or str(topic.get("id") or ""),
            "display_name": str(topic.get("display_name") or ""),
            "score": _number(topic.get("score")),
        }
        for topic in work.get("topics") or []
        if topic.get("display_name")
    ]
    referenced = [
        normalized
        for value in work.get("referenced_works") or []
        if (normalized := normalize_openalex_id(str(value)))
    ]
    related = [
        normalized
        for value in work.get("related_works") or []
        if (normalized := normalize_openalex_id(str(value)))
    ]
    return HistoricalPaper(
        canonical_paper_id=canonical_id,
        openalex_id=openalex_id,
        base_arxiv_id=base_arxiv_id,
        doi=doi,
        title=str(work.get("title") or work.get("display_name") or ""),
        authors=authors,
        abstract=_abstract_from_inverted_index(work.get("abstract_inverted_index")),
        publication_date=(str(work["publication_date"]) if work.get("publication_date") else None),
        publication_year=_integer(work.get("publication_year")),
        source_name=(str(source["display_name"]) if source.get("display_name") else None),
        source_type=(str(work["type"]) if work.get("type") else None),
        topics=topics,
        cited_by_count=_integer(work.get("cited_by_count")),
        counts_by_year=list(work.get("counts_by_year") or []),
        fwci=_number(work.get("fwci")),
        citation_normalized_percentile=_percentile_value(
            work.get("citation_normalized_percentile")
        ),
        referenced_works=referenced,
        related_works=related,
        discovery_source=[discovery_source],
        seed_paper_id=[seed_paper_id] if seed_paper_id else [],
        discovered_at=discovered_at,
        last_considered_at=None,
        recommended_at=None,
        recommendation_count=0,
        reading_status="unread",
        dismissed=False,
        metadata_updated_at=discovered_at,
        openalex_url=(f"https://openalex.org/{openalex_id}" if openalex_id else None),
        landing_page_url=primary_location.get("landing_page_url"),
        open_access_url=best_oa.get("landing_page_url"),
        pdf_url=best_oa.get("pdf_url") or primary_location.get("pdf_url"),
    )


class OpenAlexProvider:
    def __init__(
        self,
        config: OpenAlexConfig,
        data_dir: Path,
        *,
        client: httpx.Client | None = None,
        environment: Mapping[str, str] | None = None,
        now: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.cache_dir = data_dir / "history" / "cache" / "openalex"
        self.stats_path = data_dir / "history" / "provider_stats.json"
        self._environment = environment if environment is not None else os.environ
        self._api_key = self._environment.get("OPENALEX_API_KEY")
        self._now = now
        self._sleep = sleep
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )
        self._run_request_count = 0
        self._run_cache_hits = 0
        self._stats = self._load_stats()

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def stats(self) -> ProviderStats:
        return ProviderStats(
            date=self._stats.date,
            request_count=self._stats.request_count,
            cache_hits=self._stats.cache_hits,
            remaining_call_budget=max(
                0, self.config.daily_request_budget - self._stats.request_count
            ),
            last_updated_at=self._stats.last_updated_at,
        )

    @property
    def run_request_count(self) -> int:
        return self._run_request_count

    @property
    def run_cache_hits(self) -> int:
        return self._run_cache_hits

    def _load_stats(self) -> ProviderStats:
        today = self._now().date().isoformat()
        if not self.stats_path.exists():
            return ProviderStats(today, 0, 0, self.config.daily_request_budget)
        try:
            value = json.loads(self.stats_path.read_text(encoding="utf-8"))
            if value.get("date") != today:
                return ProviderStats(today, 0, 0, self.config.daily_request_budget)
            return ProviderStats(
                date=today,
                request_count=int(value.get("request_count", 0)),
                cache_hits=int(value.get("cache_hits", 0)),
                remaining_call_budget=max(
                    0,
                    self.config.daily_request_budget
                    - int(value.get("request_count", 0)),
                ),
                last_updated_at=value.get("last_updated_at"),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return ProviderStats(today, 0, 0, self.config.daily_request_budget)

    def save_stats(self) -> None:
        self._stats.last_updated_at = self._now().isoformat(timespec="seconds")
        snapshot = self.stats
        atomic_write_text(
            self.stats_path,
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        safe_params = {key: value for key, value in params.items() if key != "api_key"}
        payload = json.dumps(
            {"path": path, "params": safe_params}, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _cached(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        current = self._now()
        cached_at = datetime.fromtimestamp(path.stat().st_mtime, tz=current.tzinfo)
        age = current - cached_at
        if age > timedelta(days=self.config.cache_ttl_days):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        cache_path = self._cache_path(path, params)
        cached = self._cached(cache_path)
        if cached is not None:
            self._run_cache_hits += 1
            self._stats.cache_hits += 1
            return cached
        if not self._api_key:
            raise HistoricalProviderError(
                "OPENALEX_API_KEY is not configured; set it in the environment before a live OpenAlex command."
            )
        if self._stats.request_count >= self.config.daily_request_budget:
            raise HistoricalProviderError("Configured OpenAlex daily request budget is exhausted.")

        last_status: int | None = None
        for attempt in range(self.config.retries):
            if self._stats.request_count >= self.config.daily_request_budget:
                raise HistoricalProviderError("Configured OpenAlex daily request budget is exhausted.")
            if attempt:
                self._sleep(self.config.retry_delay_seconds * (2**(attempt - 1)))
            try:
                request_params = dict(params)
                request_params["api_key"] = self._api_key
                response = self.client.get(f"{self.config.endpoint}{path}", params=request_params)
                self._stats.request_count += 1
                self._run_request_count += 1
                last_status = response.status_code
                if response.status_code == 429 or response.status_code >= 500:
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise HistoricalProviderError("OpenAlex returned an unexpected JSON shape.")
                payload = _redact_secret(payload, self._api_key)
                atomic_write_text(
                    cache_path,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
                return payload
            except HistoricalProviderError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
                continue
            except (json.JSONDecodeError, ValueError) as exc:
                raise HistoricalProviderError("OpenAlex returned invalid JSON.") from exc
        suffix = f" HTTP {last_status}" if last_status is not None else ""
        raise HistoricalProviderError(
            f"OpenAlex request failed after {self.config.retries} attempts.{suffix}"
        )

    def _results(
        self,
        payload: dict[str, Any],
        *,
        discovery_source: str,
        seed_paper_id: str | None = None,
    ) -> list[HistoricalPaper]:
        discovered_at = self._now().isoformat(timespec="seconds")
        return [
            parse_openalex_work(
                work,
                discovered_at=discovered_at,
                discovery_source=discovery_source,
                seed_paper_id=seed_paper_id,
            )
            for work in payload.get("results") or []
            if isinstance(work, dict) and work.get("id")
        ]

    def search_works(
        self,
        query: str,
        *,
        limit: int,
        publication_year_min: int,
        publication_year_max: int,
    ) -> list[HistoricalPaper]:
        payload = self._request(
            "/works",
            {
                "search": query,
                "filter": (
                    f"from_publication_date:{publication_year_min}-01-01,"
                    f"to_publication_date:{publication_year_max}-12-31"
                ),
                "per-page": min(200, max(1, limit)),
                "sort": "relevance_score:desc",
            },
        )
        return self._results(payload, discovery_source=f"topic_search:{query}")[:limit]

    def get_work(self, identifier: str) -> HistoricalPaper:
        openalex_id = normalize_openalex_id(identifier)
        lowered = identifier.casefold()
        is_doi = lowered.startswith(("10.", "doi:")) or "doi.org/" in lowered
        doi = normalize_doi(identifier) if is_doi else None
        arxiv_id = normalize_arxiv_id(identifier) if not (openalex_id or doi) else None
        if openalex_id:
            payload = self._request(f"/works/{openalex_id}")
            return parse_openalex_work(
                payload,
                discovered_at=self._now().isoformat(timespec="seconds"),
                discovery_source="seed_resolution",
            )
        if doi:
            payload = self._request(f"/works/{quote(f'https://doi.org/{doi}', safe='')}")
            return parse_openalex_work(
                payload,
                discovered_at=self._now().isoformat(timespec="seconds"),
                discovery_source="seed_resolution",
            )
        if not arxiv_id:
            raise HistoricalProviderError(f"Unsupported seed identifier: {identifier}")
        payload = self._request(
            "/works",
            {
                "filter": f"locations.landing_page_url:https://arxiv.org/abs/{arxiv_id}",
                "per-page": 1,
            },
        )
        works = self._results(payload, discovery_source="seed_resolution")
        if not works:
            raise HistoricalProviderError(f"OpenAlex did not resolve identifier: {identifier}")
        return works[0]

    def get_works_by_ids(
        self,
        openalex_ids: list[str],
        *,
        limit: int,
        discovery_source: str = "seed_graph",
        seed_paper_id: str | None = None,
    ) -> list[HistoricalPaper]:
        ids = [value for item in openalex_ids if (value := normalize_openalex_id(item))]
        if not ids or limit <= 0:
            return []
        payload = self._request(
            "/works",
            {
                "filter": f"openalex_id:{'|'.join(ids[: min(100, limit)])}",
                "per-page": min(100, limit),
            },
        )
        return self._results(
            payload,
            discovery_source=discovery_source,
            seed_paper_id=seed_paper_id,
        )[:limit]

    def get_citing_works(
        self,
        openalex_id: str,
        *,
        limit: int,
        seed_paper_id: str | None = None,
    ) -> list[HistoricalPaper]:
        normalized = normalize_openalex_id(openalex_id)
        if not normalized or limit <= 0:
            return []
        payload = self._request(
            "/works",
            {
                "filter": f"cites:{normalized}",
                "per-page": min(200, limit),
                "sort": "cited_by_count:desc",
            },
        )
        return self._results(
            payload,
            discovery_source="seed_graph:citing_works",
            seed_paper_id=seed_paper_id,
        )[:limit]
