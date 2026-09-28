import { describe, expect, it } from "vitest";

import {
  bindingPathIssue,
  bindingTargetIssue,
  bindingTargetToString,
  caretInUrlAuthority,
  parseBindingTarget,
  pinnedArgumentIssues,
  placeholderFieldIssues,
  readbackIssues,
  requiresVarsIssues,
  urlAuthorityEnd,
  urlAuthorityHasPlaceholder,
} from "@/components/console/tools/tool-context";

/**
 * V6-11: the console-side mirror of `lkap_contracts.tool_context.placeholder_issues` and
 * friends (D-V6-22, D-V6-23) — pure-function coverage so the tool editors' inline validation
 * agrees with what the api would refuse at save.
 */

describe("placeholderFieldIssues", () => {
  it("has no issues for a plain url with no placeholders", () => {
    expect(placeholderFieldIssues("https://api.example.com/items")).toEqual([]);
  });

  it("accepts a known ctx name and a well-formed variable name", () => {
    expect(placeholderFieldIssues("{{ ctx.timezone }}/{{ var.policy_no }}")).toEqual([]);
  });

  it("flags an unknown ctx name, naming every real one", () => {
    const issues = placeholderFieldIssues("{{ ctx.nonsense }}");
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("'{{ ctx.nonsense }}' is not a session value");
    expect(issues[0]).toContain("session_id");
    expect(issues[0]).toContain("channel");
  });

  it("flags a malformed variable name (upper case)", () => {
    const issues = placeholderFieldIssues("{{ var.PolicyNo }}");
    expect(issues[0]).toContain("lower case letters, digits and _");
  });

  it("flags a malformed spelling (a name character the pattern doesn't allow)", () => {
    const issues = placeholderFieldIssues("{{ ctx.policy-no }}");
    expect(issues.some((m) => m.includes("write a value as"))).toBe(true);
  });

  it("is a no-op for an empty or null field", () => {
    expect(placeholderFieldIssues("")).toEqual([]);
    expect(placeholderFieldIssues(null)).toEqual([]);
    expect(placeholderFieldIssues(undefined)).toEqual([]);
  });
});

describe("url authority guard (D-V6-22: never in scheme, host or port)", () => {
  it("treats everything up to the first path separator as the authority", () => {
    expect(urlAuthorityEnd("https://api.example.com/items")).toBe("https://api.example.com".length);
    expect(urlAuthorityEnd("https://api.example.com?x=1")).toBe("https://api.example.com".length);
    expect(urlAuthorityEnd("https://api.example.com")).toBe("https://api.example.com".length);
  });

  it("caretInUrlAuthority is true up to and including the boundary, false past it", () => {
    const url = "https://api.example.com/items/{{ item_id }}";
    const boundary = "https://api.example.com".length;
    expect(caretInUrlAuthority(url, boundary)).toBe(true);
    expect(caretInUrlAuthority(url, boundary + 1)).toBe(false);
    expect(caretInUrlAuthority(url, 0)).toBe(true);
  });

  it("urlAuthorityHasPlaceholder finds a ctx/var reference in the host, ignoring one in the path", () => {
    expect(urlAuthorityHasPlaceholder("https://{{ ctx.agent_id }}.example.com/items")).toBe(true);
    expect(urlAuthorityHasPlaceholder("https://api.example.com/items/{{ var.id }}")).toBe(false);
    expect(urlAuthorityHasPlaceholder("https://api.example.com/{{ ctx.timezone }}")).toBe(false);
  });

  it("a url with no scheme still guards the authority-looking prefix", () => {
    expect(urlAuthorityHasPlaceholder("{{ ctx.agent_id }}.example.com/items")).toBe(true);
  });
});

describe("parseBindingTarget / bindingTargetIssue (D-V6-23's target grammar)", () => {
  it.each([
    ["details:card.holder", { kind: "details", blockId: "card", key: "holder" }],
    ["table:results", { kind: "table", blockId: "results" }],
    ["checklist:item-1", { kind: "checklist", key: "item-1" }],
    ["status", { kind: "status" }],
    ["note", { kind: "note" }],
    ["var:policy_no", { kind: "var", key: "policy_no" }],
  ] as const)("parses %s", (input, expected) => {
    expect(parseBindingTarget(input)).toMatchObject(expected);
  });

  it("round-trips through bindingTargetToString", () => {
    for (const to of ["details:card.holder", "table:results", "checklist:item-1", "status", "note", "var:policy_no"]) {
      const target = parseBindingTarget(to);
      expect(target).not.toBeNull();
      expect(bindingTargetToString(target!)).toBe(to);
    }
  });

  it("refuses anything else, naming every valid form", () => {
    expect(parseBindingTarget("link:foo")).toBeNull();
    const message = bindingTargetIssue("link:foo");
    expect(message).toContain("binding target 'link:foo' must be one of");
    expect(message).toContain("details:<block_id>.<key>");
  });

  it("bindingPathIssue: empty or starting with '/' is fine, anything else is not", () => {
    expect(bindingPathIssue("")).toBeNull();
    expect(bindingPathIssue("/policy/holder")).toBeNull();
    expect(bindingPathIssue("policy/holder")).not.toBeNull();
  });
});

describe("requiresVarsIssues", () => {
  it("accepts well-formed variable names", () => {
    expect(requiresVarsIssues(["policy_no", "email"])).toEqual([]);
  });

  it("flags a name that isn't lower case/digits/_", () => {
    expect(requiresVarsIssues(["PolicyNo"])[0]).toContain("is not a variable name");
  });
});

describe("readbackIssues (confirm_readback)", () => {
  const properties = new Set(["email", "policy_no"]);

  it("accepts a name that is one of the tool's own arguments", () => {
    expect(readbackIssues(["email"], properties)).toEqual([]);
  });

  it("flags a name that isn't one of the tool's arguments when the schema is known", () => {
    expect(readbackIssues(["nonexistent"], properties)[0]).toContain("is not one of the tool's arguments");
  });

  it("skips the argument-membership check when the schema isn't known (null)", () => {
    expect(readbackIssues(["anything"], null)).toEqual([]);
  });

  it("refuses the reserved 'confirmed' name", () => {
    expect(readbackIssues(["confirmed"], properties)[0]).toContain("added by the read-back itself");
  });

  it("refuses a repeated name", () => {
    const issues = readbackIssues(["email", "email"], properties);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("listed twice");
  });

  it("refuses a pinned argument", () => {
    expect(readbackIssues(["email"], properties, new Set(["email"]))[0]).toContain("is pinned");
  });
});

describe("pinnedArgumentIssues", () => {
  it("has no issues for a valid name and a plain string value", () => {
    expect(pinnedArgumentIssues({ calendar_id: "primary" })).toEqual([]);
  });

  it("flags a name that isn't a valid argument name", () => {
    expect(pinnedArgumentIssues({ "bad name": "x" })[0]).toContain("is not an argument name");
  });

  it("checks a string value for the same placeholder issues as any other templated field", () => {
    const issues = pinnedArgumentIssues({ calendar_id: "{{ ctx.nonsense }}" });
    expect(issues[0]).toContain("calendar_id:");
    expect(issues[0]).toContain("is not a session value");
  });
});
