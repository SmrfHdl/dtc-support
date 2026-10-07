# Phase 0 spec

## Schema
Money is stored as int `*_cents`. Time is stored as `timestamptz` UTC.

### Schema `commerce` (owner: commerce_mock)
| Table | Key columns |
|---|---|
| customers | id, email (unique), name, created_at |
| products | id, sku (unique), name, category_code |
| variants | id, product_id, size, color, list_price_cents, stock |
| orders | id, order_number (unique), customer_id, status (placed/partially_shipped/shipped/delivered/cancelled), placed_at, total_cents |
| order_items | id, order_id, variant_id, shipment_id (nullable), qty, unit_price_cents |
| shipments | id, order_id, carrier, tracking_number (unique), status (label_created/in_transit/out_for_delivery/delivered/exception/lost), shipped_at, delivered_at, eta |
| shipment_events | id, shipment_id, ts, status, location, description |
| returns | id, order_id, type (return/exchange), status (approved/pending_manager/received/inspection_passed/refunded/rejected), refund_cents (nullable), reason_codes text[], policy_version, idempotency_key (unique), created_at |
| return_items | return_id, order_item_id, qty, condition_claim (nullable), exchange_variant_id (nullable) |
| refunds | id, return_id, amount_cents, status (pending/completed/failed), approved_by (nullable), idempotency_key (unique), created_at |
| request_log | id, ts, method, path, status_code, idempotency_key (nullable), latency_ms, trace_id |

Conventions:
- Primary keys are UUIDs generated in Python (datagen derives them from its seed, so the dataset is reproducible).
- Status columns are `text` + `CHECK`, not Postgres `ENUM` (adding a value would need its own migration).
- `CHECK` constraints: `qty > 0`, `stock >= 0`, every `*_cents >= 0`.
- Category rules live only in the policy YAML. The DB stores only `category_code`.
- `variants.list_price_cents` is the only list price; products carry no price.

Mapping to `PolicyInput` (done by the commerce client):
- `VariantSnapshot.category_code` comes from the variant's product.
- `OrderItemSnapshot.shipment_status` / `delivered_at` come from the shipment linked by `order_items.shipment_id`. An item with no shipment yet is `label_created`, `delivered_at = None`. One order item ships in one shipment (no split lines in MVP).
- `committed_returned_qty` = `SUM(return_items.qty)` over the item's returns with `status <> 'rejected'`. Not stored, so it cannot drift.
- `committed_refunds_cents` = `SUM(returns.refund_cents)` over the order's returns with `status <> 'rejected'`. `returns.refund_cents` is written when the return is created (the policy's `refund_cents`; NULL for exchanges), because the `refunds` row only appears after inspection (`release_on: inspection_passed`).

`request_log` is the mock's own request log. It does not write to `support.audit_log`: that schema belongs to dtc_store (ADR-0006) and is created in P1.

### Schema `support` (owner: dtc_store, created in P1)
| Table | Key columns |
|---|---|
| conversations | id, session_id, customer_id (nullable), started_at, ended_at, primary_intent, outcome (automated/handoff/abandoned), csat |
| messages | id, conversation_id, role, content_redacted, ts, trace_id |
| episodes | id, customer_id, conversation_id, summary, intents[], outcome, created_at |
| customer_facts | id, customer_id, key, value jsonb, verified, confidence, source_conversation_id, source_turn, expires_at |
| kb_chunks | id, doc_id, content, embedding vector(1024), metadata jsonb |
| audit_log | id, actor (bot/human/system), action, entity, entity_id, payload jsonb, policy_version, trace_id, ts |

## Policy config
```yaml
version: "2026.09.28-1"
timezone: America/Los_Angeles
return:
  window_days: 30             # eligible when days_since_delivery < 30
  window_anchor: delivered_at
  allowed_conditions: [new_with_tags, like_new]
refund:
  auto_limit_cents: 5000      # < 5000 → auto, >= 5000 → manager
  aggregate_scope: order
  release_on: inspection_passed
exchange:
  require_same_price: true    # same_category only
defect:
  # soft-check failures a defect claim may bypass (item goes to NEEDS_REVIEW instead of INELIGIBLE)
  bypass_reasons: [WINDOW_EXPIRED, CATEGORY_NOT_RETURNABLE, CATEGORY_NOT_EXCHANGEABLE]
categories:
  apparel:     {returnable: true,  exchange: same_product}
  footwear:    {returnable: true,  exchange: same_product}
  accessories: {returnable: true,  exchange: same_category}
  underwear:   {returnable: false, exchange: none}
  final_sale:  {returnable: false, exchange: none}
```

## Contracts
```python
ConditionClaim = Literal["new_with_tags", "like_new", "worn", "defective"]
ShipmentStatus = Literal["label_created", "in_transit", "out_for_delivery",
                         "delivered", "exception", "lost"]

class VariantSnapshot(BaseModel):
    variant_id: UUID
    product_id: UUID
    category_code: str
    list_price_cents: int             # current list price
    stock: int

class OrderItemSnapshot(BaseModel):
    order_item_id: UUID
    variant: VariantSnapshot          # the variant that was bought
    qty: int
    unit_price_cents: int             # price actually paid per unit, after item-level discount
    committed_returned_qty: int       # qty in all non-rejected returns (pending ones included)
    shipment_status: ShipmentStatus   # status of the shipment carrying this item
    delivered_at: datetime | None

class OrderSnapshot(BaseModel):
    order_id: UUID
    items: list[OrderItemSnapshot]
    committed_refunds_cents: int      # refund amount of all non-rejected returns on this order

class RequestedItem(BaseModel):
    order_item_id: UUID
    qty: int
    condition_claim: ConditionClaim | None
    target_variant: VariantSnapshot | None = None   # exchange only

class PolicyInput(BaseModel):
    action: Literal["return", "exchange"]
    now: datetime                     # must be tz-aware
    order: OrderSnapshot
    items: list[RequestedItem]
    risk_score: float | None = None   # reserved; ignored when None

class ItemStatus(StrEnum):
    ELIGIBLE, INELIGIBLE, NEEDS_REVIEW, NEEDS_INFO

class ItemDecision(BaseModel):
    order_item_id: UUID
    status: ItemStatus
    reasons: list[ReasonCode]         # all reasons found, not only the first
    defect_claim_possible: bool = False   # see "Defect hint" below

class MissingField(BaseModel):
    order_item_id: UUID
    field: Literal["condition", "target_variant"]

class PolicyDecision(BaseModel):
    code: DecisionCode                # APPROVE | DENY | NEED_MANAGER | NEED_INFO
    reasons: list[ReasonCode]         # decision-level: NOT_DELIVERED, OVER_AUTO_LIMIT, DEFECT_CLAIM, UNKNOWN_CATEGORY
    items: list[ItemDecision]
    refund_cents: int | None
    missing: list[MissingField]
    policy_version: str
```

`ReasonCode` values: NOT_DELIVERED, WINDOW_EXPIRED, CATEGORY_NOT_RETURNABLE, CATEGORY_NOT_EXCHANGEABLE, UNKNOWN_CATEGORY, QTY_EXCEEDED, CONDITION_NOT_ACCEPTED, DEFECT_CLAIM, EXCHANGE_SCOPE, OUT_OF_STOCK, PRICE_MISMATCH, OVER_AUTO_LIMIT.

`committed_refunds_cents` and `committed_returned_qty` are computed by the commerce client (every return whose status is not `rejected`). The policy only consumes them. Counting pending returns stops two tricks: splitting a refund across requests while the first one is still pending, and returning the same unit twice.

Entry point: `evaluate(inp: PolicyInput, cfg: PolicyConfig) -> PolicyDecision`.
Config: `PolicyConfig.from_yaml(path)`, path from env `POLICY_PATH`. Single-value options (`window_anchor`, `aggregate_scope`, `release_on`) are `Literal`; `defect.bypass_reasons` is a list of `Literal[WINDOW_EXPIRED, CATEGORY_NOT_RETURNABLE, CATEGORY_NOT_EXCHANGEABLE]`. All thresholds come from `cfg`, never hard-coded. Only one version is active at a time; history lives in git.

### Meaning of NEED_MANAGER
"A human must approve before any side effect." The orchestrator routes it to the approval queue by decision reason:

| Reason | Queue type | Note |
|---|---|---|
| OVER_AUTO_LIMIT | refund_over_limit | |
| DEFECT_CLAIM | defect_review | Orchestrator asks for photos first |
| UNKNOWN_CATEGORY | config_error | System fault, the customer must not be penalised |

One decision can carry several of these reasons.

## Validation (raise `ValidationError`, not a decision)
- `now` is naive.
- `delivered_at > now + 5 min`.
- `shipment_status == "delivered"` with `delivered_at is None`.
- `items` empty, `qty ≤ 0`, duplicate `order_item_id`, or an `order_item_id` not in the order.

## Evaluation order
Per item, with `days = (now.date() - delivered_at.date()).days`, both converted to `cfg.timezone`, clamped at 0 (clock skew may put `delivered_at` a few minutes after `now`, across midnight). All reasons are collected. Item `reasons` order: DEFECT_CLAIM first (defective items), then the order the checks run below (steps 1→7).

1. **Strict checks.** Any failure → INELIGIBLE, stop. Defect claims cannot bypass these.
   - `shipment_status != delivered` → NOT_DELIVERED (lost/exception included).
   - `qty > qty - committed_returned_qty` → QTY_EXCEEDED.
2. **Soft checks** (reasons only):
   - `days ≥ cfg.return.window_days` → WINDOW_EXPIRED.
   - category not in YAML → UNKNOWN_CATEGORY.
   - return and `returnable: false` → CATEGORY_NOT_RETURNABLE; exchange and `exchange: none` → CATEGORY_NOT_EXCHANGEABLE.
3. **Soft failure gate.** Let `blocking` = WINDOW_EXPIRED / CATEGORY_NOT_* reasons from step 2.
   - Not defective and `blocking` is non-empty → INELIGIBLE, stop. Missing info is not asked for. Set `defect_claim_possible = True` when `condition_claim is None` and `blocking ⊆ cfg.defect.bypass_reasons`.
   - Defective and `blocking` contains a reason outside `cfg.defect.bypass_reasons` → INELIGIBLE, stop.
4. **Missing info**: `condition_claim is None` → `MissingField(condition)`. Exchange with `target_variant is None` → `MissingField(target_variant)`. Item → NEEDS_INFO, stop. This also applies to defective items.
5. **Defective** (`condition_claim == "defective"`): the item is always NEEDS_REVIEW. Reasons = DEFECT_CLAIM + step-2 reasons + exchange-check reasons (step 7). None of these change the status. The bot never approves or denies a defect claim on its own.
   - Exchange to the **same variant_id** (replacement) is always in scope, even for `exchange: none` categories. In that case CATEGORY_NOT_EXCHANGEABLE and EXCHANGE_SCOPE are not recorded.
   - Target out of stock → still NEEDS_REVIEW, with OUT_OF_STOCK. The reviewer decides between refund and waiting for stock.
6. `condition_claim` not in `allowed_conditions` → INELIGIBLE / CONDITION_NOT_ACCEPTED.
7. **Exchange checks.** For non-defective items a failure → INELIGIBLE.
   - Scope: `same_product` requires the same `product_id` and a different `variant_id`; `same_category` requires the same `category_code`. Otherwise EXCHANGE_SCOPE. Skipped for UNKNOWN_CATEGORY.
   - `target.stock < qty` → OUT_OF_STOCK.
   - Price (only for `same_category` when `require_same_price`): `target.list_price_cents != item.variant.list_price_cents` → PRICE_MISMATCH. `same_product` has no price check, so sale buyers can swap size. Skipped for UNKNOWN_CATEGORY.
8. UNKNOWN_CATEGORY → NEEDS_REVIEW. Otherwise the item is ELIGIBLE.

Aggregate (priority NEED_INFO > NEED_MANAGER > APPROVE > DENY):
- `refund_cents` (return only) = Σ `unit_price_cents × qty` over ELIGIBLE and NEEDS_REVIEW items. MVP has no order-level discount, tax, or shipping refund, and datagen must not generate them.
- Any NEEDS_INFO → NEED_INFO, with `missing` filled and `refund_cents = None`.
- Any NEEDS_REVIEW → NEED_MANAGER. DEFECT_CLAIM / UNKNOWN_CATEGORY are copied into decision `reasons`.
- Return with at least one ELIGIBLE or NEEDS_REVIEW item and `committed_refunds_cents + refund_cents ≥ cfg.refund.auto_limit_cents` → NEED_MANAGER + OVER_AUTO_LIMIT. A request with nothing to refund never hits the limit (it stays DENY even if earlier refunds are already over it). Exchanges are not subject to the limit.
- At least one ELIGIBLE → APPROVE, including a partial approve.
- Otherwise → DENY. `reasons = [NOT_DELIVERED]` when every requested item is not delivered; the orchestrator then switches to WISMO.
- `refund_cents`: amount for APPROVE / NEED_MANAGER on returns. `None` for DENY, NEED_INFO, and every exchange.

### Defect hint
When any item has `defect_claim_possible`, the reply must include a sentence like "This item is outside our return window. If it arrived damaged or defective, tell me and we'll have it reviewed." The output guardrail enforces this. A DENY writes no return record and has no side effects. If the customer then reports a defect, NLU extracts `condition_claim = "defective"` and the orchestrator calls `evaluate` again, which gives NEED_MANAGER / DEFECT_CLAIM. When a customer says "replace it" for a defective item, the orchestrator fills `target_variant` with the original variant so the bot does not have to ask.

## Required test cases
| # | Input | Expected |
|---|---|---|
| 1 | Delivered 29 days ago (store TZ) | APPROVE |
| 2 | Delivered 30 days ago, condition like_new | DENY, item WINDOW_EXPIRED, defect_claim_possible=False |
| 3 | delivered_at 23:30 LA time (already the next day in UTC) | Days counted in LA time |
| 4 | Window spans a DST transition | No off-by-one day |
| 5 | All items in_transit | DENY, reasons=[NOT_DELIVERED] |
| 6 | Refund 4999 | APPROVE |
| 7 | Refund 5000 | NEED_MANAGER / OVER_AUTO_LIMIT |
| 8 | committed_refunds_cents 3000 (return still pending), new request 2500 | NEED_MANAGER / OVER_AUTO_LIMIT |
| 9 | final_sale item, condition like_new | DENY, item CATEGORY_NOT_RETURNABLE |
| 10 | One eligible item + one final_sale item | Partial APPROVE, refund counts only the eligible item |
| 11 | qty exceeds `qty - committed_returned_qty` (earlier return still pending) | Item INELIGIBLE / QTY_EXCEEDED |
| 12 | Condition worn | DENY, item CONDITION_NOT_ACCEPTED |
| 13 | Condition None | NEED_INFO, missing=[MissingField(item, "condition")], refund None |
| 14 | Condition defective | NEED_MANAGER / DEFECT_CLAIM, item NEEDS_REVIEW, refund = amount |
| 15 | Exchange to another size of the same product, in stock | APPROVE, refund_cents=None |
| 16 | Exchange, target stock = 0 | DENY / OUT_OF_STOCK |
| 17 | Exchange apparel to a different category | DENY / EXCHANGE_SCOPE |
| 18 | Exchange accessories to a same-category variant with a different list price | DENY / PRICE_MISMATCH |
| 19 | Exchange same_product; item bought on sale, target has full list price | APPROVE (no price check) |
| 20 | Exchange same_product to the same variant_id (not defective) | DENY / EXCHANGE_SCOPE |
| 21 | Exchange with target_variant None | NEED_INFO, missing field target_variant |
| 22 | Exchange underwear (not defective) | DENY / CATEGORY_NOT_EXCHANGEABLE |
| 23 | Partial delivery: one item delivered, one in_transit | APPROVE, second item NOT_DELIVERED, refund counts only the delivered item |
| 24 | Condition None, delivered 45 days ago | DENY, item WINDOW_EXPIRED, defect_claim_possible=True |
| 25 | Condition None, final_sale | DENY, item CATEGORY_NOT_RETURNABLE, defect_claim_possible=True |
| 26 | Condition None, past window and QTY_EXCEEDED | INELIGIBLE, defect_claim_possible=False |
| 27 | Condition worn, past window | defect_claim_possible=False |
| 28 | Case 24 re-evaluated with condition defective | NEED_MANAGER, item NEEDS_REVIEW [DEFECT_CLAIM, WINDOW_EXPIRED] |
| 29 | Case 25 re-evaluated with condition defective | NEED_MANAGER, item NEEDS_REVIEW [DEFECT_CLAIM, CATEGORY_NOT_RETURNABLE] |
| 30 | Defective, not delivered | DENY / NOT_DELIVERED |
| 31 | Defective, cfg.defect.bypass_reasons without WINDOW_EXPIRED, past window | INELIGIBLE / WINDOW_EXPIRED |
| 32 | Defective with refund ≥ limit | NEED_MANAGER, reasons ⊇ {DEFECT_CLAIM, OVER_AUTO_LIMIT} |
| 33 | Defective exchange of underwear to the same variant_id | NEED_MANAGER, item [DEFECT_CLAIM] only, refund None |
| 34 | Defective exchange to a different category | NEED_MANAGER, item [DEFECT_CLAIM, EXCHANGE_SCOPE] |
| 35 | Defective exchange, target stock 0 | NEED_MANAGER, item [DEFECT_CLAIM, OUT_OF_STOCK] |
| 36 | Defective exchange, target_variant None | NEED_INFO, missing target_variant |
| 37 | Category missing from YAML | NEED_MANAGER / UNKNOWN_CATEGORY |
| 38 | One item missing condition + one defective item | NEED_INFO |
| 38a | committed_refunds_cents 6000, only a final_sale item requested | DENY (no OVER_AUTO_LIMIT, nothing to refund) |
| 39 | Each validation rule above | ValidationError |

Property tests (hypothesis):
- No item is ELIGIBLE when days since delivery ≥ `window_days`.
- `refund_cents` equals, and so never exceeds, the total paid for ELIGIBLE + NEEDS_REVIEW items.
- Monotonicity: if `committed_refunds_cents = X` gives APPROVE, then X−1 (all else equal) also gives APPROVE.
- A defective item is INELIGIBLE only for a strict reason, or for a soft reason outside `cfg.defect.bypass_reasons`.
- `defect_claim_possible` ⇒ status INELIGIBLE, `condition_claim is None`, no strict reason.
- UNKNOWN_CATEGORY never produces DENY for that item on its own.
- Determinism: same input, same output.

CI: a test asserts every `category_code` in the random catalog exists in the policy YAML. Case 37 fixtures deliberately use a category missing from the YAML and are exempt.

## Datagen
CLI: `datagen --seed 42 --customers 2000 --orders 5000 --now 2026-09-28T12:00:00Z`
- Fixed `now` keeps the dataset reproducible. The commerce mock reads `MOCK_NOW` instead of the system clock.
- `delivered_at` spread over 0–60 days before `now`.
- Shipment mix: delivered 70%, in_transit 15%, label_created 8%, exception 5%, lost 2%.
- About 20 fixture orders per test case above. These fixtures are reused as the P1 golden set.
  - Cases 4 (DST) and 31 (custom `bypass_reasons`) have no fixtures: with the fixed `now` no DST change falls inside the window, and 31 varies the config, not the data. Unit tests cover both. Case 39 is input validation, not data.
  - Each fixture has its own customer, products, and variants (`sku` and `order_number` prefixed `FX-`), so fixtures do not depend on `--customers` / `--orders`.
  - Each fixture stores the request and the expected `code`, decision `reasons`, and `refund_cents`. Datagen builds the `PolicyInput` from the generated rows (same mapping as the commerce client, see Schema) and runs `evaluate()`; any mismatch fails generation.
  - Written to `evals/datasets/policy_fixtures.jsonl` (committed; a test fails if it is stale).
- Reproducible: one `random.Random` per stream (bulk, fixtures) seeded from `--seed`. No `uuid4()`, no `datetime.now()`, no Faker (its output changes between versions).
- Datagen is a package, so it cannot import the app's ORM models. Rows are dataclasses whose fields are the column names; a test in `commerce_mock` checks they match the models.
- Load into the DB with COPY (`TRUNCATE` first, so re-seeding is safe). The schema must exist (`alembic upgrade head`).

## Commerce mock API
| Method | Path |
|---|---|
| GET | /customers/lookup?email= |
| GET | /customers/{id}/orders?limit= |
| GET | /orders/{order_number} |
| GET | /shipments/{tracking}/events |
| GET | /variants/{id} |
| POST | /returns (header Idempotency-Key) |
| POST | /refunds (header Idempotency-Key) |
| POST | /returns/{id}/inspect |

- Per-route fault injection via env: `MOCK_LATENCY_P50_MS`, `MOCK_LATENCY_P99_MS`, `MOCK_ERROR_RATE`.
- Every request is written to `commerce.request_log`.
- **Concurrency re-check on writes.** The policy decides on a snapshot, so two requests arriving together (two tabs, double submit) can both be approved before either return is written. `Idempotency-Key` does not help, because they are different requests. `POST /returns` and `POST /refunds` must therefore re-check inside one transaction, locking the order's `order_items` rows (`SELECT … FOR UPDATE`):
  - requested qty ≤ `qty − committed_returned_qty` for every item;
  - `committed_refunds_cents + new amount < auto_limit_cents` unless the refund is manager-approved.
  - On violation → `409 Conflict`, nothing written. Handling 409 (rebuild the snapshot, call `evaluate` again) is orchestrator work in P1.
  - Test: two concurrent `POST /returns` for the last remaining unit → exactly one 201 and one 409.
- Logfire instrumentation: fastapi, asyncpg.

## Infra
Compose services:
- `pgvector/pgvector:pg16`
- `redis:7`
- `commerce-mock`

Logfire needs only the `LOGFIRE_TOKEN` variable.

## Exit criteria
- Policy has 100% branch coverage and passes all required cases and property tests.
- `compose up` + seed < 1 min.
- Mock p50 < 20ms with fault injection off.
- `scripts/smoke_test.py`: look up order → evaluate → print decision, with the trace visible in Logfire.
