"""Keep the production-snapshot provenance registry (private repo backtest/provenance.json).

    python divergence_backtest_provenance.py [--data DIR] [--dry-run]

A separate step BEFORE the replay; the replay only ever reads the registry. The registry is
an append-only list of timestamps of the PRODUCTION feed's Myfxbook requests, copied from the
two authoritative production records only:
  * the shared request ledger's production attempts (read only - never written here), and
  * production's own attempt list in the restored production state cache.
It is never derived from the archive or from replay results, never shrinks, and holds no
data and no secret. Makes no Myfxbook request.
"""
import argparse, json, os, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import myfxbook as mf                                             # noqa: E402
from config import DATA                                           # noqa: E402
from divergence_backtest_run import PROVENANCE, _repo_json        # noqa: E402


def collect(data, ledger=None, state=None):
    """Timestamps from the authoritative production records (ledger, state cache)."""
    out = set((ledger or {}).get("production", {}).get("attempts") or [])
    out |= set((state or {}).get("attempts") or [])
    return out


def merged(registry, new):
    """Append-only: everything already recorded stays."""
    return sorted(set((registry or {}).get("production") or []) | set(new))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    stp = Path(a.data) / mf.STATE_NAME
    state = json.loads(stp.read_text()) if stp.exists() else None
    ledger = _repo_json(mf.LEDGER_PATH)
    reg = _repo_json(PROVENANCE)
    new = collect(None, ledger, state)
    out = merged(reg, new)
    before = len((reg or {}).get("production") or [])
    print(f"provenance: {before} recorded, {len(new)} in the production records now "
          f"(ledger: {'yes' if ledger else 'no'}, state cache: {'yes' if state else 'no'}), {len(out)} after merge")
    if a.dry_run or len(out) == before:
        return 0
    token = os.environ.get("MYFXBOOK_HISTORY_TOKEN", "")
    repo = os.environ.get("MYFXBOOK_HISTORY_REPO") or mf.ARCHIVE_REPO
    meta = mf._gh(urllib.request.urlopen, "GET", f"/repos/{repo}", token) if token else None
    if not meta or meta.get("private") is not True:
        print("provenance: not written (no token or the repo is not private)")
        return 0
    mf._put(urllib.request.urlopen, token, repo, PROVENANCE, json.dumps({"production": out}, indent=1).encode(),
            "backtest: provenance registry (production request times)")
    print(f"provenance: registry updated ({len(out) - before} added)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
