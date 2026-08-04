# AGENTS.md

## Project map

- `config/research_profile.yaml`: arXiv/OpenAlex settings, research rules, historical-score weights, limits, cooldowns, and daily category caps
- `fetchers/arxiv.py`, `scoring.py`, `storage.py`: recent candidate ingestion and arXiv version state
- `providers/base.py`, `providers/openalex.py`: historical provider boundary, safe OpenAlex HTTP, cache, and budget statistics
- `providers/deepseek.py`: optional DeepSeek English daily guide for already-selected papers only, using each paper's own professional terms
- `history_models.py`, `history_storage.py`, `history_discovery.py`, `historical_scoring.py`: canonical historical metadata, seeds, one-hop discovery, deduplication, and explainable scoring
- `curation.py`, `reader_pipeline.py`: V0.2 four-layer selection flow plus optional guide wiring
- `reader_models.py`, `reader_storage.py`: candidate, reading-pool, final recommendation, and guide schemas
- `reader_rendering.py`, `templates/reader.html`, `assets/reader.css`: shortlist-only static site
- `.github/workflows/openalex-discover.yml`, `.github/workflows/openalex-refresh.yml`: weekly discovery and monthly pool refresh; skip when the OpenAlex key is missing
- `data/candidates/`: recent background candidates; never render as a bulk list
- `data/history/discovery_pool.json`: historical background candidates; never render as a bulk list
- `data/history/seeds.json`: manually maintained discovery seeds
- `data/reading_pool.json`: user-admitted reading worklist
- `data/recommendations/`: only zero-to-five daily selections
- `data/daily/` and `site/archive/`: retained V0.1.1 audit history

## Commands

```bash
.venv/bin/python -m paper_radar run
.venv/bin/python -m paper_radar run --date YYYY-MM-DD
.venv/bin/python -m paper_radar pool add ARXIV_ID
.venv/bin/python -m paper_radar pool list
.venv/bin/python -m paper_radar pool status ARXIV_ID queued
.venv/bin/python -m paper_radar pool dismiss ARXIV_ID
.venv/bin/python -m paper_radar history seed add IDENTIFIER
.venv/bin/python -m paper_radar history seed list
.venv/bin/python -m paper_radar history seed remove IDENTIFIER
.venv/bin/python -m paper_radar history discover --dry-run
.venv/bin/python -m paper_radar history discover --limit 20
.venv/bin/python -m paper_radar history list --top 20
.venv/bin/python -m paper_radar history refresh IDENTIFIER
.venv/bin/python -m paper_radar history refresh --all
.venv/bin/python -m paper_radar serve
.venv/bin/python -m pytest
```

## Invariants

- Require Python 3.11. Do not add Node, a frontend framework, database, PDF analysis, paid full-text provider, or Semantic Scholar in V0.2.
- The optional DeepSeek guide may only annotate the already-selected 0-5 papers after rule-based selection. It never screens, ranks, or labels paper quality, and a failure or missing key never blocks or alters the daily page.
- Read `OPENALEX_API_KEY` and `DEEPSEEK_API_KEY` only from the environment. Never print, render, cache, snapshot, or commit a real key. `.env` remains ignored.
- Keep OpenAlex and DeepSeek requests inside their provider adapters. Preserve the common provider interface for future adapters.
- Treat missing citation metrics as unknown, never zero. Prefer OpenAlex field/year percentile; use only the documented age-adjusted fallback when absent.
- Citation counts are screening signals, not quality labels. Never claim “classic,” “best,” “seminal,” or similar without separate human/model evidence.
- Topic and knowledge-map discovery plus seed referenced/citing/related expansion stay within configured request, result, and one-hop limits.
- Recent candidates, historical discovery candidates, reading pool, and daily recommendations are separate persisted layers. Join only in memory for selection.
- Render only selected recommendations. Never add bulk candidate search or cards to the user site.
- Daily caps: total ≤5, frontier recent ≤2, historical impact ≤3, review/knowledge map ≤1. Do not lower thresholds or guarantee quotas.
- Enforce canonical alias deduplication, topic diversity, dismissal, read status, and cooldowns.
- Complete all network work before replacing pool, state, recommendation, or page files. Use atomic JSON/page writes.
- Preserve the 2026-07-31 legacy data and page as an audit sample.

## Completion standard

The full offline pytest suite passes; dry-run makes no writes; provider/cache tests prove the keys cannot persist; failures preserve pools and pages; the generated homepage/archive contain only selected papers and optional guides; local assets resolve; README and CLI agree; and V0.2.0 remains uncommitted until user acceptance. Do not claim screenshot QA without an available browser.
