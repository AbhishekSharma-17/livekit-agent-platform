/**
 * `public/widget.js` (V2-18): a plain, unbundled script third-party pages load
 * directly, so it is tested by reading the file and evaluating it in jsdom —
 * exactly how a real page would run it — rather than importing it as a module.
 */
import fs from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

const SOURCE = fs.readFileSync(path.resolve(__dirname, "../public/widget.js"), "utf8");

declare global {
  interface Window {
    LkapWidget?: Record<string, { open: () => void; close: () => void; toggle: () => void }>;
  }
}

/** Insert a `<script data-agent=…>` tag (as a page embedding the widget would) and
 * run `widget.js`'s source against the current jsdom `document`/`window`. jsdom does
 * not execute a script's textContent when appended programmatically, and does not
 * set `document.currentScript` for one added this way either — which is exactly why
 * the widget itself falls back to `querySelector('script[data-agent]')`. */
function loadWidget(attrs: Record<string, string> = {}, base = "https://cdn.example") {
  const script = document.createElement("script");
  script.src = `${base}/widget.js`;
  for (const [key, value] of Object.entries(attrs)) script.setAttribute(key, value);
  document.body.appendChild(script);
  // Evaluating the widget's own source (not a bundled import) is the point of this test.
  new Function(SOURCE)();
  return script;
}

beforeEach(() => {
  document.body.innerHTML = "";
  delete window.LkapWidget;
});

afterEach(() => {
  document.body.innerHTML = "";
  delete window.LkapWidget;
});

describe("widget.js", () => {
  it("mounts an iframe with the microphone/autoplay allow list and the agent slug", () => {
    loadWidget({ "data-agent": "my-agent" });

    const iframe = document.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe?.getAttribute("allow")).toBe("microphone; autoplay");
    expect(iframe?.getAttribute("src")).toContain("/s/my-agent?embed=1");
    expect(iframe?.getAttribute("sandbox")).toBeNull();
  });

  it("logs an error and mounts nothing without data-agent", () => {
    loadWidget({});
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("derives the embed origin from the script's own src", () => {
    loadWidget({ "data-agent": "my-agent" }, "https://cdn.example");
    const iframe = document.querySelector("iframe");
    expect(iframe?.getAttribute("src")).toMatch(/^https:\/\/cdn\.example\/s\/my-agent\?embed=1/);
  });

  it("data-base overrides the derived origin", () => {
    loadWidget({ "data-agent": "my-agent", "data-base": "https://lkap.internal/" }, "https://cdn.example");
    const iframe = document.querySelector("iframe");
    expect(iframe?.getAttribute("src")).toBe("https://lkap.internal/s/my-agent?embed=1");
  });

  it("data-channel=text is appended as a query param", () => {
    loadWidget({ "data-agent": "my-agent", "data-channel": "text" });
    const iframe = document.querySelector("iframe");
    expect(iframe?.getAttribute("src")).toContain("&channel=text");
  });

  it("floating mode starts closed and toggles via the launcher button", () => {
    loadWidget({ "data-agent": "my-agent" });
    const panel = document.querySelector("[data-lkap-widget-panel]") as HTMLElement;
    const launcher = document.querySelector("[data-lkap-widget-launcher]") as HTMLButtonElement;
    expect(panel.style.display).toBe("none");

    launcher.click();
    expect(panel.style.display).toBe("block");
    expect(launcher.getAttribute("aria-expanded")).toBe("true");

    launcher.click();
    expect(panel.style.display).toBe("none");
  });

  it("data-open=true starts the floating panel open", () => {
    loadWidget({ "data-agent": "my-agent", "data-open": "true" });
    const panel = document.querySelector("[data-lkap-widget-panel]") as HTMLElement;
    expect(panel.style.display).toBe("block");
  });

  it("inline mode appends the iframe into the script's parent, not a floating panel", () => {
    const host = document.createElement("div");
    host.id = "lkap-host";
    document.body.appendChild(host);
    const script = document.createElement("script");
    script.src = "https://cdn.example/widget.js";
    script.setAttribute("data-agent", "my-agent");
    script.setAttribute("data-mode", "inline");
    host.appendChild(script);
    new Function(SOURCE)();

    expect(host.querySelector("iframe")).not.toBeNull();
    expect(document.querySelector("[data-lkap-widget-panel]")).toBeNull();
    expect(document.querySelector("[data-lkap-widget-launcher]")).toBeNull();
  });

  it("exposes window.LkapWidget[<slug>].open/close/toggle", () => {
    loadWidget({ "data-agent": "my-agent" });
    expect(window.LkapWidget?.["my-agent"]).toBeDefined();
    window.LkapWidget?.["my-agent"]?.open();
    const panel = document.querySelector("[data-lkap-widget-panel]") as HTMLElement;
    expect(panel.style.display).toBe("block");
  });

  it("ignores a postMessage from a different origin", () => {
    loadWidget({ "data-agent": "my-agent" });
    const panel = document.querySelector("[data-lkap-widget-panel]") as HTMLElement;
    window.dispatchEvent(
      new MessageEvent("message", {
        origin: "https://evil.example",
        data: { source: "lkap-embed", type: "open" },
      }),
    );
    expect(panel.style.display).toBe("none");
  });

  it("opens the panel on a same-origin postMessage from the embed iframe", () => {
    loadWidget({ "data-agent": "my-agent" });
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    const panel = document.querySelector("[data-lkap-widget-panel]") as HTMLElement;
    window.dispatchEvent(
      new MessageEvent("message", {
        origin: "https://cdn.example",
        source: iframe.contentWindow,
        data: { source: "lkap-embed", type: "open" },
      }),
    );
    expect(panel.style.display).toBe("block");
  });
});
