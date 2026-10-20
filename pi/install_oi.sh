#!/usr/bin/env bash
# Install the daily CME OI capture service on the Pi.
#
#   sudo ./pi/install_oi.sh            install in DRY-RUN mode (fetches but does not push)
#   sudo ./pi/install_oi.sh --live     switch to LIVE (pushes oi_history.json to GitHub)
#
# Uses the SAME GitHub token as the COT checker (/etc/fxstrength-cot/github_token).
# If that token doesn't exist yet, run pi/install.sh --token first.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo $0 $*"; exit 1; }
RUN_USER="${SUDO_USER:-}"
if [ -z "$RUN_USER" ] || [ "$RUN_USER" = root ]; then
  echo "Run this with sudo from your normal login (not as root)."; exit 1
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/oi_sync.sh"
MODE=dry-run
for a in "$@"; do
  case "$a" in
    --live) MODE=live ;;
    --dry-run) MODE=dry-run ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done
[ -f "$SCRIPT" ] || { echo "cannot find $SCRIPT"; exit 1; }
chmod +x "$SCRIPT"

TOKEN_FILE=/etc/fxstrength-cot/github_token
if [ ! -s "$TOKEN_FILE" ]; then
  echo "No GitHub token found. Run pi/install.sh --token first to set one up."
  exit 1
fi

sed -e "s#@USER@#$RUN_USER#g" -e "s#@SCRIPT@#$SCRIPT#g" -e "s#@MODE@#$MODE#g" \
    "$HERE/systemd/fxstrength-oi.service" > /etc/systemd/system/fxstrength-oi.service
install -m 644 "$HERE/systemd/fxstrength-oi.timer" /etc/systemd/system/fxstrength-oi.timer
systemctl daemon-reload
systemctl enable --now fxstrength-oi.timer >/dev/null
echo
echo "Installed. Mode: $MODE   (runs as $RUN_USER every hour at :07)"
systemctl list-timers fxstrength-oi.timer --no-pager | head -3
echo
echo "Test it:  sudo systemctl start fxstrength-oi.service && journalctl -u fxstrength-oi -n 30 --no-pager"
