"""P0 smoke test: look up an order in the commerce mock, evaluate it, print the decision.

One fixture per decision code is taken from evals/datasets/policy_fixtures.jsonl, so each
run checks APPROVE, DENY, NEED_MANAGER, NEED_INFO, and the WISMO hand-off end to end.
Each check is one Logfire trace: this script -> HTTP -> the mock -> Postgres.

    docker compose -f deploy/compose/docker-compose.yml up -d --build --wait
    POLICY_PATH=packages/dtc_policy/policies/policy.yaml uv run datagen
    uv run python scripts/smoke_test.py

Exit code 0 when every decision matches the fixture's expectation.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import logfire
from opentelemetry import trace

from dtc_commerce import CommerceClient, HttpCommerceClient, ItemRequest, build_policy_input
from dtc_policy import PolicyConfig, evaluate

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "evals" / "datasets" / "policy_fixtures.jsonl"
POLICY = REPO / "packages" / "dtc_policy" / "policies" / "policy.yaml"
# One fixture per outcome: (case, why it is in the smoke test).
PICKS = (
    ("01", "APPROVE"),
    ("02", "DENY: window expired"),
    ("14", "NEED_MANAGER: defect claim"),
    ("13", "NEED_INFO: condition missing"),
    ("05", "DENY: not delivered -> WISMO"),
)


@dataclass(frozen=True)
class Check:
    label: str
    order_number: str
    ok: bool
    got: str
    want: str
    trace_id: str | None


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines); the shell environment wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def pick_fixtures(path: Path) -> list[tuple[str, dict[str, Any]]]:
    first: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        first.setdefault(record["case"], record)
    return [(label, first[case]) for case, label in PICKS]


def outcome(code: str, reasons: list[str], refund: int | None) -> str:
    return f"{code} {reasons} refund={refund}"


async def check(client: CommerceClient, cfg: PolicyConfig, label: str, fx: dict[str, Any]) -> Check:
    with logfire.span("smoke {label} {order_number}", label=label, order_number=fx["order_number"]):
        span = trace.get_current_span().get_span_context()
        trace_id = f"{span.trace_id:032x}" if span.is_valid else None
        order = await client.get_order(fx["order_number"])
        if order is None:
            return Check(label, fx["order_number"], False, "order not found", "", trace_id)
        requests = [
            ItemRequest(
                order_item_id=UUID(i["order_item_id"]),
                qty=i["qty"],
                condition_claim=i["condition_claim"],
                target_variant_id=None
                if i["target_variant_id"] is None
                else UUID(i["target_variant_id"]),
            )
            for i in fx["items"]
        ]
        now = datetime.fromisoformat(fx["now"])
        decision = evaluate(
            await build_policy_input(client, order, fx["action"], requests, now), cfg
        )
        logfire.info("decision {code}", code=decision.code.value, decision=decision.model_dump())
        want = fx["expected"]
        got = outcome(
            decision.code.value, [r.value for r in decision.reasons], decision.refund_cents
        )
        expected = outcome(want["code"], want["reasons"], want["refund_cents"])
        return Check(label, fx["order_number"], got == expected, got, expected, trace_id)


async def run(client: CommerceClient, cfg: PolicyConfig, fixtures: Path = FIXTURES) -> list[Check]:
    return [await check(client, cfg, label, fx) for label, fx in pick_fixtures(fixtures)]


async def main(base_url: str, policy: Path) -> int:
    cfg = PolicyConfig.from_yaml(policy)
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as http:
        try:
            (await http.get("/health")).raise_for_status()
        except httpx.HTTPError as e:
            print(f"commerce mock not reachable at {base_url}: {e}", file=sys.stderr)
            print("start it: docker compose -f deploy/compose/docker-compose.yml up -d --wait")
            return 2
        checks = await run(HttpCommerceClient(http), cfg)

    for c in checks:
        mark = "ok  " if c.ok else "FAIL"
        print(f"{mark} {c.label:<32} {c.order_number:<10} {c.got}")
        if not c.ok:
            print(f"     expected {c.want}")
        print(f"     trace {c.trace_id}")
    sent = os.environ.get("LOGFIRE_TOKEN")
    print("traces sent to Logfire" if sent else "LOGFIRE_TOKEN not set: traces stay local")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    load_dotenv(REPO / ".env")
    parser = argparse.ArgumentParser(description="P0 smoke test (see module docstring)")
    parser.add_argument(
        "--base-url", default=os.environ.get("COMMERCE_URL", "http://localhost:8001")
    )
    parser.add_argument("--policy", type=Path, default=Path(os.environ.get("POLICY_PATH", POLICY)))
    args = parser.parse_args()

    logfire.configure(service_name="smoke-test", send_to_logfire="if-token-present", console=False)
    # Propagates the trace to the mock (traceparent header).
    logfire.instrument_httpx()  # pyright: ignore[reportUnknownMemberType]
    code = asyncio.run(main(args.base_url, args.policy))
    logfire.force_flush()
    sys.exit(code)
