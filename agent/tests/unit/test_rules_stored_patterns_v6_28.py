"""V6-28 (R-V6-3 #166): a stored rule whose pattern the S6-4 scanner refuses loads, and the
worker's rules engine skips it (it never fires) while the agent's other rules still run."""

from __future__ import annotations

from typing import Any

from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.rules_expr import nested_repeat
from structlog.testing import capture_logs

from lkap_agent.rules.engine import RulesEngine

REFUSED = "(fire|smoke)+"


def _stored_config() -> AgentConfig:
    base = default_agent_config().model_dump(mode="json")
    base["rules"] = [
        {
            "id": "hazard",
            "when": f"var.hazard matches /{REFUSED}/i",
            "then": [{"do": "status.set", "label": "Safety first", "tone": "danger"}],
        },
        {
            "id": "known",
            "when": "var.hazard is set",
            "then": [{"do": "note.push", "text": "hazard noted"}],
        },
    ]
    return AgentConfig.model_validate(base)


async def test_the_engine_skips_a_rule_whose_pattern_the_scanner_refuses() -> None:
    assert nested_repeat(REFUSED)
    config = _stored_config()  # loads: the contract checks the grammar only
    ctx = FakePackSessionContext(config=config)
    with capture_logs() as logs:
        engine = RulesEngine(ctx, config.rules)
    unreadable = [entry for entry in logs if entry["event"] == "rules.condition_unreadable"]
    assert [(entry["rule_id"], entry["log_level"]) for entry in unreadable] == [("hazard", "warning")]

    variables: dict[str, Any] = ctx.userdata.setdefault("lkap.variables", {})
    variables["hazard"] = "smoke in the kitchen"  # would match the pattern
    assert await engine.evaluate("variables") == ["known"]
    assert await engine.evaluate("extraction") == []
    assert ctx.ui.state.status is None
    assert [payload["rule_id"] for kind, payload in ctx.events if kind == "rule_fired"] == ["known"]
