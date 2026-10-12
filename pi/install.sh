#!/usr/bin/env bash
# Install, or switch the mode of, the FX Strength Desk COT checker on a Raspberry Pi.
#
#   sudo ./pi/install.sh           install / update in DRY-RUN mode (never triggers a build)
#   sudo ./pi/install.sh --live    switch to LIVE (triggers refresh.yml) - only after PR #3 is merged
#   sudo ./pi/install.sh --token   replace the stored GitHub token
#
# The token is typed at a hidden prompt, written straight to a root-only file
# (/etc/fxstrength-cot/github_token, mode 600) and never shown, logged or passed on a
# command line.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo $0 $*"; exit 1; }
RUN_USER="${SUDO_USER:-}"
if [ -z "$RUN_USER" ] || [ "$RUN_USER" = root ]; then
  echo "Run this with sudo from your normal login (not as root) - the checker runs as that user."; exit 1
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
CHECKER="$HERE/cot_pi_check.py"
MODE=dry-run; NEW_TOKEN=0
for a in "$@"; do
  case "$a" in
    --live) MODE=live ;;
    --dry-run) MODE=dry-run ;;
    --token) NEW_TOKEN=1 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done
command -v python3 >/dev/null || { echo "python3 is missing:  sudo apt install python3"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 or newer is needed"; exit 1; }
[ -f "$CHECKER" ] || { echo "cannot find $CHECKER"; exit 1; }

install -d -m 700 -o root -g root /etc/fxstrength-cot
TOKEN_FILE=/etc/fxstrength-cot/github_token
if [ ! -s "$TOKEN_FILE" ] || [ "$NEW_TOKEN" = 1 ]; then
  echo "Paste the GitHub token now (nothing will be shown), then press Enter:"
  IFS= read -rs TOKEN; echo
  [ -n "$TOKEN" ] || { echo "No token entered - nothing changed."; exit 1; }
  ( umask 077; printf '%s' "$TOKEN" > "$TOKEN_FILE.tmp" )
  unset TOKEN
  chown root:root "$TOKEN_FILE.tmp"; chmod 600 "$TOKEN_FILE.tmp"; mv -f "$TOKEN_FILE.tmp" "$TOKEN_FILE"
  echo "Token saved (root-only file, not displayed)."
fi

sed -e "s#@USER@#$RUN_USER#g" -e "s#@CHECKER@#$CHECKER#g" -e "s#@MODE@#$MODE#g" \
    "$HERE/systemd/fxstrength-cot.service" > /etc/systemd/system/fxstrength-cot.service
install -m 644 "$HERE/systemd/fxstrength-cot.timer" /etc/systemd/system/fxstrength-cot.timer
systemctl daemon-reload
systemctl enable --now fxstrength-cot.timer >/dev/null
echo
echo "Installed. Mode: $MODE   (runs as $RUN_USER every 15 minutes)"
systemctl list-timers fxstrength-cot.timer --no-pager | head -3
echo
echo "Next: sudo systemctl start fxstrength-cot.service && journalctl -u fxstrength-cot -n 20 --no-pager"
