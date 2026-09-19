"""Unit tests for `lkap_api.errors` (W0-SCAFFOLD).

Depends on `lkap_contracts.api_models.{ErrorBody, ErrorResponse}` (W0-CONTRACTS).
"""

from __future__ import annotations

import pytest

from lkap_api.errors import (
    ApiError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)


@pytest.mark.parametrize(
    ("error_cls", "status_code", "code"),
    [
        (NotFoundError, 404, "not_found"),
        (ConflictError, 409, "conflict"),
        (ForbiddenError, 403, "forbidden"),
        (UnprocessableEntityError, 422, "unprocessable_entity"),
    ],
)
def test_api_error_subclass_status_and_code(error_cls: type[ApiError], status_code: int, code: str) -> None:
    err = error_cls("boom", details={"field": "x"})
    assert err.status_code == status_code
    assert err.code == code
    assert err.message == "boom"
    assert err.details == {"field": "x"}


def test_to_response_matches_contracts_error_envelope() -> None:
    err = NotFoundError("agent not found")
    body = err.to_response()
    dumped = body.model_dump()
    assert dumped == {"error": {"code": "not_found", "message": "agent not found", "details": None}}
