"""The "Test model" route's own policy rule (R-V4-26): the longest route-template prefix wins."""

from __future__ import annotations

import pytest

from lkap_api.auth.roles import ROUTE_POLICY, Requirement, policy_for

TEST_MODEL = "/v1/providers/{provider_id}/test-model"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", TEST_MODEL, Requirement("builder", "agents:write")),
        ("GET", TEST_MODEL, Requirement("builder", "providers:read")),
        # The parent rule still governs everything else under /v1/providers.
        ("PUT", "/v1/providers/{provider_id}/models/{model_id}", Requirement("admin", "providers:write")),
        ("GET", "/v1/providers/{provider_id}/models/{model_id}", Requirement("viewer", "providers:read")),
        ("GET", "/v1/providers/{provider_id}/catalog", Requirement("viewer", "providers:read")),
        ("PUT", "/v1/providers/{provider_id}/settings", Requirement("admin", "providers:write")),
    ],
)
def test_the_test_model_rule_wins_by_the_longest_prefix(
    method: str, path: str, expected: Requirement
) -> None:
    assert policy_for(method, path) == expected


def test_the_rule_is_more_specific_than_the_providers_rule() -> None:
    prefixes = [rule.prefix for rule in ROUTE_POLICY]
    assert TEST_MODEL in prefixes and "/v1/providers" in prefixes
    assert TEST_MODEL.startswith("/v1/providers/")


def test_a_concrete_path_cannot_smuggle_the_prefix() -> None:
    # Templates, not concrete urls, are matched: a provider id named "test-model" changes nothing.
    assert policy_for("PUT", "/v1/providers/{provider_id}/settings") == Requirement(
        "admin", "providers:write"
    )
