"""Failures of the commerce API, by what the caller should do about them."""

from dtc_contracts.commerce import ErrorCode


class CommerceError(Exception):
    def __init__(self, status_code: int, code: ErrorCode | None, detail: str) -> None:
        super().__init__(f"{status_code} {code}: {detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail


class CommerceConflictError(CommerceError):
    """409: the snapshot the policy saw is stale. Rebuild it and evaluate again."""


class CommerceInvalidError(CommerceError):
    """422 (or another 4xx): the request itself is wrong. Retrying will not help."""


class CommerceUnavailableError(CommerceError):
    """5xx or a transport failure: safe to retry with the same Idempotency-Key."""
