"""V6-28 (R-V6-3 #166, #212): a stored rule's refused pattern is an error on that rule, and
`CanvasBlockConfig.signature_mode` is superseded but still validates."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.blocks import CanvasBlockConfig, validate_block_config
from lkap_contracts.rules import Rule, rule_issues, slow_pattern_message
from lkap_contracts.rules_expr import (
    ConditionError,
    Matches,
    nested_repeat,
    parse_condition,
    referenced_patterns,
)
from lkap_contracts.ui_protocol import BlockSpec

#: Shapes S6-4 refuses that an agent saved before V6-21 could have stored (ask #166).
REFUSED_BEFORE_V6_21 = [r"\d+\.?\d*", "(fire|smoke)+", r"\w+\s*\w+"]


def _stored(when: str, rule_id: str = "hazard") -> dict[str, Any]:
    return {
        "instructions": "Help.",
        "pipeline": {},
        "rules": [
            {"id": "known", "when": "var.policy is set", "then": [{"do": "status.set", "label": "Known"}]},
            {"id": rule_id, "when": when, "then": [{"do": "status.set", "label": "Danger"}]},
        ],
    }


@pytest.mark.parametrize("pattern", REFUSED_BEFORE_V6_21)
def test_a_stored_rule_with_a_refused_pattern_loads_and_is_an_error_at_its_path(pattern: str) -> None:
    assert nested_repeat(pattern)
    config = AgentConfig.model_validate(_stored(f"var.hazard matches /{pattern}/i"))
    assert config.rules[1].when == f"var.hazard matches /{pattern}/i"
    issues = rule_issues(list(config.rules), [])
    assert [(issue.path, issue.severity) for issue in issues] == [("rules[1].when", "error")]
    assert issues[0].message == slow_pattern_message("hazard", pattern)
    assert f"/{pattern}/" in issues[0].message and "can stall the call" in issues[0].message
    assert "Write it as" in issues[0].message


def test_rule_issues_names_every_refused_pattern_of_a_condition() -> None:
    rule = Rule.model_validate(
        {
            "id": "two",
            "when": "var.a matches /(a|a)+b/ or (var.b is set and not var.c matches /a*a*b/)",
            "then": [{"do": "note.push", "text": "x"}],
        }
    )
    issues = rule_issues([rule], [])
    assert [issue.path for issue in issues] == ["rules[0].when", "rules[0].when"]
    assert "/(a|a)+b/" in issues[0].message and "/a*a*b/" in issues[1].message


def test_rule_issues_keeps_the_other_checks_of_a_rule_with_a_refused_pattern() -> None:
    rule = Rule.model_validate(
        {
            "id": "r",
            "when": "var.a matches /(a+)+/",
            "then": [{"do": "details.set", "block_id": "missing", "key": "k", "value": "v"}],
        }
    )
    issues = rule_issues([rule], [BlockSpec(id="notes", type="notes")], known_variables=[])
    assert sorted((issue.path, issue.severity) for issue in issues) == [
        ("rules[0].then[0].block_id", "error"),
        ("rules[0].when", "error"),
        ("rules[0].when", "warning"),
    ]


def test_a_safe_pattern_is_no_issue() -> None:
    rule = Rule.model_validate(
        {"id": "r", "when": "var.a matches /fire|smoke|gas/i", "then": [{"do": "note.push", "text": "x"}]}
    )
    assert rule_issues([rule], []) == []


@pytest.mark.parametrize(
    ("when", "fragment"),
    [
        ("var.a matches /(unclosed/", "not valid"),
        ("var.a matches //", "empty"),
        ("var.a is", "does not read"),
    ],
)
def test_the_contract_still_refuses_a_condition_that_does_not_read(when: str, fragment: str) -> None:
    with pytest.raises(ValidationError) as info:
        Rule.model_validate({"id": "r", "when": when, "then": [{"do": "note.push", "text": "x"}]})
    assert fragment in str(info.value)


def test_parse_condition_is_strict_by_default_and_grammar_only_on_request() -> None:
    with pytest.raises(ConditionError, match="repeats a group"):
        parse_condition("var.a matches /(fire|smoke)+/")
    expr = parse_condition("var.a matches /(fire|smoke)+/", safe_patterns=False)
    assert expr == Matches("a", "(fire|smoke)+", False)
    with pytest.raises(ConditionError, match="not valid"):
        parse_condition("var.a matches /(unclosed/", safe_patterns=False)


def test_referenced_patterns_lists_every_matches_in_order() -> None:
    expr = parse_condition("var.a matches /x/ and not (var.b matches /y/i or tool.t.ok) and var.c is set")
    assert referenced_patterns(expr) == ["x", "y"]


# --------------------------------------------------------------------------- #212


def test_a_stored_signature_mode_board_still_validates() -> None:
    config = CanvasBlockConfig.model_validate({"signature_mode": True})
    assert config.signature_mode is True
    spec = BlockSpec.model_validate({"id": "board", "type": "canvas", "config": {"signature_mode": True}})
    assert validate_block_config(spec) == []


def test_signature_mode_is_described_as_superseded() -> None:
    schema = CanvasBlockConfig.model_json_schema()
    assert schema["properties"]["signature_mode"]["description"] == (
        "Superseded by the Signature block; kept so older boards validate."
    )
