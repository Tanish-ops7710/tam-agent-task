"""Re-run tickets that failed in run_log.jsonl and update the log.

Usage:
    python scripts/rerun_failed.py [--delay SECS]

Only tickets with status != 'ok' are re-run. On success, their log entry is
replaced. Existing 'ok' entries and their JSON outputs are never touched.
"""
import argparse
import json
import time
import traceback
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tam_agent.data import load_accounts, load_tickets, load_usage, load_review_set
from tam_agent.kb import load_kb
from tam_agent.agent import Context, triage
from tam_agent.schema import validate

OUT = Path(__file__).resolve().parent.parent / "outputs"
LOG_PATH = OUT / "run_log.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=8.0,
                    help="Seconds to wait between API calls (default 8s)")
    args = ap.parse_args()

    # Load existing log
    existing = {}
    if LOG_PATH.exists():
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                existing[entry["ticket_id"]] = entry
            except Exception:
                pass

    failed = [tid for tid, entry in existing.items() if entry.get("status") != "ok"]
    if not failed:
        print("No failed tickets found. All entries already succeeded.")
        return

    print(f"Re-running {len(failed)} failed tickets: {failed}")

    tickets_df = load_tickets()
    ctx = Context(load_accounts(), tickets_df, load_usage(), load_kb())
    by_id = tickets_df.set_index("ticket_id")

    updated = {}
    ok = 0
    for i, tid in enumerate(failed):
        if i > 0:
            print(f"[Waiting {args.delay}s before next ticket...]")
            time.sleep(args.delay)
        t0, calls0 = time.time(), ctx.llm.calls
        rec = {"ticket_id": tid}
        try:
            ticket = by_id.loc[tid].to_dict() | {"ticket_id": tid}
            result = triage(ticket, ctx)
            errs = validate(result)
            (OUT / f"{tid}.json").write_text(json.dumps(result, indent=2))
            rec |= {"status": "ok" if not errs else "schema_error", "errors": errs}
            ok += not errs
            print(f"  {tid}: {rec['status']}")
        except Exception as e:
            rec |= {"status": "exception", "errors": [repr(e)],
                    "traceback": traceback.format_exc(limit=3)}
            print(f"  {tid}: FAILED -- {repr(e)[:120]}")
        rec |= {"seconds": round(time.time() - t0, 2),
                "llm_calls": ctx.llm.calls - calls0}
        updated[tid] = rec

    # Merge: keep ok entries, replace failed ones with updated results
    merged = {**existing, **updated}

    # Preserve original order (review set order)
    try:
        review_order = load_review_set()
    except Exception:
        review_order = list(merged.keys())

    ordered = []
    seen = set()
    for tid in review_order:
        if tid in merged:
            ordered.append(merged[tid])
            seen.add(tid)
    for tid, entry in merged.items():
        if tid not in seen:
            ordered.append(entry)

    LOG_PATH.write_text(
        "\n".join(json.dumps(e) for e in ordered) + "\n",
        encoding="utf-8"
    )

    print(f"\n{ok}/{len(failed)} re-runs succeeded. Log updated: {LOG_PATH}")


if __name__ == "__main__":
    main()
