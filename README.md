# EverWhy Paper Radar

EverWhy Paper Radar is a local personal reading selector for robotics and embodied-intelligence papers. It scans arXiv metadata in the background, but shows at most five papers per day. Selection uses configurable keyword rules—never an LLM—and every recommendation includes its rule-derived reasons.

## Install

Requirements: Python 3.11 and network access to the official arXiv API for live runs.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

## Daily run

Normal incremental run:

```bash
.venv/bin/python -m paper_radar run
```

This scans the configured seven-day window, compares results with version state, stores candidate metadata, and generates a shortlist of zero to five papers. It never lowers thresholds to fill the page.

Exact-day historical run:

```bash
.venv/bin/python -m paper_radar run --date 2026-08-03
```

The `--date` form keeps Asia/Shanghai natural-day semantics for deterministic backfill. arXiv normally announces papers Sunday through Thursday evening in US Eastern time—usually the following morning around 08:00 or 09:00 in Shanghai. Weekends, holidays, or an early run can produce no new papers. A future scheduler should run around **10:15 Asia/Shanghai**; V0.1.2 does not include deployment or GitHub Actions.

## Three separate data layers

1. **Candidate metadata** — `data/candidates/YYYY-MM-DD.json`
   - Full background metadata for scoring, version tracking, and audits.
   - Never rendered as an all-paper user page.
2. **Reading pool** — `data/reading_pool.json`
   - Only papers explicitly added by the user.
   - Stores status, priority, dismissal, consideration, and recommendation history.
3. **Daily recommendations** — `data/recommendations/YYYY-MM-DD.json`
   - Only the zero to five papers actually selected for the user page.

Version and discovery state remains in `data/seen_ids.json`. Legacy V0.1.1 files under `data/daily/` and the 485-paper `site/archive/2026-07-31.html` audit sample are retained but are not part of the V0.1.2 homepage flow.

## Recommendation policy

All limits and thresholds live in `config/research_profile.yaml`:

- Total: at most 5
- Recent new papers: at most 3, `research_fit ≥ 40`
- Manual reading-pool papers: at most 2
- Important version updates: at most 1, `research_fit ≥ 60`
- Recent and update candidates must match a core topic and at least one non-generic keyword
- Generic learning, agent, planning, or control terms cannot qualify alone
- Excluded off-topic terms, topic diversity, dismissal, `read` status, and cooldowns are enforced
- Selection order is recent papers, important updates, then reading-pool papers within the remaining total capacity

An empty day is valid and displays: “今日没有发现足够值得推荐的论文。” The page may link to the previous non-empty recommendation but never substitutes it as today's content.

## Reading pool

Add a paper using its arXiv ID. Metadata is fetched from the official API:

```bash
.venv/bin/python -m paper_radar pool add 2401.01234
.venv/bin/python -m paper_radar pool list
.venv/bin/python -m paper_radar pool status 2401.01234 queued
.venv/bin/python -m paper_radar pool dismiss 2401.01234
```

Valid statuses are `unread`, `queued`, `reading`, and `read`. Dismissed and read papers are not recommended. The reading pool makes no claim that a paper is classic, highly cited, or objectively high quality.

## View

```bash
.venv/bin/python -m paper_radar serve
```

Open <http://127.0.0.1:8000/>. Recommendation archives are under `site/recommendations/`.

Fixture demonstrations:

- `site/demo/recommendations-5.html`
- `site/demo/recommendations-partial.html`
- `site/demo/recommendations-0.html`

They are visibly labelled fixture pages and use stored arXiv metadata.

## Test

```bash
.venv/bin/python -m pytest
```

Tests use local Atom fixtures and fake fetchers only. Network failure tests verify that candidate state, reading pool, recommendations, and existing pages are unchanged.

## Data and affiliation

Paper titles, authors, dates, categories, abstracts, and links come from arXiv metadata. EverWhy Paper Radar is independent and is not affiliated with or endorsed by arXiv.
