"""The registry catalog drift report (docs/v4/CUSTOM-MODELS.md D-V4-27, PLAN-V4 V4-10).

Offline. The Deepgram and Rime bodies are the real public lists recorded by
V4-07 (``tests/fixtures/catalogs/``); the keyed vendors' bodies follow their
documented shapes. The ``--fixtures`` path is exercised with files named after
the request URLs (:func:`drift.fixture_names`).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import credential_home, get

from lkap_api.catalogs import drift
from lkap_api.catalogs.adapters import get_adapter
from lkap_api.catalogs.openrouter import OPENROUTER_ADAPTERS
from lkap_api.catalogs.vendors import DEEPGRAM_MODELS_URL, RIME_VOICES_URL

FIXTURES = Path(__file__).parent / "fixtures" / "catalogs"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "catalog-drift.yml"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_LLM_URL = OPENROUTER_ADAPTERS["openrouter_llm_models"].models_url
SECRET = "sk-test-DRIFTSECRET0123456789abcdef"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fixture_dir(tmp_path: Path, bodies: dict[str, Any]) -> Path:
    """Write each body under the first name ``--fixtures`` looks up for its URL."""
    directory = tmp_path / "fixtures"
    directory.mkdir()
    for url, body in bodies.items():
        name = drift.fixture_names(httpx.URL(url))[0]
        (directory / name).write_text(json.dumps(body), encoding="utf-8")
    return directory


def _openrouter_body() -> dict[str, Any]:
    spec = get("openrouter-llm")
    data: list[dict[str, Any]] = [
        {"id": m.id, "name": m.label, "created": 1_700_000_000} for m in spec.models
    ]
    data[0]["expiration_date"] = "2026-12-01"
    data += [
        {"id": "vendor/old-model", "name": "Old", "created": 1_600_000_000},
        {"id": "vendor/newest-model", "name": "Newest", "created": 1_800_000_000},
        {"id": "vendor/undated-model", "name": "Undated"},
    ]
    return {"data": data}


async def _run(transport: httpx.MockTransport, **kwargs: Any) -> drift.DriftReport:
    async with httpx.AsyncClient(transport=transport) as client:
        return await drift.run(client, **kwargs)


# ----------------------------------------------------------------------- selection
def test_parse_only_keeps_id_shaped_tokens_and_drops_the_rest() -> None:
    assert drift.parse_only(None) is None
    assert drift.parse_only("  ") is None
    assert drift.parse_only("keyless, deepgram-stt") == {"keyless", "deepgram-stt"}
    assert drift.parse_only("$(curl evil),`x`,deepgram-stt") == {"deepgram-stt"}
    assert drift.parse_only("$(curl evil)") is None


def test_registry_ids_are_the_suggestions_then_the_default() -> None:
    spec = get("deepgram-stt")
    assert drift.registry_ids(spec)[: len(spec.models)] == [m.id for m in spec.models]
    assert spec.default_model in drift.registry_ids(spec)


def test_secret_env_by_home_names_the_primary_env_fallback_of_each_home() -> None:
    assert drift.SECRET_ENV_BY_HOME["openai-llm"] == "OPENAI_API_KEY"
    assert drift.SECRET_ENV_BY_HOME["google-llm"] == "GOOGLE_API_KEY"
    assert drift.SECRET_ENV_BY_HOME["anthropic-llm"] == "ANTHROPIC_API_KEY"
    # Aliased entries share the home's variable instead of declaring their own.
    assert "openrouter-stt" not in drift.SECRET_ENV_BY_HOME


def test_every_keyed_model_list_has_a_secret_variable() -> None:
    for spec in drift.model_entries():
        assert spec.catalog is not None
        adapter = get_adapter(spec.catalog.adapter)
        if "models" in spec.catalog.kinds and adapter is not None and not drift.is_keyless(adapter):
            assert credential_home(spec) in drift.SECRET_ENV_BY_HOME, spec.id


def test_the_keyless_lists_are_openrouter_deepgram_and_rime() -> None:
    keyless = set()
    for spec in drift.model_entries():
        assert spec.catalog is not None
        adapter = get_adapter(spec.catalog.adapter)
        if adapter is not None and drift.is_keyless(adapter):
            keyless.add(spec.vendor)
    assert keyless == {"OpenRouter", "Deepgram", "Rime"}


# ---------------------------------------------------------------------- comparison
def test_compare_reports_missing_new_and_deprecation_sections() -> None:
    spec = get("openai-llm")
    items = [
        CatalogItem(id="gpt-4.1", label="gpt-4.1", meta={"shutdown_date": "2027-01-01"}),
        CatalogItem(id="gpt-4o", label="gpt-4o"),
        CatalogItem(id="gpt-9", label="gpt-9", meta={"created": 1_900_000_000}),
    ]
    missing, new, notices = drift.compare(spec, items)
    assert [(m.model, m.is_default) for m in missing] == [("gpt-4.1-mini", False)]
    assert new is not None and new.ids == ["gpt-9"] and new.total == 1
    assert [(n.model, n.signal, n.value) for n in notices] == [("gpt-4.1", "shutdown_date", "2027-01-01")]


def test_compare_flags_a_missing_default() -> None:
    spec = get("cerebras-llm")
    missing, new, _ = drift.compare(spec, [CatalogItem(id="other", label="other")])
    assert [(m.model, m.is_default) for m in missing] == [(spec.default_model, True)]
    assert new is not None and new.ids == ["other"]


@pytest.mark.parametrize(
    ("meta", "signal", "value"),
    [
        ({"shutdown_date": "2026-10-01"}, "shutdown_date", "2026-10-01"),
        ({"expiration_date": "2026-11-11"}, "expiration_date", "2026-11-11"),
        ({"archived": True}, "archived", "true"),
        ({"deprecation": "2026-12-31"}, "deprecation", "2026-12-31"),
        ({"modelLifecycle": {"status": "LEGACY"}}, "modelLifecycle.status", "LEGACY"),
    ],
)
def test_compare_reads_each_vendor_retirement_signal(meta: dict[str, Any], signal: str, value: str) -> None:
    spec = get("mistral-llm")
    _, _, notices = drift.compare(spec, [CatalogItem(id=spec.default_model or "", label="x", meta=meta)])
    assert [(n.signal, n.value) for n in notices] == [(signal, value)]


def test_compare_ignores_signals_that_mean_active() -> None:
    spec = get("mistral-llm")
    meta = {"archived": False, "modelLifecycle": {"status": "ACTIVE"}, "shutdown_date": None}
    _, _, notices = drift.compare(spec, [CatalogItem(id=spec.default_model or "", label="x", meta=meta)])
    assert notices == []


def test_upstream_new_is_capped_newest_first_then_by_id() -> None:
    spec = get("cerebras-llm")
    undated = [CatalogItem(id=f"model-{n:02d}", label="x") for n in range(40)]
    dated = [
        CatalogItem(id="dated-old", label="x", meta={"created": 100}),
        CatalogItem(id="dated-new", label="x", meta={"created_at": "2026-09-01T00:00:00Z"}),
    ]
    _, new, _ = drift.compare(spec, [*undated, *dated])
    assert new is not None
    assert new.total == 42
    assert len(new.ids) == drift.UPSTREAM_NEW_CAP
    assert new.ids[:3] == ["dated-new", "dated-old", "model-00"]
    _, again, _ = drift.compare(spec, list(reversed([*undated, *dated])))
    assert again is not None and again.ids == new.ids


def test_deepgram_short_names_match_their_canonical_ids() -> None:
    """Asks #42: the registry's ``nova-3`` is Deepgram's ``nova-3-general``."""
    spec = get("deepgram-stt")
    aliases = drift.REGISTRY_ALIASES["deepgram-stt"]
    assert set(aliases) <= set(drift.registry_ids(spec))
    canonical = {raw["canonical_name"] for raw in _fixture("deepgram_models.json")["stt"]}
    assert set(aliases.values()) <= canonical
    items = [CatalogItem(id=name, label=name) for name in sorted(canonical)]
    missing, new, _ = drift.compare(spec, items)
    assert {m.model for m in missing} == {"flux-general-en"}
    assert missing[0].expected is not None
    assert new is not None
    assert "nova-3-general" not in new.ids and "nova-2-general" not in new.ids
    assert new.total == len(canonical) - len(aliases)


def test_known_unlisted_ids_are_registry_ids() -> None:
    for provider_id, ids in drift.KNOWN_UNLISTED.items():
        assert set(ids) <= set(drift.registry_ids(get(provider_id))), provider_id


# ----------------------------------------------------------------------------- run
async def test_keyless_run_from_fixtures_covers_openrouter_deepgram_and_rime(tmp_path: Path) -> None:
    directory = _fixture_dir(
        tmp_path,
        {
            DEEPGRAM_MODELS_URL: _fixture("deepgram_models.json"),
            RIME_VOICES_URL: _fixture("rime_voices_all_v2.json"),
            OPENROUTER_LLM_URL: _openrouter_body(),
        },
    )
    report = await _run(drift.fixture_transport(directory), env={}, only={"keyless"})

    checked = {row.provider_id: row for row in report.checked}
    assert set(checked) == {"deepgram-stt", "deepgram-tts", "rime-tts", "openrouter-llm"}
    assert all(row.keyless for row in report.checked)
    assert checked["deepgram-stt"].upstream_count == 42

    missing = {(row.provider_id, row.model) for row in report.registry_not_upstream}
    assert missing == {("deepgram-stt", "flux-general-en")}

    new = {row.provider_id: row for row in report.upstream_new}
    assert new["openrouter-llm"].ids == ["vendor/newest-model", "vendor/old-model", "vendor/undated-model"]
    assert set(new["rime-tts"].ids) == {"coda", "mist", "mistv2"}
    assert len(new["deepgram-stt"].ids) == drift.UPSTREAM_NEW_CAP

    first = get("openrouter-llm").models[0].id
    assert [(n.provider_id, n.model, n.signal) for n in report.deprecation_notices] == [
        ("openrouter-llm", first, "expiration_date")
    ]

    skipped = {row.provider_id: row.reason for row in report.skipped}
    # No fixture for the other OpenRouter filters: a vendor error is a skip, never a failure.
    assert skipped["openrouter-stt"] == "OpenRouter answered HTTP 404"
    assert skipped["openai-llm"] == "not selected by --only"


async def test_openrouter_is_read_keyless_without_the_key_probe() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_openrouter_body())

    report = await _run(
        httpx.MockTransport(handler), env={"OPENROUTER_API_KEY": SECRET}, only={"openrouter-llm"}
    )
    assert [row.provider_id for row in report.checked] == ["openrouter-llm"]
    assert [str(r.url) for r in seen] == [OPENROUTER_LLM_URL]
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}])
async def test_a_keyed_list_without_its_secret_is_skipped(env: dict[str, str]) -> None:
    """An unset repository secret reaches the job as an empty string: the same as absent."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected call to {request.url}")

    report = await _run(httpx.MockTransport(handler), env=env, only={"openai-llm", "anthropic-llm"})
    assert report.checked == []
    reasons = {row.provider_id: row.reason for row in report.skipped}
    assert reasons["openai-llm"] == "no secret: OPENAI_API_KEY is not set"
    assert reasons["anthropic-llm"] == "no secret: ANTHROPIC_API_KEY is not set"


async def test_a_shared_vendor_list_is_fetched_once_and_filtered_per_entry() -> None:
    calls: list[httpx.Request] = []
    body = {
        "data": [
            {"id": "gpt-4.1", "created": 1},
            {"id": "gpt-4o", "created": 1},
            {"id": "gpt-4.1-mini", "created": 1},
            {"id": "gpt-5", "created": 2},
            {"id": "whisper-1", "created": 1},
            {"id": "gpt-4o-mini-tts", "created": 1},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(200, json=body)

    report = await _run(
        httpx.MockTransport(handler), env={"OPENAI_API_KEY": SECRET}, only={"openai-llm", "openai-tts"}
    )
    assert len(calls) == 1 and str(calls[0].url) == OPENAI_MODELS_URL
    new = {row.provider_id: row.ids for row in report.upstream_new}
    assert new["openai-llm"] == ["gpt-5"]  # whisper and the TTS model are filtered out
    assert "openai-tts" not in new  # its only item is its registry id
    assert [row.model for row in report.registry_not_upstream] == []
    assert all(not row.keyless for row in report.checked)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, text=f"boom {SECRET}"), "OpenAI answered HTTP 500"),
        (httpx.Response(401, json={"error": f"bad key {SECRET}"}), "OpenAI answered HTTP 401"),
    ],
)
async def test_a_vendor_error_is_a_skip_that_never_echoes_the_secret(
    response: httpx.Response, reason: str, tmp_path: Path
) -> None:
    report = await _run(
        httpx.MockTransport(lambda _: response), env={"OPENAI_API_KEY": SECRET}, only={"openai-llm"}
    )
    assert report.checked == []
    assert [row.reason for row in report.skipped if row.provider_id == "openai-llm"] == [reason]
    md, js = drift.write_outputs(report, tmp_path)
    assert SECRET not in md.read_text(encoding="utf-8")
    assert SECRET not in js.read_text(encoding="utf-8")


async def test_a_network_failure_is_a_skip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    report = await _run(httpx.MockTransport(handler), env={}, only={"rime-tts"})
    assert [(row.provider_id, row.reason) for row in report.skipped if row.provider_id == "rime-tts"] == [
        ("rime-tts", "Rime request failed: ConnectTimeout")
    ]


async def test_catalogs_without_a_model_list_are_skipped_with_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected call to {request.url}")

    report = await _run(httpx.MockTransport(handler), env={}, only={"elevenlabs-tts", "bey-avatar"})
    reasons = {row.provider_id: row.reason for row in report.skipped}
    assert reasons["elevenlabs-tts"] == "the catalog lists voices, not models: nothing to compare"
    assert reasons["bey-avatar"] == "the catalog lists avatars, not models: nothing to compare"


# -------------------------------------------------------------------------- output
def test_markdown_has_the_four_sections_and_quotes_vendor_ids() -> None:
    report = drift.DriftReport(
        generated_at=drift.dt.datetime(2026, 9, 25, tzinfo=drift.dt.UTC),
        registry_not_upstream=[drift.RegistryMissing(provider_id="x-llm", model="gone", is_default=True)],
        upstream_new=[drift.UpstreamNew(provider_id="x-llm", total=30, ids=["@someone `rm`", "<b>bold</b>"])],
        deprecation_notices=[
            drift.DeprecationNotice(provider_id="x-llm", model="old", signal="archived", value="true")
        ],
        skipped=[drift.Skipped(provider_id="y-tts", reason="no secret: Y_API_KEY is not set")],
    )
    body = drift.render_markdown(report)
    for section in ("registry_not_upstream", "upstream_new", "deprecation_notices", "skipped"):
        assert f"## `{section}`" in body
    assert body.startswith(f"# {drift.ISSUE_TITLE}\n")
    assert "(the entry's **default**)" in body
    assert "`@someone 'rm'`" in body  # inline code: no mention, no code-span break-out
    assert "`<b>bold</b>`" in body
    assert "and 28 more" in body


def test_markdown_is_trimmed_under_the_issue_body_limit() -> None:
    report = drift.DriftReport(
        generated_at=drift.dt.datetime(2026, 9, 25, tzinfo=drift.dt.UTC),
        skipped=[drift.Skipped(provider_id=f"p-{n}", reason="x" * 200) for n in range(1000)],
    )
    body = drift.render_markdown(report)
    assert len(body) < 65_536
    assert body.rstrip().endswith("artifact.")


def test_json_round_trips(tmp_path: Path) -> None:
    report = drift.DriftReport(
        generated_at=drift.dt.datetime(2026, 9, 25, tzinfo=drift.dt.UTC),
        skipped=[drift.Skipped(provider_id="a", reason="b")],
    )
    _, js = drift.write_outputs(report, tmp_path)
    assert drift.DriftReport.model_validate_json(js.read_text(encoding="utf-8")) == report


def test_main_writes_both_files_and_exits_zero_from_fixtures(tmp_path: Path) -> None:
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    shutil.copy(
        FIXTURES / "rime_voices_all_v2.json", fixtures / drift.fixture_names(httpx.URL(RIME_VOICES_URL))[0]
    )
    out = tmp_path / "out"
    code = drift.main(["--only", "rime-tts", "--fixtures", str(fixtures), "--out", str(out)], env={})
    assert code == 0
    data = json.loads((out / "catalog-drift.json").read_text(encoding="utf-8"))
    assert [row["provider_id"] for row in data["checked"]] == ["rime-tts"]
    assert set(data) >= {"registry_not_upstream", "upstream_new", "deprecation_notices", "skipped"}
    assert (out / "catalog-drift.md").read_text(encoding="utf-8").startswith("# Catalog drift")


def test_main_exits_zero_and_reports_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any, **__: Any) -> drift.DriftReport:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(drift, "run", boom)
    assert drift.main(["--out", str(tmp_path)], env={}) == 0
    body = (tmp_path / "catalog-drift.md").read_text(encoding="utf-8")
    assert "internal error (RuntimeError)" in body
    assert SECRET not in body


def test_fixture_names_prefer_the_query_specific_file() -> None:
    names = drift.fixture_names(httpx.URL("https://openrouter.ai/api/v1/models?supported_parameters=tools"))
    assert names == [
        "openrouter.ai_api_v1_models_supported_parameters_tools.json",
        "openrouter.ai_api_v1_models.json",
    ]


# ------------------------------------------------------------------------ workflow
def _workflow() -> dict[Any, Any]:
    yaml = pytest.importorskip("yaml")
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_workflow_is_weekly_and_dispatchable_with_least_privilege() -> None:
    wf = _workflow()
    triggers = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert triggers["schedule"] == [{"cron": "23 6 * * 1"}]
    assert "workflow_dispatch" in triggers
    assert wf["permissions"] == {"contents": "read"}
    writers = [name for name, job in wf["jobs"].items() if "permissions" in job]
    assert writers == ["report"]
    assert wf["jobs"]["report"]["permissions"] == {"issues": "write"}
    assert all(job.get("continue-on-error") is True for job in wf["jobs"].values())


def test_workflow_passes_exactly_the_secrets_the_module_reads() -> None:
    wf = _workflow()
    steps = wf["jobs"]["drift"]["steps"]
    fetch = next(step for step in steps if "lkap_api.catalogs.drift" in step.get("run", ""))
    assert fetch["continue-on-error"] is True
    passed = {name for name, value in fetch["env"].items() if "secrets." in str(value)}
    read = set()
    for spec in drift.model_entries():
        assert spec.catalog is not None
        adapter = get_adapter(spec.catalog.adapter)
        if "models" in spec.catalog.kinds and adapter is not None and not drift.is_keyless(adapter):
            read.add(drift.SECRET_ENV_BY_HOME[credential_home(spec)])
    assert passed == read


def test_workflow_never_interpolates_expressions_inside_run() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    wf = _workflow()
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")
    assert re.search(r"DRIFT_ONLY: \$\{\{ inputs\.only \}\}", text)
    assert '"Catalog drift"' in text and drift.ISSUE_TITLE == "Catalog drift"
