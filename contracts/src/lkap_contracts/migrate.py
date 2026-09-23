"""Pure ``AgentConfig`` migrations between v1 and v2 (CONTRACTS-V2 §4.3).

The ``v2_008_agentconfig_v2`` Alembic revision rewrites every stored
``agents.config`` and ``agent_config_versions.config`` through
:func:`agent_config_v1_to_v2`, and its ``downgrade()`` uses
:func:`agent_config_v2_to_v1`. Both functions are pure dict transforms: no
database, no imports of the api, and no dependency on the registry, so they can
run inside a migration without importing the whole platform.
"""

from copy import deepcopy
from typing import Any

#: Panel id the v1 ``generic`` panel becomes.
COMPOSITE_PANEL_ID = "composite"

#: Blocks the migrated ``generic`` panel shows, in render order.
DEFAULT_COMPOSITE_BLOCKS: list[dict[str, Any]] = [
    {"id": "status", "type": "status", "title": None, "config": {}, "order": 0},
    {"id": "notes", "type": "notes", "title": "Notes", "config": {}, "order": 1},
    {"id": "checklist", "type": "checklist", "title": "Still needed", "config": {}, "order": 2},
    {"id": "activity", "type": "activity", "title": "Activity", "config": {}, "order": 3},
]

#: Keys v2 adds to ``AgentConfig``; dropped again by :func:`agent_config_v2_to_v1`.
V2_ONLY_KEYS = ("panel", "recording", "qa", "flow")


def default_panel_for(ui_panel_id: str) -> dict[str, Any]:
    """Return the ``PanelLayout`` dict a v1 ``ui_panel_id`` maps to.

    Args:
        ui_panel_id: The v1 panel id stored on the agent row (``"generic"``,
            ``"insurance_notebook"``, ...).

    Returns:
        A ``PanelLayout``-shaped dict: the built-in ``composite`` panel with the
        four default blocks for ``"generic"``, otherwise the custom panel id
        with no blocks.
    """
    if ui_panel_id == "generic":
        return {
            "panel_id": COMPOSITE_PANEL_ID,
            "layout": "side",
            "blocks": deepcopy(DEFAULT_COMPOSITE_BLOCKS),
        }
    return {"panel_id": ui_panel_id, "layout": "side", "blocks": []}


def agent_config_v1_to_v2(cfg: dict[str, Any], ui_panel_id: str) -> dict[str, Any]:
    """Upgrade one stored ``AgentConfig`` document from v1 to v2.

    The pipeline, voice, capabilities, tools, knowledge, pack settings and
    timezone are carried over untouched; v2 defaults are filled in for the new
    sections and the panel is derived from the agent's v1 ``ui_panel_id``.
    Already-v2 documents are returned unchanged (apart from a deep copy), so the
    migration is idempotent.

    Args:
        cfg: The stored config document.
        ui_panel_id: The agent row's v1 panel id.

    Returns:
        A new v2 document; the input is never mutated.
    """
    out = deepcopy(cfg)
    if out.get("v") == 2:
        return out
    out["v"] = 2
    out.setdefault("panel", default_panel_for(ui_panel_id))
    out.setdefault("recording", {"enabled": False, "audio_only": True})
    out.setdefault("qa", {"enabled": False})
    out.setdefault("flow", None)
    pipeline = out.get("pipeline")
    if isinstance(pipeline, dict):
        pipeline.setdefault("mode", "cascaded")
    return out


def agent_config_v2_to_v1(cfg: dict[str, Any]) -> dict[str, Any]:
    """Downgrade one ``AgentConfig`` document from v2 back to v1.

    Drops the v2-only sections (``panel``, ``recording``, ``qa``, ``flow``) and
    the v2-only ``pipeline`` slots; a ``half_cascade`` pipeline degrades to
    ``realtime`` because v1 has no such mode.

    Args:
        cfg: A v2 config document.

    Returns:
        A new v1 document; the input is never mutated.
    """
    out = deepcopy(cfg)
    out["v"] = 1
    for key in V2_ONLY_KEYS:
        out.pop(key, None)
    pipeline = out.get("pipeline")
    if isinstance(pipeline, dict):
        if pipeline.get("mode") == "half_cascade":
            pipeline["mode"] = "realtime"
        for slot in ("vad", "turn_detection", "noise_cancellation", "avatar_options"):
            pipeline.pop(slot, None)
    voice = out.get("voice")
    if isinstance(voice, dict):
        voice.pop("first_speaker", None)
    capabilities = out.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities.pop("dtmf", None)
    return out
