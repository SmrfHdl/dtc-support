# ADR-0003: Hybrid LLM
- Status: Accepted (2026-09-28)
- Decision:
  - NLU (intent, entities, emotion, escalate flag): Qwen2.5-1.5B + LoRA on vLLM with guided JSON. GPU temporarily rented on vast.ai.
  - Response: Claude Haiku 4.5. Fallback agent: Sonnet.
  - Model switching via `FallbackModel`. When vast is down, NLU falls back to Haiku.
- Consequences: RTT to vast must be included in the latency budget. Staging/prod place the GPU in the same region as the cluster.
