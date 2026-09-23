"""Regenerate ``v1_seed.sqlite``: a v1 database with 2 agents and 3 sessions.

Run from the ``api/`` directory::

    uv run python tests/fixtures/make_v1_seed.py

The file it writes is committed, so the v1→v2 migration tests do not depend on
the v1 revision still being runnable in the future. The two agents cover both
branches of :func:`lkap_contracts.migrate.default_panel_for`: one ``generic``
agent (which becomes the ``composite`` panel with four blocks) and one
``insurance_notebook`` agent (which keeps its custom panel id).

Only the v1 initial revision is applied and only v1 columns are written, so the
fixture is exactly what a v1 deployment looked like.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[2]
SEED = Path(__file__).resolve().parent / "v1_seed.sqlite"

#: The v1 revision the fixture is stamped at.
V1_REVISION = "4135323c6ecc"


def _v1_config(*, panel_id: str) -> dict[str, object]:
    """Return a valid v1 `AgentConfig` document (no `panel`/`recording`/`qa`/`flow`)."""
    realtime = panel_id != "generic"
    pipeline: dict[str, object] = (
        {"mode": "realtime", "realtime": {"provider_id": "google-realtime"}}
        if realtime
        else {
            "mode": "cascaded",
            "stt": {"provider_id": "livekit-inference-stt"},
            "llm": {"provider_id": "livekit-inference-llm"},
            "tts": {"provider_id": "livekit-inference-tts"},
        }
    )
    return {
        "v": 1,
        "instructions": f"You are the {panel_id} agent.",
        "pipeline": pipeline,
        "voice": {"greeting": "Hello."},
        "capabilities": {"camera": realtime},
        "tools": {},
        "knowledge": {},
        "pack_settings": {},
        "timezone": "UTC",
    }


def build(target: Path = SEED) -> Path:
    """Create the seed database at `target`, replacing any existing file.

    Args:
        target: Where to write the SQLite file.

    Returns:
        The path written.
    """
    target.unlink(missing_ok=True)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    config.attributes["configure_logger"] = False
    # `alembic/env.py` resolves the url from `-x url` / LKAP_DATABASE_URL / the
    # LKAP_DATA_DIR default and ignores `sqlalchemy.url`, so the env var is the
    # only way to be sure this never touches the developer's `data/lkap.db`.
    previous = os.environ.get("LKAP_DATABASE_URL")
    os.environ["LKAP_DATABASE_URL"] = f"sqlite+aiosqlite:///{target}"
    try:
        command.upgrade(config, V1_REVISION)
    finally:
        if previous is None:
            os.environ.pop("LKAP_DATABASE_URL", None)
        else:
            os.environ["LKAP_DATABASE_URL"] = previous

    # Bound as an ISO string: sqlite3 deprecated its implicit datetime adapter in 3.12,
    # and this is exactly the text SQLAlchemy stores in a DATETIME column anyway.
    now = dt.datetime(2026, 9, 1, 12, 0, 0).isoformat(sep=" ")
    agents = [
        ("a" * 32, "claims-desk", "Claims desk", "generic", "generic"),
        ("b" * 32, "fnol-intake", "FNOL intake", "insurance_claim", "insurance_notebook"),
    ]
    sessions = [
        ("s" * 32, "a" * 32, "room-one", "ended"),
        ("t" * 32, "a" * 32, "room-two", "ended"),
        ("u" * 32, "b" * 32, "room-three", "failed"),
    ]

    connection = sqlite3.connect(target)
    try:
        connection.executemany(
            "INSERT INTO agents (id, slug, name, description, pack_id, ui_panel_id, published,"
            " config, config_version, created_at, updated_at)"
            " VALUES (?, ?, ?, '', ?, ?, 1, ?, 1, ?, ?)",
            [
                (
                    agent_id,
                    slug,
                    name,
                    pack_id,
                    panel_id,
                    json.dumps(_v1_config(panel_id=panel_id)),
                    now,
                    now,
                )
                for agent_id, slug, name, pack_id, panel_id in agents
            ],
        )
        connection.executemany(
            "INSERT INTO sessions (id, agent_id, config_version, room_name,"
            " participant_identity, participant_name, status, pipeline_mode, created_at)"
            " VALUES (?, ?, 1, ?, 'caller', 'Caller', ?, 'cascaded', ?)",
            [(session_id, agent_id, room, status, now) for session_id, agent_id, room, status in sessions],
        )
        connection.executemany(
            "INSERT INTO session_events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
            [(session_id, now, "session_started", "{}") for session_id, *_ in sessions],
        )
        connection.execute(
            "INSERT INTO knowledge_bases (id, name, description, embedder_id, chunk_count,"
            " created_at, updated_at) VALUES (?, 'Policies', '', 'fastembed-embedding', 0, ?, ?)",
            ("k" * 32, now, now),
        )
        connection.execute(
            "INSERT INTO tools (id, agent_id, kind, name, definition, enabled, created_at,"
            " updated_at) VALUES (?, ?, 'http', 'lookup_policy', '{}', 1, ?, ?)",
            ("o" * 32, "b" * 32, now, now),
        )
        connection.commit()
    finally:
        connection.close()
    return target


if __name__ == "__main__":  # pragma: no cover - fixture generator
    print(f"wrote {build()}")
