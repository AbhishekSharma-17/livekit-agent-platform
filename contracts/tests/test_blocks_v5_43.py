"""V5-43 contracts: the ``link``, ``slots`` and ``cards`` blocks, their tools and the link hook."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import tools
from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    CardsBlockConfig,
    LinkBlockConfig,
    SlotsBlockConfig,
    validate_block_config,
)
from lkap_contracts.ui_protocol import (
    MAX_URL_CHARS,
    AgentAction,
    BlockSpec,
    CardsBlockState,
    LinkBlockState,
    LinkCompletedPacket,
    LinkHookIn,
    SlotsBlockState,
    StateDeltaPayload,
    UiSnapshotRequestPacket,
    host_allowed,
    https_url_problem,
    normalize_host,
)

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "panels" / "blocks" / "__fixtures__"


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("javascript:alert(1)", "only https://"),
        ("JavaScript:alert(1)", "only https://"),
        ("data:text/html,<b>x</b>", "characters"),
        ("data:text/html;base64,PGI+", "only https://"),
        ("http://example.com/pay", "only https://"),
        ("//example.com/pay", "only https://"),
        ("https://user:pw@example.com/", "user name or password"),
        ("https://example.com/a b", "characters"),
        ("https:\\\\example.com", "characters"),
        ("https://example.com/\x00", "characters"),
        ("https:///path", "no valid site name"),
        ("https://10.0.0.1/pay", "no valid site name"),
        ("https://example.com/" + "a" * MAX_URL_CHARS, "longer than"),
        ("", "empty"),
    ],
)
def test_unsafe_links_are_refused(url: str, problem: str) -> None:
    found = https_url_problem(url)
    assert found is not None and problem in found


def test_safe_links_and_host_allowlists() -> None:
    assert https_url_problem("https://checkout.example.com/c/pay/cs_test_a1#frag") is None
    assert https_url_problem("https://example.com:8443/pay?ref=1") is None
    assert https_url_problem("https://pay.example.com/x", allowed_hosts=["*.example.com"]) is None
    refused = https_url_problem("https://example.com/x", allowed_hosts=["*.example.com"])
    assert refused is not None and "not one of the sites" in refused
    assert https_url_problem("https://evil-example.com/", allowed_hosts=["example.com"]) is not None
    assert not host_allowed("notexample.com", ["*.example.com"])
    assert host_allowed("A.Example.COM", ["*.example.com"])


@pytest.mark.parametrize(
    "bad", ["https://example.com", "example.com/pay", "example", "*.", "1.2.3.4", "exa mple.com"]
)
def test_allowlist_entries_are_site_names(bad: str) -> None:
    with pytest.raises(ValueError, match="not a site name"):
        normalize_host(bad)


def test_link_config_needs_at_least_one_site() -> None:
    issues = validate_block_config(BlockSpec(id="pay", type="link"))
    assert [i.path for i in issues] == ["config.allowed_hosts"]
    assert "at least one site" in issues[0].message
    config = LinkBlockConfig.model_validate(
        {"allowed_hosts": ["Example.com", "example.com", "*.Stripe.example"]}
    )
    assert config.allowed_hosts == ["example.com", "*.stripe.example"]
    assert validate_block_config(
        BlockSpec(id="pay", type="link", config={"allowed_hosts": ["x.com"], "foo": 1})
    )


def test_link_state_holds_only_https_links() -> None:
    assert LinkBlockState().status == "idle"
    with pytest.raises(ValidationError):
        LinkBlockState(url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        LinkBlockState(url="https://example.com", reference="has spaces")
    assert LinkBlockState(url="").url is None


def test_slots_need_offsets_and_order() -> None:
    ok = {"id": "a", "start": "2026-10-05T09:00:00+01:00", "end": "2026-10-05T10:00:00+01:00"}
    SlotsBlockState(slots=[ok])
    with pytest.raises(ValidationError, match="UTC offset"):
        SlotsBlockState(slots=[{**ok, "start": "2026-10-05T09:00:00"}])
    with pytest.raises(ValidationError, match="end after"):
        SlotsBlockState(slots=[{**ok, "end": ok["start"]}])
    with pytest.raises(ValidationError, match="unique"):
        SlotsBlockState(slots=[ok, ok])
    assert SlotsBlockConfig().timezone_mode == "caller"


def test_cards_validate_pictures_ids_and_actions() -> None:
    card = {"id": "a", "title": "A"}
    CardsBlockState(cards=[card, {**card, "id": "b", "image_url": "https://example.com/a.png"}])
    with pytest.raises(ValidationError):
        CardsBlockState(cards=[{**card, "image_url": "data:image/png;base64,AAAA"}])
    with pytest.raises(ValidationError, match="unique"):
        CardsBlockState(cards=[card, card])
    with pytest.raises(ValidationError):
        CardsBlockState(cards=[{**card, "actions": [{"name": "Buy Now", "label": "Buy"}]}])
    assert CardsBlockConfig.model_validate({"image_hosts": ["CDN.example.com"]}).image_hosts == [
        "cdn.example.com"
    ]
    assert CardsBlockConfig().image_hosts == []


@pytest.mark.parametrize(
    ("stem", "model"), [("link", LinkBlockState), ("slots", SlotsBlockState), ("cards", CardsBlockState)]
)
def test_the_web_fixtures_are_valid_states(stem: str, model: type[Any]) -> None:
    model.model_validate(json.loads((FIXTURES / f"{stem}.json").read_text()))


def test_the_fixture_layout_configs_validate() -> None:
    layout = json.loads((FIXTURES / "layout.json").read_text())
    for spec in layout["blocks"]:
        assert validate_block_config(BlockSpec.model_validate(spec)) == [], spec["id"]


def test_every_block_type_has_a_config_model() -> None:
    assert {"link", "slots", "cards"} <= set(BLOCK_CONFIG_MODELS)


def test_the_block_tools_and_their_registration() -> None:
    new = ("describe_panel", "send_link", "request_slot", "resolve_slot", "show_cards")
    assert set(new) <= set(tools.BLOCK_TOOL_NAMES)
    assert tools.BLOCK_TOOL_TYPES["describe_panel"] == tools.ALL_BLOCK_TYPES
    assert tools.BLOCK_TOOL_TYPES["send_link"] == {"link"}
    assert tools.BLOCK_TOOL_TYPES["request_slot"] == tools.BLOCK_TOOL_TYPES["resolve_slot"] == {"slots"}
    assert tools.BLOCK_TOOL_TYPES["show_cards"] == {"cards"}
    assert "cards" in tools.UPDATABLE_BLOCK_TYPES
    assert not {"link", "slots"} & tools.UPDATABLE_BLOCK_TYPES
    assert all(tools.never_background(name) for name in new)
    assert not set(new) & set(tools.BUILTIN_TOOL_NAMES)
    document = tools.builtin_tools_document()
    assert document["block_tool_types"]["describe_panel"] == sorted(tools.ALL_BLOCK_TYPES)


def test_the_link_hook_names_a_link() -> None:
    with pytest.raises(ValidationError, match="block_id or reference"):
        LinkHookIn()
    with pytest.raises(ValidationError):
        LinkHookIn(block_id="pay", status="paid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        LinkHookIn.model_validate({"block_id": "pay", "extra": 1})
    assert LinkHookIn(reference="CLM-1").status == "completed"
    packet = LinkCompletedPacket(id="p1", session_id="s1", block_id="pay")
    assert json.loads(packet.model_dump_json())["op"] == "link_completed"
    assert UiSnapshotRequestPacket(session_id="s1").op == "snapshot"


def test_the_state_delta_action() -> None:
    assert AgentAction(action="state_delta", payload={"delta": []}).action == "state_delta"
    payload = StateDeltaPayload.model_validate(
        {"type": "STATE_DELTA", "delta": [{"op": "add", "path": "/blocks/x/y", "value": 1}], "timestamp": 1}
    )
    assert payload.delta[0]["op"] == "add"
    with pytest.raises(ValidationError):
        StateDeltaPayload.model_validate({"delta": []})
    with pytest.raises(ValidationError):
        StateDeltaPayload.model_validate({"type": "STATE_SNAPSHOT", "delta": [{"op": "add"}]})
