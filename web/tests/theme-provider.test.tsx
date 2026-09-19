import * as React from "react";

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/components/console/shell/theme-provider";
import { DEFAULT_THEME, THEME_OPTIONS, THEME_STORAGE_KEY, isThemePreference, useThemePreference } from "@/lib/theme";

function stubMatchMedia(prefersDark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("dark") ? prefersDark : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

/**
 * Node ≥ 22 ships an experimental global `localStorage` that shadows jsdom's
 * (and is inert without `--localstorage-file`), so the tests install an
 * in-memory Storage — the browser boundary next-themes persists through.
 */
function stubLocalStorage(): Storage {
  const data = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => Array.from(data.keys())[index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
  vi.stubGlobal("localStorage", storage);
  return storage;
}

function Probe() {
  const { theme, resolvedTheme, setTheme, current, mounted, options } = useThemePreference();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <span data-testid="resolved">{resolvedTheme ?? "unknown"}</span>
      <span data-testid="current">{current.label}</span>
      <span data-testid="mounted">{String(mounted)}</span>
      {options.map((option) => (
        <button key={option.value} type="button" onClick={() => setTheme(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

let storage: Storage;

beforeEach(() => {
  storage = stubLocalStorage();
  document.documentElement.className = "";
  stubMatchMedia(false);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lib/theme", () => {
  it("offers Light / Dark / System in menu order, defaulting to light", () => {
    expect(THEME_OPTIONS.map((option) => option.label)).toEqual(["Light", "Dark", "System"]);
    expect(DEFAULT_THEME).toBe("light");
    expect(THEME_STORAGE_KEY).toBe("lkap-theme");
  });

  it.each([
    ["light", true],
    ["dark", true],
    ["system", true],
    ["sepia", false],
    [undefined, false],
  ])("isThemePreference(%s) → %s", (value, expected) => {
    expect(isThemePreference(value)).toBe(expected);
  });
});

describe("ThemeProvider + useThemePreference", () => {
  it("defaults to light and applies the class to <html>", () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("mounted").textContent).toBe("true");
    expect(screen.getByTestId("theme").textContent).toBe("light");
    expect(screen.getByTestId("resolved").textContent).toBe("light");
    expect(screen.getByTestId("current").textContent).toBe("Light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("switches to dark, persists under lkap-theme and updates <html>", () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Dark" }));
    });
    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(storage.getItem("lkap-theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("restores a stored preference and resolves System from the media query", () => {
    stubMatchMedia(true);
    storage.setItem("lkap-theme", "system");
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme").textContent).toBe("system");
    expect(screen.getByTestId("resolved").textContent).toBe("dark");
    expect(screen.getByTestId("current").textContent).toBe("System");
  });
});
