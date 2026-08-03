# EverWhy Paper Radar

EverWhy Paper Radar is a local, static daily reading site for robotics and embodied-intelligence papers from the official arXiv API. It uses configurable keyword rules—not an LLM—to produce transparent `research_fit` and `video_potential` scores.

## Requirements and installation

- Python 3.11
- Internet access to `https://export.arxiv.org/api/query` for live runs

From the project directory:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

The system `python3` on some Macs is older than 3.11. Use `.venv/bin/python` explicitly if the virtual environment is not activated.

## Build the radar

Run the normal incremental radar:

```bash
.venv/bin/python -m paper_radar run
```

The default command searches a rolling seven-day metadata window, compares every result with `data/seen_ids.json`, and records only:

- `new_submission`: a base arXiv ID never seen locally before
- `version_update`: a known base ID whose arXiv version number increased

Repeated retrieval of the same version updates `last_seen_at` but does not create another paper event. New submissions drive the main recommendation sections; version updates appear in a separate folded section.

Build or rebuild an exact Asia/Shanghai calendar date for historical backfill or deterministic checks:

```bash
.venv/bin/python -m paper_radar run --date 2026-08-03
```

Output is written to:

- `data/daily/YYYY-MM-DD.json`: readable daily snapshots
- `data/seen_ids.json`: schema-v2 state keyed by base arXiv ID, including version and first/last-seen timestamps
- `site/index.html`: latest useful radar plus current-run status
- `site/archive/`: dated pages

`--date` retains exact-day semantics: submissions and updates whose arXiv metadata timestamp falls within that Asia/Shanghai natural day. The default command uses rolling discovery instead. If a run finds no new papers, the empty dated archive is retained while the homepage continues to show the most recent non-empty radar and links to both dates. If the API fails, existing data, state, and pages are preserved.

### arXiv announcement timing

arXiv normally announces new papers Sunday through Thursday evening in US Eastern time, after its daily submission cutoff. This is usually the following morning in Asia/Shanghai, around 08:00 during US daylight-saving time and 09:00 during standard time. There are normally no new announcements on Friday or Saturday evenings, and holidays or processing delays can also produce empty or late results.

For a future cloud scheduler, approximately **10:15 Asia/Shanghai** is a practical daily run time with some margin after the usual announcement. V0.1.1 does not create GitHub Actions or any deployment workflow.

## View the site

The generated HTML can be opened directly, or served locally for the best experience:

```bash
.venv/bin/python -m paper_radar serve
```

Then open <http://127.0.0.1:8000/>. Use `--host` and `--port` to change the bind address, for example `serve --port 8080`.

## Tune the research profile

Edit `config/research_profile.yaml` to change:

- arXiv categories and category bonuses
- research topics, aliases, and weights
- exclusions and penalties
- video-potential signals
- section thresholds and API pagination limits
- the default rolling lookback window

Scoring is additive and capped to 0–100. A topic scores once per paper: a title hit receives its full weight and an abstract-only hit receives the configured multiplier. Every contribution is visible on the paper card.

## Test

```bash
.venv/bin/python -m pytest
```

Tests use local Atom fixtures and mocked HTTP transports; they never require live network access.

Manual viewport and interaction checks are documented in `reports/manual_visual_checklist.md`.

## Data and affiliation

Paper titles, authors, dates, categories, abstracts, and links come from arXiv metadata. EverWhy Paper Radar is independent and is not affiliated with or endorsed by arXiv.
