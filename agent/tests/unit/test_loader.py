"""`PackLoader` discovers packs by import path and degrades to `NULL_PACK`."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
from lkap_contracts.agent_config import CapabilitiesConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import PackManifest, ToolMeta

from lkap_agent.packs.loader import NULL_PACK, NullPack, PackLoader, null_manifest


def _manifest(pack_id: str) -> PackManifest:
    return PackManifest(
        id=pack_id,
        version="1.0.0",
        name=pack_id.title(),
        description="",
        ui_panel_id=f"{pack_id}_panel",
        default_instructions="Help the user.",
        default_greeting="Hi.",
        recommended_pipeline=PipelineConfig(llm=ProviderRef(provider_id="livekit-inference-llm")),
        capabilities=CapabilitiesConfig(),
        tool_names=["do_thing"],
        state_schema={"type": "object"},
    )


class _StubPack:
    def __init__(self, pack_id: str) -> None:
        self.manifest = _manifest(pack_id)

    def tools(self, ctx: Any) -> list[Any]:
        return []

    def tool_meta(self) -> list[ToolMeta]:
        return [ToolMeta(name="do_thing")]


@pytest.fixture
def stub_modules() -> Iterator[None]:
    """Install two importable `<path>.pack` modules and remove them afterwards."""
    created: list[str] = []
    for path, pack_id in (("stubpacks.alpha", "alpha"), ("stubpacks.beta", "beta")):
        for name in (path.split(".")[0], path, f"{path}.pack"):
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
                created.append(name)
        sys.modules[f"{path}.pack"].PACK = _StubPack(pack_id)  # type: ignore[attr-defined]
    yield
    for name in created:
        sys.modules.pop(name, None)


def test_discover_loads_every_configured_pack(stub_modules: None) -> None:
    """Each `LKAP_PACKS` entry contributes its `PACK` under its manifest id."""
    loader = PackLoader(["stubpacks.alpha", "stubpacks.beta"])

    found = loader.discover()

    assert sorted(found) == ["alpha", "beta"]
    assert [m.id for m in loader.manifests()] == ["alpha", "beta"]


def test_discover_is_cached(stub_modules: None) -> None:
    """Import happens once; later calls reuse the result."""
    loader = PackLoader(["stubpacks.alpha"])

    assert loader.discover() is loader.discover()


def test_discover_skips_a_pack_that_cannot_be_imported(
    stub_modules: None, caplog: pytest.LogCaptureFixture
) -> None:
    """An uninstalled pack is a warning, not a dead worker.

    `packs.generic` and `packs.insurance_claim` land in later waves, so the
    default `LKAP_PACKS` value names modules that do not exist yet.
    """
    loader = PackLoader(["stubpacks.alpha", "packs.does_not_exist"])

    assert sorted(loader.discover()) == ["alpha"]


def test_discover_skips_a_module_without_a_usable_pack() -> None:
    """A module that imports but exposes no `PACK` is ignored."""
    name = "emptypack.pack"
    sys.modules.setdefault("emptypack", types.ModuleType("emptypack"))
    sys.modules[name] = types.ModuleType(name)
    try:
        assert PackLoader(["emptypack"]).discover() == {}
    finally:
        sys.modules.pop(name, None)
        sys.modules.pop("emptypack", None)


def test_get_returns_the_null_pack_for_an_unknown_id(stub_modules: None) -> None:
    """A stale `pack_id` degrades the session instead of dropping the call."""
    loader = PackLoader(["stubpacks.alpha"])

    assert loader.get("alpha").manifest.id == "alpha"
    assert loader.get("insurance_claim") is NULL_PACK


async def test_null_pack_hooks_are_inert() -> None:
    """Every `Pack` member exists and does nothing, so hooks need no guards."""
    pack = NullPack()

    assert pack.tools(None) == []  # type: ignore[arg-type]
    assert pack.tool_meta() == []
    assert pack.initial_state(None) == {}  # type: ignore[arg-type]
    await pack.on_session_start(None)  # type: ignore[arg-type]
    await pack.on_agent_turn_completed(None, "hi", False)  # type: ignore[arg-type]
    await pack.on_session_end(None, "done")  # type: ignore[arg-type]
    result = await pack.on_ui_action(None, "confirm_sketch", {})  # type: ignore[arg-type]

    assert result["ok"] is False


def test_null_manifest_points_at_the_generic_panel() -> None:
    """The fallback renders through the generic panel with no pack tools."""
    manifest = null_manifest()

    assert manifest.ui_panel_id == "generic"
    assert manifest.tool_names == []
