# Phase 1 spec (PoC)

Status: accepted (2026-10-14). Decisions: NLU and response on an LLM API, at most two calls per turn (ADR-0010); dev/test on the Groq free tier; verification by email + order number; a confirmation step before any side effect; conversation state in Redis (24h TTL); golden set = template-generated + LLM-written conversations.

## Goal
A text-in/text-out chat API that handles the three intents (return, exchange, WISMO) end to end against the commerce mock, with every decision taken by `dtc_policy.evaluate()`, and a measured latency breakdown per span.

Exit criteria (roadmap): intent accuracy ≥ 90% (per turn), decision accuracy 100%, real latency breakdown available. Added here: verification is never bypassed (100%); side effects match the decision and the customer's confirmation (100%); the share of turns that end in handoff is reported (no target in P1, it sizes the P2 fallback agent).

Metrics measured on a Groq model are provisional (ADR-0010): accuracy and latency are re-measured on the production model before P1 is closed for good.

Out of scope (P2+): widget, gateway/WebSocket, Chatwoot, manager approval UI, fallback agent, memory worker (episodic/profile memory), Presidio, CSAT survey, user simulator, fine-tuned NLU (optional, ADR-0010).

## Components
| Component | Where | Notes |
|---|---|---|
| Orchestrator | `apps/orchestrator` | FastAPI app, `POST /v1/chat`. Runs one turn. Knows commerce only through the `CommerceClient` Protocol (import-linter contract). |
| NLU | `apps/orchestrator` (pydantic-ai agent) | Structured output `NluResult`. Model from `NLU_MODEL`. |
| Response | `apps/orchestrator` (pydantic-ai agent) | Structured output `Reply` + output guardrail (Hard rule 3). Model from `RESPONSE_MODEL`. |
| Session state | Redis | `ConversationState`, key `conv:{session_id}`, TTL 24h, refreshed every turn. |
| Conversation store | `packages/dtc_store`, schema `support` | P1 subset: `conversations`, `messages`, `audit_log`, `nlu_log`. Own Alembic history (ADR-0006). |
| Commerce | `dtc_commerce` + mock | One addition to the mock API, see "Commerce API changes". |

## Turn pipeline
```
POST /v1/chat {session_id, message}
  1. acquire per-session lock (Redis SET NX, 10s) ── busy → 409 "previous message still processing"
  2. load ConversationState (new session if absent)
  3. NLU (LLM call #1)          ║ in parallel: prefetch the order if one is verified
  4. escalation rules ── handoff → template reply, outcome=handoff
  5. workflow step for the active intent (pydantic-graph):
       verification gate → slot filling → PolicyInput → evaluate() → confirm → side effect
  6. reply: template (WISMO happy path, verification prompts, confirmation, handoff, fallback)
            or Response agent (LLM call #2) + output guardrail
  7. save state (Redis), write messages, nlu_log, audit_log (Postgres), release lock
→ ChatOut
```

## Contracts
New in `dtc_contracts`:

```python
# nlu.py
Intent = Literal["return", "exchange", "wismo", "provide_info",
                 "affirm", "negate", "request_human", "other"]

class ItemMention(ContractModel):       # what the customer said about one item
    text: str                           # "the blue hoodie", "both shirts"
    size: str | None = None
    color: str | None = None
    qty: PositiveInt | None = None

class NluResult(ContractModel):
    intent: Intent
    confidence: float                   # 0..1
    order_number: str | None = None
    email: str | None = None
    items: list[ItemMention] = []
    item_ids: list[UUID] = []           # chosen from the order's item list in the prompt, if given
    condition_claim: ConditionClaim | None = None
    target: ItemMention | None = None   # exchange: "size L", "the black one"
    target_variant_id: UUID | None = None   # chosen from the variant list in the prompt, if given

# chat.py
class ChatIn(ContractModel):  session_id: str (1..128); message: str (1..2000)
class ChatOut(ContractModel): reply: str; intent: Intent; decision: PolicyDecision | None; trace_id: str | None
```

Rule 5: everything in `NluResult` is an unverified claim.
- Only the verification step turns `email` + `order_number` into a verified `customer_id`.
- After verification the NLU prompt lists the order's items (and, for an exchange, the product's variants) with their ids. The NLU picks ids from those lists; code keeps only ids that are in the list it sent. This resolves "the second one" or "the one for my son" without an extra LLM call. `items` text mentions are the fallback when no list was sent.

## Conversation state
```python
class ConversationState(BaseModel):          # Redis, JSON
    session_id: str
    turn: int
    customer_id: UUID | None                 # set only by verification
    verify_attempts: int                     # failed attempts
    claimed_email: str | None; claimed_order_number: str | None   # unverified, kept across turns
    intent: Intent | None                    # active workflow
    turns_on_intent: int
    low_confidence_streak: int
    order_number: str | None                 # verified
    selected_item_ids: list[UUID]
    qty: dict[UUID, int]
    conditions: dict[UUID, ConditionClaim]
    targets: dict[UUID, UUID]                # order_item_id -> variant_id (exchange)
    pending: PendingAction | None            # awaiting the customer's confirmation
    last_decision: PolicyDecision | None
    return_id: UUID | None                   # set after a successful create_return
    outcome: Literal["open", "automated", "handoff", "abandoned"]
```
Slots accumulate across turns: an NLU result only adds or overrides the fields it mentions. `PendingAction` holds the request (items, qty, conditions, targets) and the decision shown to the customer.

## Verification
- Required before any order detail is shown and before any side effect, for every intent (shipment details are personal data too).
- Check: `lookup_customer(email)` exists and `get_order(order_number).customer_id` equals it. The comparison uses the commerce system's data, never text.
- A failed check never says which part was wrong (no account enumeration). After 3 failed attempts: handoff.
- Once verified, `customer_id` and `order_number` stay in the state for the session. Another order number later in the session is checked again against the verified `customer_id`.

## Workflows
One pydantic-graph per intent. Shared nodes: `Verify`, `PickItems`, `Decide`, `Confirm`, `Execute`, `Reply`.

**Return**
1. `Verify`.
2. `PickItems`: an order with one item selects it; otherwise use `NluResult.item_ids`, then text mentions matched by product name, size, color. Ambiguous or no match → ask, listing the items.
3. Ask for the condition of each selected item that lacks one (the policy's NEED_INFO says which).
4. `Decide`: build `PolicyInput` (`dtc_commerce.build_policy_input`), `evaluate()`.
5. By decision code:
   - APPROVE or NEED_MANAGER → `Confirm`: summarise what will happen (items, qty, condition as understood, refund amount or "a manager will review") and ask yes/no. Store `PendingAction`.
   - NEED_INFO → ask for `decision.missing`.
   - DENY → explain the reasons; include the defect hint when `defect_claim_possible` (guardrail-enforced); `NOT_DELIVERED` switches to WISMO. No confirmation: nothing is executed.
6. Next turn, with `pending` set:
   - `affirm` → `Execute`: rebuild the snapshot and evaluate again (the confirmation turn can be minutes later). Same decision → `create_return` (APPROVE: `status=approved`; NEED_MANAGER: `status=pending_manager`, `reason_codes=decision.reasons`) and reply with the RMA id or the queue and expected wait; DEFECT_CLAIM asks for photos. Different decision → explain the new one and confirm again if needed.
   - `negate`, or a correction ("no, it's worn") → clear `pending`, apply the correction, go back to `Decide`.
   - Anything else → ask the yes/no question once more; the second time → handoff.
   - `409` from commerce → rebuild and evaluate once more; a second `409` → handoff.

**Exchange**: as Return, plus resolving the target among the product's variants (`GET /products/{id}/variants`): `NluResult.target_variant_id` when the list was in the prompt, else size/color matching. Out of stock or out of scope is the policy's call, not the matcher's.

**WISMO**: `Verify` → shipments and events → template reply for normal states (label created, in transit with ETA, delivered). `exception`, `lost`, or ETA in the past → Response agent with the events, and offer a human. No side effects, so no confirmation.

**Handoff (P1)**: no Chatwoot yet. Mark `outcome=handoff`, write `audit_log`, reply with a template.

## Escalation rules (code, before the workflow)
| Trigger | Action |
|---|---|
| `intent = request_human` | handoff |
| `confidence < 0.6` | ask once to rephrase; the second low-confidence turn in a row → handoff |
| same intent unresolved after 3 turns (`turns_on_intent`, confirmation turns excluded) | handoff |
| 3 failed verifications | handoff |
Emotion and legal keywords come with the escalation detector in P2.

## Side effects
- Only `create_return` in P1 (inspection and refunds are back-office actions, not chat), and only after an explicit `affirm`.
- `Idempotency-Key = sha256(session_id, the pending action)`: a retry of the same confirmation reuses it; a changed request is a new key.
- Every side effect and every handoff writes `support.audit_log` (actor `bot`, `policy_version`, `trace_id`).
- `NEED_MANAGER` writes a `pending_manager` return: a record awaiting approval, no RMA label and no money (Hard rule 4 still holds: the refund needs `approved_by`).

## LLM usage
- pydantic-ai agents; models from settings: `NLU_MODEL`, `RESPONSE_MODEL` (pydantic-ai model strings). Dev/test: `groq:<model>` (choose one with tool calling / JSON schema support). Target: `anthropic:claude-haiku-5-5`, thinking off or low effort, prompt caching on the static prefix.
- Prompts live in git (`apps/orchestrator/prompts/`) with a version string logged on every call (ADR-0004).
- `nlu_log` stores every NLU call: prompt version, model, scrubbed input, output, latency. It is the training set for a possible distilled NLU (ADR-0010).
- Groq free tier: requests are sequential with retry and backoff on 429; the eval runner caches responses by (model, prompt version, input) so re-runs cost no quota. Only synthetic data is sent.
- Tests and CI never call a real model: pydantic-ai `FunctionModel`/`TestModel` stubs. The stub NLU replays the golden set's ground-truth `NluResult`, so CI checks workflows, policy wiring, confirmation, and side effects deterministically.

## Response generation and guardrail
- Response agent input (`deps`): the decision, the verified order facts it needs, the last turns. It calls no tools.
- Output `Reply(text: str, code: DecisionCode | None, mentions_defect_hint: bool)`.
- Guardrail (output validator):
  - `code` must equal `decision.code`, and the text must not promise what the decision does not allow (keyword list per code in P1, e.g. no refund wording on DENY).
  - `defect_claim_possible` → `mentions_defect_hint` must be true.
  - Money in the text must equal `refund_cents` formatted as dollars.
  - On failure: one `ModelRetry`; if still wrong, the decision template (Hard rule 3).
- Templates exist for every decision code and every workflow prompt, so a turn always has an answer even with the LLM down.

## `dtc_store` (schema `support`, P1 subset)
| Table | Columns |
|---|---|
| conversations | id, session_id (unique), customer_id (nullable), started_at, ended_at, primary_intent, outcome |
| messages | id, conversation_id, turn, role (customer/bot), content_redacted, intent, decision_code, ts, trace_id |
| nlu_log | id, conversation_id, turn, model, prompt_version, input_redacted, output jsonb, latency_ms, ts |
| audit_log | id, actor (bot/human/system), action, entity, entity_id, payload jsonb, policy_version, trace_id, ts |

`*_redacted`: emails and phone numbers masked with regexes in P1; Presidio in P2. The same scrubbing runs before text reaches Logfire or an LLM provider's logs.

## Commerce API changes
- `GET /products/{product_id}/variants` → `ProductVariantsOut` (every variant of the product, for exchange targets). Added to the mock, `dtc_contracts.commerce`, and `CommerceClient` (`product_variants`, `None` for an unknown product).

## Golden set (200 conversations)
- `evals/datasets/golden_conversations.jsonl`, committed.
- ~150 generated from the datagen fixtures with templates: varied phrasing, typos, information in different orders, verification failures, multi-item orders, corrections at the confirmation step, intent switches (DENY/NOT_DELIVERED → WISMO). Reproducible from a seed.
- ~50 written by an LLM from fixture seeds for more natural language, reviewed by hand before commit; about 30 of the 200 are off-script (questions about the policy, two requests at once, small talk) to measure the handoff share.
- Each conversation: customer turns; per turn the expected intent and ground-truth `NluResult`; the expected final decision (`code`, `reasons`, `refund_cents`); the expected side effect (return created? status) given the scripted confirmation; the expected outcome (automated/handoff).
- Coverage: every decision code, all three intents, every escalation rule, confirm and reject at the confirmation step, at least 20 verification-failure conversations.

Metrics:
| Metric | Definition | Target |
|---|---|---|
| Intent accuracy | turns where NLU intent = expected | ≥ 90% |
| Decision accuracy | conversations reaching `evaluate()` whose final decision equals the expected one | 100% |
| Verification | conversations with order details or side effects before verification | 0 |
| Side effects | conversations whose created returns match the expectation (none without `affirm`) | 100% |
| Handoff share | turns ending in handoff, overall and on off-script conversations | reported |

## Latency measurement
Spans per turn: `session.load`, `nlu`, `prefetch`, `workflow`, `policy`, `commerce.write`, `response`, `guardrail`, `persist`. `evals/runners/latency_report.py` prints p50/p95 per span and end to end from a golden run. The report on the production model is committed to `docs/phases/phase-1-latency.md`; Groq runs are kept apart and labelled.

## Order of work
1. Contracts (`nlu.py`, `chat.py`); `GET /products/{id}/variants` in the mock, contracts, and client.
2. `dtc_store`: schema `support`, migrations, repository functions.
3. State, verification, item matching, confirmation, workflows: pure code with a stub NLU; unit tests.
4. Orchestrator API, Redis, session lock, persistence; integration tests with mock + Redis + Postgres.
5. NLU agent + prompt (Groq in dev).
6. Response agent + guardrail + templates.
7. Golden set (generator + LLM-written part) and eval runner.
8. Latency report; P1 exit review (re-run on the production model when a Claude key is available).

## Open questions
- Confidence threshold 0.6 is a guess; calibrate on the first golden run.
