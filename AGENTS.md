# AGENTS.md

## Project map

- `config/research_profile.yaml`: user-editable categories, keywords, weights, exclusions, fetch limits
- `src/paper_radar/`: CLI, arXiv client/parser, scoring, JSON storage, rendering
- `src/paper_radar/templates/` and `assets/`: static site source
- `tests/fixtures/`: local Atom metadata used by tests
- `data/daily/` and `data/seen_ids.json`: Git-trackable generated history
- `site/`: generated static website
- `reports/`: rule-derived relevance audit and manual visual checklist

## Commands

```bash
.venv/bin/python -m paper_radar run
.venv/bin/python -m paper_radar run --date YYYY-MM-DD
.venv/bin/python -m paper_radar serve
.venv/bin/python -m pytest
```

## Constraints

- Require Python 3.11; do not introduce Node, frontend frameworks, databases, or a persistent backend.
- Fetch only the official arXiv Atom API with the configured User-Agent, timeout, retries, and at least 3 seconds between API requests.
- Keep all date semantics in Asia/Shanghai and deduplicate by base arXiv ID while retaining version updates.
- Default `run` uses the configured rolling window and state comparison. `run --date` must remain exact-day, deterministic historical backfill.
- `seen_ids.json` schema v2 is keyed by base ID and retains `latest_version`, metadata timestamps, `first_seen_at`, and `last_seen_at`; migrate schema v1 only after a successful fetch.
- Never put `version_update` events into Must Read, Highly Relevant, or Broaden the View; render them in the separate folded updates section.
- When the latest daily file has no new submissions, preserve its archive and render the latest non-empty radar as the homepage body with an explicit run-status banner.
- Never invent paper metadata, summaries, conclusions, or scoring evidence. Rules must remain configurable and explainable.
- Tests must be deterministic and offline. Preserve existing JSON and site files when live fetching fails.
- Keep generated JSON readable, stable, and suitable for Git review.

## Completion standard

A change is complete when the full offline pytest suite passes, historical JSON record counts are preserved, the latest empty-run fallback works, local assets resolve, search/filter/sort remain efficient for the 485-paper fixture, and README/AGENTS commands remain accurate. Do not claim screenshot-based visual QA when no browser instance is available.
