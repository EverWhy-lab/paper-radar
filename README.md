# Paper Radar

Paper Radar is a local personal reader for robotics and embodied-intelligence research. Version **0.2.4** balances the current Robot AI frontier with recent model-based methods, formal publications in T-RO/IJRR/RA-L, a separate Rising Papers signal, and an OpenAlex-backed historical discovery pool. It shows at most five papers per day. Selection is transparent and rule-based: the reader does not infer claims from full text or equate citations, citation growth, or venue with paper quality. Optionally, DeepSeek writes a grounded English reading guide for the already-selected papers only, using each paper's own professional terminology.

想自己从零搭一个？见 [docs/BUILD_TUTORIAL.md](docs/BUILD_TUTORIAL.md)（含偏好设置模板和导读提示词）。

## Install

Requirements: Python 3.11. Live recent-paper runs need access to the official arXiv API. Historical discovery additionally needs an OpenAlex API key, and the optional daily guide needs a DeepSeek API key.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Copying `.env.example` to `.env` is optional. At CLI startup Paper Radar loads
simple `KEY=value` entries from the project-root `.env`; an existing process
environment value always wins, so GitHub Actions and shell-injected secrets are
never overwritten. You can also export the key directly when using live
historical commands:

```bash
export OPENALEX_API_KEY='your-key-from-openalex'
```

Providers still read keys only from environment variables such as
`OPENALEX_API_KEY` and `DEEPSEEK_API_KEY`. `.env` files and OpenAlex response
caches are ignored by Git. The program never prints or writes a key into data,
cache, pages, or provider statistics.

## Daily reader

Normal incremental run:

```bash
.venv/bin/python -m paper_radar run
```

This scans the configured seven-day arXiv window, compares metadata with version state, reads the local historical and Rising candidate pools, and writes a zero-to-five-paper recommendation. It does not call OpenAlex automatically; the 540-day Rising scan is weekly only.

Exact-day arXiv backfill:

```bash
.venv/bin/python -m paper_radar run --date 2026-08-03
```

The `--date` form retains Asia/Shanghai natural-day semantics for deterministic backfill. arXiv posts new papers Sunday through Thursday evening in US Eastern time—usually the following morning around 08:00 or 09:00 in Shanghai, and makes no announcements Friday or Saturday. Weekends and holidays can have no new papers. The bundled GitHub Actions workflow (`.github/workflows/daily-run.yml`) runs the incremental command around **12:30 Asia/Shanghai** every day and publishes the rendered site to GitHub Pages. The noon schedule leaves more margin for the daily arXiv batch, network delay, and GitHub runner scheduling, while making the shortlist available for a midday read.

## Daily automation and mobile access (GitHub)

`.github/workflows/daily-run.yml` automates the daily run and publishes the site:

- A scheduled run executes `python -m paper_radar run` every day at **12:30 Asia/Shanghai** (04:30 UTC). The daily reader never calls OpenAlex, so no API key is needed for the scheduled run.
- The workflow commits the updated `data/` state and `site/` pages back to the repository, then deploys `site/` to GitHub Pages.
- Manual runs are available under **Actions → daily-run → Run workflow**, with two optional inputs:
  - `date`: exact-day backfill in `YYYY-MM-DD` form;
  - `discover_history`: also run OpenAlex historical discovery (requires the `OPENALEX_API_KEY` repository secret).
- Separate maintenance workflows keep the OpenAlex pool fresh:
  - `openalex-discover` runs every **Monday 10:30 Asia/Shanghai** (`history discover`, then `history rising`) to expand the historical pool, update Rising citation snapshots, and replace the local Rising candidate pool;
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

- One API call per run (all selected papers in a single request); up to 3,000 abstract characters per paper are sent, never full text or PDFs.
- The reader profile comes from `llm_analysis.reader_profile`, and each payload includes selection category, source name, core topics, subtopics, document type, domain affinity, publication year, and abstract—not Rising scores, citation counts, FWCI, or other internal ranking signals.
- Guides are grounded in supplied metadata: they must not invent results, datasets, model scale, training resources, or hardware, and they adapt the reading emphasis for methods, surveys, and benchmarks/datasets.
- If the call fails or the key is missing, the page is still generated without the guide.
- The key is read only from `DEEPSEEK_API_KEY` (export it locally, or set it as a repository secret for GitHub runs). It is never written to data, cache, provider stats, or pages.

## Not relevant, favorites, and batch sync

Every recommended paper card has two buttons:

- **Save** (star): adds the paper to your favorites. The **Favorites** link in the navigation opens a dedicated page listing all saved papers.
- **Not Relevant** (thumbs-down): marks a paper as not relevant.

Clicks fill the icon on that device and queue the item locally. The navigation bar shows a **同步反馈 (N)** button while items are pending; clicking it opens one pre-filled GitHub issue containing the whole batch. Submitting that issue persists everything at once — a `feedback` workflow records the dismissals and favorites, then closes the issue.

Effects on future selection:

- The dismissed paper itself is permanently excluded.
- If two or more dismissals share the same primary topic within the configured 30-day feedback window, that topic enters cooldown from the most recent triggering dismissal. With the default 14-day duration, elapsed days 0–13 are blocked and day 14 is eligible again. Thresholds live in the `dismissals` block of `config/research_profile.yaml`.

The same state can be managed from the CLI:

```bash
.venv/bin/python -m paper_radar dismiss add 2608.02571
.venv/bin/python -m paper_radar dismiss add openalex:W1234567890 --reason not_interested
.venv/bin/python -m paper_radar dismiss list
.venv/bin/python -m paper_radar dismiss remove 2608.02571
.venv/bin/python -m paper_radar favorite add 2608.02571
.venv/bin/python -m paper_radar favorite list
.venv/bin/python -m paper_radar favorite remove 2608.02571
.venv/bin/python -m paper_radar candidates prune --older-than 30
.venv/bin/python -m paper_radar feedback apply feedback.txt
```

Dismissals live in `data/dismissals.json`, favorites in `data/favorites.json` (with full metadata, independent of candidate files), and both are committed like the rest of the state. The monthly maintenance run also executes `candidates prune --older-than 30`, removing candidate metadata files older than 30 days — favorites and recommendations are never touched.

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

Run the live, read-only Rising audit (OpenAlex only; no cache, snapshots, pools,
provider statistics, arXiv, or DeepSeek writes/calls):

```bash
.venv/bin/python -m paper_radar history rising --dry-run
```

The weekly state-updating form is `python -m paper_radar history rising`.

Discovery uses three source types:

- Fourteen focused Robot AI topic queries from `config/research_profile.yaml`.
- Separate review, survey, tutorial, taxonomy, and benchmark queries.
- One-hop seed expansion through referenced works, citing works, and OpenAlex related works.

The topic and knowledge-map searches use a rolling active-reading window: by
default the current year through ten years back. The preferred recent window is
five years. Older seed or cached papers may remain available as background
lineage, but they are not eligible for a daily reading slot.

### Daily Frontier

The seven-day arXiv feed answers “what appeared today in the user's research
directions?” It retains the six frozen Robot AI core topics and the independent
model-based robotics method lane.

### Core Robotics Journals

The `journals` block explicitly separates `robotics_core` (T-RO
`S144620930`, IJRR `S73484101`, and RA-L `S4210169774`) from the seven unchanged
`control_supplement` sources (IEEE TAC, Automatica, IEEE TIE, IEEE TCST, IEEE
TII, Control Engineering Practice, and Journal of Process Control). The last 60
days are fetched with source-specific caps: T-RO 100, IJRR 60, and RA-L 600;
RA-L therefore paginates instead of silently keeping only the newest 15 works.
The venue group controls discovery and auditing only—it adds no quality bonus.
All journal candidates still pass research relevance, utility, cooldown,
semantic redundancy, and diversity rules before the **期刊新论文** lane can use
at most two slots.

### Rising Papers

The weekly Rising scan asks a different question: “which papers from the three
robotics-core journals, published in the last 540 days, are receiving unusually
fast community attention for their age?” It scans recent works by source and
date (T-RO cap 600, IJRR 300, RA-L 3000), calculates locally, and stores only a
small local candidate pool plus citation snapshots. The daily run makes no
540-day OpenAlex request.

`rising_score` combines research relevance (20%), smoothed age-normalized
citation velocity (25%), OpenAlex field/year citation percentile (25%), FWCI
(10%), and observed snapshot growth (20%). Citation age has a 90-day floor, so
a five-day-old paper cannot explode merely because its denominator is tiny.
Observed growth uses the closest valid prior snapshot at least six days old and
an approximately-28-day signal only after 21 days. Missing FWCI, percentile,
count, or observed history remains unknown and is handled by available-component
normalization, never as zero. Cold start therefore uses the available age,
velocity, percentile, FWCI, and relevance signals; later scans add real deltas.

Rising eligibility requires a robotics-core source, a valid date within 540
days, explicit robotics context, `research_fit ≥ 12`, no configured off-topic
match, no retraction, and a non-survey document. It does not require one of the
six core topics, so sufficiently strong planning/control work can broaden the
reader's view. The configured threshold is `rising_score ≥ 55`; it is a gate,
not a quota. Rising is an attention/impact proxy—not a paper-quality ranking or
citation leaderboard. Daily selection remains capped at one Rising paper and at
two Rising papers in any rolling seven-day window, counted from existing
recommendation archives; neither cap is filled by lowering the threshold.

Per-query, per-seed, per-run, depth, year, cache lifetime, and request-budget limits are all configurable. Successful JSON responses are cached under ignored `data/history/cache/openalex/`. Aggregate request counts, cache hits, and remaining configured daily budget are written to `data/history/provider_stats.json`. A failed provider run never replaces the discovery pool or reader pages.

## Five separate data layers

1. **Recent candidates** — `data/candidates/YYYY-MM-DD.json`
   - Background arXiv metadata used for scoring, deduplication, and version tracking.
2. **Historical discovery pool** — `data/history/discovery_pool.json`
   - OpenAlex/topic/seed candidates with canonical identifiers, provenance, citation signals, and explainable historical scores.
3. **Rising candidate pool and snapshots** — `data/rising/candidates.json`, `data/rising/citation_snapshots.json`
   - Weekly robotics-core journal candidates, Rising components, and compact time-series citation observations; separate from historical value.
4. **Reading pool** — `data/reading_pool.json`
   - Papers explicitly added by the user, with reading state, priority, and dismissal state.
5. **Daily recommendations** — `data/recommendations/YYYY-MM-DD.json`
   - Only the zero to five selected papers rendered on the homepage and recommendation archives.

Seed definitions are stored in `data/history/seeds.json`. arXiv version state remains in `data/seen_ids.json`.

## Scoring and recommendation policy

Paper Radar is not trying to answer “which robotics papers are the most famous
in history?” Its daily question is narrower and more practical: **which robotics
method advances are genuinely worth opening and reading today?** The policy is
recent-first, prefers active-reading history from the last ten years, and
combines scientific eligibility with cross-day redundancy, personal domain
affinity, and daily diversity. Five is a ceiling, not a quota; one to four
recommendations—or an empty day—is normal.

Daily reading tracks two layers:

- **Robot AI frontier:** VLA and robot foundation models, world models and
  embodied reasoning, robot learning and policy post-training, humanoid
  loco-manipulation, dexterous multimodal manipulation, and robot-data scaling
  with sim-to-real.
- **Model-based robotics methods:** motion and kinodynamic planning, trajectory
  optimization, MPC/WBC, safety-critical control, and robot state estimation.

The second layer is not a quota. It has an independent maximum of one recent
paper per day and remains empty unless a paper clears its robotics-context,
strong-method, fit, utility, cooldown, and redundancy gates. Paper Radar does
not treat model-based robotics as obsolete, and it does not assume every Robot
AI paper deserves more reading time than a substantive planning or control
advance.

The six core directions remain frozen: VLA/robot foundation models, robot world
models and embodied reasoning, humanoid whole-body loco-manipulation, robot
policy post-training, dexterous multimodal manipulation, and robot-data scaling
with sim-to-real. `robot_control_optimization` remains a lower-weight support
topic rather than a seventh core direction. High-quality control-only work can
qualify only through the independent `model_based_recent` lane; it still cannot
pass a Robot AI core gate by pretending to be a seventh core topic.

Historical eligibility first requires explicit robotics context from the title, abstract, or OpenAlex topic metadata, then `research_fit ≥ 18`, at least one configured core topic, at least one non-generic keyword, and publication within the rolling ten-year active-reading window. General terms such as foundation model, world model, LLM, diffusion model, RL, MPC, or control do not establish robotics context by themselves. Configured off-topic exclusions are applied before impact signals. A highly cited but irrelevant or over-ten-year-old paper cannot qualify for daily reading.

`historical_value_score` is a transparent weighted score:

- Research relevance: 30%
- Recency: 20% (`≤2 years: 100`, `3–5: 90`, `6–7: 70`, `8–10: 50`, older: `0`)
- OpenAlex field/year-normalized citation percentile, with an age-adjusted annual-citation fallback: 15%
- FWCI: 10%
- Recent citation momentum: 10%
- Independent seed-graph provenance: 5%
- Review/tutorial or method-rule signal: 5%
- Metadata completeness: 5%

Unavailable citation, FWCI, or yearly-count fields remain `null` and are omitted from the weighted mean; they are not treated as zero. The available-component mean is multiplied by `0.78 + 0.22 × available_weight/total_weight`, so missing evidence produces a bounded, visible downgrade. Raw citation count is never the direct sort key. The fallback annualizes citations by paper age and caps them against the configured reference of 20 citations/year; OpenAlex normalized percentile is preferred whenever present.

Current daily gates and caps are configurable:

- Total recommendations: at most 5
- Frontier recent papers: at most 2; `research_fit ≥ 40`
- Fresh journal and frontier papers combined: at most 3
- Model-based recent methods: at most 1; independent of the preceding three-paper ceiling and never guaranteed
- Rising recent papers: at most 1 per day and 2 in any rolling seven-day window; `rising_score ≥ 55`, independent of the frontier/journal three-paper ceiling and never guaranteed
- Historical-impact candidates: at most 1; `historical_value_score ≥ 42`
- Review/knowledge-map candidates: at most 1; `historical_value_score ≥ 50`

Frontier recent papers are considered first, followed by fresh journals, the model-based method lane, Rising, a review/knowledge map, and at most one 5–10-year historical foundation. `max_recent_total = 3` still applies only to frontier plus fresh journals; Rising remains subject to the final five-paper ceiling. No category has a guaranteed quota, and thresholds are never lowered to fill the page. A paper can occupy only one category, so a journal-selected alias cannot reappear as Rising. Canonical aliases, topic diversity, dismissal, `read` status, 45-day Rising/historical exact-paper cooldowns, and the existing semantic cooldown are enforced. Empty days display “今日没有发现足够值得推荐的论文。”

The recommendation layer deliberately does not rewrite `research_fit`:

- Configured subtopics identify recurring reading themes such as VLA post-training, robot world models, humanoid whole-body learning, diffusion/flow policies, tactile manipulation, robot-data scaling, motion/kinodynamic planning, MPC, WBC, trajectory optimization, safety-critical control, and state estimation.
- An exact repeated frontier or model-based subtopic with no new secondary theme has a four-day short cooldown, then a decaying 5–7 day utility penalty; a penalized repeat must still clear a higher utility bar. The cooldown applies to specific methods rather than the whole `robot_control_optimization` support topic.
- A survey on the same specific subtopic receives a stronger 14–30 day penalty. A generic survey with no specific subtopic falls back to `survey:<primary-topic>`, preventing consecutive broad VLA (or other same-family) surveys. A genuinely different secondary theme retains the reduced-penalty/bypass behavior.
- Personal domain affinity is a bounded soft adjustment: preferred platforms and general-purpose manipulation receive `+6`, neutral domains receive `0`, and peripheral application domains receive `-3`. Scientific gates still run first, and a strong peripheral-domain method can outrank a weaker preferred-domain paper.
- Daily archives record core topics, subtopics, affinity, redundancy penalty, and final `recommendation_utility`, leaving an explainable interface for future feedback learning without performing online learning now.
- Daily reading order uses recommendation utility, the rule-engine base/fit score, recency, and a stable canonical identifier. `video_potential` remains stored for future content-radar use but does not break reading-recommendation ties.

## Offline recommendation backtest

Replay the policy against local candidate, historical, and archive data without
calling arXiv, OpenAlex, or DeepSeek:

```bash
.venv/bin/python -m paper_radar backtest \
  --from 2026-08-04 \
  --to 2026-08-16 \
  --compare-actual
```

The replay advances an isolated simulated recommendation history day by day, so
paper and subtopic cooldowns from day 1 affect day 2. It filters papers and
reliably dated candidate/discovery records that were not yet available, never
uses future actual recommendations as state, and never writes `data/`, `site/`,
favorites, dismissals, or the reading pool. Reports are written to
`reports/backtests/` as Markdown and JSON. They include quantity, recency, core
topic and model-based method distributions, method-only/method-plus-core counts,
planning/MPC-WBC/safety/state-estimation counts, same-paper and same-subtopic repetition,
affinity, quality/diversity, daily selections, and optional actual-vs-replay
comparison. OpenAlex influence fields come from the currently cached snapshot,
so the result is a recommendation-policy replay rather than a perfect
point-in-time reconstruction. The `rising_recent` lane is disabled in historical
backtests unless a real point-in-time Rising snapshot is explicitly available;
current or future citation observations are never leaked into an earlier date.

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
