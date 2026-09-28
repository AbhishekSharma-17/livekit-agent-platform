"""The dataset tools (V6-16, D-V6-27), against the scratch api."""

from __future__ import annotations

from typing import Any

from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES

from lkap_mcp.catalog import declared_specs
from lkap_mcp.registry import DESTRUCTIVE, READ

CSV = (
    "Policy Number,Holder Name,Phone\n"
    "PD-1001,Demo — Asha Rao,+91 98765 43210\n"
    "PD-1002,Demo — Ben Cole,(415) 555-0100\n"
)


def test_dataset_tools_are_declared_with_their_scopes() -> None:
    specs = {spec.name: spec for spec in declared_specs() if "dataset" in spec.name}

    assert set(specs) == {
        "dataset_list",
        "dataset_create",
        "dataset_delete",
        "dataset_lookup",
        "tool_create_dataset",
    }
    assert specs["dataset_list"].annotations == READ
    assert specs["dataset_delete"].annotations == DESTRUCTIVE


async def test_create_lookup_attach_and_delete_a_dataset(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call(
            "dataset_create",
            name="Demo — Policies",
            key_columns={"Phone": "phone", "Policy Number": "string"},
            text=CSV,
        )
        assert created["ok"] is True, created
        dataset = created["data"]
        assert dataset["status"] == "ready" and dataset["row_count"] == 2

        found = await mcp.call("dataset_lookup", dataset_id=dataset["id"], keys={"phone": "098765 43210"})
        assert found["ok"] is True, found
        assert found["data"]["count"] == 1
        assert "Demo — Asha Rao" in found["data"]["rows"]["content"]
        assert found["data"]["rows"]["source"] == f"dataset:{dataset['id']}"

        tool = await mcp.call(
            "tool_create_dataset",
            name="lookup_policy",
            description="Find the caller's policy by phone number.",
            dataset_id=dataset["id"],
            key_columns=["phone"],
            pinned_arguments={"phone": "{{ ctx.caller_phone }}"},
        )
        assert tool["ok"] is True, tool
        assert tool["data"]["definition"]["kind"] == "dataset"

        unconfirmed = await mcp.call("dataset_delete", dataset_id=dataset["id"])
        assert unconfirmed["ok"] is False and unconfirmed["error"]["code"] == "needs_confirmation"
        blocked = await mcp.call("dataset_delete", dataset_id=dataset["id"], confirm=True)
        assert blocked["ok"] is False and "lookup_policy" in blocked["error"]["message"]

        await mcp.call("lkap_delete", kind="tool", id=tool["data"]["id"], confirm=True)
        deleted = await mcp.call("dataset_delete", dataset_id=dataset["id"], confirm=True)
        assert deleted["ok"] is True, deleted
        assert (await mcp.call("dataset_list"))["data"] == []


async def test_a_bad_file_is_refused_with_the_apis_plain_reason(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        refused = await mcp.call("dataset_create", name="Demo — Bad", key_columns={"Fax": "string"}, text=CSV)

    assert refused["ok"] is False
    assert "no column 'Fax'" in refused["error"]["message"]


async def test_a_read_only_key_lists_but_cannot_create(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        names = await mcp.tool_names()

    assert "dataset_list" in names
    assert "dataset_create" not in names and "tool_create_dataset" not in names
