"""Command-line entry point for ARENA.

Examples::

    python -m arena run --scenario T1 --seed 4471
    python -m arena run --scenario T1 --seed 4471 --log out.jsonl --verbose
    python -m arena batch --scenario T1 --seeds 0-49
    python -m arena replay --log out.jsonl        # verify + rescore a saved log
"""

from __future__ import annotations

import argparse
import json
import sys

from .agents import RuleChallenger, RuleWarden
from .control import ResourceLimits
from .harness import run_batch
from .log import MatchLog
from .match import run_match
from .scoring import score_match


def _parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        elif part:
            out.append(int(part))
    return out


def _cmd_run(args) -> int:
    limits = ResourceLimits(max_ticks=args.max_ticks)
    result = run_match(args.scenario, args.seed, RuleChallenger(), RuleWarden(),
                       limits=limits, sensor_coverage=args.sensor_coverage)
    card = score_match(result.log)
    print(f"match {result.match_id}: winner={result.winner} reason={result.reason} "
          f"ticks={result.ticks} final_availability={result.availability_final}")
    print(json.dumps(card.to_dict(), indent=2))
    if args.log:
        result.log.save(args.log)
        print(f"log written to {args.log} ({len(result.log)} records, "
              f"chain_valid={result.log.verify_chain()})")
    if args.verbose:
        for ev in result.log:
            if ev.get("type") in ("resolution", "alert", "match_end"):
                print(" ", json.dumps(ev, separators=(",", ":")))
    return 0


def _cmd_batch(args) -> int:
    seeds = _parse_seeds(args.seeds)
    report = run_batch(args.scenario, seeds, sensor_coverage=args.sensor_coverage)
    out = {"scenario": args.scenario, "n": len(seeds),
           "win_rates": report.win_rates, "aggregates": report.aggregates,
           "manifest": report.manifest}
    print(json.dumps(out, indent=2))
    if args.out:
        import pathlib
        pathlib.Path(args.out).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"full report written to {args.out}")
    return 0


def _cmd_replay(args) -> int:
    log = MatchLog.load(args.log)
    ok = log.verify_chain()
    card = score_match(log)
    print(f"loaded {len(log)} records chain_valid={ok}")
    print(json.dumps(card.to_dict(), indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="arena", description="ARENA cyber-defense sparring range")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run a single match")
    pr.add_argument("--scenario", default="T1")
    pr.add_argument("--seed", type=int, default=0)
    pr.add_argument("--max-ticks", type=int, default=200)
    pr.add_argument("--sensor-coverage", type=float, default=1.0)
    pr.add_argument("--log", default=None, help="write the match log (JSONL) here")
    pr.add_argument("--verbose", action="store_true")
    pr.set_defaults(func=_cmd_run)

    pb = sub.add_parser("batch", help="run many seeds and aggregate")
    pb.add_argument("--scenario", default="T1")
    pb.add_argument("--seeds", default="0-49", help="e.g. 0-49 or 1,2,3")
    pb.add_argument("--sensor-coverage", type=float, default=1.0)
    pb.add_argument("--out", default=None, help="write full JSON report here")
    pb.set_defaults(func=_cmd_batch)

    prp = sub.add_parser("replay", help="verify and rescore a saved log")
    prp.add_argument("--log", required=True)
    prp.set_defaults(func=_cmd_replay)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
