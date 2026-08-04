# Paper Radar V0.2.0 — Manual Visual Checklist

Automated tests validate rendered markup and data boundaries, but do not replace a manual browser check. Do not install a large browser solely for this checklist.

## Pages

Run `.venv/bin/python -m paper_radar serve`, then inspect:

- Current reader: <http://127.0.0.1:8000/>
- Current recommendation archive under `/recommendations/`
- Existing five/partial/zero V0.1.2 fixtures under `/demo/`
- Retained legacy audit only: <http://127.0.0.1:8000/archive/2026-07-31.html>
- A test-generated or locally prepared recommendation containing one historical-impact and one review/knowledge-map card

## Historical cards

- Category pills distinguish `前沿新论文`, `领域内高影响力`, and `综述 / 知识地图`.
- Publication year/date, OpenAlex citation count (or `unknown`), metric update time, normalized percentile/FWCI when available, and discovery provenance wrap without overlap.
- `historical_value_score` remains visually distinct from `research_fit` and `video_potential`.
- OpenAlex, arXiv, PDF, DOI, and BibTeX links appear only when corresponding metadata exists.
- The abstract is folded by default. The missing-abstract note appears only when OpenAlex supplied no abstract.
- The OpenAlex screening-signal disclaimer is visible and does not imply that citations equal paper quality.
- No historical discovery candidate appears unless it is present in the day's recommendation JSON.
- The DeepSeek guide (AI · DeepSeek) renders inside each recommendation card when `llm_analysis` exists; there is no separate guide section, and a missing guide never affects the cards.
- Every recommendation card has a thumbs-down **Not Relevant** button; clicking it fills the thumb and opens a pre-filled GitHub issue link, and the card itself stays visible.

## 375 px phone

- No horizontal scroll in header, summary, long titles, three score chips, impact signals, provenance, reasons, abstract, actions, or footer.
- Recommendation category, identifier, authors, and metadata wrap without overlap.
- Previous-recommendation and archive controls remain distinct and tappable.

## 768 px tablet

- Header and date block switch cleanly to the tablet layout.
- Five cards remain a single readable stream rather than a dense dashboard.
- Long titles, rule explanations, and discovery-source labels do not stretch or clip the page.

## Desktop

- Content stays within the focused 900 px reading column.
- The page shows no search box, global candidate filters, “All Other Papers,” or candidate-card dump.
- Daily cards never exceed five; empty days display “今日没有发现足够值得推荐的论文。”

## Behavior and links

- Previous recommendation is a link only; it never replaces today's empty content.
- History lists only recommendation dates, not background discovery dates.
- `reader.css` and `favicon.svg` return 200 from homepage and recommendation archive paths.
- Browser Console has no errors. External links open in a new tab.
- The reader has no candidate-search JavaScript; even a large background discovery pool does not create browser-side list work.
