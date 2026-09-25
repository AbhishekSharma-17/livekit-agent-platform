"""The connected-apps contracts (V5-18, docs/v5/COMPOSIO.md §3)."""

import pytest
from pydantic import ValidationError

from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.providers import REGISTRY, get
from lkap_contracts.tool_providers import (
    COMPOSIO_PROVIDER_ID,
    TOOL_PROVIDER_ACCOUNT,
    TOOL_PROVIDER_MODELS,
    AppActionsPickIn,
    AppConnectIn,
    AppConnectionOut,
    AppKeyTestIn,
    ToolkitOut,
    ToolkitPage,
    action_risk,
    agent_subject,
    workspace_subject,
)


def test_every_model_is_exported_under_its_own_name() -> None:
    for name, model in TOOL_PROVIDER_MODELS.items():
        assert EXPORTED_MODELS[name] is model
        assert model.__name__ == name


def test_no_export_name_clashes_with_an_existing_model() -> None:
    names = [name for name, model in EXPORTED_MODELS.items() if model in TOOL_PROVIDER_MODELS.values()]
    assert len(names) == len(TOOL_PROVIDER_MODELS)
    assert "ConnectionOut" not in TOOL_PROVIDER_MODELS, "ConnectionOut is the LiveKit connection"


def test_toolkit_page_round_trips() -> None:
    page = ToolkitPage(
        items=[
            ToolkitOut(slug="googlecalendar", name="Google Calendar", auth=["oauth_managed", "oauth_custom"])
        ],
        next_cursor="abc",
        total=10,
    )
    again = ToolkitPage.model_validate_json(page.model_dump_json())
    assert again == page
    assert again.items[0].connected is False and again.items[0].auth_fields == {}


def test_connect_defaults_to_managed_for_the_workspace() -> None:
    payload = AppConnectIn(toolkit="googlecalendar")
    assert payload.method == "managed" and payload.subject == "workspace" and payload.fields == {}


@pytest.mark.parametrize("toolkit", ["", "has space", "slash/es", "a" * 129])
def test_connect_rejects_odd_toolkit_slugs(toolkit: str) -> None:
    with pytest.raises(ValidationError):
        AppConnectIn(toolkit=toolkit)


def test_connection_out_has_no_vendor_account_fields() -> None:
    fields = set(AppConnectionOut.model_fields)
    assert not fields & {"connected_account_id", "auth_config_id", "fields", "api_key", "redirect_url"}


def test_key_test_bounds_the_pasted_value() -> None:
    with pytest.raises(ValidationError):
        AppKeyTestIn(api_key="")
    with pytest.raises(ValidationError):
        AppKeyTestIn(api_key="x" * 513)


def test_picks_need_at_least_one_action_and_default_to_no_destructive() -> None:
    with pytest.raises(ValidationError):
        AppActionsPickIn(connection_id="c1", actions=[])
    assert AppActionsPickIn(connection_id="c1", actions=["A"]).allow_destructive is False


def test_subjects() -> None:
    assert workspace_subject("w1") == "ws:w1"
    assert agent_subject("a1") == "agent:a1"


@pytest.mark.parametrize(
    ("slug", "tags", "risk"),
    [
        ("GOOGLECALENDAR_FIND_FREE_SLOTS", None, "read"),
        ("GMAIL_FETCH_EMAILS", [], "read"),
        ("GMAIL_SEND_EMAIL", [], "write"),
        ("GITHUB_DELETE_REPO", [], "destructive"),
        ("PAYPAL_SEND_MONEY", [], "destructive"),
        ("SHOP_PURGE_CACHE", ["readOnlyHint"], "destructive"),
        ("CRM_UPDATE_DEAL", ["readOnlyHint"], "read"),
    ],
)
def test_action_risk(slug: str, tags: list[str] | None, risk: str) -> None:
    assert action_risk(slug, tags) == risk


def test_registry_has_composio_as_a_tool_provider_with_one_secret() -> None:
    spec = get(COMPOSIO_PROVIDER_ID)
    assert spec.kind == "tool_provider"
    assert spec.availability == "available" and spec.worker_image == "full"
    assert [field.name for field in spec.secret_fields] == ["api_key"]
    assert spec.test is None and spec.catalog is None, "tested by the api's key check, not a catalog"
    assert spec.package == "" and spec.python_class == ""


def test_connected_app_rows_are_not_a_registry_provider() -> None:
    """``POST /v1/credentials`` validates against the registry, so a connection row cannot be forged."""
    assert TOOL_PROVIDER_ACCOUNT not in {spec.id for spec in REGISTRY}
