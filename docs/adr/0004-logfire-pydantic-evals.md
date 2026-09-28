# ADR-0004: Logfire + pydantic-evals, prompts in git
- Status: Accepted (2026-09-28). Replaces Langfuse.
- Decision:
  - Logfire for tracing, pydantic-evals for offline evals.
  - Prompts are versioned in git and must pass the eval gate in CI.
  - Prometheus/Grafana only for infra metrics and KEDA.
- Consequences: Logfire is SaaS, so scrubbing and Presidio are mandatory before logging, and sampling must be configured for 200 RPS.
