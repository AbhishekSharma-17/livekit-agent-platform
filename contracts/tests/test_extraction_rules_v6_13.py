"""V6-13: the rule condition grammar, the extraction and rule contracts and their checks."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.extraction import (
    EveryNTurnsTrigger,
    ExtractionConfig,
    ExtractionField,
    ManualTrigger,
    NodeExitTrigger,
    ToolTrigger,
    extraction_issues,
    parse_show_in,
    still_needed_item_id,
)
from lkap_contracts.rules import Rule, rule_issues
from lkap_contracts.rules_expr import (
    MAX_CONDITION_CHARS,
    MAX_DEPTH,
    And,
    Compare,
    ConditionError,
    IsSet,
    Matches,
    Not,
    Or,
    ToolOutcome,
    evaluate,
    nested_repeat,
    parse_condition,
    referenced_tools,
    referenced_variables,
)
from lkap_contracts.tools import BUILTIN_TOOL_NAMES
from lkap_contracts.ui_protocol import BlockSpec

# --------------------------------------------------------------------------- grammar: valid


@pytest.mark.parametrize(
    ("text", "tree"),
    [
        ("var.policy_number is set", IsSet("policy_number")),
        ("var.policy_number is not set", Not(IsSet("policy_number"))),
        ("var.notes is empty", Not(IsSet("notes"))),
        ("var.notes is not empty", IsSet("notes")),
        ('var.claim_type == "auto"', Compare("claim_type", "==", "auto")),
        ("var.claim_type != 'home'", Compare("claim_type", "!=", "home")),
        ("var.estimate >= 5000", Compare("estimate", ">=", 5000.0)),
        ("var.estimate < -1.5", Compare("estimate", "<", -1.5)),
        ("var.injured == true", Compare("injured", "==", True)),
        ("var.injured == FALSE", Compare("injured", "==", False)),
        ("var.hazard matches /fire|smoke/i", Matches("hazard", "fire|smoke", True)),
        (r"var.code matches /^PX-\d{5}$/", Matches("code", r"^PX-\d{5}$", False)),
        ("tool.lookup_policy.ok", ToolOutcome("lookup_policy", "ok")),
        ("tool.LookupPolicy.failed", ToolOutcome("LookupPolicy", "failed")),
        (
            "var.a is set and var.b is set or var.c is set",
            Or((And((IsSet("a"), IsSet("b"))), IsSet("c"))),
        ),
        (
            "not (var.a is set or tool.x.ok)",
            Not(Or((IsSet("a"), ToolOutcome("x", "ok")))),
        ),
        ("NOT var.a IS SET AND var.b Is Set", And((Not(IsSet("a")), IsSet("b")))),
        ('var.name == "say \\"hi\\""', Compare("name", "==", 'say "hi"')),
    ],
)
def test_parse_condition_valid_builds_the_tree(text: str, tree: Any) -> None:
    assert parse_condition(text) == tree


def test_parse_condition_not_without_space_before_paren_parses() -> None:
    assert parse_condition("not(var.a is set)") == Not(IsSet("a"))


# --------------------------------------------------------------------------- grammar: invalid


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("(var.a is set", "not closed"),
        ("var.a is set)", "unexpected"),
        ("var.a.b is set", "variable is written"),
        ("var.A is set", "variable is written"),
        ("f()", "not something a condition understands"),
        ("__import__('os')", "not something a condition understands"),
        ("var.a()", "after a variable"),
        ("var.a is", "after 'is'"),
        ("var.a is maybe", "not something"),
        ("var.a == ", "after '=='"),
        ("var.a >= 'ten'", "write a number"),
        ("var.a matches fire", "not something"),
        ("var.a matches /fire", "must end with '/'"),
        ("var.a matches /fire/g", "only pattern flag"),
        ("/fire/", "may only follow 'matches'"),
        ("var.a == 'open", "not closed"),
        ("tool.x", "tool outcome"),
        ("tool.x.status", "tool outcome"),
        ("var.a is set and", "ends too early"),
        ("var.a is set var.b is set", "unexpected"),
        ("true", "expected var.<name>"),
        ("var.a + 1 > 2", "unexpected character"),
        ("self.__class__", "not something"),
        ("and.x", "cannot read attributes"),
    ],
)
def test_parse_condition_invalid_raises_a_readable_error(text: str, fragment: str) -> None:
    with pytest.raises(ConditionError) as info:
        parse_condition(text)
    assert fragment in str(info.value)


# --------------------------------------------------------------------------- grammar: adversarial


def test_parse_condition_too_long_is_refused() -> None:
    text = "var.a is set" + " or var.a is set" * 20
    assert len(text) > MAX_CONDITION_CHARS
    with pytest.raises(ConditionError, match="at most"):
        parse_condition(text)


def test_parse_condition_nesting_past_the_cap_is_refused() -> None:
    text = "(" * (MAX_DEPTH + 1) + "var.a is set" + ")" * (MAX_DEPTH + 1)
    with pytest.raises(ConditionError, match="nest at most"):
        parse_condition(text)


def test_parse_condition_nested_not_past_the_cap_is_refused() -> None:
    with pytest.raises(ConditionError, match="nest at most"):
        parse_condition("not " * (MAX_DEPTH + 1) + "var.a is set")


def test_parse_condition_nesting_at_the_cap_parses() -> None:
    text = "(" * (MAX_DEPTH - 1) + "var.a is set" + ")" * (MAX_DEPTH - 1)
    assert parse_condition(text) == IsSet("a")


def test_parse_condition_too_many_checks_is_refused() -> None:
    with pytest.raises(ConditionError, match="at most 12 checks"):
        parse_condition(" or ".join(["tool.a.ok"] * 13))


def test_parse_condition_long_string_literal_is_refused() -> None:
    with pytest.raises(ConditionError, match="longer than 100"):
        parse_condition(f'var.a == "{"x" * 101}"')


@pytest.mark.parametrize("pattern", ["(a+)+", r"(\w*\s)*", "(x{2,})+", "((ab)*c)*"])
def test_parse_condition_catastrophic_regex_is_refused(pattern: str) -> None:
    assert nested_repeat(pattern)
    with pytest.raises(ConditionError, match="repeats a group"):
        parse_condition(f"var.a matches /{pattern}/")


def test_parse_condition_invalid_regex_is_refused() -> None:
    with pytest.raises(ConditionError, match="not valid"):
        parse_condition("var.a matches /(unclosed/")


@pytest.mark.parametrize("pattern", ["fire|smoke", r"[a-z]+\d*", "(ab)+", r"(\w+)\s"])
def test_nested_repeat_safe_patterns_pass(pattern: str) -> None:
    assert not nested_repeat(pattern)


# --------------------------------------------------------------------------- evaluation


@pytest.mark.parametrize(
    ("text", "variables", "tools", "expected"),
    [
        ("var.a is set", {"a": "x"}, {}, True),
        ("var.a is set", {"a": "  "}, {}, False),
        ("var.a is set", {"a": []}, {}, False),
        ("var.a is set", {"a": 0}, {}, True),
        ("var.a is set", {"a": False}, {}, True),
        ("var.a is empty", {}, {}, True),
        ('var.t == "Auto"', {"t": " auto "}, {}, True),
        ('var.t != "auto"', {"t": "home"}, {}, True),
        ('var.t != "auto"', {}, {}, False),
        ("var.n >= 5000", {"n": "5,000"}, {}, True),
        ("var.n >= 5000", {"n": 4999.5}, {}, False),
        ("var.n > 3", {"n": "three"}, {}, False),
        ("var.n == 3", {"n": 3}, {}, True),
        ("var.n < 3", {"n": True}, {}, False),
        ("var.i == true", {"i": "yes"}, {}, True),
        ("var.i == true", {"i": True}, {}, True),
        ("var.i == false", {"i": "no"}, {}, True),
        ("var.i == true", {"i": "maybe"}, {}, False),
        ("var.h matches /fire|smoke/i", {"h": "There is SMOKE in the kitchen"}, {}, True),
        ("var.h matches /fire/", {"h": "FIRE"}, {}, False),
        ("var.h matches /fire/", {}, {}, False),
        ("var.h matches /\\d+/", {"h": 12345}, {}, True),
        ("tool.lookup.ok", {}, {"lookup": True}, True),
        ("tool.lookup.ok", {}, {"lookup": False}, False),
        ("tool.lookup.failed", {}, {"lookup": False}, True),
        ("tool.lookup.failed", {}, {}, False),
        ("tool.lookup.ok", {}, {}, False),
        ("not var.a is set and var.b is set", {"b": 1}, {}, True),
        ("var.a is set or var.b is set", {}, {}, False),
    ],
)
def test_evaluate_table(text: str, variables: dict[str, Any], tools: dict[str, bool], expected: bool) -> None:
    assert evaluate(parse_condition(text), variables, tools) is expected


def test_evaluate_matches_searches_only_the_first_thousand_characters() -> None:
    expr = parse_condition("var.h matches /needle/")
    assert not evaluate(expr, {"h": "x" * 1000 + "needle"})
    assert evaluate(expr, {"h": "x" * 990 + "needle"})


def test_referenced_names_are_collected() -> None:
    expr = parse_condition("(var.a is set or tool.lookup.ok) and not var.b == 1 and var.c matches /x/")
    assert referenced_variables(expr) == {"a", "b", "c"}
    assert referenced_tools(expr) == {"lookup"}


# --------------------------------------------------------------------------- rule contract


def _rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": "hazard",
        "when": "var.hazard matches /fire|smoke|gas/i",
        "then": [{"do": "status.set", "label": "Safety first", "tone": "danger"}],
    }
    rule.update(overrides)
    return rule


def test_rule_valid_parses_every_action_kind() -> None:
    rule = Rule.model_validate(
        _rule(
            then=[
                {"do": "checklist.set_item", "id": "photos", "label": "Photos of the damage"},
                {"do": "checklist.check", "id": "photos"},
                {"do": "status.set", "label": "Urgent", "tone": "danger"},
                {"do": "details.set", "block_id": "summary", "key": "hazard", "value": "{{ var.hazard }}"},
                {"do": "note.push", "text": "Hazard reported"},
                {"do": "var.set", "name": "urgent", "value": True},
                {"do": "escalate", "reason": "Caller reports a hazard", "mode": "callback"},
                {"do": "instruct", "text": "Ask whether everyone is safe before anything else."},
                {"do": "disposition.set", "value": "safety_escalation"},
            ]
        )
    )
    assert [action.do for action in rule.then] == [
        "checklist.set_item",
        "checklist.check",
        "status.set",
        "details.set",
        "note.push",
        "var.set",
        "escalate",
        "instruct",
        "disposition.set",
    ]
    assert rule.once is True


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"when": "var.a is"}, "does not read"),
        ({"when": "var.a matches /(a+)+/"}, "does not read"),
        ({"then": []}, "at least one action"),
        ({"then": [{"do": "note.push", "text": "x"}] * 11}, "at most 10"),
        ({"then": [{"do": "shell", "cmd": "rm"}]}, "does not match any of the expected tags"),
        ({"id": "has space"}, "should match pattern"),
        ({"then": [{"do": "var.set", "name": "Bad", "value": 1}]}, "should match pattern"),
    ],
)
def test_rule_invalid_is_refused(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError) as info:
        Rule.model_validate(_rule(**overrides))
    assert fragment in str(info.value)


def _config(**extra: Any) -> dict[str, Any]:
    return {"instructions": "Help.", "pipeline": {}, **extra}


def test_agent_config_rules_are_bounded_and_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        AgentConfig.model_validate(_config(rules=[_rule(), _rule()]))
    with pytest.raises(ValidationError, match="at most 50"):
        AgentConfig.model_validate(_config(rules=[_rule(id=f"r{i}") for i in range(51)]))


def test_agent_config_without_extraction_or_rules_keeps_the_defaults() -> None:
    """Compatibility: a config saved before V6-13 validates and reads as off / empty."""
    config = AgentConfig.model_validate(_config())
    assert config.extraction.enabled is False
    assert config.extraction.fields == []
    assert config.rules == []
    again = AgentConfig.model_validate(config.model_dump(mode="json"))
    assert again == config


# --------------------------------------------------------------------------- extraction contract


def test_extraction_field_extends_variable_spec() -> None:
    field = ExtractionField.model_validate(
        {"name": "policy_number", "required": True, "hint": "like PX-12345", "show_in": "details:summary"}
    )
    assert field.type == "string" and field.required and field.show_in == "details:summary"
    assert still_needed_item_id(field.name) == "need_policy_number"


@pytest.mark.parametrize(
    ("text", "kind", "block", "key"),
    [
        ("details:summary", "details", "summary", None),
        ("details:summary.policy", "details", "summary", "policy"),
        ("notebook:nb.summary", "notebook", "nb", "summary"),
    ],
)
def test_parse_show_in_forms(text: str, kind: str, block: str, key: str | None) -> None:
    target = parse_show_in(text)
    assert (target.kind, target.block_id, target.key) == (kind, block, key)


@pytest.mark.parametrize("text", ["status", "table:t", "details:", "notebook:nb", "var:x"])
def test_extraction_field_bad_show_in_is_refused(text: str) -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        ExtractionField.model_validate({"name": "a", "show_in": text})


def test_extraction_config_defaults_to_every_turn() -> None:
    config = ExtractionConfig()
    assert config.triggers == [EveryNTurnsTrigger(n=1)]
    assert config.min_turn_chars == 12


def test_extraction_config_triggers_one_per_kind() -> None:
    config = ExtractionConfig.model_validate(
        {
            "triggers": [
                {"kind": "every_n_turns", "n": 2},
                {"kind": "tool", "tools": ["lookup_policy"]},
                {"kind": "node_exit"},
                {"kind": "manual"},
            ]
        }
    )
    assert [type(t) for t in config.triggers] == [
        EveryNTurnsTrigger,
        ToolTrigger,
        NodeExitTrigger,
        ManualTrigger,
    ]
    with pytest.raises(ValidationError, match="one trigger of each kind"):
        ExtractionConfig.model_validate({"triggers": [{"kind": "manual"}, {"kind": "manual"}]})
    with pytest.raises(ValidationError, match="at least one tool"):
        ExtractionConfig.model_validate({"triggers": [{"kind": "tool", "tools": []}]})


def test_extraction_config_fields_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ExtractionConfig.model_validate({"fields": [{"name": "a"}, {"name": "a"}]})


def test_extract_now_is_a_builtin_name() -> None:
    assert "extract_now" in BUILTIN_TOOL_NAMES


# --------------------------------------------------------------------------- semantic checks

_BLOCKS = [
    BlockSpec(id="summary", type="details"),
    BlockSpec(id="todo", type="checklist"),
    BlockSpec(id="notes", type="notes"),
]


def test_rule_issues_missing_block_is_an_error() -> None:
    rule = Rule.model_validate(
        _rule(then=[{"do": "details.set", "block_id": "missing", "key": "k", "value": "v"}])
    )
    issues = rule_issues([rule], _BLOCKS)
    assert [(i.path, i.severity) for i in issues] == [("rules[0].then[0].block_id", "error")]
    assert "not on this agent's panel" in issues[0].message


def test_rule_issues_wrong_block_type_is_an_error() -> None:
    rule = Rule.model_validate(
        _rule(then=[{"do": "details.set", "block_id": "notes", "key": "k", "value": "v"}])
    )
    (issue,) = rule_issues([rule], _BLOCKS)
    assert issue.severity == "error" and "notes block" in issue.message


def test_rule_issues_unknown_variable_and_tool_are_warnings() -> None:
    rule = Rule.model_validate(_rule(when="var.unknown is set and tool.nope.ok"))
    issues = rule_issues([rule], _BLOCKS, known_variables={"hazard"}, known_tools={"lookup"})
    assert {(i.severity, "var.unknown" in i.message or "tool.nope" in i.message) for i in issues} == {
        ("warning", True)
    }
    assert len(issues) == 2


def test_rule_issues_var_set_makes_a_name_known() -> None:
    first = Rule.model_validate(_rule(id="a", then=[{"do": "var.set", "name": "urgent", "value": True}]))
    second = Rule.model_validate(_rule(id="b", when="var.urgent == true"))
    assert rule_issues([first, second], _BLOCKS, known_variables={"hazard"}) == []


def test_rule_issues_checklist_without_block_is_a_warning() -> None:
    rule = Rule.model_validate(_rule(then=[{"do": "checklist.check", "id": "x"}]))
    (issue,) = rule_issues([rule], [BlockSpec(id="summary", type="details")])
    assert issue.severity == "warning"


def test_extraction_issues_off_and_empty_says_nothing() -> None:
    assert extraction_issues(ExtractionConfig(), []) == []


def test_extraction_issues_targets_and_triggers() -> None:
    config = ExtractionConfig.model_validate(
        {
            "enabled": True,
            "fields": [
                {"name": "a", "show_in": "details:missing"},
                {"name": "b", "show_in": "details:notes"},
                {"name": "c", "show_in": "notebook:nb.summary"},
                {"name": "d", "show_in": "details:summary.dee"},
            ],
            "triggers": [
                {"kind": "node_exit", "nodes": ["intake"]},
                {"kind": "tool", "tools": ["nope"]},
                {"kind": "manual"},
            ],
            "still_needed": "checklist",
        }
    )
    issues = extraction_issues(
        config, _BLOCKS, flow_node_ids=None, known_tools={"lookup"}, builtin_disabled=["extract_now"]
    )
    by_path = {(i.path, i.severity) for i in issues}
    assert ("extraction.fields[0].show_in", "error") in by_path
    assert ("extraction.fields[1].show_in", "error") in by_path
    assert ("extraction.fields[2].show_in", "error") in by_path  # no notebook block yet
    assert not any(i.path.startswith("extraction.fields[3]") for i in issues)
    assert ("extraction.triggers[0]", "warning") in by_path  # not a flow
    assert ("extraction.triggers[1].tools", "warning") in by_path
    assert ("extraction.triggers[2]", "warning") in by_path  # extract_now switched off
    assert ("extraction.still_needed", "warning") in by_path  # no required field


def test_extraction_issues_flow_overlap_and_steps() -> None:
    config = ExtractionConfig.model_validate(
        {"enabled": True, "fields": [{"name": "a"}], "triggers": [{"kind": "node_exit", "nodes": ["x"]}]}
    )
    issues = extraction_issues(config, [], flow_node_ids={"start", "intake"}, flow_extracted={"a": "intake"})
    assert {(i.path, i.severity) for i in issues} == {
        ("extraction.fields[0].name", "warning"),
        ("extraction.triggers[0].nodes", "warning"),
    }


def test_extraction_issues_notebook_sections() -> None:
    notebook = BlockSpec(
        id="book",
        type="notebook",
        config={
            "sections": [
                {"id": "summary", "kind": "details"},
                {"id": "notes", "kind": "text"},
                {"id": "todo", "kind": "checklist"},
            ]
        },
    )
    config = ExtractionConfig.model_validate(
        {
            "enabled": True,
            "fields": [
                {"name": "a", "show_in": "notebook:book.summary"},
                {"name": "b", "show_in": "notebook:book.notes"},
                {"name": "c", "show_in": "notebook:book.todo"},
                {"name": "d", "show_in": "notebook:book.gone"},
            ],
        }
    )
    issues = {(i.path, i.severity): i.message for i in extraction_issues(config, [notebook])}
    assert set(issues) == {
        ("extraction.fields[2].show_in", "error"),
        ("extraction.fields[3].show_in", "error"),
    }
    assert "checklist section" in issues[("extraction.fields[2].show_in", "error")]
    assert "no section 'gone'" in issues[("extraction.fields[3].show_in", "error")]
