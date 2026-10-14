"""Chat API contract of the orchestrator (docs/phases/phase-1.md)."""

from typing import Annotated

from pydantic import Field, StringConstraints

from dtc_contracts.base import ContractModel
from dtc_contracts.nlu import Intent
from dtc_contracts.policy import PolicyDecision

# Safe in a Redis key and a log line.
SessionId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ChatIn(ContractModel):
    session_id: SessionId
    message: Message


class ChatOut(ContractModel):
    reply: str
    intent: Intent
    decision: PolicyDecision | None = None  # set on turns that ran evaluate()
    trace_id: str | None = None
