# ADR-0001: Deterministic policy; the LLM does not decide
- Status: Accepted (2026-09-28)
- Context: Refunds, the 30-day window, and exchange scope have financial consequences. LLMs are non-deterministic and vulnerable to prompt injection.
- Decision: `dtc_policy.evaluate()` is a pure function with versioned YAML config. The LLM only does NLU and phrases the reply. A guardrail enforces that the reply matches the decision.
- Consequences: Fully testable and auditable by `policy_version`. Every new rule must be coded and tested.
