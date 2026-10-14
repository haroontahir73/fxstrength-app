#!/usr/bin/env bash
# Nightly BACKUP copy of the durable Myfxbook history (the private GitHub repo that the
# refresh job writes to - see engine/myfxbook.py archive()). The Pi is the second copy,
# not the primary store: GitHub keeps the history; this keeps it again at home.
#
#   * bare mirror of the private repo  -> $DEST/fxstrength-data.git (every commit, so any
#     version of any day file can be recovered)
#   * plus a dated tar.gz snapshot of the latest files, the newest $KEEP kept
#
# The token (fine-grained, that one repo, Contents: READ-ONLY) is read from a root-only
# file and handed to git through GIT_ASKPASS - it is never in a URL, a command line, the
# git config or a log. Run by systemd (myfxbook-mirror.timer); safe to run by hand.
set -euo pipefail

REPO="${MYFXBOOK_HISTORY_REPO:-haroontahir73/fxstrength-data}"
DEST="${MIRROR_DEST:-/var/backups/fxstrength}"
TOKEN_FILE="${TOKEN_FILE:-${CREDENTIALS_DIRECTORY:-/etc/fxstrength-cot}/history_token}"
KEEP="${KEEP:-30}"

[ -r "$TOKEN_FILE" ] || { echo "token file $TOKEN_FILE not readable" >&2; exit 2; }
mkdir -p "$DEST/snapshots"
ASKPASS="$(mktemp)"
trap 'rm -f "$ASKPASS"' EXIT
chmod 700 "$ASKPASS"
# git asks for a username, then a password; answer x-access-token, then the token.
# shellcheck disable=SC2016  # $1 is for the helper script, not this one
printf '#!/bin/sh\ncase "$1" in *sername*) echo x-access-token ;; *) cat "%s" ;; esac\n' "$TOKEN_FILE" > "$ASKPASS"
export GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0

URL="${HISTORY_URL:-https://github.com/$REPO.git}"
if [ -d "$DEST/fxstrength-data.git" ]; then
  git -C "$DEST/fxstrength-data.git" remote update --prune >/dev/null
else
  git clone --quiet --mirror "$URL" "$DEST/fxstrength-data.git"
fi
HEAD_SHA="$(git -C "$DEST/fxstrength-data.git" rev-parse --short HEAD)"
SNAP="$DEST/snapshots/fxstrength-data-$(date -u +%Y-%m-%d).tar.gz"
git -C "$DEST/fxstrength-data.git" archive --format=tar.gz -o "$SNAP" HEAD
# shellcheck disable=SC2012  # our own fixed-pattern file names
ls -1t "$DEST"/snapshots/fxstrength-data-*.tar.gz | tail -n +"$((KEEP + 1))" | xargs -r rm -f
DAYS="$(git -C "$DEST/fxstrength-data.git" ls-tree -r --name-only HEAD -- myfxbook/daily | wc -l)"
echo "mirrored $REPO at $HEAD_SHA: $DAYS day file(s); snapshot $(basename "$SNAP")"
