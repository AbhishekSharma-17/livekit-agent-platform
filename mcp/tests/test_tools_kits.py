"""The tool kit tools (V6-18, D-V6-26), against the scratch api."""

from __future__ import annotations

from typing import Any

from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES

from lkap_mcp.catalog import declared_specs
from lkap_mcp.registry import READ, WRITE
from lkap_mcp.tools.agents import dropped_kit_sections


def test_kit_tools_are_declared_with_their_scopes() -> None:
    specs = {spec.name: spec for spec in declared_specs() if spec.name.startswith("kit_")}

    assert set(specs) == {"kit_list", "kit_add"}
    assert specs["kit_list"].annotations == READ
    assert specs["kit_add"].annotations == WRITE


async def test_list_preview_and_add_a_kit(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        listed = await mcp.call("kit_list")
        assert listed["ok"] is True, listed
        kits = {kit["id"]: kit for kit in listed["data"]}
        assert "structured_intake" in kits and "record_lookup" in kits
        assert {v["id"] for v in kits["record_lookup"]["variants"]} == {"dataset", "rest"}
        assert kits["record_lookup"]["settings"][0]["name"] == "base_url"

        one = await mcp.call("kit_list", kit_id="verify_identity")
        assert one["ok"] is True and one["data"]["default_prefix"] == "verify"

        agent = (await mcp.call("agent_create", name="Demo — Intake", pack_id="generic"))["data"]["agent"]
        preview = await mcp.call("kit_add", kit_id="structured_intake", agent_id=agent["id"], dry_run=True)
        assert preview["ok"] is True, preview
        assert preview["data"]["dry_run"] is True
        assert any("without dry_run" in step for step in preview["next_steps"])

        added = await mcp.call("kit_add", kit_id="structured_intake", agent_id=agent["id"])
        assert added["ok"] is True, added
        assert added["data"]["validation"]["ok"] is True
        again = await mcp.call("kit_add", kit_id="structured_intake", agent_id=agent["id"])
        assert {change["status"] for change in again["data"]["changes"]} == {"exists"}


async def test_a_missing_setting_comes_back_as_the_apis_plain_reason(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = (await mcp.call("agent_create", name="Demo — Cases", pack_id="generic"))["data"]["agent"]
        refused = await mcp.call("kit_add", kit_id="case_ticket", agent_id=agent["id"])
        planned = await mcp.call(
            "kit_add",
            kit_id="case_ticket",
            agent_id=agent["id"],
            settings={"base_url": "https://helpdesk.example.com/api/cases"},
            secret_key_id="key_1",
            plan=True,
        )

    assert refused["ok"] is False and "base_url" in refused["error"]["message"]
    [step] = planned["plan"]
    assert step["path"] == "/v1/tool-kits/case_ticket/instantiate"
    assert step["body"]["credential_id"] == "key_1"


async def test_a_read_only_key_lists_but_cannot_add(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        names = await mcp.tool_names()

    assert "kit_list" in names and "kit_add" not in names


# --------------------------------------------------------------------------- V6-30 (F-6)

_LOOKUP = "<!-- kit:record_lookup:policy -->\nLook it up.\n<!-- /kit:record_lookup:policy -->"
_ESCALATE = "<!-- kit:notify_escalate:escalate -->\nEscalate.\n<!-- /kit:notify_escalate:escalate -->"


def test_dropped_kit_sections_names_each_kit_whose_marker_is_gone() -> None:
    before = {"instructions": f"Hi.\n{_LOOKUP}\n{_ESCALATE}"}
    kept = {"instructions": f"New.\n{_ESCALATE}"}
    assert dropped_kit_sections(before, {"instructions": "Be brief."}) == ["notify_escalate", "record_lookup"]
    assert dropped_kit_sections(before, kept) == ["record_lookup"]
    assert dropped_kit_sections(before, before) == []
    assert dropped_kit_sections({"instructions": "plain"}, {"instructions": "other"}) == []


async def test_agent_update_warns_when_new_instructions_drop_a_kits_text(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = (await mcp.call("agent_create", name="Demo — Intake", pack_id="generic"))["data"]["agent"]
        added = await mcp.call("kit_add", kit_id="structured_intake", agent_id=agent["id"])
        assert added["ok"] is True, added
        replaced = await mcp.call("agent_update", id_or_slug=agent["id"], patch={"instructions": "Be brief."})
        renamed = await mcp.call("agent_update", id_or_slug=agent["id"], name="Demo — Intake 2")

    assert replaced["ok"] is True, replaced
    [warning] = [w for w in replaced["warnings"] if "kit_add" in w]
    assert "structured_intake" in warning
    assert not [w for w in renamed.get("warnings") or [] if "kit_add" in w]
