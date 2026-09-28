"""V6-29 (S6-27): the redacted worker environment never carries the OTLP headers value.

`OTEL_EXPORTER_OTLP_HEADERS` is, by OpenTelemetry's convention, a telemetry vendor's auth
header. The api copies it from its own environment into the worker's; the admin
``worker-env`` template (and the console's **Copy worker settings**) must show a placeholder,
while the supervisor's real environment keeps the value.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from connection_fakes import connection_row

from lkap_api.connections.bundle import (
    TEMPLATE_FORMATS,
    deploy_bundle,
    redacted_env,
    worker_env,
    worker_env_template,
)
from lkap_api.connections.clients import ConnectionCredentials
from lkap_api.settings import Settings
from lkap_api.vault import Vault

HEADERS_VALUE = "Authorization=Bearer placeholder-otlp-token-0000"
ENDPOINT_VALUE = "https://otlp.example.com:4318"


@pytest.fixture
def otlp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", HEADERS_VALUE)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT_VALUE)


@pytest.mark.parametrize("fmt", TEMPLATE_FORMATS)
def test_worker_env_template_never_carries_the_otlp_headers_value(
    settings: Settings, otlp_env: None, fmt: str
) -> None:
    row = connection_row(Vault(settings.master_key))

    text = worker_env_template(row, settings, "…abcd", fmt)

    assert HEADERS_VALUE not in text and "placeholder-otlp-token" not in text
    assert "<OTEL_EXPORTER_OTLP_HEADERS>" in text
    assert ENDPOINT_VALUE in text  # the endpoint is not a secret and the worker needs it


def test_the_supervisors_real_environment_keeps_the_otlp_headers(settings: Settings, otlp_env: None) -> None:
    row = connection_row(Vault(settings.master_key))
    creds = ConnectionCredentials(
        connection_id=row.id,
        credentials_version=1,
        url=row.url,
        agent_name=row.agent_name,
        api_key="placeholder-key",
        api_secret="placeholder-secret",
    )

    assert worker_env(row, creds, settings).env["OTEL_EXPORTER_OTLP_HEADERS"] == HEADERS_VALUE
    redacted = redacted_env(row, settings, "…abcd")
    assert redacted["OTEL_EXPORTER_OTLP_HEADERS"] == "<OTEL_EXPORTER_OTLP_HEADERS>"


def test_the_deploy_bundle_never_carries_the_otlp_headers_value(settings: Settings, otlp_env: None) -> None:
    row = connection_row(Vault(settings.master_key))

    with zipfile.ZipFile(io.BytesIO(deploy_bundle(row, settings, "…abcd"))) as archive:
        contents = "".join(archive.read(name).decode() for name in archive.namelist())

    assert HEADERS_VALUE not in contents
    assert "OTEL_EXPORTER_OTLP_HEADERS=<OTEL_EXPORTER_OTLP_HEADERS>" in contents


def test_no_otlp_headers_placeholder_when_the_variable_is_unset(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    row = connection_row(Vault(settings.master_key))

    assert "OTEL_EXPORTER_OTLP_HEADERS" not in redacted_env(row, settings, "…abcd")
