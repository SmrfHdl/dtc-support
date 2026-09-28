# Roadmap

| Phase | Main work | Exit criteria |
|---|---|---|
| **P0 Foundation** | contracts, policy + tests, schema, datagen, commerce mock, compose, Logfire, CI | Policy 100% branch coverage, all required cases + property tests pass. Compose + seed < 1 min. Mock p50 < 20ms |
| **P1 PoC** | Text-in/text-out API. NLU temporarily on Haiku. Workflows for 3 intents. 200-conversation golden set. Per-span latency measurement. `dtc_commerce`, `dtc_store` | Intent accuracy ≥ 90%, decision accuracy 100%, real latency breakdown available |
| **P2 MVP** | Widget, gateway WS, Chatwoot handoff, manager queue, NLU fine-tune (vast) + vLLM, guardrails, memory worker, CSAT survey, user simulator. k3s + Helm + ArgoCD | On simulated traffic: automation ≥ 70%, CSAT proxy ≥ 4.5, p95 < 2.5s at 20 RPS |
| **P3 Hardening** | Eval regression gate in CI, red teaming, chaos (vast down, 429s, slow commerce), load test with LLM stub | All red-team cases behave correctly, degradation modes work |
| **P4 Staging** | Terraform to cloud, GPU in the same region. Load test 200 RPS (stub) + 10–20 RPS real LLM. Shadow mode | SLOs met at 200 RPS, capacity plan in place |
| **P5 Go-live** | Canary 5% → 25% → 100%, automatic rollback, 5% online eval, runbooks | |
| **P6 Black Friday** | Pre-scaling, raised rate limits, degradation ladder, load test at 2× peak | |

Degradation ladder: disable agent path → template + response generation only → template-only → queue for humans.

Out of scope (deferred): return-abuse detection. See ADR-0007.
