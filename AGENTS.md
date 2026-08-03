# AGENTS.md

## Project map

- `config/research_profile.yaml`: research scoring plus all recommendation thresholds, caps, exclusions, diversity, and cooldown rules
- `src/paper_radar/fetchers/`, `scoring.py`, `storage.py`: candidate ingestion and version state
- `reader_models.py`, `reader_storage.py`, `recommendation.py`, `reader_pipeline.py`: V0.1.2 three-layer data and selection flow
- `reader_rendering.py`, `templates/reader.html`, `assets/reader.css`: shortlist-only static site
- `data/candidates/`: background candidate metadata; never render all candidates
- `data/reading_pool.json`: manually admitted historical papers
- `data/recommendations/`: zero-to-five daily selections
- `data/daily/` and `site/archive/`: retained V0.1.1 audit history
- `site/recommendations/`: V0.1.2 recommendation archives
- `site/demo/`: clearly labelled fixture scenarios

## Commands

```bash
.venv/bin/python -m paper_radar run
.venv/bin/python -m paper_radar run --date YYYY-MM-DD
.venv/bin/python -m paper_radar pool add ARXIV_ID
.venv/bin/python -m paper_radar pool list
.venv/bin/python -m paper_radar pool status ARXIV_ID queued
.venv/bin/python -m paper_radar pool dismiss ARXIV_ID
.venv/bin/python -m paper_radar serve
.venv/bin/python -m pytest
```

## Invariants

- Require Python 3.11; no Node build chain, frontend framework, database, LLM API, deployment, or external scholarly index.
- Fetch only official arXiv Atom metadata with the configured User-Agent, retries, timeout, and ≥3-second request spacing.
- Candidate metadata, reading pool, and recommendations are separate schemas and directories. Never render candidate collections as user cards.
- Daily recommendations: total ≤5, recent ≤3, pool ≤2, important updates ≤1. Do not lower thresholds to fill a quota.
- Generic keywords cannot qualify alone. Apply configured off-topic exclusions and recent-paper topic diversity.
- Dismissed and read pool entries never qualify. Enforce cooldown history. New versions only reappear through the important-update rule.
- Pool add must fetch real arXiv metadata before writing. Never invent titles, abstracts, citations, quality claims, or recommendation explanations.
- Network failure must preserve `seen_ids.json`, candidate metadata, reading pool, recommendation JSON, and existing pages.
- Keep the legacy 2026-07-31 data and page as an audit sample; do not link it as the new reader experience.

## Completion standard

The full offline pytest suite passes; generated homepage and archives contain only selected papers; 5/partial/0 demos render; all local assets resolve; legacy 485-paper data remains intact; and documentation matches CLI behavior. Do not claim screenshot QA without an available browser.
