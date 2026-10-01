import { describe, expect, it } from "vitest";

import {
  ConditionError,
  conditionErrorMessage,
  conditionIssue,
  conditionToRows,
  hasNestedRepeat,
  MAX_CONDITION_CHARS,
  parseCondition,
  rowsToCondition,
} from "@/components/console/agents/rules/condition";

/**
 * V6-15 (ask #74, V6-13's brief): a table of the grammar this TS port must accept and
 * refuse identically to the api's `lkap_contracts.rules_expr` — valid, invalid and
 * adversarial (catastrophic regex) conditions, plus the builder <-> text round trip.
 */

describe("parseCondition — valid", () => {
  const valid = [
    'var.policy_number is set',
    'var.policy_number is not set',
    'var.hazard is empty',
    'var.hazard is not empty',
    'var.claim_type == "auto" and var.estimate >= 5000',
    "var.hazard matches /fire|smoke|gas/i",
    "tool.lookup_policy.ok",
    "tool.lookup_policy.failed",
    "not (var.injured == true or var.hazard is set)",
    "var.n != 3",
    "var.n <= 3.5",
    "var.flag == false",
  ];
  it.each(valid)("%s", (text) => {
    expect(() => parseCondition(text)).not.toThrow();
  });
});

describe("parseCondition — invalid, with the api's exact wording", () => {
  it("an empty condition", () => {
    expect(() => parseCondition("")).toThrow("the condition is empty");
    expect(() => parseCondition("   ")).toThrow("the condition is empty");
  });

  it("too long", () => {
    const text = `var.a == "${"x".repeat(MAX_CONDITION_CHARS)}"`;
    expect(() => parseCondition(text)).toThrow(`a condition is at most ${MAX_CONDITION_CHARS} characters`);
  });

  it("a word the grammar doesn't understand", () => {
    try {
      parseCondition("banana is set");
      expect.fail("expected a ConditionError");
    } catch (error) {
      expect(error).toBeInstanceOf(ConditionError);
      expect((error as ConditionError).message).toMatch(
        /'banana' is not something a condition understands\. Name a variable as var\.<name> and put text in quotes \(at character 1\)/,
      );
    }
  });

  it("an attribute access after a keyword", () => {
    // `var.x.y` fails earlier, as a malformed variable name (the same as the api: `_VAR_RE`'s
    // negative lookahead excludes a trailing '.') — this message is for a keyword word
    // followed by '.', e.g. a stray `not.thing`.
    expect(() => parseCondition("not.thing")).toThrow("conditions cannot read attributes or call anything");
  });

  it("an unterminated string", () => {
    expect(() => parseCondition('var.x == "unterminated')).toThrow("text in quotes is not closed");
  });

  it("ends too early", () => {
    try {
      parseCondition("var.x is set and");
      expect.fail("expected a ConditionError");
    } catch (error) {
      expect((error as ConditionError).message).toBe("the condition ends too early (at character 17)");
    }
  });

  it("an incomplete comparison names what belongs after the operator", () => {
    expect(() => parseCondition("var.x ==")).toThrow("after '==' write text in quotes, a number, true or false");
  });

  it("nests too deep", () => {
    const text = "(".repeat(9) + "var.x is set" + ")".repeat(9);
    expect(() => parseCondition(text)).toThrow("conditions nest at most 8 levels deep");
  });

  it("too many predicates", () => {
    // Short tool-outcome predicates keep the whole string under `MAX_CONDITION_CHARS`
    // (200) so the length check doesn't fire first — 13 * "tool.X.ok" (9) + 12 * " and " (5) = 177.
    const text = Array.from({ length: 13 }, (_, i) => `tool.${String.fromCharCode(97 + i)}.ok`).join(" and ");
    expect(text.length).toBeLessThan(MAX_CONDITION_CHARS);
    expect(() => parseCondition(text)).toThrow("a condition has at most 12 checks");
  });

  it("a catastrophic (nested-repeat) pattern is refused, not just slow", () => {
    expect(() => parseCondition("var.x matches /(a+)+/")).toThrow(
      "this pattern repeats a group that already repeats, which can take very long. Simplify it",
    );
    expect(() => parseCondition("var.x matches /(\\w*\\s)*/")).toThrow(/repeats a group that already repeats/);
  });

  // V6-21 (S6-4): the same lists as `contracts/tests/test_extraction_rules_v6_13.py`
  // (`SLOW_SHAPES`, `SAFE_SHAPES`, V6-13's and the api's older cases), so the console
  // refuses exactly what `rules_expr.nested_repeat` refuses.
  const slowShapes = [
    "(a|a)+b",
    "(a|aa)+b",
    "a*a*a*b",
    "(\\w+\\s?)+$",
    "\\w*\\s*\\w*",
    "\\d+\\.?\\d*",
    "a+a+b",
    "(a*)(a*)b",
    "(a*|b)a*",
    "(?:fire|smoke)+",
    "(?=(a+)+)x",
    ".*.*x",
    "(a+)+",
    "(\\w*\\s)*",
    "(?:x{2,})+",
    "((ab)+c)*",
    "(\\d+)+$",
  ];
  const safeShapes = [
    "\\d{3}-\\d{4}",
    "(ab)+c",
    "^[a-z]+@[a-z]+$",
    "fire|smoke|gas",
    "[a-z]+\\d*",
    "\\w+\\s\\w+",
    "\\d+(\\.\\d+)?",
    "\\b(?:\\d[ -]?){13,19}\\b",
    "\\(a+\\)+",
    ".*foo.*",
    "[^\\d]+\\d+",
    "(?P<policy>[A-Z]{2}-\\d{6})",
    "(ab)+",
    "(\\w+)\\s",
    "(ab){2,}",
    "[(+]+",
    "\\d{3}-\\d{2}-\\d{4}",
    "\\bfire\\b",
  ];

  it.each(slowShapes)("V6-21: %s is refused like the api refuses it", (pattern) => {
    expect(hasNestedRepeat(pattern)).toBe(true);
    expect(() => parseCondition(`var.x matches /${pattern}/`)).toThrow(/repeats a group that already repeats/);
  });

  it.each(safeShapes)("V6-21: %s is accepted like the api accepts it", (pattern) => {
    expect(hasNestedRepeat(pattern)).toBe(false);
    expect(() => parseCondition(`var.x matches /${pattern}/`)).not.toThrow();
  });

  it.each(["(", ")", "[", "\\", "a{", "a{}", "(?", "[]a", "(?P<x", "x{2,1}", "😀+😀+"])(
    "V6-21: the scanner never throws on %s",
    (pattern) => {
      expect(typeof hasNestedRepeat(pattern)).toBe("boolean");
    },
  );

  it("V6-21: a character outside the BMP counts once, as the api's scanner reads it", () => {
    expect(hasNestedRepeat("😀+😀+x")).toBe(true);
    expect(hasNestedRepeat("😀+a+x")).toBe(false);
  });

  it("a pattern flag other than 'i'", () => {
    expect(() => parseCondition("var.x matches /abc/g")).toThrow("the only pattern flag is 'i' (ignore case)");
  });

  it("a '(' left unclosed", () => {
    expect(() => parseCondition("(var.x is set")).toThrow("a '(' is not closed");
  });

  it("a bad comparison target", () => {
    expect(() => parseCondition("var.x >= \"abc\"")).toThrow("after '>=' write a number");
  });
});

describe("conditionIssue / conditionErrorMessage — the api's exact prefix", () => {
  it("prefixes with 'the condition does not read: ' and a 1-based position", () => {
    expect(conditionIssue("banana")).toBe(
      "the condition does not read: 'banana' is not something a condition understands. Name a variable as var.<name> and put text in quotes (at character 1)",
    );
  });

  it("is null for a condition that parses", () => {
    expect(conditionIssue("var.x is set")).toBeNull();
  });

  it("conditionErrorMessage matches conditionIssue for the same error", () => {
    try {
      parseCondition("banana");
      expect.fail("expected a ConditionError");
    } catch (error) {
      expect(conditionErrorMessage(error as ConditionError)).toBe(conditionIssue("banana"));
    }
  });
});

describe("the builder <-> text bridge", () => {
  it("round-trips a single predicate", () => {
    const rows = conditionToRows(parseCondition("var.policy_number is set"));
    expect(rows).toEqual([{ subject: "var", name: "policy_number", op: "is_set" }]);
    expect(rowsToCondition(rows!)).toBe("var.policy_number is set");
  });

  it("round-trips an 'and' of several predicates, including a tool outcome", () => {
    const text = 'var.claim_type == "auto" and var.estimate >= 5000 and tool.lookup_policy.ok';
    const rows = conditionToRows(parseCondition(text));
    expect(rows).toHaveLength(3);
    const rebuilt = rowsToCondition(rows!);
    expect(parseCondition(rebuilt)).toEqual(parseCondition(text));
  });

  it("returns null for a condition using 'or' — the raw-text escape hatch is one-way past that point", () => {
    const rows = conditionToRows(parseCondition("var.a is set or var.b is set"));
    expect(rows).toBeNull();
  });

  it("returns null for a condition using 'not' over something other than is-set", () => {
    const rows = conditionToRows(parseCondition("not tool.x.ok"));
    expect(rows).toBeNull();
  });

  it("a 'matches' row keeps its pattern and ignore-case flag", () => {
    const rows = conditionToRows(parseCondition("var.hazard matches /fire/i"));
    expect(rows).toEqual([{ subject: "var", name: "hazard", op: "matches", value: "fire", ignoreCase: true }]);
    expect(rowsToCondition(rows!)).toBe("var.hazard matches /fire/i");
  });
});
