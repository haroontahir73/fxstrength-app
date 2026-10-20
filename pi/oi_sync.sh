#!/usr/bin/env bash
# Push daily CME Open Interest from the Pi to GitHub.
#
# The Pi has a residential IP, so CME's XLS export endpoint works here (it 403s
# from datacenter IPs like GitHub Actions runners). This script:
#   1. Shallow-clones (or pulls) the repo into a local work dir
#   2. Runs oi.py to fetch today's CME preliminary data into oi_history.json
#   3. If anything changed, commits and pushes oi_history.json
#
# The GitHub token is read from the systemd credential store (same mechanism as
# the COT checker). Install with pi/install_oi.sh.
#
#   FXOI_MODE=dry-run  -> fetch + log but do NOT push (safe default)
#   FXOI_MODE=live     -> fetch + push to GitHub
#
set -euo pipefail

REPO="https://github.com/haroontahir73/fxstrength-app.git"
WORKDIR="${STATE_DIRECTORY:-/var/lib/fxstrength-oi}/repo"
MODE="${FXOI_MODE:-dry-run}"

# Read the GitHub token (systemd credential or fallback file)
TOKEN=""
if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -f "$CREDENTIALS_DIRECTORY/github_token" ]; then
  TOKEN="$(cat "$CREDENTIALS_DIRECTORY/github_token")"
elif [ -f /etc/fxstrength-cot/github_token ]; then
  TOKEN="$(cat /etc/fxstrength-cot/github_token)"
fi

if [ -z "$TOKEN" ]; then
  echo "ERROR: no GitHub token found"
  exit 1
fi

AUTH_URL="https://x-access-token:${TOKEN}@github.com/haroontahir73/fxstrength-app.git"

# Clone or pull
if [ -d "$WORKDIR/.git" ]; then
  echo "Pulling latest..."
  git -C "$WORKDIR" fetch origin main --depth=1 -q
  git -C "$WORKDIR" reset --hard origin/main -q
else
  echo "Cloning (shallow)..."
  mkdir -p "$(dirname "$WORKDIR")"
  git clone --depth=1 "$REPO" "$WORKDIR" -q
fi

cd "$WORKDIR/engine"

# Run the OI updater
echo "Running oi.py..."
python3 oi.py 2>&1

# Check if oi_history.json changed
if git -C "$WORKDIR" diff --quiet -- engine/data/oi_history.json; then
  echo "No new OI data — nothing to push."
  exit 0
fi

echo "New OI data captured."

if [ "$MODE" = "dry-run" ]; then
  echo "[DRY-RUN] Would push oi_history.json — skipping."
  git -C "$WORKDIR" diff --stat -- engine/data/oi_history.json
  exit 0
fi

# Commit and push
git -C "$WORKDIR" config user.name "fxstrength-pi[bot]"
git -C "$WORKDIR" config user.email "pi@fxstrength.local"
git -C "$WORKDIR" add engine/data/oi_history.json
git -C "$WORKDIR" commit -m "oi: daily CME capture from Pi ($(date -u +%Y-%m-%d))" -q

echo "Pushing..."
git -C "$WORKDIR" push "$AUTH_URL" HEAD:main -q 2>&1 | grep -v 'x-access-token' || true
echo "Done — pushed to main."
