# dtc-support

AI customer-support agent system for a DTC e-commerce brand.
- Scope: returns, exchanges, WISMO.
- Channel: web chat widget, English.
- Solo developer.

## SLOs
- Automation ≥ 70%: resolved with no human touch and not reopened within 72h. Manager-approved refunds count as human.
- CSAT > 4.5/5.
- Latency, **full end-to-end response** per turn: p50 < 1s, p95 < 2.5s.
- Peak 200 RPS, measured in customer messages per second (Black Friday).

## Hard rules
1. **The LLM never decides policy.** `dtc_policy.evaluate()` (deterministic) decides. The LLM only interprets intent and phrases the reply.
2. **Workflow-first, no supervisor LLM.** Routing is done by the NLU small model plus code. At most one LLM API call per turn on the main path.
3. **Output guardrail:** the reply must match `PolicyDecision.code`. On mismatch: one `ModelRetry`; if still wrong, fall back to the decision template.
4. **Side effects (RMA, refund):** idempotent via `Idempotency-Key`, written to `audit_log`, executed only after `APPROVE`.
5. **System of record beats memory.** Facts extracted from customer text are always `verified=false` and never reach the policy engine.
6. **Policy never calls `datetime.now()`.** `now` is passed in.
7. **Money and time:** money is int `*_cents`, time is `timestamptz` UTC, return-window days are counted in the store timezone.

## Stack
| Area | Components |
|---|---|
| Language, tooling | Python 3.13, uv workspace, ruff, pyright, pytest, hypothesis, import-linter |
| Backend | FastAPI, Pydantic v2, SQLAlchemy 2 async, asyncpg, Alembic |
| Agents | pydantic-ai, pydantic-graph (workflows), pydantic-evals (evals) |
| Observability | Logfire (traces), Prometheus/Grafana (infra metrics, autoscaling) |
| Data | Postgres 16 + pgvector, Redis 7 (session, cache, streams) |
| LLM | NLU: Qwen2.5-1.5B + LoRA on vLLM (vast.ai GPU, temporary). Response: Claude Haiku 4.5. Agent path: Sonnet. Fallback via `FallbackModel` |
| Human desk | Chatwoot |
| Frontend | React + TS widget |
| Infra | docker compose (P0–P1), then k3s + Helm + ArgoCD (P2+) |
| Load testing | k6 |

## Structure
```
apps/       commerce_mock, orchestrator, gateway, memory_worker, widget
packages/   dtc_contracts, dtc_policy, dtc_observability, dtc_datagen, dtc_commerce, dtc_store
ml/nlu/     NLU fine-tuning
evals/      datasets, scenarios, evaluators, runners
tests/      integration, contract, e2e
deploy/     compose, docker, vllm, helm, argocd
docs/       architecture, phases, adr, runbooks
```
Conventions:
- Code lives in `src/<module>/`; packages use the `dtc_` prefix.
- Each app/package has its own `pyproject.toml` and `tests/`.
- Import boundaries are declared in `.importlinter`:
  - `dtc_policy` imports only `dtc_contracts`;
  - `packages` never import `apps`;
  - apps never import each other;
  - the orchestrator only knows the `CommerceClient` Protocol.
- Postgres has two schemas:
  - `commerce`: migrations in `apps/commerce_mock/migrations`;
  - `support`: migrations in `packages/dtc_store/migrations`.

## Current phase: Phase 0 (Foundation)
Order of work:
1. Repo, tooling, CI.
2. `dtc_contracts` + `dtc_policy` with tests (first; no DB dependency).
3. Schema + Alembic.
4. Datagen.
5. Commerce mock + fault injection + Logfire.
6. Smoke test.

Detailed spec: `docs/phases/phase-0.md`.

## Commands
```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
docker compose -f deploy/compose/docker-compose.yml up -d
uv run alembic -c apps/commerce_mock/alembic.ini upgrade head          # commerce schema
uv run alembic -c apps/commerce_mock/alembic.ini revision --autogenerate -m "..."
COMMERCE_DATABASE_URL=postgresql+asyncpg://dtc:dtc@localhost:5432/dtc uv run pytest -m integration
```

## Working style
- **The user writes the code.** By default, review, explain, point out bugs, and suggest tests. Write implementation only when explicitly asked.
- Small, targeted edits; no broad rewrites. Keep output short and concrete.
- Reply to the user in Vietnamese. Write code, comments, docs, and commit messages in English.
- Every policy change needs a test case.
- Every new architectural decision needs an ADR in `docs/adr/`.

## Docs
- `docs/architecture/overview.md`: architecture, agents, memory, latency budget, capacity.
- `docs/phases/roadmap.md`: phases and exit criteria.
- `docs/phases/phase-0.md`: schema, policy, test cases, datagen, mock API.
- `docs/adr/`: accepted decisions.
- `docs/glossary.md`: plain-language definitions of business, policy, AI, and infra terms.
