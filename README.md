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
