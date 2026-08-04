# Paper Radar

Paper Radar is a local personal reader for robotics and embodied-intelligence research. It tracks relevant arXiv frontiers and builds a separate OpenAlex-backed historical discovery pool, then shows at most five papers per day. Selection is transparent and rule-based: the reader does not infer claims from full text or equate citation counts with paper quality. Optionally, DeepSeek writes a short English daily guide for the already-selected papers only, using each paper's own professional terminology.

## Install

Requirements: Python 3.11. Live recent-paper runs need access to the official arXiv API. Historical discovery additionally needs an OpenAlex API key, and the optional daily guide needs a DeepSeek API key.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Copying `.env.example` is optional, but the application does not load `.env` files. Export the key in the process environment when using live historical commands:

```bash
export OPENALEX_API_KEY='your-key-from-openalex'
```

The key is read only from `OPENALEX_API_KEY`. `.env` files and OpenAlex response caches are ignored by Git. The program never prints or writes the key into data, cache, pages, or provider statistics.

## Daily reader

Normal incremental run:

```bash
.venv/bin/python -m paper_radar run
```

This scans the configured seven-day arXiv window, compares metadata with version state, reads the local historical discovery pool, and writes a zero-to-five-paper recommendation. It does not call OpenAlex automatically.

Exact-day arXiv backfill:

```bash
.venv/bin/python -m paper_radar run --date 2026-08-03
```

The `--date` form retains Asia/Shanghai natural-day semantics for deterministic backfill. arXiv posts new papers Sunday through Thursday evening in US Eastern time—usually the following morning around 08:00 or 09:00 in Shanghai, and makes no announcements Friday or Saturday. Weekends, holidays, and early runs can have no new papers. The bundled GitHub Actions workflow (`.github/workflows/daily-run.yml`) runs the incremental command around **09:30 Asia/Shanghai** every day and publishes the rendered site to GitHub Pages.

## Daily automation and mobile access (GitHub)

`.github/workflows/daily-run.yml` automates the daily run and publishes the site:

- A scheduled run executes `python -m paper_radar run` every day at **09:30 Asia/Shanghai** (01:30 UTC). The daily reader never calls OpenAlex, so no API key is needed for the scheduled run.
- The workflow commits the updated `data/` state and `site/` pages back to the repository, then deploys `site/` to GitHub Pages.
- Manual runs are available under **Actions → daily-run → Run workflow**, with two optional inputs:
  - `date`: exact-day backfill in `YYYY-MM-DD` form;
  - `discover_history`: also run OpenAlex historical discovery (requires the `OPENALEX_API_KEY` repository secret).
- Separate maintenance workflows keep the OpenAlex pool fresh:
  - `openalex-discover` runs every **Monday 10:30 Asia/Shanghai** (`history discover`) to expand the historical discovery pool;
  - `openalex-refresh` runs on the **1st of every month** (`history refresh --all`) to update citation metadata for every pool paper.
  Both skip silently when `OPENALEX_API_KEY` is not set, and both auto-commit pool changes back to the repository.

Prerequisites:

1. Push this repository to GitHub. Free GitHub Pages requires a **public** repository; private repositories need a paid plan.
2. In the repository's **Settings → Pages**, set **Source** to **GitHub Actions**.
3. Optional: add `OPENALEX_API_KEY` as a repository secret (**Settings → Secrets and variables → Actions**) to enable the optional historical discovery.
4. Optional: add `DEEPSEEK_API_KEY` as a repository secret to enable the English daily guide.

After the first successful run, the site is available from any device, including a phone, at:

```text
https://<owner>.github.io/<repository-name>/
```

If the scheduled run fails (for example arXiv is unreachable), nothing is committed or published and the previously deployed page stays intact.

## LLM daily guide (DeepSeek, optional)

The rule-based selector remains authoritative: it chooses at most five papers with transparent thresholds. If `DEEPSEEK_API_KEY` is set and `llm_analysis.enabled` is true in `config/research_profile.yaml`, DeepSeek writes an English daily guide for those already-selected papers only, keeping professional terms (model names, method names, acronyms) exactly as they appear in each paper. The guide never screens candidates, never ranks papers, and never labels quality.

- One API call per run (all selected papers in a single request); abstracts are sent, never full text or PDFs.
- If the call fails or the key is missing, the page is still generated without the guide.
- The key is read only from `DEEPSEEK_API_KEY` (export it locally, or set it as a repository secret for GitHub runs). It is never written to data, cache, provider stats, or pages.

## Marking papers as not interested

Every recommended paper card has a **不感兴趣** button. Clicking it immediately hides the card on that device and opens a pre-filled GitHub issue (submit it once to persist the feedback). A `feedback` workflow then records the dismissal and closes the issue automatically.

Effects on future selection:

- The dismissed paper itself is permanently excluded.
- If two or more dismissals share the same primary topic within 30 days, that topic enters a 14-day cooldown and its papers are reduced. Thresholds live in the `dismissals` block of `config/research_profile.yaml`.

The same state can be managed from the CLI:

```bash
.venv/bin/python -m paper_radar dismiss add 2608.02571
.venv/bin/python -m paper_radar dismiss add openalex:W1234567890 --reason not_interested
.venv/bin/python -m paper_radar dismiss list
.venv/bin/python -m paper_radar dismiss remove 2608.02571
```

Dismissals are stored in `data/dismissals.json` and committed like the rest of the state.

## Historical discovery

Seed papers accept an arXiv ID, DOI, or OpenAlex Work ID:

```bash
.venv/bin/python -m paper_radar history seed add 2401.01234
.venv/bin/python -m paper_radar history seed add 10.1234/example
.venv/bin/python -m paper_radar history seed add W1234567890
.venv/bin/python -m paper_radar history seed list
.venv/bin/python -m paper_radar history seed remove 2401.01234
```

Preview a discovery plan without API calls or writes:

```bash
.venv/bin/python -m paper_radar history discover --dry-run
.venv/bin/python -m paper_radar history discover --dry-run --limit 20
```

Run discovery or refresh metadata:

```bash
.venv/bin/python -m paper_radar history discover
.venv/bin/python -m paper_radar history discover --limit 20
.venv/bin/python -m paper_radar history list --top 20
.venv/bin/python -m paper_radar history refresh W1234567890
.venv/bin/python -m paper_radar history refresh --all
```

Discovery uses three source types:

- Eleven configured robotics topic queries from `config/research_profile.yaml`.
- Separate review, survey, tutorial, taxonomy, and benchmark queries.
- One-hop seed expansion through referenced works, citing works, and OpenAlex related works.

Per-query, per-seed, per-run, depth, year, cache lifetime, and request-budget limits are all configurable. Successful JSON responses are cached under ignored `data/history/cache/openalex/`. Aggregate request counts, cache hits, and remaining configured daily budget are written to `data/history/provider_stats.json`. A failed provider run never replaces the discovery pool or reader pages.

## Four separate data layers

1. **Recent candidates** — `data/candidates/YYYY-MM-DD.json`
   - Background arXiv metadata used for scoring, deduplication, and version tracking.
2. **Historical discovery pool** — `data/history/discovery_pool.json`
   - OpenAlex/topic/seed candidates with canonical identifiers, provenance, citation signals, and explainable historical scores.
3. **Reading pool** — `data/reading_pool.json`
   - Papers explicitly added by the user, with reading state, priority, and dismissal state.
4. **Daily recommendations** — `data/recommendations/YYYY-MM-DD.json`
   - Only the zero to five selected papers rendered on the homepage and recommendation archives.

Seed definitions are stored in `data/history/seeds.json`. arXiv version state remains in `data/seen_ids.json`. The legacy 485-paper `data/daily/2026-07-31.json` and `site/archive/2026-07-31.html` remain as V0.1.1 audit samples and are not part of the current reader experience.

## Scoring and recommendation policy

Historical eligibility first requires `research_fit ≥ 18`, at least one configured core topic, and at least one non-generic keyword. Configured off-topic exclusions are applied before impact signals. A highly cited but irrelevant paper cannot qualify.

`historical_value_score` is a transparent weighted score:

- Research relevance: 35%
- OpenAlex field/year-normalized citation percentile, with an age-adjusted annual-citation fallback: 20%
- FWCI: 10%
- Recent citation momentum: 10%
- Independent seed-graph provenance: 10%
- Review/tutorial or method-rule signal: 5%
- Metadata completeness: 10%

Unavailable citation, FWCI, or yearly-count fields remain `null` and are omitted from the weighted mean; they are not treated as zero. The available-component mean is multiplied by `0.78 + 0.22 × available_weight/total_weight`, so missing evidence produces a bounded, visible downgrade. Raw citation count is never the direct sort key. The fallback annualizes citations by paper age and caps them against the configured reference of 20 citations/year; OpenAlex normalized percentile is preferred whenever present.

Current daily gates and caps are configurable:

- Total recommendations: at most 5
- Frontier recent papers: at most 2; `research_fit ≥ 40`
- Historical-impact candidates: at most 3; `historical_value_score ≥ 42`
- Review/knowledge-map candidates: at most 1; `historical_value_score ≥ 50`

Historical categories are considered first, but no category has a guaranteed quota. Thresholds are never lowered to fill the page. A paper can occupy only one category. Canonical aliases, topic diversity, dismissal, `read` status, and a 45-day historical cooldown are enforced. Empty days display “今日没有发现足够值得推荐的论文。”

Citation and impact metadata is a screening signal only. The site uses neutral wording such as “领域内高影响力” and does not label papers “classic,” “best,” or objectively high quality.

## Manual reading pool

```bash
.venv/bin/python -m paper_radar pool add 2401.01234
.venv/bin/python -m paper_radar pool list
.venv/bin/python -m paper_radar pool status 2401.01234 queued
.venv/bin/python -m paper_radar pool dismiss 2401.01234
```

Valid statuses are `unread`, `queued`, `reading`, and `read`. Pool add fetches real metadata from the official arXiv API. Dismissed and read papers are not recommended by default.

## View and test

```bash
.venv/bin/python -m paper_radar serve
.venv/bin/python -m pytest
```

Open <http://127.0.0.1:8000/>. Recommendation archives are under `site/recommendations/`. Tests use local Atom/OpenAlex fixtures and mocked transports only; they do not need an API key or live network.

Recent metadata comes from arXiv. Historical citation and influence metadata comes from OpenAlex. Paper Radar is independent and is not affiliated with or endorsed by either provider.
