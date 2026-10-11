# Raspberry Pi: new-COT-report trigger

The Pi watches for each new weekly CFTC Commitments of Traders report and asks GitHub to
run the **tested** refresh build. GitHub does all the data work and publishes the page.
The Pi never builds, commits or publishes anything, so it cannot put untested code or
unchecked data on the dashboard.

## What runs where

| Task | Runs on | How |
|---|---|---|
| Watch for a new CFTC report: the release schedule, holiday delays, catching up after downtime | **Raspberry Pi** | `pi/cot_pi_check.py` via a systemd timer, every 15 min |
| Trigger the refresh build **once** per new report, and retry only if it fails | **Raspberry Pi** | GitHub API `workflow_dispatch` on `refresh.yml` (`cot_legacy_fetch=true`) |
| Confirm the build succeeded **and** the live page shows the new week | **Raspberry Pi** | checks the run on GitHub, then the page's `data-cot-report` marker |
| Fetch CFTC data, validate it, keep the history, one-time backfill | GitHub (`refresh.yml`) | `engine/cot_table.py` |
| Test gate before every deploy | GitHub | `tests.yml` |
| Build and publish the dashboard | GitHub | `refresh.yml` → GitHub Pages |
| 15-minute scheduled refresh (still runs if the Pi is off) | GitHub | `refresh.yml` cron |
| Full real-data build check on every pull request | GitHub | `build-check.yml` |
| News watcher and phone alerts | GitHub | `news-watch.yml` (unchanged) |
| Manual overrides, open-interest capture, stale-page pulse | Windows PC | `sync_cloud.py` and the PC tasks (unchanged) |

## How one check works

Every 15 minutes the timer starts one check:

1. It takes a lock. If a previous check is still running, the new one exits at once, so checks never overlap.
2. It asks the CFTC for the newest *complete* report week, meaning at least 15 of the 16 contracts are published (futures only). It only asks when a report is due:
   - from Friday 15:30 US Eastern after the last report, every 15 minutes until it appears;
   - hourly once the report is more than a day late (holiday or shutdown);
   - otherwise once a day as a sanity check.

   Daylight saving is handled in the code, so the Pi's own time zone does not matter. Its clock must be NTP-synced.
3. If that week is newer than the last week **confirmed live**, it first looks at the published page, because the scheduled refresh may already have it. Only if the page is still behind does it trigger `refresh.yml`, once.
4. On later checks it follows that run. When the run succeeds **and** the page shows the new week, the week is **confirmed** and never triggered again.
   - If the run fails, is cancelled, never appears, or succeeds while the page still shows the old week (after a 20-minute allowance for Pages), that attempt counts as failed.
   - Failed attempts are retried after 15, 30 and 60 minutes, then every 6 hours. There is exactly one trigger per attempt.
5. **Downtime:** the first check after the Pi comes back compares the CFTC's newest week with the last confirmed one and triggers **one** build. That build backfills every missed week.
   - A check missed while the Pi was off runs at boot (`Persistent=true`).
   - A power cut mid-check is harmless, because the state file is written atomically.

The GitHub token is read from a root-only file that systemd hands to the service alone. It is only ever sent to `api.github.com` in a request header. It is **never** printed, logged, saved in the state file or put in a URL, and everything the checker prints passes through a scrubber.

## Setup, step by step

You need a Raspberry Pi with Raspberry Pi OS (or any systemd Linux), Python 3.9+, internet access, and your normal login with `sudo`.

1. **Check the Pi.** Run this on the Pi and send me the output. It prints no secrets.
   ```bash
   echo "== $(hostname) / $(whoami) / $(uname -m)"; grep PRETTY /etc/os-release; python3 --version; \
   systemctl --version | head -1; timedatectl | grep -E 'Time zone|synchronized'; \
   systemctl list-timers --all --no-pager | grep -iE 'fx|cot|strength' || echo "no fx/cot timers"; \
   systemctl list-units --type=service --all --no-pager | grep -iE 'fx|cot|strength' || echo "no fx/cot services"; \
   crontab -l 2>/dev/null | sed -E 's/(gh[pousr]_|github_pat_)[A-Za-z0-9_]+/***/g; s#://[^/@ ]+@#://***@#g' || echo "no crontab"; \
   ls -d ~/fxstrength* 2>/dev/null || echo "no fxstrength folder"; df -h / | tail -1; \
   curl -s -o /dev/null -w 'CFTC %{http_code}\n' 'https://publicreporting.cftc.gov/resource/6dca-aqww.json?$limit=1'; \
   curl -s -o /dev/null -w 'GitHub %{http_code}\n' https://api.github.com/zen
   ```
2. **Get the code.** The repository is public, so no credentials are needed.
   ```bash
   git clone https://github.com/haroontahir73/fxstrength-app.git ~/fxstrength-app
   ```
   Until PR #3 is merged the Pi files are only on its branch: add `-b feat/cot-legacy-table` to the clone command, or run `git -C ~/fxstrength-app checkout feat/cot-legacy-table`.
3. **Create the GitHub token** in the browser, not on the Pi.
   - Go to GitHub → *Settings* → *Developer settings* → *Fine-grained tokens* → *Generate new token*.
   - Resource owner: `haroontahir73`. Repository access: **Only select repositories** → `fxstrength-app`.
   - Permissions → Repository → **Actions: Read and write**. GitHub adds *Metadata: Read-only* by itself. Grant nothing else.
   - Pick an expiry, e.g. 90 days, and note it.

   Copy the token. Do not paste it into chat, email or a file.
4. **Install.** The script asks for the token at a hidden prompt and installs the checker in **dry-run** mode, which never triggers a build.
   ```bash
   cd ~/fxstrength-app && sudo ./pi/install.sh
   ```
5. **Check the setup.** This should report the token OK, CFTC OK, dashboard OK and the next due time.
   ```bash
   sudo python3 ~/fxstrength-app/pi/cot_pi_check.py --check-setup \
        --token-file /etc/fxstrength-cot/github_token --state-dir /var/lib/fxstrength-cot
   ```
6. **Watch a dry run.** Expect `CONFIRMED`/`up to date`, or `DRY RUN: would trigger …`.
   ```bash
   sudo systemctl start fxstrength-cot.service && journalctl -u fxstrength-cot -n 20 --no-pager
   ```
7. **Go live, only after PR #3 is merged and you approve.** The `cot_legacy_fetch` trigger input exists only from that merge on.
   ```bash
   git -C ~/fxstrength-app checkout main && git -C ~/fxstrength-app pull --ff-only
   cd ~/fxstrength-app && sudo ./pi/install.sh --live
   ```
8. **Confirm the first live report.** On the next Friday after 15:30 New York time (19:30 UTC in summer, 20:30 UTC in winter), the log should show `TRIGGERED` → `run … is in_progress` → `run … succeeded` → `CONFIRMED: the dashboard shows <date>`.
   ```bash
   journalctl -u fxstrength-cot --since "-2h" --no-pager
   ```

## Day to day

| Want to… | Command |
|---|---|
| See what it last did | `journalctl -u fxstrength-cot -n 30 --no-pager` |
| See its saved state (no secrets) | `sudo cat /var/lib/fxstrength-cot/state.json` |
| Run a check now | `sudo systemctl start fxstrength-cot.service` |
| Pause / resume | `sudo systemctl disable --now fxstrength-cot.timer` / `sudo systemctl enable --now fxstrength-cot.timer` |
| Go back to dry run | `cd ~/fxstrength-app && sudo ./pi/install.sh` |
| Replace or rotate the token | `cd ~/fxstrength-app && sudo ./pi/install.sh --token` (add `--live` if it was live) |
| Update the code (after reviewing a change) | `git -C ~/fxstrength-app pull --ff-only` — the next check uses it |
| Remove it | `sudo systemctl disable --now fxstrength-cot.timer && sudo rm /etc/systemd/system/fxstrength-cot.* && sudo rm -r /etc/fxstrength-cot /var/lib/fxstrength-cot && sudo systemctl daemon-reload` |

The code is never updated automatically. The Pi runs only what you have pulled.

## Files

- `cot_pi_check.py`: the checker (stdlib only; reads the release schedule and contract codes from `engine/cot_table.py`).
- `systemd/fxstrength-cot.service`: one-shot, runs as your user, hardened; the token comes in via `LoadCredential`.
- `systemd/fxstrength-cot.timer`: every 15 min, plus 3 min after boot; `Persistent=true` catches up after downtime.
- `install.sh`: installs or updates the units, stores the token at a hidden prompt, and switches between dry-run and live.
- `test_pi_check.py`: offline tests (they run in CI) covering scheduling, trigger-once, retries and back-off, success without a page update, scheduled refresh first, downtime catch-up, half-published weeks, overlapping checks, dry run, and token hygiene.
