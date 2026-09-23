"""The pack test fakes -- a re-export of the shared `lkap_testing` package (REVIEW-FINAL F-18).

This file used to be a hand-kept copy of `agent/tests/fakes/fake_ctx.py`
(`packs` cannot import `lkap_agent`, ARCHITECTURE §10). Both now import the
single implementation in `testing/src/lkap_testing/fake_ctx.py`, which depends
only on `lkap_contracts` and `packs.base`. A second pack's suite imports
`lkap_testing` directly.
"""

from __future__ import annotations

from lkap_testing.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeImageGen,
    FakeKbClient,
    FakeLogger,
    FakePackSessionContext,
    FakeStructuredLLM,
    FakeUiChannel,
    apply_patch_op,
    default_agent_config,
)

__all__ = [
    "FakeBackgroundRunner",
    "FakeFrameBuffer",
    "FakeImageGen",
    "FakeKbClient",
    "FakeLogger",
    "FakePackSessionContext",
    "FakeStructuredLLM",
    "FakeUiChannel",
    "apply_patch_op",
    "default_agent_config",
]
