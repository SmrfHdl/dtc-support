# Glossary

Plain-language definitions for the terms used across `docs/` and `CLAUDE.md`. Grouped by area; alphabetical inside each group. `code-style` names are exact identifiers from the spec.

## 1. E-commerce and customer support

| Term | Meaning |
|---|---|
| Automation rate | Share of conversations the bot resolves with no human involved and that the customer does not reopen within 72 hours. Target ≥ 70%. |
| Black Friday | The biggest shopping day of the year (late November, US). Used here as the peak-traffic scenario. |
| Carrier | The delivery company (UPS, FedEx, USPS…). |
| Catalog | The full list of products the store sells. |
| Category | A group of products with the same return rules, e.g. `apparel` (clothes), `footwear` (shoes), `accessories` (bags, hats), `underwear`, `final_sale`. Stored in the DB as `category_code`; its rules live only in the policy YAML. |
| Chargeback | The customer asks their bank/card company to reverse a payment instead of asking the store. Costly for the store, so the word triggers an escalation. |
| Chatwoot | Open-source help-desk software where human agents read and answer chats handed off by the bot. |
| Condition (`condition_claim`) | What the customer says about the item's state. `new_with_tags` = unused, tags still on; `like_new` = used briefly, no visible wear; `worn` = visibly used; `defective` = broken, damaged, or faulty from the factory/shipping. It is a *claim*; the warehouse verifies it. |
| CSAT | Customer Satisfaction score: the 1–5 rating a customer gives after the chat. Target > 4.5. |
| Customer fact | Something the customer said about themselves ("I'm usually size M"). Stored as memory, never trusted for policy. |
| Defect claim | Customer says the item is defective. Never decided by the bot; always goes to a human. |
| Delivered at (`delivered_at`) | Timestamp when the carrier marked the package delivered. Starting point of the return window. |
| DTC | Direct-to-Consumer: a brand selling on its own website rather than through Amazon or retail stores. |
| ETA | Estimated Time of Arrival of a package. |
| Escalation | Moving a conversation from the bot to a human (see *Handoff*) or to an approval queue. |
| Exchange | Customer sends an item back and receives a different item (another size, color, or product) instead of money. |
| Exchange scope | Which items an exchange may target. `same_product`: same product, different size/color. `same_category`: any product in the same category. `none`: no exchanges. |
| Final sale | Items sold as "no returns, no exchanges", usually heavily discounted. |
| Fixture | A prepared, known test record (e.g. an order delivered exactly 30 days ago) used by tests. |
| Handoff | Transferring the chat to a human agent in Chatwoot. |
| Inspection | Warehouse checks the returned item. `inspection_passed` means it is accepted; only then is the refund paid. For defect returns it confirms the defect is real. |
| List price (`list_price_cents`) | The item's current normal price on the store, before any discount. |
| Manager approval / approval queue | A list of requests waiting for a human to approve before anything is executed. Types: `refund_over_limit`, `defect_review`, `config_error`. |
| Order | One purchase. Has an `order_number` shown to the customer (e.g. `#10234`). |
| Order item | One line in an order: a specific variant and quantity (e.g. "2 × T-shirt, blue, M"). |
| Partial approve | Some items in the request are accepted, others are not; the accepted ones proceed. |
| Partial delivery | An order shipped in several packages, some delivered and some not yet. |
| PII | Personally Identifiable Information: name, email, address, phone, card number… Must be hidden before logging. |
| Product | A sellable thing, e.g. "Classic T-shirt". Has several *variants*. |
| Qty | Quantity (number of units). |
| Refund | Money returned to the customer. |
| Refund limit / auto limit (`auto_limit_cents`) | Refunds below this amount ($50) are approved automatically; at or above, a manager must approve. Counted per order, not per request. |
| Return | Customer sends an item back and gets money back. |
| Return abuse / fraud ring | Customers (or groups) exploiting return policies, e.g. returning used items repeatedly. Out of scope for now. |
| Return window | The period after delivery during which a return is allowed: fewer than 30 days, counted in store-local calendar days. |
| Returned qty (`returned_qty`, `committed_returned_qty`) | Units of an order item already returned. *Committed* includes returns still in progress (not rejected), so the same unit cannot be returned twice. |
| RMA | Return Merchandise Authorization: the official "return approved" record/number the customer uses to send the item back. |
| Shipment | One package of an order. Statuses: `label_created` (shipping label printed, not picked up yet), `in_transit` (on the way), `out_for_delivery` (on the truck today), `delivered`, `exception` (a problem: wrong address, damage, customs), `lost`. |
| SKU | Stock Keeping Unit: the store's internal code for a product. |
| Stock | Number of units available in the warehouse. |
| Store timezone | The store's local time (`America/Los_Angeles`). Day counting uses it, not UTC. |
| Tracking number | The carrier's code for following a package. |
| Unit price (`unit_price_cents`) | The price the customer actually paid per unit, after item discounts. Used to compute refunds. |
| Variant | A specific version of a product: size + color (e.g. "T-shirt, blue, M"). Has its own stock and price. |
| WISMO | "Where Is My Order?" — the most common support question: order status and tracking. |

## 2. Policy engine (`dtc_policy`)

| Term | Meaning |
|---|---|
| Aggregate (`aggregate_scope: order`) | Amounts are added up across the whole order, so a customer cannot avoid the $50 limit by splitting a refund into small requests. |
| Blocking reason | A soft-check failure (window, category) that makes a normal item ineligible. |
| Bypass (`defect.bypass_reasons`) | The list of soft-check failures that a defect claim is allowed to skip (it then goes to review instead of being denied). |
| Committed refunds (`committed_refunds_cents`) | Refund money of every return on the order that is not rejected, including ones still pending. |
| Decision code | Overall result of `evaluate()`: `APPROVE` (go ahead), `DENY` (not allowed), `NEED_MANAGER` (a human must approve before any action), `NEED_INFO` (ask the customer something first). Priority: NEED_INFO > NEED_MANAGER > APPROVE > DENY. |
| Defect hint (`defect_claim_possible`) | Flag on an item denied only for reasons a defect could bypass, when the customer has not stated a condition. The bot's reply must then invite the customer to report a defect. |
| Deterministic | Same input always gives the same output. No randomness, no clock, no network. |
| `evaluate()` | The single function that decides a return/exchange request. |
| Fail closed | When something is unknown or broken, choose the safe outcome. Here: send to a human, never silently deny the customer. |
| Hard / strict check | Check that nothing can bypass: item delivered, quantity available. |
| Item status | Per-item result: `ELIGIBLE`, `INELIGIBLE`, `NEEDS_REVIEW` (a human decides), `NEEDS_INFO` (missing data). |
| Missing field (`MissingField`) | What must be asked from the customer: `condition` or `target_variant`, for a specific item. |
| Monotonicity | Property test idea: if a larger amount is approved, a smaller one (all else equal) must also be approved. |
| Policy | The business rules for returns, exchanges, and refunds. |
| Policy config / YAML | The rules' numbers and options (window days, limit, categories) in a YAML file, loaded by `PolicyConfig.from_yaml`. |
| Policy version (`policy_version`) | Label of the config used for a decision (e.g. `2026.09.28-1`), stored with each return for auditing. |
| Pure function | A function whose result depends only on its arguments and which changes nothing outside itself. |
| Reason code | Why an item/decision came out that way: `NOT_DELIVERED`, `WINDOW_EXPIRED`, `CATEGORY_NOT_RETURNABLE`, `CATEGORY_NOT_EXCHANGEABLE`, `UNKNOWN_CATEGORY` (category missing from config), `QTY_EXCEEDED`, `CONDITION_NOT_ACCEPTED`, `DEFECT_CLAIM`, `EXCHANGE_SCOPE`, `OUT_OF_STOCK`, `PRICE_MISMATCH`, `OVER_AUTO_LIMIT`. |
| Re-evaluate | Calling `evaluate()` again with new information from a later turn (e.g. customer now says it is defective). |
| `release_on: inspection_passed` | Refund money is paid only after the warehouse accepts the item. |
| Soft check | Check that a defect claim may bypass: return window, category rules. |
| Target variant (`target_variant`) | The variant the customer wants to receive in an exchange. |
| `window_anchor: delivered_at` | The return window starts counting from the delivery date. |

## 3. AI / LLM

| Term | Meaning |
|---|---|
| Agent | An LLM plus instructions and tools that handles one kind of task (e.g. `returns_agent`). Exactly one runs per turn. |
| Async agent | Agent that runs in the background after the reply is sent (summaries, memory, judge), so it adds no delay. |
| Confidence | NLU's certainty (0–1) about the intent it detected. Low → ask to clarify. |
| Continuous batching | GPU serving technique that processes many requests together for high throughput. |
| `deps` | pydantic-ai term: data passed into an agent run (order, decision…) so the agent does not need to fetch it. |
| Embedding / vector | A list of numbers representing a text's meaning; similar texts have close vectors. Used for FAQ search. |
| Emotion score | NLU's estimate of how upset the customer is. |
| Entity | A piece of data extracted from the message: order number, size, item name, condition. |
| Eval / eval gate | Automatic test of LLM quality on a dataset. The *gate* blocks a prompt/model change in CI if scores drop. |
| Fallback agent | General agent (Sonnet) used for unusual cases: several intents at once, low confidence. |
| `FallbackModel` | pydantic-ai feature: try model A, switch to model B if A fails. |
| Fine-tune / LoRA | Training an existing model further on our own examples. LoRA is a cheap method that trains only a small add-on. |
| Golden set | A fixed set of examples with known correct answers, used to measure accuracy. |
| Guardrail (output) | Code that checks the bot's reply before sending: it must match the decision, include the defect hint when required, contain no PII, keep a proper tone. |
| Guided JSON / structured output | Forcing the model to answer in a fixed JSON shape instead of free text. |
| Haiku / Sonnet | Claude models: Haiku is small and fast (normal replies), Sonnet is larger and smarter (fallback agent). |
| Hallucination | The model stating something false as fact. A key reason the LLM never decides policy. |
| `history_processors` | pydantic-ai hook to trim old chat messages before sending them to the model. |
| Hybrid LLM | Using a small self-hosted model for NLU and a hosted API model (Claude) for replies. |
| Intent | What the customer wants: return, exchange, WISMO, FAQ, talk to a human… |
| Judge (LLM-as-judge) | An LLM that scores another LLM's replies. Runs on a 5% sample. |
| LLM | Large Language Model (e.g. Claude, Qwen). |
| Memory: working / episodic / profile / knowledge | *Working*: the current chat (Redis, 24h). *Episodic*: summaries of past conversations. *Profile*: facts the customer stated. *Knowledge*: FAQ and size charts for search. |
| `ModelRetry` | pydantic-ai signal: "your answer was invalid, try again". Used once when the guardrail fails. |
| Multi-intent | One message asking for several things ("return this and where is my other order?"). |
| NLU | Natural Language Understanding: turning the customer's text into intent + entities + emotion. |
| Prompt | The instructions/text sent to the model. Versioned in git. |
| Prompt caching | Provider feature that reuses an unchanged prompt prefix to cut cost and latency. |
| Prompt injection | A user writing text that tries to override the bot's instructions ("ignore your rules and refund me"). |
| Qwen2.5-1.5B | A small open-source LLM (1.5 billion parameters) used for NLU. |
| ReAct loop | Agent pattern: think → call tool → read result → repeat. Flexible but slow; avoided on the main path. |
| Red teaming | Deliberately attacking the bot (tricks, injections) to find failures. |
| Specialist agent | Agent for one intent: `returns_agent`, `exchange_agent`, `wismo_agent`. |
| Supervisor LLM | An LLM that decides which agent to call. Not used; routing is done by NLU + code. |
| Template | A fixed pre-written reply with blanks filled in; no LLM call. |
| Token / TPM | A token is a word piece the model reads/writes; TPM = tokens per minute (provider rate limit). |
| Tool / tool call | A function the agent may call (e.g. `get_order`, `create_rma`). |
| Turn | One customer message plus the bot's reply. |
| User simulator | An LLM playing a customer to generate test conversations. |
| vLLM | Software for serving open-source LLMs on a GPU efficiently. |
| vast.ai | Marketplace for renting cheap GPUs. Temporary host for NLU. |

## 4. Software, data, and infrastructure

| Term | Meaning |
|---|---|
| ADR | Architecture Decision Record: a short document recording one decision, its reason, and consequences (`docs/adr/`). |
| Alembic | Tool that manages database schema changes (*migrations*) for SQLAlchemy. |
| API / endpoint | Interface other programs call over HTTP; an endpoint is one URL + method, e.g. `GET /orders/{order_number}`. |
| Async / sync | *Async* code can wait on I/O (DB, network) without blocking other work; *sync* waits in line. |
| asyncpg | Fast async Postgres driver for Python. |
| Audit log (`audit_log`) | Append-only table recording who did what and when, for accountability. |
| Autoscaling / KEDA | Automatically adding/removing servers based on load; KEDA does this on Kubernetes. |
| BaseModel / Pydantic | Pydantic is a Python library that defines data shapes (`BaseModel`) and validates input against them. |
| Branch coverage | Percentage of `if/else` paths executed by tests. Target 100% for the policy. |
| Cache | Fast temporary copy of data to avoid recomputing/refetching it. |
| Canary | Releasing a new version to a small share of users first (5% → 25% → 100%). |
| CDC | Change Data Capture: streaming every DB change to another system. |
| Cents (`*_cents`) | Money stored as whole cents (`4999` = $49.99) to avoid decimal rounding errors. |
| Chaos testing | Deliberately breaking parts (GPU down, slow DB) to check the system degrades gracefully. |
| CI | Continuous Integration: automatic checks (tests, lint, types, evals) on every push. |
| Column / table / schema | A *table* holds rows of one kind (orders); *columns* are its fields; a Postgres *schema* is a named group of tables (`commerce`, `support`). |
| Contract | The agreed data shapes between components (`PolicyInput`, `PolicyDecision`), in `dtc_contracts`. |
| Contract test | Test that two components still agree on those shapes. |
| COPY | Postgres command for bulk-loading data quickly. |
| Datagen | Our script that generates fake but realistic customers/orders for testing. |
| `datetime` naive / aware | *Naive* has no timezone (ambiguous); *aware* includes one. The policy rejects naive times. |
| Degradation ladder | Planned steps to reduce features under failure/overload instead of crashing. |
| Docker / docker compose | Docker packages an app with its dependencies in a *container*; compose starts several containers together locally. |
| DST | Daylight Saving Time: clocks shift one hour in spring/autumn; a naive day count can be off by one around it. |
| E2E test | End-to-end test: exercises the full system as a user would. |
| Enum / `StrEnum` / `Literal` | Ways to restrict a value to a fixed set of options (e.g. only `"return"` or `"exchange"`). |
| Env var | Environment variable: a setting passed to a program from outside (e.g. `POLICY_PATH`, `LOGFIRE_TOKEN`). |
| Fault injection | Deliberately adding delays/errors to the mock (`MOCK_LATENCY_*`, `MOCK_ERROR_RATE`) to test resilience. |
| FastAPI | Python web framework used for the gateway and mock API. |
| GDPR / CCPA | EU / California privacy laws; include the right to have personal data deleted. |
| GET / POST / header | HTTP methods: GET reads, POST creates/acts. Headers are extra metadata on a request (e.g. `Idempotency-Key`). |
| Gateway | Entry server that accepts chat connections, checks auth and rate limits, and passes messages on. |
| Graph DB / HugeGraph | Database specialised in relationships (who is linked to whom). Not used. |
| Helm / ArgoCD / k3s / Kubernetes (k8s) | Kubernetes runs containers across servers; k3s is a light version; Helm packages app configs; ArgoCD deploys from git automatically. |
| Hypothesis / property test | Library that generates many random inputs and checks a rule always holds (e.g. "never approve after 30 days"). |
| Idempotency / `Idempotency-Key` | Sending the same request twice has the same effect as once. The key identifies the request so a retry does not create a second refund. |
| import-linter | Tool enforcing which package may import which (e.g. `dtc_policy` imports only `dtc_contracts`). |
| Integration test | Test of several components together (e.g. code + real DB). |
| JSON / jsonb | Text data format; `jsonb` is Postgres's column type for storing it. |
| k6 | Load-testing tool. |
| Latency / p50 / p95 / p99 | Latency = response time. p95 < 2.5s means 95% of replies arrive within 2.5s; p50 is the median. |
| Load test | Sending heavy artificial traffic to measure capacity. |
| Logfire | Hosted (SaaS) observability tool from the Pydantic team, used for traces. |
| Migration | A versioned script that changes the DB structure. |
| Mock (commerce mock) | A fake version of the store's backend (orders, shipments) so we can develop without Shopify. |
| MVP / PoC | *PoC*: Proof of Concept, a rough prototype to test feasibility. *MVP*: Minimum Viable Product, the smallest version usable by real users. |
| Nullable | A DB column that may be empty (`NULL`). |
| Observability / trace / span | Ability to see what the system did. A *trace* is the full record of one request; a *span* is one step inside it (NLU call, DB query) with its duration. |
| Orchestrator | The component that runs the per-turn workflow: NLU → fetch order → policy → reply → guardrail. |
| Package / uv workspace | *Package*: a reusable Python library (`packages/dtc_*`). *uv workspace*: one repo holding several packages managed together by the `uv` tool. |
| pgvector | Postgres extension for storing and searching embeddings. |
| Pod | Smallest running unit on Kubernetes (one or more containers). |
| Postgres | The main relational database. |
| Presidio / scrubbing | Presidio is a library that detects and masks PII; scrubbing removes secrets/PII from logs. |
| Prometheus / Grafana | Metrics collection and dashboards for infrastructure (CPU, memory, request counts). |
| Protocol (`CommerceClient`) | Python interface: lists the methods a class must have, without a concrete implementation. Lets us swap the mock for Shopify. |
| pydantic-ai / pydantic-graph / pydantic-evals | Pydantic libraries for LLM agents / step-by-step workflows / LLM evaluations. |
| pyright / ruff / pytest | Type checker / linter + formatter / test runner. |
| Rate limit / 429 | A cap on requests per time; HTTP 429 = "too many requests". |
| React / TS | JavaScript UI library / TypeScript (JavaScript with types). Used for the chat widget. |
| Redis / Redis Streams | Fast in-memory store for sessions and cache; *Streams* is its message queue feature. |
| Regression | Something that used to work breaks after a change. |
| Rollback | Reverting to the previous version. |
| RPS | Requests per second (here: customer messages per second). Peak target 200. |
| RTT | Round-Trip Time: network delay to a server and back. |
| Runbook | Step-by-step instructions for operating or fixing the system. |
| SaaS | Software as a Service: hosted by a vendor, used over the internet. |
| Sampling | Keeping only part of the traces to save cost (all errors/slow ones, a share of the rest). |
| Seed | (1) A number that makes random generation reproducible (`--seed 42`). (2) Loading initial data into the DB. |
| Session | Server-side state for one chat (kept in Redis). |
| Shadow mode | New system runs on real traffic but its answers are not shown; only compared. |
| Side effect | An action that changes the outside world: creating an RMA, issuing a refund, writing to DB. |
| SLO | Service Level Objective: a measurable target (latency, CSAT, automation). |
| Smoke test | A quick basic check that the main path works end-to-end. |
| SQLAlchemy | Python library for working with SQL databases. |
| Staging / prod | *Staging*: a copy of production for final testing. *Prod*: the live system. |
| Stateless | A server that keeps no per-user data in memory, so any copy can handle any request. |
| Stub | A trivial fake that returns canned answers (e.g. an LLM stub for load tests). |
| System of record | The authoritative data source (the commerce DB), trusted over anything the customer says. |
| `timestamptz` / UTC | Postgres timestamp with timezone; UTC is the universal reference time. All times are stored in UTC. |
| Terraform | Tool that creates cloud infrastructure from code. |
| TTL | Time To Live: data expires automatically after this time (e.g. 24h). |
| Unique | DB constraint: no two rows may have the same value in that column. |
| UUID | A long random unique ID, e.g. `3f2b…`. |
| `ValidationError` | Error raised when input data does not match the expected shape or rules. |
| WebSocket (WS) | Persistent two-way connection between browser and server, used for live chat. |
| Widget | The small chat box embedded on the store website. |
| YAML | Human-readable config file format. |

## 5. Symbols used in the spec

| Symbol | Meaning |
|---|---|
| `≥`, `≤` | Greater/less than or equal. |
| `⊆` | "is contained in": every element of the left set is also in the right set. |
| `⊇` | "contains": the left set includes all of the right set. |
| `Σ` | Sum of. |
| `⇒` | "implies": if the left is true, the right must be true. |
| `→` | "results in" / "maps to". |
| `X \| None` | Value of type X, or empty. |
