"""V6-01 (D-V6-9): OpenRouter prices in the pickers and estimates.

Defect A: a ref without a credential (every picker quote) reads the keyed catalogue
rows of the workspace's default, else only, OpenRouter credential. Defect B: speech
models are priced in their own unit (per second, per token, per character) or not at
all ("no price (unit unknown)"). The catalogue items are copied from OpenRouter's
public ``/models`` listing on 2026-09-28 (``pricing`` and ``architecture`` only);
nothing here calls a vendor.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx
from auth_helpers import make_workspace
from lkap_contracts.agent_config import AgentConfig, PipelineConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import UNIT_UNKNOWN_NOTE

from lkap_api.costs.assumptions import apply_overrides, default_assumptions
from lkap_api.costs.estimate import build_estimate
from lkap_api.costs.prices import LiveSheet, PriceBook, load_live_sheets
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Credential, ProviderCatalogCache, WorkspaceProvider
from lkap_api.db.session import Database

LLM = "openai/gpt-4.1-mini"
NOVA = "deepgram/nova-3"
MINI_TRANSCRIBE = "openai/gpt-4o-mini-transcribe"
MAI = "microsoft/mai-transcribe-2"
AURA = "deepgram/aura-2"
GEMINI_TTS = "google/gemini-3.8-flash-tts"
SEED = "bytedance-seed/seed-audio-1-0"


def _arch(inputs: str, outputs: str, tokenizer: str) -> dict[str, Any]:
    return {
        "modality": f"{inputs}->{outputs}",
        "input_modalities": [inputs],
        "output_modalities": [outputs],
        "tokenizer": tokenizer,
        "instruct_type": None,
    }


def _item(model_id: str, prompt: str, completion: str, arch: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": model_id,
        "meta": {
            "id": model_id,
            "pricing": {"prompt": prompt, "completion": completion},
            "architecture": arch,
        },
    }


CATALOGUE: dict[str, list[dict[str, Any]]] = {
    "openrouter-llm": [
        _item(LLM, "0.0000004", "0.0000016", _arch("text", "text", "GPT")),
    ],
    "openrouter-stt": [
        _item(NOVA, "0.0000716666666667", "0", _arch("audio", "transcription", "Other")),
        _item(MINI_TRANSCRIBE, "0.00000125", "0.000005", _arch("audio", "transcription", "GPT")),
        _item(MAI, "0.1", "0", _arch("audio", "transcription", "Other")),
    ],
    "openrouter-tts": [
        _item(AURA, "0.00003", "0", _arch("text", "speech", "Other")),
        _item(GEMINI_TTS, "0.0000005", "0.000009", _arch("text", "speech", "Gemini")),
        _item(SEED, "0", "0.0025", _arch("text", "speech", "Other")),
    ],
}


def _credential(workspace_id: str = DEFAULT_WORKSPACE_ID, label: str = "OpenRouter") -> Credential:
    # Never decrypted here: the price lookup only needs the row's id.
    return Credential(
        workspace_id=workspace_id,
        provider_id="openrouter-llm",
        label=label,
        ciphertext=b"sk-or-v1-placeholder-ciphertext",
        fingerprint=label[:8],
    )


async def _cache_under(database: Database, credential_id: str) -> None:
    async with database.session() as session:
        for provider_id, items in CATALOGUE.items():
            session.add(
                ProviderCatalogCache(
                    provider_id=provider_id,
                    credential_id=credential_id,
                    kind="models",
                    items=items,
                    ttl_s=21600,
                )
            )


async def _one_key_workspace(database: Database) -> str:
    async with database.session() as session:
        credential = _credential()
        session.add(credential)
        await session.flush()
        credential_id = credential.id
    await _cache_under(database, credential_id)
    return credential_id


async def _quotes(admin_client: httpx.AsyncClient, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    response = await admin_client.post(
        "/v1/pricing/quotes", json={"items": [{"provider_id": p, "model": m} for p, m in pairs]}
    )
    assert response.status_code == 200, response.text
    return {item["model"]: item for item in response.json()["items"]}


def _units(item: dict[str, Any]) -> set[str]:
    return {quote["unit"] for quote in item["quotes"]}


# ------------------------------------------------------------------ defect A: the picker
async def test_a_picker_quote_in_a_workspace_with_one_openrouter_key_is_live(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await _one_key_workspace(database)

    items = await _quotes(
        admin_client,
        [("openrouter-llm", LLM), ("openrouter-stt", NOVA), ("openrouter-tts", AURA)],
    )

    llm, stt, tts = items[LLM], items[NOVA], items[AURA]
    for item in (llm, stt, tts):
        assert item["note"] is None, item
        assert item["quotes"] and {q["source"] for q in item["quotes"]} == {"live"}
        assert item["per_minute_usd"] is not None
    assert Decimal(next(q for q in llm["quotes"] if q["unit"] == "tokens_in")["usd_per_unit"]) == Decimal(
        "0.0000004"
    )
    # Nova-3 through OpenRouter: per audio second, 60 billed seconds a minute ($0.0043).
    assert _units(stt) == {"audio_s_in"}
    assert Decimal(stt["per_minute_usd"]) == Decimal("0.0043")
    # Aura-2: per character, 150 words x 6 characters x 45 % talk = 405 a minute.
    assert _units(tts) == {"chars"}
    assert Decimal(tts["per_minute_usd"]) == Decimal("0.01215")


async def test_a_picker_quote_uses_the_default_key_when_there_are_several(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.session() as session:
        first, second = _credential(label="first"), _credential(label="second")
        session.add_all([first, second])
        await session.flush()
        second_id = second.id
    await _cache_under(database, second_id)

    ambiguous = await _quotes(admin_client, [("openrouter-llm", LLM)])
    assert ambiguous[LLM]["quotes"] == [] and ambiguous[LLM]["note"] == "no price"

    async with database.session() as session:
        session.add(
            WorkspaceProvider(
                workspace_id=DEFAULT_WORKSPACE_ID,
                provider_id="openrouter-llm",
                default_credential_id=second_id,
            )
        )
    chosen = await _quotes(admin_client, [("openrouter-llm", LLM)])
    assert chosen[LLM]["note"] is None and chosen[LLM]["quotes"][0]["source"] == "live"


async def test_a_bare_ref_never_reads_another_workspace_s_key(database: Database) -> None:
    """S5-27 for defect A: the default/only credential is resolved inside the caller's workspace."""
    other = await make_workspace(database, "v6-01-other")
    async with database.session() as session:
        theirs = _credential(workspace_id=other, label="theirs")
        session.add(theirs)
        await session.flush()
        theirs_id = theirs.id
    await _cache_under(database, theirs_id)

    async with database.session() as session:
        ours = await load_live_sheets(session, [("openrouter-llm", LLM)], workspace_id=DEFAULT_WORKSPACE_ID)
        public_only = await load_live_sheets(session, [("openrouter-llm", LLM)], workspace_id=None)
        in_their_workspace = await load_live_sheets(session, [("openrouter-llm", LLM)], workspace_id=other)
    assert ours == {}
    assert public_only == {}
    assert ("openrouter-llm", LLM) in in_their_workspace


# ------------------------------------------------------------------ defect B: the units
async def test_picker_speech_quotes_are_priced_in_each_model_s_own_unit(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await _one_key_workspace(database)

    items = await _quotes(
        admin_client,
        [
            ("openrouter-stt", MINI_TRANSCRIBE),
            ("openrouter-stt", MAI),
            ("openrouter-tts", GEMINI_TTS),
            ("openrouter-tts", SEED),
        ],
    )

    # gpt-4o-mini-transcribe: per token. 60 s x 40 tokens/s x $1.25/1M = $0.003 heard, plus the
    # transcript (405 characters / 4 = 101.25 tokens x $5/1M = $0.000506) written.
    mini = items[MINI_TRANSCRIBE]
    assert _units(mini) == {"audio_tokens_in", "tokens_out"}  # never `audio_s_in`
    assert Decimal(mini["per_minute_usd"]) == Decimal("0.003506")
    per_second_reading = Decimal("0.00000125") * 60  # what the picker showed before V6-01
    assert Decimal(mini["per_minute_usd"]) / per_second_reading > 40

    # mai-transcribe-2 lists 0.1 a second ($6 a minute): refused, never a number.
    mai = items[MAI]
    assert mai["quotes"] == [] and mai["per_minute_usd"] is None
    assert mai["note"] == UNIT_UNKNOWN_NOTE

    # Gemini TTS: the text read (101.25 tokens x $0.50/1M) and the audio spoken
    # (60 s x 45 % x 25 tokens/s = 675 tokens x $9/1M, OpenRouter's `completion`).
    gemini = items[GEMINI_TTS]
    assert _units(gemini) == {"tokens_in", "tokens_out"}
    assert Decimal(gemini["per_minute_usd"]) == Decimal("0.000051") + Decimal("0.006075")

    seed = items[SEED]
    assert seed["quotes"] == [] and seed["note"] == UNIT_UNKNOWN_NOTE


def _book(*pairs: tuple[str, str]) -> PriceBook:
    fetched = dt.datetime.now(dt.UTC)
    live: dict[tuple[str, str], LiveSheet] = {}
    for provider_id, model in pairs:
        item = next(i for i in CATALOGUE[provider_id] if i["id"] == model)
        live[(provider_id, model)] = LiveSheet(meta=item["meta"], fetched_at=fetched, ttl_s=21600)
    return PriceBook(live=live)


def _estimate(stt: str, tts: str) -> Any:
    config = AgentConfig(
        instructions="You are a receptionist.",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="openrouter-stt", model=stt),
            llm=ProviderRef(provider_id="openrouter-llm", model=LLM),
            tts=ProviderRef(provider_id="openrouter-tts", model=tts),
        ),
    )
    book = _book(("openrouter-stt", stt), ("openrouter-llm", LLM), ("openrouter-tts", tts))
    assumptions = apply_overrides(
        default_assumptions(config), {"prompt_tokens": 1500, "tool_calls_per_session": 0}
    )
    return build_estimate(config, assumptions, book, embedding_provider=None)


def test_the_estimate_prices_token_billed_speech_through_the_token_lines() -> None:
    estimate = _estimate(MINI_TRANSCRIBE, GEMINI_TTS)
    lines = {(line.slot, line.unit): line for line in estimate.lines}

    assert ("stt", "audio_s_in") not in lines
    heard, written = lines[("stt", "audio_tokens_in")], lines[("stt", "tokens_out")]
    assert heard.quantity_per_min == Decimal("2400.000") and heard.usd_per_min == Decimal("0.003")
    assert written.usd_per_min == Decimal("0.000506")
    assert ("tts", "chars") not in lines
    spoken = lines[("tts", "tokens_out")]
    assert spoken.quantity_per_min == Decimal("675.000")
    assert spoken.usd_per_min == Decimal("0.006075")
    assert spoken.label.endswith("(audio spoken)")
    rates = {a.key: a for a in estimate.assumptions}
    assert rates["audio_tokens_in_per_s"].value == 40.0
    assert rates["audio_tokens_out_per_s"].value == 25.0
    assert estimate.unpriced == []


def test_the_estimate_says_unit_unknown_for_a_refused_model() -> None:
    estimate = _estimate(MAI, SEED)
    stt = next(line for line in estimate.lines if line.slot == "stt")
    tts = next(line for line in estimate.lines if line.slot == "tts")

    assert stt.quote is None and stt.usd_per_min is None and stt.note == UNIT_UNKNOWN_NOTE
    assert tts.quote is None and tts.usd_per_min is None and tts.note == UNIT_UNKNOWN_NOTE
    assert len(estimate.unpriced) == 2


def test_the_estimate_keeps_the_per_second_and_per_character_lines() -> None:
    estimate = _estimate(NOVA, AURA)
    lines = {(line.slot, line.unit): line for line in estimate.lines}

    assert lines[("stt", "audio_s_in")].usd_per_min == Decimal("0.0043")
    assert lines[("tts", "chars")].usd_per_min == Decimal("0.01215")
    assert {line.slot for line in estimate.lines if line.unit in ("tokens_in", "tokens_out")} == {"llm"}
