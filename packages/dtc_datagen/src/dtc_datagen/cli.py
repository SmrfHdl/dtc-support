"""`datagen` command (docs/phases/phase-0.md, Datagen)."""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dtc_datagen.fixtures import to_jsonl
from dtc_datagen.generate import DEFAULT_NOW, DEFAULT_SEED, generate
from dtc_datagen.load import load
from dtc_policy import PolicyConfig

DEFAULT_DATABASE_URL = "postgresql+asyncpg://dtc:dtc@localhost:5432/dtc"
DEFAULT_FIXTURES_OUT = Path("evals/datasets/policy_fixtures.jsonl")


def _aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("--now must include a timezone, e.g. ...Z")
    return dt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="datagen", description=__doc__)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--customers", type=int, default=2000)
    p.add_argument("--orders", type=int, default=5000)
    p.add_argument("--now", type=_aware, default=DEFAULT_NOW)
    p.add_argument(
        "--policy", type=Path, default=os.environ.get("POLICY_PATH"), help="default: $POLICY_PATH"
    )
    p.add_argument("--fixtures-out", type=Path, default=DEFAULT_FIXTURES_OUT)
    p.add_argument(
        "--database-url",
        default=os.environ.get("COMMERCE_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="default: $COMMERCE_DATABASE_URL",
    )
    p.add_argument("--no-load", action="store_true", help="generate and write fixtures only")
    args = p.parse_args(argv)
    if args.policy is None:
        p.error("--policy or POLICY_PATH is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = PolicyConfig.from_yaml(args.policy)
    start = time.perf_counter()
    gen = generate(
        seed=args.seed, customers=args.customers, orders=args.orders, now=args.now, cfg=cfg
    )
    args.fixtures_out.parent.mkdir(parents=True, exist_ok=True)
    args.fixtures_out.write_text(to_jsonl(gen.dataset, gen.fixtures, now=args.now, cfg=cfg))
    print(f"generated {len(gen.fixtures)} fixtures -> {args.fixtures_out}")

    if not args.no_load:
        counts = asyncio.run(load(gen.dataset, args.database_url))
        for table, n in counts.items():
            print(f"  {table:<16} {n:>7}")
    print(f"done in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
