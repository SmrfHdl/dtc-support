# ADR-0002: Workflow-first, no supervisor LLM
- Status: Accepted (2026-09-28)
- Context: The p95 < 2.5s SLO covers the full response. A ReAct loop with 3 LLM calls takes 2–4s.
- Decision: Routing is done by the NLU small model plus code. Three specialist agents + one fallback agent; exactly one agent runs per turn, with at most one LLM API call on the main path. Prefetch and policy run before the agent. Auxiliary agents run async.
- Consequences: Stable latency. Multi-intent cases go to the fallback agent (slower) or to handoff.
