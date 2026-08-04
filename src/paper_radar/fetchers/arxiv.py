from __future__ import annotations

import email.utils
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from paper_radar.config import FetchConfig
from paper_radar.models import Paper


ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"


class ArxivFetchError(RuntimeError):
    """A friendly wrapper for arXiv API failures."""


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return " ".join(element.text.split())


def split_arxiv_id(value: str) -> tuple[str, int]:
    parsed = urlparse(value)
    identifier = parsed.path.split("/abs/", 1)[-1].strip("/") if parsed.scheme else value
    match = re.fullmatch(r"(.+?)(?:v(\d+))?", identifier)
    if not match:
        return identifier, 1
    return match.group(1), int(match.group(2) or 1)


def parse_atom(xml_text: str) -> list[Paper]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ArxivFetchError(f"arXiv returned invalid Atom XML: {exc}") from exc

    papers: list[Paper] = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        raw_id = _text(entry.find(f"{{{ATOM}}}id"))
        base_id, version = split_arxiv_id(raw_id)
        arxiv_id = f"{base_id}v{version}"
        links = {
            link.attrib.get("rel", "alternate"): link.attrib.get("href", "")
            for link in entry.findall(f"{{{ATOM}}}link")
        }
        pdf_url = next(
            (
                link.attrib.get("href", "")
                for link in entry.findall(f"{{{ATOM}}}link")
                if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf"
            ),
            f"https://arxiv.org/pdf/{base_id}",
        )
        categories = [
            category.attrib["term"]
            for category in entry.findall(f"{{{ATOM}}}category")
            if category.attrib.get("term")
        ]
        primary = entry.find(f"{{{ARXIV}}}primary_category")
        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                base_id=base_id,
                version=version,
                title=_text(entry.find(f"{{{ATOM}}}title")),
                authors=[
                    _text(author.find(f"{{{ATOM}}}name"))
                    for author in entry.findall(f"{{{ATOM}}}author")
                ],
                published=_text(entry.find(f"{{{ATOM}}}published")),
                updated=_text(entry.find(f"{{{ATOM}}}updated")),
                primary_category=(primary.attrib.get("term", "") if primary is not None else ""),
                categories=categories,
                summary=_text(entry.find(f"{{{ATOM}}}summary")),
                abs_url=links.get("alternate", f"https://arxiv.org/abs/{base_id}"),
                pdf_url=pdf_url,
            )
        )
    return papers


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    newest: dict[str, Paper] = {}
    for paper in papers:
        current = newest.get(paper.base_id)
        if current is None or (paper.version, paper.updated) > (current.version, current.updated):
            newest[paper.base_id] = paper
    return sorted(newest.values(), key=lambda paper: (paper.updated, paper.base_id), reverse=True)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ArxivClient:
    def __init__(
        self,
        config: FetchConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )
        self.sleep = sleep
        self._request_count = 0

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None and response.headers.get("Retry-After"):
            value = response.headers["Retry-After"]
            try:
                seconds = float(value)
            except ValueError:
                try:
                    parsed = email.utils.parsedate_to_datetime(value)
                    seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError):
                    seconds = 0.0
            if seconds <= 0:
                seconds = self.config.page_delay_seconds
            # Honor the server's instruction, but never let one wait exceed two
            # minutes inside a single request so the job can fail fast and let
            # the workflow-level retry handle longer arXiv throttling windows.
            return min(seconds, 120.0)
        return self.config.page_delay_seconds * (2**attempt)

    def _get(self, params: dict[str, str | int]) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.retries):
            if self._request_count:
                self.sleep(self.config.page_delay_seconds)
            self._request_count += 1
            response: httpx.Response | None = None
            try:
                response = self.client.get(self.config.endpoint, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"arXiv API returned HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.text
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable:
                    break
                if attempt + 1 < self.config.retries:
                    self.sleep(self._retry_delay(response, attempt))
        detail = str(last_error) if last_error else "unknown network error"
        raise ArxivFetchError(
            f"Unable to fetch arXiv metadata after {self.config.retries} attempts: {detail}"
        ) from last_error

    def _fetch_pages(
        self,
        search_query: str,
        *,
        sort_by: str,
        stop_before: datetime | None = None,
    ) -> list[Paper]:
        collected: list[Paper] = []
        for page in range(self.config.max_pages):
            xml_text = self._get(
                {
                    "search_query": search_query,
                    "start": page * self.config.page_size,
                    "max_results": self.config.page_size,
                    "sortBy": sort_by,
                    "sortOrder": "descending",
                }
            )
            batch = parse_atom(xml_text)
            collected.extend(batch)
            if len(batch) < self.config.page_size:
                break
            if stop_before is not None and batch:
                oldest = min(_parse_datetime(paper.updated) for paper in batch)
                if oldest < stop_before:
                    break
        return collected

    def fetch_for_date(self, target_date: date, timezone_name: str, categories: list[str]) -> list[Paper]:
        local_zone = ZoneInfo(timezone_name)
        start_local = datetime.combine(target_date, datetime_time.min, tzinfo=local_zone)
        end_local = start_local + timedelta(days=1)
        return self.fetch_for_window(start_local, end_local, categories)

    def fetch_by_id(self, arxiv_id: str) -> Paper:
        base_id, _ = split_arxiv_id(arxiv_id)
        if not base_id or any(character.isspace() for character in base_id):
            raise ArxivFetchError(f"Invalid arXiv ID: {arxiv_id}")
        papers = parse_atom(self._get({"id_list": base_id, "max_results": 1}))
        paper = next((item for item in papers if item.base_id == base_id), None)
        if paper is None:
            raise ArxivFetchError(f"arXiv ID was not found: {base_id}")
        return paper

    def fetch_recent(
        self,
        now: datetime,
        lookback_days: int,
        timezone_name: str,
        categories: list[str],
    ) -> list[Paper]:
        local_zone = ZoneInfo(timezone_name)
        local_now = now.astimezone(local_zone)
        return self.fetch_for_window(local_now - timedelta(days=lookback_days), local_now, categories)

    def fetch_for_window(
        self,
        start: datetime,
        end: datetime,
        categories: list[str],
    ) -> list[Paper]:
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        inclusive_end = end_utc - timedelta(minutes=1)

        category_query = " OR ".join(f"cat:{category}" for category in categories)
        date_range = f"[{start_utc:%Y%m%d%H%M} TO {inclusive_end:%Y%m%d%H%M}]"
        submitted = self._fetch_pages(
            f"({category_query}) AND submittedDate:{date_range}",
            sort_by="submittedDate",
        )
        recently_updated = self._fetch_pages(
            f"({category_query})",
            sort_by="lastUpdatedDate",
            stop_before=start_utc,
        )
        updates_in_window = [
            paper
            for paper in recently_updated
            if start_utc <= _parse_datetime(paper.updated) < end_utc
        ]
        return deduplicate_papers(submitted + updates_in_window)
