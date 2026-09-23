import { describe, expect, it } from "vitest";

import { BUILTIN_SECTIONS } from "@/components/console/agents/editor/builtin-sections";
import { resolveEditorSections, resolveEditorSlots, visibleSections } from "@/components/console/agents/editor/registry";
import type { EditorExtension, EditorSectionDef } from "@/components/console/agents/editor/types";
import {
  classifyIssue,
  displayMessage,
  firstSectionWithIssues,
  formPathForIssue,
  GENERAL_SECTION,
  issuesFromApiError,
  issuesFromFieldErrors,
  issuesFromValidation,
  leadingPath,
  normalizeIssuePath,
  sectionForPath,
  sectionTone,
  summarizeIssues,
  validationMessages,
} from "@/components/console/agents/editor/validation-map";
import type { AgentOut } from "@/contracts/lkap-contracts";
import { ApiError } from "@/lib/api";

const AGENT = { id: "a", mode: "prompt" } as AgentOut;
/** The sections a prompt-mode agent sees (flow hidden). */
const PROMPT_RULES = visibleSections(BUILTIN_SECTIONS, { agent: AGENT, mode: "prompt" });
const FLOW_RULES = visibleSections(BUILTIN_SECTIONS, { agent: AGENT, mode: "flow" });

describe("normalizeIssuePath", () => {
  it.each([
    ["config.pipeline.stt", "pipeline.stt"],
    ["pipeline.stt", "pipeline.stt"],
    ["tools.tool_ids[2]", "tools.tool_ids.2"],
    ["limits.rate_per_ip_per_min", "limits.rate_per_ip_per_min"],
  ])("%s → %s", (input, expected) => {
    expect(normalizeIssuePath(input)).toBe(expected);
  });
});

describe("sectionForPath", () => {
  it.each([
    ["pipeline.stt", "providers"],
    ["pipeline.llm.model", "providers"],
    ["config.voice.greeting", "instructions"],
    ["instructions", "instructions"],
    ["timezone", "instructions"],
    ["capabilities.camera", "panel"],
    ["panel.blocks.0", "panel"],
    ["tools.tool_ids", "tools"],
    ["knowledge.top_k", "knowledge"],
    ["recording.retention_days", "recording"],
    ["limits.max_concurrent_sessions", "limits"],
    ["allowed_origins.0", "limits"],
  ])("%s → %s", (path, section) => {
    expect(sectionForPath(path, PROMPT_RULES)).toBe(section);
  });

  it("returns null for unclaimed paths and does not match partial words", () => {
    expect(sectionForPath("name", PROMPT_RULES)).toBeNull();
    expect(sectionForPath("pipelines.x", PROMPT_RULES)).toBeNull();
  });

  it("ignores hidden sections (flow in prompt mode) and uses them when visible", () => {
    expect(sectionForPath("flow.nodes.0", PROMPT_RULES)).toBeNull();
    expect(sectionForPath("flow.nodes.0", FLOW_RULES)).toBe("flow");
  });

  it("prefers the longest matching prefix", () => {
    const rules = [
      { id: "providers", order: 10, issuePaths: ["pipeline"] },
      { id: "avatar", order: 15, issuePaths: ["pipeline.avatar"] },
    ];
    expect(sectionForPath("pipeline.avatar.fields.x", rules)).toBe("avatar");
    expect(sectionForPath("pipeline.stt", rules)).toBe("providers");
  });
});

describe("leadingPath", () => {
  it.each([
    ["pipeline.stt: unknown provider 'x'", "pipeline.stt"],
    ["pipeline.realtime is required when mode is 'realtime'", "pipeline.realtime"],
    ["instructions: too long", "instructions"],
    ["tools.tool_ids[0]: tool not found", "tools.tool_ids[0]"],
  ])("%s → %s", (message, path) => {
    expect(leadingPath(message)).toBe(path);
  });

  it("does not treat a plain sentence's first word as a path", () => {
    expect(leadingPath("Instructions are empty")).toBeNull();
    expect(leadingPath("agent configuration is invalid")).toBeNull();
  });
});

describe("classifyIssue (keyword heuristic, §7.14)", () => {
  it.each([
    ["The selected model is not in the registry", "providers"],
    ["No credential for this vendor", "providers"],
    ["Greeting is empty", "instructions"],
    ["Unknown timezone 'Mars/Base'", "instructions"],
    ["MCP server unreachable", "tools"],
    ["search_knowledge tool needs a knowledge base", "tools"],
    ["knowledge base 'kb1' was deleted", "knowledge"],
    ["camera is on but nothing can see frames", "panel"],
    ["Recording needs a storage config", "recording"],
    ["Allowed origin is malformed", "limits"],
    ["Something unexpected happened", GENERAL_SECTION],
  ])("%s → %s", (message, section) => {
    expect(classifyIssue(null, message, PROMPT_RULES)).toBe(section);
  });

  it("routes by path before keywords", () => {
    // Mentions "camera" (panel keyword) but the path says providers.
    expect(classifyIssue("pipeline.llm", "pipeline.llm: 'x' can't see camera frames", PROMPT_RULES)).toBe("providers");
  });
});

describe("issuesFromValidation", () => {
  it("uses issues[] when present, defaulting severity to error", () => {
    const issues = issuesFromValidation(
      {
        ok: false,
        errors: ["ignored because issues[] wins"],
        issues: [
          { path: "pipeline.tts", message: "Choose a voice", severity: "error" },
          { path: "knowledge.kb_ids", message: "A knowledge base was deleted", severity: "warning" },
          { path: "config.voice.language", message: "Unsupported language" },
          { path: "qa.rubric_prompt", message: "Rubric is empty", severity: "warning" },
        ],
      } as never,
      PROMPT_RULES,
    );
    expect(issues.map((issue) => [issue.section, issue.severity, issue.path])).toEqual([
      ["providers", "error", "pipeline.tts"],
      ["knowledge", "warning", "knowledge.kb_ids"],
      ["instructions", "error", "voice.language"],
      [GENERAL_SECTION, "warning", "qa.rubric_prompt"],
    ]);
    expect(issues.every((issue) => issue.source === "server")).toBe(true);
  });

  it("falls back to the errors/warnings strings with path prefixes and keywords", () => {
    const issues = issuesFromValidation(
      {
        ok: false,
        errors: ["pipeline.stt: unknown provider 'nope'", "tool 'lookup' not found"],
        warnings: ["pipeline.llm: model 'x' is not in the registry", "Timezone looks unusual"],
      },
      PROMPT_RULES,
    );
    expect(issues.map((issue) => [issue.section, issue.severity])).toEqual([
      ["providers", "error"],
      ["tools", "error"],
      ["providers", "warning"],
      ["instructions", "warning"],
    ]);
    expect(new Set(issues.map((issue) => issue.key)).size).toBe(4);
  });

  it("returns nothing for null or a clean result", () => {
    expect(issuesFromValidation(null, PROMPT_RULES)).toEqual([]);
    expect(issuesFromValidation({ ok: true, errors: [], warnings: [] }, PROMPT_RULES)).toEqual([]);
  });
});

describe("issuesFromApiError", () => {
  it("maps a 422 with validation details", () => {
    const error = new ApiError(422, "unprocessable", "agent configuration is invalid", {
      errors: ["pipeline.realtime is required when mode is 'realtime'"],
      warnings: [],
    });
    const issues = issuesFromApiError(error, PROMPT_RULES);
    expect(issues).toHaveLength(1);
    expect(issues?.[0]).toMatchObject({ section: "providers", path: "pipeline.realtime", severity: "error" });
  });

  it("returns null for other errors", () => {
    expect(issuesFromApiError(new Error("boom"), PROMPT_RULES)).toBeNull();
    expect(issuesFromApiError(new ApiError(500, "x", "down"), PROMPT_RULES)).toBeNull();
    expect(issuesFromApiError(new ApiError(409, "conflict", "busy", { errors: [] }), PROMPT_RULES)).toBeNull();
  });
});

describe("issuesFromFieldErrors", () => {
  it("flattens the nested react-hook-form tree and maps each leaf", () => {
    const issues = issuesFromFieldErrors(
      {
        name: { type: "too_small", message: "Name is required" },
        config: {
          pipeline: { stt: { type: "custom", message: "Choose a speech-to-text provider." } },
          instructions: { type: "too_small", message: "Instructions are required", ref: {} },
        },
        limits: { rate_per_ip_per_min: { type: "too_small", message: "At least 1" } },
        allowed_origins: [undefined, { type: "invalid_format", message: "Use an origin" }],
      },
      PROMPT_RULES,
    );
    expect(issues.map((issue) => [issue.path, issue.section])).toEqual([
      ["name", GENERAL_SECTION],
      ["pipeline.stt", "providers"],
      ["instructions", "instructions"],
      ["limits.rate_per_ip_per_min", "limits"],
      ["allowed_origins.1", "limits"],
    ]);
    expect(issues.every((issue) => issue.source === "client" && issue.severity === "error")).toBe(true);
  });
});

describe("summaries", () => {
  const issues = issuesFromValidation(
    {
      ok: false,
      errors: ["pipeline.stt: bad"],
      warnings: ["pipeline.tts: meh", "tools.tool_ids: stale", "Something else"],
    },
    PROMPT_RULES,
  );

  it("counts per section and picks the dot tone", () => {
    const summary = summarizeIssues(issues);
    expect(summary.providers).toEqual({ errors: 1, warnings: 1 });
    expect(summary.tools).toEqual({ errors: 0, warnings: 1 });
    expect(summary[GENERAL_SECTION]).toEqual({ errors: 0, warnings: 1 });
    expect(sectionTone(summary.providers)).toBe("error");
    expect(sectionTone(summary.tools)).toBe("warning");
    expect(sectionTone(summary.knowledge)).toBeNull();
  });

  it("finds the first section with errors, then warnings, in nav order", () => {
    const order = PROMPT_RULES.map((rule) => rule.id);
    expect(firstSectionWithIssues(issues, order, "error")).toBe("providers");
    expect(firstSectionWithIssues(issues.filter((i) => i.section !== "providers"), order, "warning")).toBe("tools");
    expect(firstSectionWithIssues([], order)).toBeNull();
  });

  it("validationMessages reads issues[] or the string lists", () => {
    expect(
      validationMessages({
        ok: false,
        issues: [
          { path: "a", message: "one" },
          { path: "b", message: "two", severity: "warning" },
        ],
      }),
    ).toEqual({ errors: ["one"], warnings: ["two"] });
    expect(validationMessages({ ok: true, warnings: ["w"] })).toEqual({ errors: [], warnings: ["w"] });
    expect(validationMessages(null)).toEqual({ errors: [], warnings: [] });
  });
});

describe("display helpers", () => {
  it("strips a redundant path prefix", () => {
    expect(displayMessage({ path: "pipeline.stt", message: "pipeline.stt: unknown provider 'x'" })).toBe(
      "Unknown provider 'x'",
    );
    expect(displayMessage({ path: "pipeline.stt", message: "Choose a provider" })).toBe("Choose a provider");
    expect(displayMessage({ path: null, message: "Plain" })).toBe("Plain");
  });

  it("maps issue paths to form paths", () => {
    expect(formPathForIssue("pipeline.stt")).toBe("config.pipeline.stt");
    expect(formPathForIssue("limits.rate_per_ip_per_min")).toBe("limits.rate_per_ip_per_min");
    expect(formPathForIssue("name")).toBe("name");
    expect(formPathForIssue(null)).toBeNull();
  });
});

describe("section registry", () => {
  const Stub = () => null;
  const extra = (def: Partial<EditorSectionDef> & { id: string }): EditorSectionDef => ({
    label: def.id,
    icon: (() => null) as unknown as EditorSectionDef["icon"],
    order: 100,
    Component: Stub,
    ...def,
  });

  it("orders built-ins as the v2 amendments list them and hides flow in prompt mode", () => {
    expect(BUILTIN_SECTIONS.map((s) => s.id)).toEqual([
      "providers",
      "instructions",
      "flow",
      "panel",
      "tools",
      "knowledge",
      "recording",
      "limits",
    ]);
    expect(PROMPT_RULES.map((s) => s.id)).not.toContain("flow");
    expect(FLOW_RULES.map((s) => s.id)).toContain("flow");
  });

  it("adds, replaces and patches sections from extensions without touching the built-ins", () => {
    const Replacement = () => null;
    const extensions: EditorExtension[] = [
      { id: "V2-13", sections: [extra({ id: "providers", label: "Providers v2", order: 10, Component: Replacement })] },
      { id: "V2-17", sections: [extra({ id: "telephony", label: "Phone", order: 75 })] },
      { id: "V2-11", sectionPatches: [{ id: "panel", patch: { label: "Panel" } }, { id: "missing", patch: { order: 1 } }] },
    ];
    const resolved = resolveEditorSections(BUILTIN_SECTIONS, extensions);
    expect(resolved.map((s) => s.id)).toEqual([
      "providers",
      "instructions",
      "flow",
      "panel",
      "tools",
      "knowledge",
      "recording",
      "telephony",
      "limits",
    ]);
    expect(resolved[0].Component).toBe(Replacement);
    expect(resolved[0].label).toBe("Providers v2");
    expect(resolved.find((s) => s.id === "panel")?.label).toBe("Panel");
    expect(BUILTIN_SECTIONS.find((s) => s.id === "panel")?.label).toBe("Panel & capabilities");
  });

  it("resolves slots: last single-component slot wins, array slots concatenate", () => {
    const A = () => null;
    const B = () => null;
    const slots = resolveEditorSlots([
      { id: "one", slots: { connectionChip: A, testCallItems: [A] } },
      { id: "two", slots: { connectionChip: B, testCallItems: [B], headerActions: [A] } },
    ]);
    expect(slots.connectionChip).toBe(B);
    expect(slots.testCallItems).toEqual([A, B]);
    expect(slots.headerActions).toEqual([A]);
    expect(slots.modeChip).toBeUndefined();
    expect(resolveEditorSlots([])).toEqual({ testCallItems: [], headerActions: [] });
  });
});
