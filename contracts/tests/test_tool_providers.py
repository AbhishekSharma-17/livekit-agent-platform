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
    AppsMode,
    ConnectionRenameIn,
    ToolkitOut,
    ToolkitPage,
    action_risk,
    agent_subject,
    effective_denied_actions,
    label_slug,
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


DELETE = "GMAIL_DELETE_EMAIL"
LIST = "GMAIL_LIST_EMAILS"


@pytest.mark.parametrize(
    ("denied", "reviewed", "expected"),
    [
        pytest.param([], [], [DELETE], id="unreviewed-destructive-is-denied"),
        pytest.param([], [DELETE], [], id="reviewed-and-not-denied-is-allowed"),
        pytest.param([DELETE], [DELETE], [DELETE], id="reviewed-and-denied-is-denied"),
        pytest.param([LIST], [], [DELETE, LIST], id="non-destructive-denial-is-kept"),
    ],
)
def test_effective_denied_actions_four_cases(
    denied: list[str], reviewed: list[str], expected: list[str]
) -> None:
    apps = AppsMode(mode="router", denied_actions=denied, reviewed_actions=reviewed)

    assert effective_denied_actions(apps, [DELETE]) == expected


def test_effective_denied_actions_leaves_a_non_destructive_action_untouched() -> None:
    apps = AppsMode(mode="server", reviewed_actions=[LIST])

    assert effective_denied_actions(apps, []) == []
    assert effective_denied_actions(AppsMode(mode="server"), [DELETE]) == [DELETE]


def test_effective_denied_actions_without_reviews_matches_the_console_seed() -> None:
    """R-V5-9 compatibility: ``reviewed_actions=[]`` denies every destructive action in scope."""
    seeded = AppsMode(mode="router", denied_actions=["SLACK_SEND_MESSAGE", "SLACK_DELETE_MESSAGE"])
    fresh = AppsMode(mode="router", denied_actions=["SLACK_SEND_MESSAGE"])
    scope = ["SLACK_DELETE_MESSAGE"]

    assert effective_denied_actions(seeded, scope) == sorted(seeded.denied_actions)
    assert effective_denied_actions(fresh, scope) == effective_denied_actions(seeded, scope)


def test_effective_denied_actions_is_case_insensitive_sorted_and_unique() -> None:
    apps = AppsMode(mode="router", denied_actions=["b_remove_x", "A_GET"], reviewed_actions=["c_delete_y"])

    assert effective_denied_actions(apps, ["C_DELETE_Y", "b_remove_x", "D_PURGE_Z", " "]) == [
        "A_GET",
        "B_REMOVE_X",
        "D_PURGE_Z",
    ]


def test_apps_mode_reviewed_actions_is_additive_and_bounded() -> None:
    assert AppsMode.model_validate({"mode": "router"}).reviewed_actions == []
    with pytest.raises(ValidationError):
        AppsMode(reviewed_actions=["X"] * 501)


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


# ------------------------------------------------------------ R-V5-13: several accounts
def test_connection_out_reads_a_pre_label_payload_as_the_default_account() -> None:
    """Compatibility: a payload without the new fields is the app's default account."""
    out = AppConnectionOut.model_validate(
        {"id": "c1", "toolkit": "gmail", "subject": "ws:w1", "status": "active", "method": "managed"}
    )
    assert out.is_default is True and out.label == ""


def test_connect_label_is_optional_and_bounded() -> None:
    assert AppConnectIn(toolkit="gmail").label is None
    assert AppConnectIn(toolkit="gmail", label="Work").label == "Work"
    with pytest.raises(ValidationError):
        AppConnectIn(toolkit="gmail", label="x" * 41)
    with pytest.raises(ValidationError):
        AppConnectIn(toolkit="gmail", label="")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="nothing-to-change"),
        pytest.param({"is_default": False}, id="un-defaulting-is-not-a-change"),
        pytest.param({"label": "   "}, id="blank-label"),
        pytest.param({"label": "x" * 41}, id="too-long"),
    ],
)
def test_connection_rename_refuses(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConnectionRenameIn.model_validate(payload)


def test_connection_rename_accepts_a_label_a_default_or_both() -> None:
    assert ConnectionRenameIn(label="Work").is_default is None
    assert ConnectionRenameIn(is_default=True).label is None
    both = ConnectionRenameIn(label="Work", is_default=True)
    assert both.label == "Work" and both.is_default is True


@pytest.mark.parametrize(
    ("label", "slug"),
    [
        ("Work", "work"),
        ("Work Inbox", "work_inbox"),
        ("  me@example.com ", "me_example_com"),
        ("Gmail account 2", "gmail_account_2"),
        ("***", "account"),
        ("Ünïcode", "n_code"),
    ],
)
def test_label_slug(label: str, slug: str) -> None:
    assert label_slug(label) == slug


def test_apps_mode_accounts_default_is_empty_and_additive() -> None:
    assert AppsMode.model_validate({"mode": "router"}).accounts == {}


def test_apps_mode_accounts_normalises_slugs_and_dedupes_ids() -> None:
    apps = AppsMode(accounts={" Gmail ": ["c1", "c1", " c2 ", ""], "gmail": ["c3"]})
    assert apps.accounts == {"gmail": ["c1", "c2", "c3"]}


@pytest.mark.parametrize(
    "accounts",
    [
        pytest.param({f"app{i}": ["c"] for i in range(21)}, id="21-apps"),
        pytest.param({"gmail": [f"c{i}" for i in range(6)]}, id="6-accounts"),
        pytest.param({" ": ["c1"]}, id="blank-app"),
    ],
)
def test_apps_mode_accounts_is_bounded(accounts: dict[str, list[str]]) -> None:
    with pytest.raises(ValidationError):
        AppsMode(accounts=accounts)
