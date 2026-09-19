"""packs — LKAP use-case packs.

Each pack lives at `packs.<pack_id>` and exposes two modules:

- `manifest.py`: `MANIFEST: PackManifest` (pure Pydantic, no `livekit` import;
  `lkap_api` imports this alone to list packs and seed agents).
- `pack.py`: `PACK: Pack` (imports `livekit.agents`; only the worker imports
  this).

Dependency direction is `agent -> packs -> contracts`: packs never import
`lkap_agent`. See docs/CONTRACTS.md §8.
"""

__all__: list[str] = []

__version__ = "0.1.0"
