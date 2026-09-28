# ADR-0007: No graph DB; return-abuse detection deferred
- Status: Accepted (2026-09-28)
- Context: customer → order → shipment data is key-based relational lookup, which Postgres handles well. HugeGraph only adds value for fraud-ring detection.
- Decision: HugeGraph is out of scope. Reserve `PolicyInput.risk_score: float | None`; the engine ignores it when None.
- Revisit: When abuse detection is needed. Expected approach: CDC into a graph, a job computes risk_score into Redis, policy reads it from Redis.
