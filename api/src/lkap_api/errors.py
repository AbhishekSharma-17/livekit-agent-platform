"""Typed API error hierarchy for `lkap_api`.

Every error response follows docs/CONTRACTS.md §7:
`{"error": {"code": str, "message": str, "details": object | null}}`.
FastAPI exception handlers (registered in `main.py`, owned by W1-API-CORE)
catch `ApiError` and render `ApiError.to_response()`.
"""

from __future__ import annotations

from lkap_contracts.api_models import ErrorBody, ErrorResponse


class ApiError(Exception):
    """Base class for every error `lkap_api` raises deliberately.

    Subclasses set the class-level `status_code` and `code`; instances carry
    a human-readable `message` and optional structured `details` (never a
    secret value — this may be logged or shown to an admin).
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_response(self) -> ErrorResponse:
        """Render the CONTRACTS §7 error envelope."""
        return ErrorResponse(error=ErrorBody(code=self.code, message=self.message, details=self.details))


class BadRequestError(ApiError):
    """400 — malformed or semantically invalid request."""

    status_code = 400
    code = "bad_request"


class UnauthorizedError(ApiError):
    """401 — missing or invalid admin/service token."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    """403 — authenticated but not allowed (e.g. unpublished agent connect)."""

    status_code = 403
    code = "forbidden"


class NotFoundError(ApiError):
    """404 — resource does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(ApiError):
    """409 — conflicts with current state (e.g. credential still referenced)."""

    status_code = 409
    code = "conflict"


class UnprocessableEntityError(ApiError):
    """422 — well-formed request, invalid content (registry/config validation)."""

    status_code = 422
    code = "unprocessable_entity"


class InternalError(ApiError):
    """500 — unexpected server error."""

    status_code = 500
    code = "internal_error"
