"""Discover and load `Pack` objects named by `LKAP_PACKS` (docs/ARCHITECTURE.md §10).

Discovery is deliberately boring: for every dotted path in `LKAP_PACKS`, import
`<path>.pack` and read its module-level `PACK`. No entry-point magic, no
filesystem scanning.

A pack that cannot be imported (not installed yet, broken dependency) is logged
and skipped rather than failing the worker: a session whose `pack_id` is missing
still runs, with `NULL_PACK` supplying empty tools and no-op hooks, so the user
gets a working voice agent and a generic panel instead of a dropped call.
"""

from __future__ import annotations

import importlib
from typing import Any

from livekit.agents import ChatContext, ChatMessage, llm
from lkap_contracts.agent_config import CapabilitiesConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import PackManifest, ToolMeta
from packs.base import Pack, PackSessionContext

from lkap_agent.logging import get_logger

__all__ = ["NULL_PACK", "NullPack", "PackLoader", "null_manifest"]

logger = get_logger(__name__)

_PACK_MODULE_SUFFIX = ".pack"


def null_manifest() -> PackManifest:
    """The manifest used when a pack is unavailable: generic panel, no tools."""
    return PackManifest(
        id="generic",
        version="0.0.0",
        name="Generic",
        description="Fallback pack: no code tools, generic UI panel.",
        ui_panel_id="generic",
        default_instructions="You are a helpful voice assistant.",
        default_greeting="Hello! How can I help you today?",
        recommended_pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt"),
            llm=ProviderRef(provider_id="livekit-inference-llm"),
            tts=ProviderRef(provider_id="livekit-inference-tts"),
        ),
        capabilities=CapabilitiesConfig(),
        tool_names=[],
        state_schema={"type": "object"},
    )


class NullPack:
    """A `packs.base.Pack` that contributes nothing.

    Used when `pack_id` names a pack that is not installed, so a misconfigured
    agent degrades to a plain voice assistant instead of failing the job.
    """

    def __init__(self, manifest: PackManifest | None = None) -> None:
        self.manifest = manifest or null_manifest()

    def tools(self, ctx: PackSessionContext) -> list[llm.FunctionTool[..., Any]]:
        """No code tools."""
        return []

    def tool_meta(self) -> list[ToolMeta]:
        """No tool metadata."""
        return []

    def initial_state(self, ctx: PackSessionContext) -> dict[str, Any]:
        """An empty `UiState.custom`."""
        return {}

    async def on_session_start(self, ctx: PackSessionContext) -> None:
        """No-op."""

    async def on_user_turn_completed(
        self, ctx: PackSessionContext, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """No-op."""

    async def on_agent_turn_completed(self, ctx: PackSessionContext, text: str, interrupted: bool) -> None:
        """No-op."""

    async def on_ui_action(
        self, ctx: PackSessionContext, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Report that the pack handles no UI actions."""
        return {"ok": False, "error": f"no pack handler for action {action!r}"}

    async def on_session_end(self, ctx: PackSessionContext, reason: str) -> None:
        """No-op."""


#: Shared fallback instance; `NullPack` holds no per-session state.
NULL_PACK: Pack = NullPack()


class PackLoader:
    """Imports the configured pack modules once and resolves them by `pack_id`."""

    def __init__(self, import_paths: list[str]) -> None:
        """Create a loader.

        Args:
            import_paths: Dotted module paths from `LKAP_PACKS`, e.g.
                `["packs.insurance_claim", "packs.generic"]`. `.pack` is appended.
        """
        self._import_paths = list(import_paths)
        self._packs: dict[str, Pack] | None = None

    def discover(self) -> dict[str, Pack]:
        """Import every configured pack module, caching the result.

        Returns:
            A mapping of `manifest.id` to the loaded `Pack`. Modules that fail to
            import, or that expose no usable `PACK`, are logged and omitted.
        """
        if self._packs is not None:
            return self._packs

        found: dict[str, Pack] = {}
        for path in self._import_paths:
            module_name = f"{path}{_PACK_MODULE_SUFFIX}"
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                logger.warning("pack module not importable, skipping", module=module_name, error=str(exc))
                continue
            pack = getattr(module, "PACK", None)
            manifest = getattr(pack, "manifest", None)
            if pack is None or not isinstance(manifest, PackManifest):
                logger.warning(
                    "pack module exposes no usable PACK, skipping",
                    module=module_name,
                    has_pack=pack is not None,
                )
                continue
            found[manifest.id] = pack
            logger.debug("loaded pack", pack_id=manifest.id, module=module_name)

        self._packs = found
        return found

    def get(self, pack_id: str) -> Pack:
        """Return the pack for `pack_id`, or `NULL_PACK` when it is unavailable."""
        pack = self.discover().get(pack_id)
        if pack is None:
            logger.warning(
                "pack not available, falling back to the null pack",
                pack_id=pack_id,
                available=sorted(self.discover()),
            )
            return NULL_PACK
        return pack

    def manifests(self) -> list[PackManifest]:
        """Every discovered pack's manifest, sorted by id."""
        return [p.manifest for _, p in sorted(self.discover().items())]
