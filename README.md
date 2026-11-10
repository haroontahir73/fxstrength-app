# fxstrength-app

Static host for the **FX Strength Desk** Android app. GitHub Pages serves this folder; the app
fetches `dashboard.html` from it and checks `update.json` for new app builds.

- `dashboard.html` — the live dashboard, overwritten by `push_dashboard.py` after each rebuild
- `update.json` — app self-update manifest
- `FXStrengthDesk-*.apk` — the app binary the manifest points at
- `.nojekyll` — tells Pages to serve files as-is
- `index.html` — redirects the bare URL to `dashboard.html`

Publisher: `E:\VSISA\FXStrengthApp\push_dashboard.py` (run by `fxstrength-publish.cmd`).
Not linked anywhere; `noindex`. Do not put anything private here — Pages content is world-readable.

## Daily options open interest

The Open interest tab and `open-interest.html` always show standard options for GC, SI, CL,
6E, 6B, 6J, 6A, 6N, 6C and 6S. The figures are raw contract counts (calls + puts), monthly
standard options only — not a futures-equivalent number. Each row has the total, calls, puts,
the change when an earlier day of the same series exists, the trade date, and PRELIMINARY or
FINAL. A missing figure is a dash, not a zero. Futures rows are unchanged.

CME's VOI Totals workbooks do not contain the trade date or the preliminary/final flag, so
both are typed. Upload the three files you downloaded (Energy, Metals, FX) and, from `engine/`:

```
python voi_totals_oi.py apply --date YYYY-MM-DD --status preliminary --data data energy.xls metals.xls fx.xls
```

Use `--status final` only when that session is final. A preliminary file never replaces a
final options reading, and the first day of the series has no change. Apply an older date
before a newer one when catching up, so the newer day can show a change. Run `report` with
the same flags first if you want to see the figures without writing them.

This command does not write futures cells, even when that date has none yet. Futures stay on
the PC workbook / Pi path. The report prints the file's futures figure next to the stored one.

Commit `engine/data/oi_history.json`. The 15-minute Pages rebuild checks that file out and
renders it. The rebuild does not rewrite the file, so the options stay on the dashboard.
