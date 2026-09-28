# ADR-0006: Separate `commerce` and `support` schemas
- Status: Accepted (2026-09-28)
- Decision: Two schemas with separate Alembic histories. `commerce` is owned by commerce_mock, `support` by dtc_store. The orchestrator depends only on the `CommerceClient` Protocol.
- Consequences: Replacing the mock with Shopify means adding an adapter and dropping the `commerce` schema, without touching conversation data.
