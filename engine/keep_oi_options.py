"""Put back option fields a futures sync dropped from oi_history.json.

The PC copy of the archive is futures-only. sync_cloud.py now merges it, but a
machine still running the old replace commits a file with those fields missing.
This compares HEAD with its parent and, when option fields were omitted, writes
them back. Every value HEAD still has — futures and any options it did store —
stays as HEAD has it.

    python keep_oi_options.py --check     # report only
    python keep_oi_options.py              # commit and push when something was omitted

Prints UNCHANGED or RESTORED. Stdlib only. Does not contact CME.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import oi                                                                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HIST = "engine/data/oi_history.json"
MSG = (
    "oi: restore options fields omitted by the futures sync\n\n"
    "The previous commit dropped oi_opt readings that were already stored.\n"
    "Futures values in that commit are unchanged. This puts the omitted\n"
    "option fields back so the Open interest tab can keep showing them."
)


def _git(*args, check=True):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if check and r.returncode:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        raise SystemExit(r.returncode or 1)
    return r


def _rev(rev):
    raw = _git("show", f"{rev}:{HIST}").stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{rev}:{HIST} is not JSON ({e})")
    if not isinstance(data, dict):
        raise SystemExit(f"{rev}:{HIST} is not a date map")
    return data


def _futures_intact(current, merged):
    """A repair may only add option keys. Futures numbers stay on the new commit."""
    for day, vals in current.items():
        if not isinstance(vals, dict) or day not in merged:
            return False
        for inst, rec in vals.items():
            if not isinstance(rec, dict):
                continue
            got = merged[day].get(inst)
            if not isinstance(got, dict):
                return False
            for k in ("oi", "chg", "volume"):
                if rec.get(k) != got.get(k):
                    return False
    return True


def main(argv):
    check = "--check" in argv
    if _git("rev-parse", "--verify", "HEAD~1", check=False).returncode:
        shallow = _git("rev-parse", "--is-shallow-repository", check=False).stdout.strip()
        if shallow == b"true":
            raise SystemExit("shallow checkout has no parent; fetch-depth must be at least 2")
        print("UNCHANGED")
        return 0
    previous, current = _rev("HEAD~1"), _rev("HEAD")
    merged = oi.restore_omitted_options(previous, current)
    if merged == current:
        print("UNCHANGED")
        return 0
    if not _futures_intact(current, merged):
        raise SystemExit("refusing to repair: futures values would change")
    if check:
        print("RESTORED")
        return 0
    text = json.dumps(merged, indent=1) + "\n"
    (ROOT / HIST).write_text(text, encoding="utf-8", newline="\n")
    _git("add", HIST)
    _git("commit", "-m", MSG)
    pushed = _git("push", "origin", "HEAD:main", check=False)
    if pushed.returncode:
        _git("pull", "--rebase", "origin", "main")
        _git("push", "origin", "HEAD:main")
    print("RESTORED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
