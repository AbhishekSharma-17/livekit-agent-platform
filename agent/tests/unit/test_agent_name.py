"""`LKAP_AGENT_NAME` (CONTRACTS-V2 §5, D-V2-1): one agent name per connection, D-W2-11 kept.

The SDK tripwires (`test_sdk_rtc_session_precedence_still_matches_our_assumptions`,
`test_sdk_default_text_input_cb_still_matches_our_assumptions`) stay in
`test_main.py` / `test_platform_agent.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_main import _settings

from lkap_agent import main as worker_main
from lkap_agent.main import configured_agent_name, effective_agent_name, only_lkap_jobs, require_agent_name

AGENT_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "lkap-agent"),
        ({"LKAP_AGENT_NAME": ""}, "lkap-agent"),
        ({"LKAP_AGENT_NAME": "   "}, "lkap-agent"),
        ({"LKAP_AGENT_NAME": " lkap-agent-b "}, "lkap-agent-b"),
    ],
)
def test_configured_agent_name_never_yields_an_empty_name(environ: dict[str, str], expected: str) -> None:
    assert configured_agent_name(environ) == expected


def test_require_agent_name_accepts_a_per_connection_name() -> None:
    settings = _settings()
    settings.agent_name = "lkap-agent-b"

    environ = {"LKAP_AGENT_NAME": "lkap-agent-b", "LIVEKIT_AGENT_NAME": "lkap-agent-b"}
    assert require_agent_name(environ, settings) == "lkap-agent-b"


@pytest.mark.parametrize(
    ("environ", "settings_name", "livekit_name"),
    [
        ({"LKAP_AGENT_NAME": ""}, "", None),
        ({}, "lkap-agent-b", None),  # LKAP_AGENT_NAME only in a .env file the decorator never saw
        ({"LKAP_AGENT_NAME": "lkap-agent-b"}, "lkap-agent-b", "lkap-agent"),
        ({"LKAP_AGENT_NAME": "lkap-agent-b", "LIVEKIT_AGENT_NAME": "other-project-agent"}, "lkap-agent-b", None),
        (
            {"LKAP_AGENT_NAME": "lkap-agent-b", "LIVEKIT_AGENT_NAME_OVERRIDE": "other-project-agent"},
            "lkap-agent-b",
            None,
        ),
    ],
    ids=["empty", "dotenv-only", "settings-livekit-name", "livekit-env", "override"],
)
def test_require_agent_name_refuses_any_disagreeing_source(
    environ: dict[str, str], settings_name: str, livekit_name: str | None
) -> None:
    settings = _settings()
    settings.agent_name = settings_name
    settings.livekit_agent_name = livekit_name

    with pytest.raises(RuntimeError):
        require_agent_name(environ, settings)


def test_effective_agent_name_uses_lkap_agent_name_below_the_sdk_override() -> None:
    assert effective_agent_name({"LKAP_AGENT_NAME": "lkap-agent-b"}) == "lkap-agent-b"
    assert effective_agent_name({"LKAP_AGENT_NAME": "b", "LIVEKIT_AGENT_NAME_OVERRIDE": "forced"}) == "forced"


class _Req:
    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.room = SimpleNamespace(name="room-1")
        self.accepted = self.rejected = False

    async def accept(self) -> None:
        self.accepted = True

    async def reject(self) -> None:
        self.rejected = True


@pytest.mark.parametrize(
    ("job_name", "accepted"), [("lkap-agent-b", True), ("lkap-agent", False), ("", False)]
)
async def test_only_lkap_jobs_compares_against_the_configured_name(
    monkeypatch: pytest.MonkeyPatch, job_name: str, accepted: bool
) -> None:
    monkeypatch.setattr(worker_main, "AGENT_NAME", "lkap-agent-b")
    req = _Req(job_name)

    await only_lkap_jobs(cast(Any, req))

    assert (req.accepted, req.rejected) == (accepted, not accepted)


def test_the_worker_registers_under_lkap_agent_name_at_import() -> None:
    """The name is read from the process env at import, because `rtc_session` resolves it there."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LKAP_", "LIVEKIT_AGENT_NAME"))}
    env.update(
        LKAP_AGENT_NAME="lkap-agent-b",
        LIVEKIT_URL="wss://example.livekit.cloud",
        LIVEKIT_API_KEY="k",
        LIVEKIT_API_SECRET="s",
    )
    code = (
        "from lkap_agent import main\n"
        "print(main.server._agent_name, main.AGENT_NAME, main.server._request_fnc is main.only_lkap_jobs)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline code
        [sys.executable, "-c", code],
        cwd=AGENT_DIR / "tests",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.split()[-3:] == ["lkap-agent-b", "lkap-agent-b", "True"]
