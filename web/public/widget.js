/*
 * LKAP embeddable widget (V2-18, ARCHITECTURE-V2 §"Widget").
 *
 * Usage on a third-party page:
 *
 *   <script src="https://your-lkap-host/widget.js" data-agent="my-agent"></script>
 *
 * Optional attributes on that same <script> tag:
 *   data-mode="floating" (default) | "inline"  — floating launcher + panel,
 *     or an iframe appended into the script's parent element (or
 *     `data-target`, a CSS selector, when given).
 *   data-channel="text"  — mounts the text-mode embed layout instead of the
 *     default voice session (both still go through `/s/[slug]?embed=1`).
 *   data-open="true"     — the floating panel starts open.
 *   data-base="https://…" — override the api/web origin the iframe and its
 *     postMessage checks use, instead of deriving it from this script's own
 *     `src` (useful when the script is proxied/mirrored from a CDN).
 *
 * Plain ES5-ish vanilla JS on purpose: this file is served as a static asset
 * (`web/public/widget.js` -> `/widget.js`) to arbitrary third-party pages, not
 * bundled or transpiled, so it must run unmodified in whatever browser embeds
 * it. No `sandbox` attribute on the iframe: LiveKit's WebRTC/WASM stack needs
 * capabilities (same-origin storage, unrestricted scripts, media) a sandbox
 * would strip, and the origin/CSP checks (`middleware.ts` `frame-ancestors`,
 * `connect`/`text-sessions`'s server-side `Origin` check) are the platform's
 * real security boundary here, not iframe sandboxing.
 */
(function () {
  "use strict";

  function findScriptTag() {
    // `document.currentScript` is unset once this file finishes evaluating
    // (and jsdom/some loaders never set it at all for a dynamically inserted
    // script), so fall back to the first `data-agent` script on the page.
    return document.currentScript || document.querySelector("script[data-agent]");
  }

  var scriptTag = findScriptTag();
  if (!scriptTag) {
    console.error("[lkap-widget] could not find its own <script data-agent=…> tag");
    return;
  }

  var agentSlug = scriptTag.getAttribute("data-agent");
  if (!agentSlug) {
    console.error("[lkap-widget] the widget <script> tag needs a data-agent attribute");
    return;
  }

  var mode = scriptTag.getAttribute("data-mode") === "inline" ? "inline" : "floating";
  var channel = scriptTag.getAttribute("data-channel");

  function originOf(scriptEl) {
    var explicit = scriptEl.getAttribute("data-base");
    if (explicit) return explicit.replace(/\/+$/, "");
    try {
      return new URL(scriptEl.src, window.location.href).origin;
    } catch {
      return window.location.origin;
    }
  }

  var base = originOf(scriptTag);
  var embedUrl =
    base +
    "/s/" +
    encodeURIComponent(agentSlug) +
    "?embed=1" +
    (channel ? "&channel=" + encodeURIComponent(channel) : "");

  function buildIframe() {
    var iframe = document.createElement("iframe");
    iframe.src = embedUrl;
    iframe.title = "Talk to us";
    iframe.setAttribute("allow", "microphone; autoplay");
    iframe.style.cssText = "display:block;width:100%;height:100%;border:0;";
    return iframe;
  }

  var isOpen = scriptTag.getAttribute("data-open") === "true";
  var panelEl = null;
  var launcherEl = null;
  var iframeEl = null;

  function applyOpenState() {
    if (!panelEl) return;
    panelEl.style.display = isOpen ? "block" : "none";
    if (launcherEl) launcherEl.setAttribute("aria-expanded", isOpen ? "true" : "false");
  }

  function open() {
    isOpen = true;
    applyOpenState();
  }

  function close() {
    isOpen = false;
    applyOpenState();
  }

  function toggle() {
    if (isOpen) close();
    else open();
  }

  function mountFloating() {
    panelEl = document.createElement("div");
    panelEl.setAttribute("data-lkap-widget-panel", agentSlug);
    panelEl.style.cssText =
      "position:fixed;bottom:88px;right:24px;width:380px;max-width:calc(100vw - 32px);" +
      "height:600px;max-height:calc(100vh - 120px);overflow:hidden;border-radius:16px;" +
      "box-shadow:0 12px 40px rgba(0,0,0,.22);z-index:2147483000;display:none;";
    iframeEl = buildIframe();
    panelEl.appendChild(iframeEl);

    launcherEl = document.createElement("button");
    launcherEl.type = "button";
    launcherEl.setAttribute("data-lkap-widget-launcher", agentSlug);
    launcherEl.setAttribute("aria-label", "Chat with us");
    launcherEl.setAttribute("aria-expanded", "false");
    launcherEl.style.cssText =
      "position:fixed;bottom:24px;right:24px;width:56px;height:56px;border:0;border-radius:9999px;" +
      "background:#111827;color:#fff;font-size:22px;line-height:56px;cursor:pointer;" +
      "box-shadow:0 8px 24px rgba(0,0,0,.28);z-index:2147483000;";
    launcherEl.textContent = "💬";
    launcherEl.addEventListener("click", toggle);

    document.body.appendChild(panelEl);
    document.body.appendChild(launcherEl);
    applyOpenState();
  }

  function mountInline() {
    var targetSelector = scriptTag.getAttribute("data-target");
    var host = targetSelector ? document.querySelector(targetSelector) : scriptTag.parentElement;
    if (!host) {
      console.error("[lkap-widget] inline mode: no host element (check data-target)");
      return;
    }
    if (!host.style.height && !host.style.minHeight) host.style.minHeight = "600px";
    iframeEl = buildIframe();
    host.appendChild(iframeEl);
  }

  if (mode === "inline") mountInline();
  else mountFloating();

  // `postMessage` bridge (UI_UX_SPEC-V2-AMENDMENTS §2.6): the embedded page
  // (`components/session/embed/**`) posts `{source:"lkap-embed", type, …}` to
  // its parent. Both the origin *and* the sending window are checked, so a
  // same-origin-but-different-iframe page cannot spoof these messages.
  window.addEventListener("message", function (event) {
    if (event.origin !== base) return;
    if (!iframeEl || event.source !== iframeEl.contentWindow) return;
    var data = event.data;
    if (!data || typeof data !== "object" || data.source !== "lkap-embed") return;
    if (mode === "floating") {
      if (data.type === "close") close();
      else if (data.type === "open") open();
    }
  });

  window.LkapWidget = window.LkapWidget || {};
  window.LkapWidget[agentSlug] = { open: open, close: close, toggle: toggle };
})();
