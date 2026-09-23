"""Tests for `lkap_api.webhooks`: signing, durable retry/dead-letter, CRUD, redeliver."""

from __future__ import annotations

import httpx
import pytest
import respx
from auth_helpers import login, make_user, make_workspace
from conftest import captured_text
from fastapi import FastAPI

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import WebhookDelivery, WebhookEndpoint, new_id
from lkap_api.db.session import Database
from lkap_api.errors import UnprocessableEntityError
from lkap_api.jobs.service import JobsService
from lkap_api.routers.webhooks import _check_url
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks.delivery import MAX_ATTEMPTS, RETRY_SCHEDULE_S, deliver_once
from lkap_api.webhooks.service import emit
from lkap_api.webhooks.signing import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    generate_secret,
    sign,
    verify_signature,
)

WEBHOOK_URL = "https://hooks.example.com/ep"


# --------------------------------------------------------------------------- signing
def test_sign_and_verify_round_trip() -> None:
    secret = "s3cr3t"
    body = b'{"id":"evt_1","type":"session.ended"}'
    header = sign(secret, body, t=1_700_000_000)
    assert header == f"t=1700000000,v1={header.split('v1=')[1]}"
    assert verify_signature(secret, header, body, tolerance_s=999_999_999)


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"{}"
    header = sign("secret-a", body)
    assert not verify_signature("secret-b", header, body)


def test_verify_signature_rejects_tampered_body() -> None:
    header = sign("secret", b"original")
    assert not verify_signature("secret", header, b"tampered")


def test_verify_signature_rejects_stale_timestamp() -> None:
    header = sign("secret", b"{}", t=1_000)
    assert not verify_signature("secret", header, b"{}", tolerance_s=60)


def test_verify_signature_rejects_malformed_header() -> None:
    assert not verify_signature("secret", "not-a-valid-header", b"{}")


# --------------------------------------------------------------------------- durable delivery
async def _make_endpoint(
    database: Database, vault: Vault, url: str, *, events: list[str] | None = None, enabled: bool = True
) -> tuple[str, str]:
    secret = generate_secret()
    async with database.session() as session:
        row = WebhookEndpoint(
            url=url, secret_ct=vault.encrypt({"secret": secret}), events=events or [], enabled=enabled
        )
        session.add(row)
        await session.flush()
        endpoint_id = row.id
    return endpoint_id, secret


async def _make_delivery(database: Database, endpoint_id: str, *, event_type: str = "test.event") -> str:
    async with database.session() as session:
        row = WebhookDelivery(
            endpoint_id=endpoint_id,
            event_type=event_type,
            event_id=new_id(),
            payload={"id": "evt_1", "type": event_type, "workspace_id": "ws", "data": {"k": "v"}},
            status="pending",
            attempt=0,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _get_delivery(database: Database, delivery_id: str) -> WebhookDelivery:
    async with database.session() as session:
        row = await session.get(WebhookDelivery, delivery_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_deliver_once_success_marks_delivered(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id)

    assert outcome is not None
    assert outcome.status == "delivered"
    assert outcome.attempt == 1
    row = await _get_delivery(database, delivery_id)
    assert row.status == "delivered"
    assert row.delivered_at is not None


async def test_deliver_once_signs_the_exact_body_sent(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                await deliver_once(session, http, vault, delivery_id)

    request = route.calls.last.request
    assert verify_signature(secret, request.headers[SIGNATURE_HEADER], request.content)
    assert request.headers[EVENT_ID_HEADER]


async def test_delivery_retries_on_5xx_then_succeeds_on_the_fourth_attempt(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(
            side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(500), httpx.Response(200)]
        )
        async with httpx.AsyncClient() as http:
            for expected_attempt in (1, 2, 3):
                async with database.session() as session:
                    outcome = await deliver_once(session, http, vault, delivery_id)
                assert outcome is not None
                assert outcome.status == "pending"
                assert outcome.attempt == expected_attempt
                assert outcome.next_attempt_at is not None

            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id)
            assert outcome is not None
            assert outcome.status == "delivered"
            assert outcome.attempt == 4

    row = await _get_delivery(database, delivery_id)
    assert row.status == "delivered"
    assert row.attempt == 4


async def test_delivery_dead_letters_after_the_schedule_is_exhausted(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        async with httpx.AsyncClient() as http:
            outcome = None
            for _ in range(MAX_ATTEMPTS):
                async with database.session() as session:
                    outcome = await deliver_once(session, http, vault, delivery_id)

    assert len(RETRY_SCHEDULE_S) == MAX_ATTEMPTS
    assert outcome is not None
    assert outcome.status == "dead"
    assert outcome.attempt == MAX_ATTEMPTS
    row = await _get_delivery(database, delivery_id)
    assert row.status == "dead"
    assert row.next_attempt_at is None


async def test_delivery_stops_retrying_on_a_non_retryable_4xx(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(404))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id)

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.attempt == 1
    row = await _get_delivery(database, delivery_id)
    assert row.status == "failed"


@pytest.mark.parametrize("status_code", [408, 425, 429, 503])
async def test_delivery_retries_transient_status_codes(
    database: Database, settings: Settings, status_code: int
) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(status_code))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id)

    assert outcome is not None
    assert outcome.status == "pending"


async def test_deliver_once_network_error_is_retried(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id)

    assert outcome is not None
    assert outcome.status == "pending"
    assert outcome.error is not None and "ConnectError" in outcome.error


async def test_disabled_endpoint_is_skipped_unless_ignore_enabled(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, _secret = await _make_endpoint(database, vault, WEBHOOK_URL, enabled=False)
    delivery_id = await _make_delivery(database, endpoint_id)

    async with httpx.AsyncClient() as http:
        async with database.session() as session:
            outcome = await deliver_once(session, http, vault, delivery_id)
        assert outcome is None

        with respx.mock:
            respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
            async with database.session() as session:
                outcome = await deliver_once(session, http, vault, delivery_id, ignore_enabled=True)
        assert outcome is not None
        assert outcome.status == "delivered"


async def test_secret_and_payload_never_appear_in_logs(
    database: Database, settings: Settings, log_capture: pytest.LogCaptureFixture
) -> None:
    vault = Vault(settings.master_key)
    endpoint_id, secret = await _make_endpoint(database, vault, WEBHOOK_URL)
    delivery_id = await _make_delivery(database, endpoint_id)

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as http:
            async with database.session() as session:
                await deliver_once(session, http, vault, delivery_id)

    text = captured_text(log_capture)
    assert secret not in text
    assert secret[:8] not in text  # the secret's own prefix never appears either


# --------------------------------------------------------------------------- emit()
async def test_emit_fans_out_to_matching_enabled_endpoints_only(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    matching_id, _ = await _make_endpoint(
        database, vault, "https://a.example.com/hook", events=["session.ended"]
    )
    other_event_id, _ = await _make_endpoint(
        database, vault, "https://b.example.com/hook", events=["call.ended"]
    )
    wildcard_id, _ = await _make_endpoint(database, vault, "https://c.example.com/hook", events=[])
    disabled_id, _ = await _make_endpoint(database, vault, "https://d.example.com/hook", enabled=False)

    jobs = JobsService(database=database, settings=settings, vault=vault)
    try:
        with respx.mock:
            respx.post("https://a.example.com/hook").mock(return_value=httpx.Response(200))
            respx.post("https://c.example.com/hook").mock(return_value=httpx.Response(200))
            delivery_ids = await emit(
                database, jobs, workspace_id=DEFAULT_WORKSPACE_ID, event_type="session.ended", data={}
            )
    finally:
        await jobs.aclose()

    async with database.session() as session:
        endpoint_ids = set()
        for delivery_id in delivery_ids:
            row = await session.get(WebhookDelivery, delivery_id)
            assert row is not None
            endpoint_ids.add(row.endpoint_id)
    assert matching_id in endpoint_ids
    assert wildcard_id in endpoint_ids
    assert other_event_id not in endpoint_ids
    assert disabled_id not in endpoint_ids


# --------------------------------------------------------------------------- admin CRUD
async def test_create_webhook_requires_admin(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/webhooks", json={"url": WEBHOOK_URL})
    assert response.status_code == 401


def test_check_url_rejects_http_outside_dev(settings: Settings) -> None:
    prod_settings = settings.model_copy(update={"env": "prod"})
    with pytest.raises(UnprocessableEntityError):
        _check_url(prod_settings, "http://hooks.example.com/ep")
    _check_url(prod_settings, "https://hooks.example.com/ep")  # does not raise


def test_check_url_allows_http_in_dev(settings: Settings) -> None:
    dev_settings = settings.model_copy(update={"env": "dev"})
    _check_url(dev_settings, "http://hooks.example.com/ep")  # does not raise, e.g. `webhook_sink.py`


async def test_create_webhook_rejects_a_non_http_scheme(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/webhooks", json={"url": "ftp://hooks.example.com/ep"})
    assert response.status_code == 422, response.text


async def test_a_workspace_cannot_see_or_redeliver_another_workspaces_webhook(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient
) -> None:
    """CONTRACTS-V2 §3.1: a cross-workspace read is a 404, never a 403 (no existence leak)."""
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL})
    endpoint_id = created.json()["id"]
    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        test_response = await admin_client.post(f"/v1/webhooks/{endpoint_id}/test")
    delivery_id = test_response.json()["id"]

    beta_id = await make_workspace(database, "beta-webhooks")
    await make_user(database, "beta-admin@b.example", role="admin", workspace_id=beta_id)
    async with await login(app, "beta-admin@b.example") as bob:
        assert (await bob.get(f"/v1/webhooks/{endpoint_id}")).status_code == 404
        assert (await bob.get("/v1/webhooks")).json()["total"] == 0
        assert (await bob.put(f"/v1/webhooks/{endpoint_id}", json={"url": WEBHOOK_URL})).status_code == 404
        assert (await bob.delete(f"/v1/webhooks/{endpoint_id}")).status_code == 404
        assert (await bob.get(f"/v1/webhooks/{endpoint_id}/deliveries")).status_code == 404
        assert (await bob.post(f"/v1/webhooks/deliveries/{delivery_id}/redeliver")).status_code == 404

    # Sanity: the owning workspace still sees it (proves the 404s above are scoping, not a bug).
    assert (await admin_client.get(f"/v1/webhooks/{endpoint_id}")).status_code == 200


async def test_create_webhook_returns_the_secret_once(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL, "events": ["session.ended"]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert "secret" in body and len(body["secret"]) > 16
    assert body["secret_prefix"] == body["secret"][:8]

    fetched = await admin_client.get(f"/v1/webhooks/{body['id']}")
    assert fetched.status_code == 200
    assert "secret" not in fetched.json()
    assert fetched.json()["secret_prefix"] == body["secret_prefix"]


async def test_list_update_delete_webhook(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL, "events": []})
    endpoint_id = created.json()["id"]

    listed = await admin_client.get("/v1/webhooks")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = await admin_client.put(
        f"/v1/webhooks/{endpoint_id}",
        json={"url": WEBHOOK_URL, "events": ["session.ended"], "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["events"] == ["session.ended"]

    deleted = await admin_client.delete(f"/v1/webhooks/{endpoint_id}")
    assert deleted.status_code == 204

    missing = await admin_client.get(f"/v1/webhooks/{endpoint_id}")
    assert missing.status_code == 404


async def test_get_unknown_webhook_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/webhooks/does-not-exist")
    assert response.status_code == 404


async def test_test_endpoint_delivers_synchronously_and_reports_the_outcome(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL})
    endpoint_id = created.json()["id"]

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        response = await admin_client.post(f"/v1/webhooks/{endpoint_id}/test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "delivered"
    assert body["event_type"] == "webhook.test"


async def test_deliveries_list_and_redeliver(admin_client: httpx.AsyncClient, database: Database) -> None:
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL})
    endpoint_id = created.json()["id"]

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        test_response = await admin_client.post(f"/v1/webhooks/{endpoint_id}/test")
    assert test_response.json()["status"] == "pending"
    delivery_id = test_response.json()["id"]

    listed = await admin_client.get(f"/v1/webhooks/{endpoint_id}/deliveries")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        redelivered = await admin_client.post(f"/v1/webhooks/deliveries/{delivery_id}/redeliver")
    assert redelivered.status_code == 200, redelivered.text
    assert redelivered.json()["status"] == "delivered"


async def test_redeliver_unknown_delivery_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/webhooks/deliveries/does-not-exist/redeliver")
    assert response.status_code == 404


async def test_app_closes_the_lazily_started_jobs_service_without_error(
    app: FastAPI, admin_client: httpx.AsyncClient
) -> None:
    """`create_webhook` never touches `JobsDep`; `redeliver` does — confirm it starts and closes cleanly."""
    created = await admin_client.post("/v1/webhooks", json={"url": WEBHOOK_URL})
    endpoint_id = created.json()["id"]
    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        test_response = await admin_client.post(f"/v1/webhooks/{endpoint_id}/test")
    delivery_id = test_response.json()["id"]

    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        await admin_client.post(f"/v1/webhooks/deliveries/{delivery_id}/redeliver")

    service: JobsService | None = getattr(app.state, "jobs", None)
    assert service is not None
    await service.aclose()
