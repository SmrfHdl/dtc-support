# Architecture overview

## Request flow
```
Chat Widget (React/TS) ──WS──▶ Gateway (FastAPI)
                                   │  auth, rate limit, session (Redis)
                                   ▼
                            Orchestrator (pydantic-graph)
      ┌────────────────────────┼─────────────────────────┐
      ▼                        ▼                         ▼
 NLU (vLLM, small model)  Commerce client          Escalation detector
 intent+entities+         (prefetches order        (rules + emotion score)
 emotion, JSON            in parallel with NLU)
      └──────────┬─────────────┘
                 ▼
          Policy Engine (dtc_policy, deterministic, versioned YAML)
                 │ APPROVE / DENY / NEED_MANAGER (human approval before side effects) / NEED_INFO
      ┌──────────┼──────────────┬───────────────────┐
      ▼          ▼              ▼                   ▼
  Template    Specialist     Fallback agent      Handoff
  (WISMO)     agent          (complex cases)     → Chatwoot
              (1 API call)                       → Manager approval queue
                 ▼
        Output guardrail (matches decision, defect hint, PII, tone)
                 ▼
        Response ──▶ Redis Streams ──▶ memory_worker / Postgres / Logfire
```

On the happy path, the order is prefetched and the decision is computed before the agent runs. The agent receives everything via `deps`, calls no tools, and produces a single structured output.

## Agents
**Sync** (exactly one agent runs per turn):

| Agent | Tools | Notes |
|---|---|---|
| returns_agent | get_order, create_rma, request_photos | |
| exchange_agent | get_order, check_variant_stock, create_exchange | Suggests only within category scope |
| wismo_agent | get_tracking | Happy path uses a template; the agent runs only on anomalies |
| fallback_agent | Read-only tools | Multi-intent or low NLU confidence. Uses Sonnet |

**Async** (no latency impact):

| Agent | Trigger | Output |
|---|---|---|
| handoff_summarizer | Handoff | Summary posted to Chatwoot |
| memory_extractor | End of turn or conversation | Writes `customer_facts` |
| manager_brief | NEED_MANAGER | Brief for the approval queue |
| judge | 5% sample | Score written to Logfire |

## Escalation
| Trigger | Action |
|---|---|
| Customer asks for a human | Immediate handoff |
| Strongly negative emotion for 2 consecutive turns, or legal/chargeback keywords | Handoff, high priority |
| Same intent unresolved after 3 turns | Handoff |
| NLU confidence below threshold | Ask once to clarify, then handoff |
| Refund ≥ $50 (order aggregate) | No handoff. Approval queue `refund_over_limit`; bot tells the customer the expected wait |
| Customer reports a defective item | Policy returns NEED_MANAGER / DEFECT_CLAIM. Request photos, then approval queue `defect_review` |
| Category missing from policy YAML | Approval queue `config_error` |

## Memory
| Type | Store | Read | Write |
|---|---|---|---|
| Working (trimmed history, slots, workflow state) | Redis, 24h TTL | Every turn | Every turn |
| Episodic (past ticket summaries, return/exchange counts, CSAT) | Postgres `episodes` | Once at session start, cached in Redis | Async |
| Profile (facts stated by the customer) | Postgres `customer_facts` (verified, confidence, source_turn, expires_at) | Together with episodic | Async |
| Knowledge (FAQ, size charts) | pgvector `kb_chunks` | Only for FAQ intent | Offline |

Rules:
- Memory is keyed by an authenticated `customer_id`. Anonymous customers get working memory only.
- Trim history with `history_processors`.
- Provide a per-customer deletion API (GDPR/CCPA).

## Latency budget (estimates, to be measured in the PoC)
| Step | Estimate |
|---|---|
| Gateway + session | 10–30ms |
| NLU (small model) | 50–150ms, plus 50–200ms RTT to vast.ai during the temporary setup |
| Order prefetch (parallel with NLU) | 10–50ms |
| Policy | <10ms |
| Response: template / 1 API call | 0 / 600–1200ms |
| Guardrail | 20–80ms |

## Capacity at 200 RPS
| Item | Estimate | Mitigation |
|---|---|---|
| LLM API | ~130 calls/s, ~15M input TPM | Prompt caching (static prefix), high rate-limit tier, provider fallback |
| WebSocket | ~4,000 concurrent connections | 3–4 stateless gateway pods |
| NLU | One GPU with continuous batching is enough | Fall back to Haiku when vast is down |
| Load testing | | Use an LLM stub. Full-scale tests run in staging |

## Observability
- Logfire: use `instrument_pydantic_ai`, `instrument_fastapi`, `instrument_asyncpg`. Shared configuration lives in `dtc_observability`.
- Logfire is SaaS: enable scrubbing and run Presidio before logging.
- Sampling: keep 100% of error, slow (p95+), and escalation traces; sample the rest.
- One Logfire project per environment (dev/staging/prod).
- Prompts are versioned in git and must pass the eval gate in CI.
