"""The tool kit tools (V6-18, D-V6-26), against the scratch api."""

from __future__ import annotations

from typing import Any

from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES

from lkap_mcp.catalog import declared_specs
from lkap_mcp.registry import READ, WRITE


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
