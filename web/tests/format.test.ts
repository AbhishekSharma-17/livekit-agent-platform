import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatRelative,
  formatTime,
  pluralize,
  toMillis,
} from "@/lib/format";

const ISO = "2026-09-19T03:04:05Z";
const MS = Date.parse(ISO);

describe("toMillis", () => {
  it.each([
    ["ISO string", ISO, MS],
    ["epoch seconds (number)", MS / 1000, MS],
    ["fractional epoch seconds", 1_789_787_045.5, 1_789_787_045_500],
    ["epoch milliseconds (number)", MS, MS],
    ["numeric string in seconds", String(MS / 1000), MS],
    ["numeric string in ms", String(MS), MS],
  ])("normalises %s", (_label, input, expected) => {
    expect(toMillis(input)).toBe(expected);
  });

  it("treats 1e11 as milliseconds and anything below as seconds", () => {
    expect(toMillis(99_999_999_999)).toBe(99_999_999_999_000);
    expect(toMillis(100_000_000_000)).toBe(100_000_000_000);
  });

  it.each([["not a date"], [""], [Number.NaN], [Number.POSITIVE_INFINITY]])("returns NaN for %s", (input) => {
    expect(Number.isNaN(toMillis(input))).toBe(true);
  });
});

describe("date formatting", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-19T10:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("formatDateTime renders day, short month and 24h time without seconds", () => {
    expect(formatDateTime(ISO, { timeZone: "UTC" })).toBe("19 Sep, 03:04");
  });

  it("formatDateTime adds seconds on request", () => {
    expect(formatDateTime(ISO, { timeZone: "UTC", seconds: true })).toBe("19 Sep, 03:04:05");
  });

  it("formatDateTime adds the year outside the current year", () => {
    expect(formatDateTime("2025-01-02T13:30:00Z", { timeZone: "UTC" })).toBe("2 Jan 2025, 13:30");
  });

  it("formatDateTime accepts epoch seconds and respects the time zone", () => {
    expect(formatDateTime(MS / 1000, { timeZone: "Asia/Kolkata" })).toBe("19 Sep, 08:34");
  });

  it("formatTime renders HH:mm (and seconds on request)", () => {
    expect(formatTime(ISO, { timeZone: "UTC" })).toBe("03:04");
    expect(formatTime(ISO, { timeZone: "UTC", seconds: true })).toBe("03:04:05");
  });

  it("returns an em dash for invalid input", () => {
    expect(formatDateTime("nope")).toBe("—");
    expect(formatTime("nope")).toBe("—");
  });

  it.each([
    [10_000, "just now"],
    [4 * 60_000 + 10_000, "4 min ago"],
    [3 * 3_600_000 + 5_000, "3 h ago"],
    [26 * 3_600_000, "yesterday"],
    [5 * 86_400_000, "5 days ago"],
  ])("formatRelative(%d ms ago) → %s", (ago, expected) => {
    const now = Date.now();
    expect(formatRelative(now - ago, now)).toBe(expected);
  });

  it("formatRelative phrases future times and falls back to the date after a week", () => {
    const now = Date.now();
    expect(formatRelative(now + 5 * 60_000, now)).toBe("in 5 min");
    expect(formatRelative("2026-09-01T03:04:00Z", now, { timeZone: "UTC" })).toBe("1 Sep, 03:04");
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [999, "0s"],
    [42_000, "42s"],
    [102_000, "1m 42s"],
    [3_600_000 + 5 * 60_000 + 9_000, "1h 5m"],
  ])("%d ms → %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });

  it.each([[-1], [Number.NaN], [Number.POSITIVE_INFINITY]])("returns an em dash for %s", (ms) => {
    expect(formatDuration(ms)).toBe("—");
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1024, "1 KB"],
    [1536, "1.5 KB"],
    [3.25 * 1024 * 1024, "3.3 MB"],
    [130 * 1024 * 1024, "130 MB"],
    [1.1 * 1024 ** 3, "1.1 GB"],
  ])("%d → %s", (n, expected) => {
    expect(formatBytes(n)).toBe(expected);
  });

  it("returns an em dash for negative input", () => {
    expect(formatBytes(-5)).toBe("—");
  });
});

describe("pluralize", () => {
  it.each([
    [0, "0 documents"],
    [1, "1 document"],
    [3, "3 documents"],
    [1200, "1,200 documents"],
  ])("%d → %s", (n, expected) => {
    expect(pluralize(n, "document", "documents")).toBe(expected);
  });
});
