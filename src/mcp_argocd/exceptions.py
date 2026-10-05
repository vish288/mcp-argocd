"""Argo CD API exceptions."""

from __future__ import annotations


class ArgoCDError(Exception):
    """Base exception for Argo CD operations."""


class ArgoCDApiError(ArgoCDError):
    """Raised when the Argo CD API returns a non-success response.

    ``grpc_code`` is the grpc-gateway ``code`` field (a gRPC status code), which
    distinguishes otherwise identical HTTP statuses — e.g. a 400 carrying code 9
    (FailedPrecondition) is a conflict, not a validation error.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        grpc_code: int | None = None,
        body: str = "",
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.grpc_code = grpc_code
        self.body = body
        super().__init__(f"Argo CD API Error {status_code}: {message}")


class ArgoCDAuthError(ArgoCDApiError):
    """Raised on 401, and on 403 responses that mean permission denied."""


class ArgoCDNotFoundError(ArgoCDApiError):
    """Raised on 404 responses."""


class ArgoCDConflictError(ArgoCDApiError):
    """Raised on 400/409 with grpc code 9 (FailedPrecondition) or 10 (Aborted)."""


class ArgoCDWriteDisabledError(ArgoCDError):
    """Raised when a write operation is attempted in read-only mode."""

    def __init__(self) -> None:
        super().__init__("Write operations are disabled (ARGOCD_READ_ONLY=true)")


class ArgoCDTimeoutError(ArgoCDError):
    """Raised when an operation or HTTP request exceeds its deadline."""
