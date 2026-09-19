import { describe, expect, it } from "vitest";

import { formatUsageLabel, formatUsageValue } from "@/components/console/sessions/session-detail-view";

describe("formatUsageLabel", () => {
  it("title-cases snake_case usage keys", () => {
    expect(formatUsageLabel("llm_completion_tokens")).toBe("Llm Completion Tokens");
    expect(formatUsageLabel("tts_characters_count")).toBe("Tts Characters Count");
  });
});

describe("formatUsageValue", () => {
  it("formats integers with locale grouping", () => {
    expect(formatUsageValue(1234)).toBe("1,234");
  });

  it("formats non-integer numbers to two decimals", () => {
    expect(formatUsageValue(46.2345)).toBe("46.23");
  });

  it("passes through strings and booleans", () => {
    expect(formatUsageValue("ok")).toBe("ok");
    expect(formatUsageValue(true)).toBe("true");
  });

  it("renders null/undefined as an em dash", () => {
    expect(formatUsageValue(null)).toBe("—");
    expect(formatUsageValue(undefined)).toBe("—");
  });

  it("falls back to JSON for nested values", () => {
    expect(formatUsageValue({ a: 1 })).toBe('{"a":1}');
  });
});
