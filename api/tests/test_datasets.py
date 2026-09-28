"""V6-16 (D-V6-27): datasets — upload, import, lookup, preview, export, delete, the tenant rule,
the ``dataset`` tool kind at save, and the agent-config checks.

The inline job backend runs the import inside the upload request (the ASGI test client drains
background tasks before ``post()`` returns), so a fresh upload is already ``ready``.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from auth_helpers import login, make_user, make_workspace
from conftest import create_agent, inference_config
from connection_fakes import add_agent, connection_row
from fastapi import FastAPI
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.datasets import MAX_DATASET_ROWS

from lkap_api import limits
from lkap_api.config_service import ValidationContext
from lkap_api.datasets import service as dataset_service
from lkap_api.datasets.normalise import normalise_key
from lkap_api.datasets.parse import neutralise_cell
from lkap_api.datasets.validation import dataset_tool_issues
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Dataset, new_id
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault

POLICIES = (
    "Policy Number,Holder Name,Phone,Email,Premium,Note\r\n"
    'PD-1001,Demo — Asha Rao,+91 98765 43210,Asha@Example.com,1200.50,=HYPERLINK("http://x.example")\r\n'
    "PD-1002,Demo — Ben Cole,(415) 555-0100,ben@example.com,980,@SUM(A1:A2)\r\n"
    "PD-1003,Demo — Cleo Diaz,+44 20 7946 0958,cleo@example.com,1500,-2+3\r\n"
)
KEYS = {"Policy Number": "string", "phone": "phone", "email": "email"}


async def _upload(
    client: httpx.AsyncClient,
    content: str | bytes = POLICIES,
    *,
    filename: str = "policies.csv",
    name: str = "Demo — Policies",
    keys: object = KEYS,
) -> httpx.Response:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return await client.post(
        "/v1/datasets",
        data={"name": name, "key_columns": json.dumps(keys)},
        files={"file": (filename, data, "text/csv")},
    )


async def _dataset(client: httpx.AsyncClient) -> dict[str, object]:
    """Upload the policies file; the answer is ``pending``, the import has run once it returns."""
    response = await _upload(client)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    fetched = await client.get(f"/v1/datasets/{response.json()['id']}")
    body: dict[str, object] = fetched.json()
    return body


# ---------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("left", "right", "kind"),
    [
        ("+91 98765 43210", "098765 43210", "phone"),
        ("+91 98765 43210", "0091-98765-43210", "phone"),
        ("(415) 555-0100", "+1 415 555 0100", "phone"),
        ("Asha@Example.com ", "asha@example.com", "email"),
        ("1,200.50", "1200.5", "number"),
        ("  PD-1001 ", "pd-1001", "string"),
        ("Café  Noir", "CAFÉ NOIR", "string"),
    ],
)
def test_normalise_key_matches_the_same_value_written_two_ways(left: str, right: str, kind: str) -> None:
    assert normalise_key(left, kind) == normalise_key(right, kind)  # type: ignore[arg-type]
    assert normalise_key(left, kind) is not None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "kind"), [("", "string"), ("n/a", "phone"), ("abc", "number"), (None, "email")]
)
def test_normalise_key_has_no_value_for_blank_or_malformed_cells(value: str | None, kind: str) -> None:
    assert normalise_key(value, kind) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ('=HYPERLINK("x")', '\'=HYPERLINK("x")'),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
        ("\tx", "'\tx"),
        ("-12.5", "-12.5"),
        ("+91 98765 43210", "+91 98765 43210"),
        ("plain", "plain"),
        (None, ""),
    ],
)
def test_neutralise_cell_prefixes_formula_starts_only(cell: str | None, expected: str) -> None:
    assert neutralise_cell(cell) == expected


# ---------------------------------------------------------------------- upload and import


async def test_upload_imports_rows_and_reports_columns(admin_client: httpx.AsyncClient) -> None:
    body = await _dataset(admin_client)

    assert body["status"] == "ready"
    assert body["progress"] == 1.0
    assert body["row_count"] == 3
    assert body["slug"] == "demo-policies"
    columns = {c["name"]: c for c in body["columns"]}  # type: ignore[union-attr]
    assert columns["policy_number"] == {
        "name": "policy_number",
        "label": "Policy Number",
        "type": "string",
        "key": True,
    }
    assert columns["phone"]["type"] == "phone"
    assert columns["premium"]["type"] == "number"
    assert columns["note"]["key"] is False
    assert [k["name"] for k in body["key_columns"]] == ["policy_number", "phone", "email"]  # type: ignore[union-attr]

    listed = (await admin_client.get("/v1/datasets")).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == body["id"]
    rows = (await admin_client.get(f"/v1/datasets/{body['id']}/rows", params={"limit": 2})).json()
    assert rows["total"] == 3 and len(rows["rows"]) == 2
    assert rows["rows"][0]["holder_name"] == "Demo — Asha Rao"


async def test_a_file_over_the_row_limit_is_refused_with_a_plain_message(
    admin_client: httpx.AsyncClient,
) -> None:
    lines = ["phone"] + [f"{9000000000 + index}" for index in range(MAX_DATASET_ROWS + 1)]

    response = await _upload(admin_client, "\n".join(lines), keys={"phone": "phone"})

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert "more than 50,000 rows" in error["message"]
    assert error["details"]["reason"] == "too_many_rows"
    assert (await admin_client.get("/v1/datasets")).json()["total"] == 0


@pytest.mark.parametrize(
    ("content", "filename", "keys", "status", "fragment"),
    [
        (POLICIES, "policies.xlsx", KEYS, 415, ".csv, .tsv or .json"),
        (POLICIES, "policies.csv", {"Missing": "string"}, 422, "no column 'Missing'"),
        (POLICIES, "policies.csv", {}, 422, "at least one key column"),
        (POLICIES, "policies.csv", {"phone": "fax"}, 422, "key_columns is a JSON object"),
        ("phone\n", "empty.csv", {"phone": "phone"}, 422, "no data rows"),
        ('{"not": "a list"}', "rows.json", {"phone": "phone"}, 422, "list of objects"),
        ("a,b\n1,2,3\n", "ragged.csv", {"a": "string"}, 422, "more cells than the header"),
    ],
)
async def test_bad_files_are_refused_before_anything_is_stored(
    admin_client: httpx.AsyncClient, content: str, filename: str, keys: object, status: int, fragment: str
) -> None:
    response = await _upload(admin_client, content, filename=filename, keys=keys)

    assert response.status_code == status, response.text
    assert fragment in response.json()["error"]["message"]
    assert (await admin_client.get("/v1/datasets")).json()["total"] == 0


async def test_a_file_over_five_megabytes_is_413(admin_client: httpx.AsyncClient) -> None:
    content = "phone\n" + "1" * (5 * 1024 * 1024)

    response = await _upload(admin_client, content, keys={"phone": "phone"})

    assert response.status_code == 413


async def test_json_upload_reads_a_list_of_objects(admin_client: httpx.AsyncClient) -> None:
    rows = {"rows": [{"sku": "A-1", "stock": 4, "active": True}, {"sku": "B-2", "stock": 0, "tags": ["x"]}]}

    response = await _upload(admin_client, json.dumps(rows), filename="stock.json", keys={"sku": "string"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "json"
    assert [c["name"] for c in body["columns"]] == ["sku", "stock", "active", "tags"]
    found = await admin_client.post(f"/v1/datasets/{body['id']}/lookup", json={"keys": {"sku": "b-2"}})
    assert found.json()["rows"] == [{"sku": "B-2", "stock": "0", "active": None, "tags": '["x"]'}]


# ---------------------------------------------------------------------- lookups


async def test_phone_lookup_matches_either_way_of_writing_the_number(admin_client: httpx.AsyncClient) -> None:
    dataset = await _dataset(admin_client)
    path = f"/v1/datasets/{dataset['id']}/lookup"

    for written in ("+91 98765 43210", "098765 43210", "9876543210"):
        found = (await admin_client.post(path, json={"keys": {"phone": written}})).json()
        assert [row["policy_number"] for row in found["rows"]] == ["PD-1001"], written
    assert (await admin_client.post(path, json={"keys": {"phone": "+1 415 555 0100"}})).json()["rows"][0][
        "holder_name"
    ] == "Demo — Ben Cole"


async def test_lookup_bounds_columns_rows_and_match(admin_client: httpx.AsyncClient) -> None:
    dataset = await _dataset(admin_client)
    path = f"/v1/datasets/{dataset['id']}/lookup"

    exact = await admin_client.post(
        path, json={"keys": {"email": "ASHA@example.com"}, "return_columns": ["holder_name", "premium"]}
    )
    assert exact.json()["rows"] == [{"holder_name": "Demo — Asha Rao", "premium": "1200.50"}]

    prefix = (
        await admin_client.post(
            path, json={"keys": {"policy_number": "pd-10"}, "match": "prefix", "max_rows": 2}
        )
    ).json()
    assert [row["policy_number"] for row in prefix["rows"]] == ["PD-1001", "PD-1002"]
    assert prefix["truncated"] is True

    both = await admin_client.post(
        path, json={"keys": {"policy_number": "PD-1001", "email": "ben@example.com"}}
    )
    assert both.json()["rows"] == []

    for payload, fragment in (
        ({"keys": {"holder_name": "x"}}, "not a key column"),
        ({"keys": {"phone": "1"}, "return_columns": ["nope"]}, "not a column"),
        ({"keys": {"policy_number": "p"}, "match": "prefix"}, "at least 2 characters"),
    ):
        refused = await admin_client.post(path, json=payload)
        assert refused.status_code == 422 and fragment in refused.json()["error"]["message"], refused.text
    assert (await admin_client.post(path, json={"keys": {"phone": "x"}, "max_rows": 21})).status_code == 422


async def test_lookup_like_wildcards_are_literal(admin_client: httpx.AsyncClient) -> None:
    dataset = await _dataset(admin_client)

    found = await admin_client.post(
        f"/v1/datasets/{dataset['id']}/lookup", json={"keys": {"policy_number": "p%"}, "match": "prefix"}
    )

    assert found.json()["rows"] == []


async def test_export_neutralises_formula_cells(admin_client: httpx.AsyncClient) -> None:
    dataset = await _dataset(admin_client)

    response = await admin_client.get(f"/v1/datasets/{dataset['id']}/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "Policy Number"
    notes = [row[5] for row in rows[1:]]
    assert notes == ['\'=HYPERLINK("http://x.example")', "'@SUM(A1:A2)", "'-2+3"]
    assert rows[1][2] == "+91 98765 43210"


# ---------------------------------------------------------------------- import failures and quotas


async def test_a_missing_upload_fails_the_import_with_a_reason(
    admin_client: httpx.AsyncClient, database: Database, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = await _dataset(admin_client)

    class _Empty:
        async def get(self, key: str) -> bytes:
            raise FileNotFoundError(key)

    await dataset_service.import_rows(database, _Empty(), str(dataset["id"]))  # type: ignore[arg-type]

    body = (await admin_client.get(f"/v1/datasets/{dataset['id']}")).json()
    assert body["status"] == "failed"
    assert "missing from storage" in body["error"]
    assert (await admin_client.get(f"/v1/datasets/{dataset['id']}/rows")).json()["total"] == 0
    refused = await admin_client.post(f"/v1/datasets/{dataset['id']}/lookup", json={"keys": {"phone": "1"}})
    assert refused.status_code == 409 and "import failed" in refused.json()["error"]["message"]


async def test_workspace_quotas_refuse_one_more_dataset(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dataset_service, "MAX_DATASETS_PER_WORKSPACE", 1)
    await _dataset(admin_client)

    response = await _upload(admin_client, name="Demo — Second")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["limit"] == "datasets_per_workspace"
    assert limits.MAX_DATASETS_PER_WORKSPACE == 100


async def test_workspace_row_quota(admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dataset_service, "MAX_DATASET_ROWS_PER_WORKSPACE", 4)
    await _dataset(admin_client)

    response = await _upload(admin_client, name="Demo — Second")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["limit"] == "rows_per_workspace"


# ---------------------------------------------------------------------- the tenant rule


@dataclass
class TwoWorkspaces:
    beta_id: str
    bob: httpx.AsyncClient
    dataset_id: str


@pytest.fixture
async def two(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> AsyncIterator[TwoWorkspaces]:
    """A dataset in the default workspace, and Bob, admin of workspace B."""
    beta_id = await make_workspace(database, "beta")
    await make_user(database, "bob@b.example", role="admin", workspace_id=beta_id)
    dataset = await _dataset(admin_client)
    async with await login(app, "bob@b.example") as bob:
        yield TwoWorkspaces(beta_id=beta_id, bob=bob, dataset_id=str(dataset["id"]))


async def _session_in(database: Database, workspace_id: str) -> str:
    async with database.session() as session:
        agent = Agent(
            id=new_id(),
            workspace_id=workspace_id,
            slug=f"agent-{new_id()[:8]}",
            name="Agent",
            pack_id="generic",
            ui_panel_id="generic",
            published=True,
            config=inference_config().model_dump(mode="json"),
            config_version=1,
        )
        session.add(agent)
        await session.flush()
        row = SessionRow(
            id=new_id(),
            workspace_id=workspace_id,
            agent_id=agent.id,
            config_version=1,
            room_name=f"lkap-{new_id()}",
            participant_identity="u",
            participant_name="U",
            status="active",
            pipeline_mode="cascaded",
        )
        session.add(row)
        await session.flush()
        return row.id


async def test_another_workspaces_dataset_is_404_on_every_route(two: TwoWorkspaces) -> None:
    base = f"/v1/datasets/{two.dataset_id}"
    for method, path, body in (
        ("GET", base, None),
        ("GET", f"{base}/rows", None),
        ("GET", f"{base}/export", None),
        ("POST", f"{base}/lookup", {"keys": {"phone": "9876543210"}}),
        ("DELETE", base, None),
    ):
        response = await two.bob.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.text)
    assert (await two.bob.get("/v1/datasets")).json()["total"] == 0


async def test_a_lookup_never_returns_rows_of_another_workspaces_dataset(
    two: TwoWorkspaces, service_client: httpx.AsyncClient, database: Database
) -> None:
    own = await _session_in(database, DEFAULT_WORKSPACE_ID)
    foreign = await _session_in(database, two.beta_id)
    path = f"/internal/v1/datasets/{two.dataset_id}/lookup"
    keys = {"phone": "098765 43210"}

    allowed = await service_client.post(path, json={"session_id": own, "keys": keys})
    refused = await service_client.post(path, json={"session_id": foreign, "keys": keys})
    unknown = await service_client.post(path, json={"session_id": "0" * 32, "keys": keys})

    assert allowed.status_code == 200 and allowed.json()["rows"][0]["policy_number"] == "PD-1001"
    assert refused.status_code == 404 and "rows" not in refused.text
    assert unknown.status_code == 404


async def test_internal_lookup_needs_the_service_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/internal/v1/datasets/x/lookup", json={"session_id": "s", "keys": {"a": "1"}}
    )

    assert response.status_code == 401


async def test_a_tool_cannot_read_another_workspaces_dataset(two: TwoWorkspaces) -> None:
    response = await two.bob.post("/v1/tools", json=_tool_payload(two.dataset_id))

    assert response.status_code == 422
    assert "unknown lookup table" in response.json()["error"]["message"]


# ---------------------------------------------------------------------- the dataset tool kind


def _tool_payload(dataset_id: str, **definition: object) -> dict[str, object]:
    return {
        "kind": "dataset",
        "name": "lookup_policy",
        "definition": {
            "kind": "dataset",
            "name": "lookup_policy",
            "description": "Find the caller's policy by phone number.",
            "dataset_id": dataset_id,
            "key_columns": ["phone"],
            **definition,
        },
    }


async def test_dataset_tool_saves_and_blocks_deleting_its_dataset(admin_client: httpx.AsyncClient) -> None:
    dataset = await _dataset(admin_client)

    created = await admin_client.post(
        "/v1/tools",
        json=_tool_payload(
            str(dataset["id"]),
            return_columns=["holder_name"],
            pinned_arguments={"phone": "{{ ctx.caller_phone }}"},
        ),
    )
    assert created.status_code == 201, created.text
    assert created.json()["definition"]["kind"] == "dataset"
    listed = (await admin_client.get("/v1/tools", params={"kind": "dataset"})).json()
    assert [t["name"] for t in listed["items"]] == ["lookup_policy"]

    blocked = await admin_client.delete(f"/v1/datasets/{dataset['id']}")
    assert blocked.status_code == 409 and "lookup_policy" in blocked.json()["error"]["message"]

    assert (await admin_client.delete(f"/v1/tools/{created.json()['id']}")).status_code == 204
    assert (await admin_client.delete(f"/v1/datasets/{dataset['id']}")).status_code == 204
    assert (await admin_client.get(f"/v1/datasets/{dataset['id']}")).status_code == 404


@pytest.mark.parametrize(
    ("definition", "fragment"),
    [
        ({"key_columns": ["holder_name"]}, "not a key column"),
        ({"return_columns": ["nope"]}, "not a column"),
        ({"pinned_arguments": {"phone": "{{ ctx.nope }}"}}, "not a session value"),
    ],
)
async def test_dataset_tool_refuses_columns_the_dataset_lacks(
    admin_client: httpx.AsyncClient, definition: dict[str, object], fragment: str
) -> None:
    dataset = await _dataset(admin_client)

    response = await admin_client.post("/v1/tools", json=_tool_payload(str(dataset["id"]), **definition))

    assert response.status_code == 422, response.text
    assert fragment in response.text


async def test_resolved_config_carries_the_dataset_tool(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    dataset = await _dataset(admin_client)
    tool = (await admin_client.post("/v1/tools", json=_tool_payload(str(dataset["id"])))).json()
    config = inference_config()
    config.tools.tool_ids = [tool["id"]]
    async with database.session() as session:
        conn = connection_row(Vault(settings.master_key))
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, config, connection_id=conn.id)

    started = await service_client.post(
        "/internal/v1/sessions/start",
        json={"agent_id": agent.id, "room_name": "lkap-ds-room", "participant_identity": "u"},
    )

    assert started.status_code == 201, started.text
    tools = started.json()["tools"]
    assert [t["kind"] for t in tools] == ["dataset"]
    assert tools[0]["dataset_id"] == dataset["id"]


# ---------------------------------------------------------------------- the agent-config checks


def _ctx(definition: dict[str, object], datasets: dict[str, object]) -> ValidationContext:
    config = AgentConfig.model_validate(inference_config().model_dump(mode="json"))
    config.tools.tool_ids = ["t1"]
    return ValidationContext(
        config=config,
        tool_definitions_by_id={"t1": definition},
        datasets_by_id=datasets,  # type: ignore[arg-type]
    )


def test_validator_reports_a_missing_dataset_and_unknown_columns() -> None:
    definition = {
        "kind": "dataset",
        "name": "lookup_policy",
        "dataset_id": "d1",
        "key_columns": ["phone", "holder"],
        "return_columns": ["ghost"],
    }
    dataset = {
        "name": "Demo",
        "status": "pending",
        "keys": {"phone": "phone"},
        "columns": ["phone", "holder"],
    }

    missing = dataset_tool_issues(_ctx(definition, {}))
    found = dataset_tool_issues(_ctx(definition, {"d1": dataset}))

    assert [issue.path for issue in missing] == ["tools[0].definition.dataset_id"]
    assert {(issue.path, issue.severity) for issue in found} == {
        ("tools[0].definition.key_columns[1]", "error"),
        ("tools[0].definition.return_columns[0]", "error"),
        ("tools[0].definition.dataset_id", "warning"),
    }


async def test_agent_validation_flags_a_dataset_tool_whose_table_was_deleted(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    dataset = await _dataset(admin_client)
    tool = (await admin_client.post("/v1/tools", json=_tool_payload(str(dataset["id"])))).json()
    config = inference_config()
    config.tools.tool_ids = [tool["id"]]
    agent = await create_agent(admin_client, config=json.loads(config.model_dump_json()), published=False)
    async with database.session() as session:
        row = await session.get(Dataset, dataset["id"])
        assert row is not None
        await session.delete(row)

    result = (await admin_client.post(f"/v1/agents/{agent['id']}/validate")).json()

    assert any(
        issue["path"] == "tools[0].definition.dataset_id" and "not in this workspace" in issue["message"]
        for issue in result["issues"]
    ), result
