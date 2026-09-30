import * as React from "react";

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, render, renderHook } from "@testing-library/react";

import {
  Highlight,
  matchRanges,
  matchesQuery,
  normalizeText,
  queryWords,
  useRememberedChoice,
} from "@/components/shared/list-search";

/**
 * The build library's list search (docs/ui/DESIGN-SYSTEM.md section 9):
 * accent-insensitive, every word must match somewhere, matches highlighted,
 * and a remembered filter that is validated when it is read back.
 */
/** jsdom here ships a `localStorage` without methods; give each test a real in-memory one. */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => Array.from(data.keys())[index] ?? null,
    removeItem: (key) => void data.delete(key),
    setItem: (key, value) => void data.set(key, String(value)),
  };
}

const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");

describe("list search", () => {
  beforeEach(() => {
    Object.defineProperty(window, "localStorage", { value: memoryStorage(), configurable: true });
  });
  afterEach(() => {
    if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
  });

  it("normalizes case and accents", () => {
    expect(normalizeText("Café Crème")).toBe("cafe creme");
    expect(queryWords("  Déjà   VU ")).toEqual(["deja", "vu"]);
  });

  it.each([
    [["Policy directory", "policy_number"], "policy", true],
    [["Policy directory", "policy_number"], "POLICY dir", true],
    [["Café menu"], "cafe", true],
    [["Cafe menu"], "café", true],
    [["Policy directory"], "policy claims", false],
    [["Policy directory", null, undefined], "", true],
    [["weather_lookup", "GET api.weather.test"], "weather api", true],
  ] as const)("matchesQuery(%j, %j) is %s", (fields, query, expected) => {
    expect(matchesQuery(fields, query)).toBe(expected);
  });

  it("maps matches back to the original text, merged", () => {
    expect(matchRanges("Café au lait", "cafe")).toEqual([[0, 4]]);
    expect(matchRanges("aaa", "a aa")).toEqual([[0, 3]]);
    expect(matchRanges("Policy directory", "")).toEqual([]);
  });

  it("wraps each match in a mark and leaves the rest as text", () => {
    const { container } = render(<Highlight text="Crème brûlée recipes" query="creme brulee" />);
    const marks = Array.from(container.querySelectorAll("mark")).map((mark) => mark.textContent);
    expect(marks).toEqual(["Crème", "brûlée"]);
    expect(container.textContent).toBe("Crème brûlée recipes");
  });

  it("remembers a filter and ignores an unknown stored value", async () => {
    const allowed = ["all", "enabled", "disabled"] as const;
    window.localStorage.setItem("lkap:list:test-filter", "nonsense");
    const { result } = renderHook(() => useRememberedChoice("test-filter", "all", allowed));
    expect(result.current[0]).toBe("all");

    act(() => result.current[1]("disabled"));
    expect(result.current[0]).toBe("disabled");
    expect(window.localStorage.getItem("lkap:list:test-filter")).toBe("disabled");

    const second = renderHook(() => useRememberedChoice("test-filter", "all", allowed));
    expect(second.result.current[0]).toBe("disabled");
  });

  it("moves a filter saved under a legacy key to the list key, once", () => {
    const allowed = ["all", "enabled", "disabled"] as const;
    window.localStorage.setItem("lkap.tools.kind", "enabled");
    const { result } = renderHook(() => useRememberedChoice("tools-kind", "all", allowed, { legacyKeys: ["lkap.tools.kind"] }));
    expect(result.current[0]).toBe("enabled");
    expect(window.localStorage.getItem("lkap:list:tools-kind")).toBe("enabled");
    expect(window.localStorage.getItem("lkap.tools.kind")).toBeNull();
  });

  it("highlights with the brand-subtle pair the contrast check holds", () => {
    const { container } = render(<Highlight text="Policy directory" query="policy" />);
    const mark = container.querySelector("mark");
    expect(mark?.getAttribute("data-slot")).toBe("search-highlight");
    expect(mark?.className).toContain("bg-brand-subtle");
    expect(mark?.className).toContain("text-foreground");
  });
});
