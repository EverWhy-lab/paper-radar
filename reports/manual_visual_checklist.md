# EverWhy Paper Radar V0.1.1 — Manual Visual Checklist

This checklist is intentionally manual. The implementation environment did not provide a controllable browser instance, and no browser package was installed solely for screenshots.

## Setup and pages

Run:

```bash
.venv/bin/python -m paper_radar serve
```

Check these pages in a local browser:

- Homepage and empty-run fallback: <http://127.0.0.1:8000/>
- Non-empty radar: <http://127.0.0.1:8000/archive/2026-07-31.html>
- Preserved empty archive: <http://127.0.0.1:8000/archive/2026-08-03.html>

Open DevTools, keep the Console and Network panels visible, and enable “Disable cache” while checking assets.

## 375 px phone

- Set the viewport to 375 × 812.
- Confirm there is no horizontal page scroll at the header, controls, cards, score pills, reason lists, or footer.
- Confirm the Chinese empty-run banner wraps cleanly and both archive links remain tappable.
- Confirm long paper titles wrap inside the card without overlapping the version badge or arXiv ID.
- Confirm search, sort, history, and topic controls form a single readable column.
- Open “Original abstract” and “Why these scores?”; confirm long text and two reason columns collapse to one column.
- Open “All Other Papers” and “Paper Updates”; confirm the summary remains visible and the `+` rotates without layout shift.

## 768 px tablet

- Set the viewport to 768 × 1024.
- Confirm the toolbar uses the intended two-column layout and the search field spans the row.
- Confirm cards use a single column at this breakpoint and do not become excessively narrow.
- Confirm the run-status links align left and do not overflow.
- Verify score pills, categories, and research tags wrap without clipping.

## Desktop

- Set the viewport to 1440 × 900 or wider.
- Confirm cards render in two balanced columns and long titles do not create overlap.
- Confirm the header date panel, toolbar, and section headings align consistently.
- Confirm Must Read contains 3 cards, Highly Relevant 5, Broaden the View 5, All Other Papers 263, and Paper Updates 209 on the 2026-07-31 archive.
- Confirm version-update cards appear only inside “Paper Updates,” not in the four recommendation groups.

## Empty-run fallback

- On `/`, confirm the top banner says “本次运行未发现新论文” and shows the last check time.
- Confirm the homepage body contains the 2026-07-31 non-empty radar rather than an empty state.
- Confirm “当天归档 · 2026-08-03” opens the preserved empty archive.
- Confirm “最近非空归档 · 2026-07-31” opens the non-empty archive.
- On the empty archive, confirm it shows `0 new submissions` and does not silently copy papers into that date.

## Search, filters, sorting, and 485-paper load

- On the 2026-07-31 archive, open both folded groups so all 485 cards are present in the document.
- Type a distinctive title fragment rapidly into search. Confirm input remains responsive and the visible count updates on the next animation frame.
- Search an author, abstract phrase, category, and matched keyword; confirm each can match.
- Select and deselect several research tags; confirm filtering combines selected tags and no stale cards remain.
- Switch between `research_fit` and `video_potential`; confirm cards reorder within their existing section and updates never move into recommendation sections.
- Clear all controls and confirm the visible count returns to 485.

## Links and static assets

- In Network, confirm `styles.css`, `app.js`, and `favicon.svg` return 200 on the homepage and both archive depths.
- Confirm no request for a local asset returns 404 and the Console has no JavaScript error.
- Open one Abstract page, PDF, and Export BibTeX link; confirm each points to `arxiv.org` and opens in a new tab.
- Test both direct file opening of `site/index.html` and the HTTP preview; history and relative asset paths should work in both.
