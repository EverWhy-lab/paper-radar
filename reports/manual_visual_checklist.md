# EverWhy Paper Radar V0.1.2 — Manual Visual Checklist

No controllable browser was available during automated implementation. Do not treat this document as completed screenshot QA, and do not install a large browser solely for this check.

## Pages

Run `.venv/bin/python -m paper_radar serve`, then inspect:

- Current reader: <http://127.0.0.1:8000/>
- Current recommendation archive under `/recommendations/`
- Five-paper fixture: <http://127.0.0.1:8000/demo/recommendations-5.html>
- Partial fixture: <http://127.0.0.1:8000/demo/recommendations-partial.html>
- Empty fixture: <http://127.0.0.1:8000/demo/recommendations-0.html>
- Retained legacy audit only: <http://127.0.0.1:8000/archive/2026-07-31.html>

## 375 px phone

- No horizontal scroll in header, summary, long titles, scores, rule reasons, abstract, links, or footer.
- Recommendation type, arXiv ID, authors, and metadata wrap without overlap.
- Original abstract remains folded by default and is comfortable to open and scroll.
- Previous-recommendation and archive controls remain distinct and tappable.

## 768 px tablet

- Header and date block switch cleanly to the tablet layout.
- Five cards remain a single readable stream rather than a dense dashboard.
- Long titles and rule explanations do not stretch or clip the page.

## Desktop

- Content stays within the focused 900 px reading column.
- The page shows no search box, global candidate filters, “All Other Papers,” or candidate-card dump.
- Five-paper fixture has exactly five cards; partial has fewer than five; empty has none.
- Empty fixture displays “今日没有发现足够值得推荐的论文。”

## Behavior and links

- Previous recommendation is a link only; it must not replace today's empty content.
- History lists only V0.1.2 recommendation dates.
- Each card includes type, two scores, topic tags, transparent reasons, folded original abstract, and arXiv/PDF/BibTeX links.
- `reader.css` and `favicon.svg` return 200 from homepage, recommendation archive, and demo paths.
- Browser Console has no errors. External links open in a new tab and point to arXiv.
