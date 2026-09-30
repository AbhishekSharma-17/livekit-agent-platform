import { describe, expect, it } from "vitest";

import { CALLER_ERROR, callerConnectError } from "@/components/session/caller-error";
import { deviceErrorHint, DEVICE_ERROR_MESSAGE } from "@/components/session/session-state";
import { ConnectError, describeConnectError } from "@/lib/livekit";

const PUBLIC_NO_WORKER = "This agent can't take calls right now. Please try again in a few minutes.";

/**
 * S7: a caller never sees the api's internal text, vendor messages or config
 * names (docs/ui/DESIGN-SYSTEM.md sections 1, 3, 8.4), while the V6-27
 * no-worker answer and test mode's builder-facing text keep working.
 */
describe("callerConnectError", () => {
  it("passes the api's no-worker message through (V6-27, ask #181)", () => {
    const cause = new ConnectError(409, "no_worker_running", PUBLIC_NO_WORKER);
    expect(callerConnectError(cause)).toBe(PUBLIC_NO_WORKER);
    const builder = new ConnectError(409, "no_worker_running", "No worker is running for connection 'prod'.");
    expect(callerConnectError(builder, { testMode: true })).toBe("No worker is running for connection 'prod'.");
  });

  it("falls back to the plain no-worker sentence when the api sent none", () => {
    expect(callerConnectError(new ConnectError(409, "no_worker_running", " "))).toBe(CALLER_ERROR.cantTakeCalls);
  });

  it.each([
    [new ConnectError(403, "forbidden", "raw"), describeConnectError(new ConnectError(403, "forbidden", "raw"))],
    [new ConnectError(404, "not_found", "raw"), describeConnectError(new ConnectError(404, "not_found", "raw"))],
    [new TypeError("fetch failed"), describeConnectError(new TypeError("fetch failed"))],
    [new ConnectError(0, "session_closed", "This session has ended."), "This session has ended."],
  ])("keeps the existing plain copy for %s", (cause, expected) => {
    expect(callerConnectError(cause)).toBe(expected);
  });

  it.each([
    [new ConnectError(500, "internal_error", "Traceback: KeyError 'room'"), CALLER_ERROR.server],
    [new ConnectError(502, "livekit_error", "twirp error unknown: 502 Bad Gateway"), CALLER_ERROR.server],
    [new ConnectError(0, "missing_api_base_url", "NEXT_PUBLIC_API_BASE_URL is not configured."), CALLER_ERROR.unreachable],
    [new ConnectError(422, "validation_error", "body.participant_name: too long"), CALLER_ERROR.generic],
    [new ConnectError(429, "unknown_error", "Request failed (429)."), CALLER_ERROR.tooMany],
    [new ConnectError(409, "calls_busy", "concurrency cap 4 reached"), CALLER_ERROR.busy],
  ])("never shows a public caller the raw text of %s", (cause, expected) => {
    const message = callerConnectError(cause);
    expect(message).toBe(expected);
    expect(message).not.toContain(cause.message);
  });

  it("keeps describeConnectError's text for builders in test mode", () => {
    const cause = new ConnectError(0, "missing_api_base_url", "NEXT_PUBLIC_API_BASE_URL is not configured.");
    expect(callerConnectError(cause, { testMode: true })).toBe(describeConnectError(cause));
  });
});

describe("deviceErrorHint", () => {
  it.each([
    ["microphone", "NotAllowedError", /Allow the microphone for this site/],
    ["camera", "NotFoundError", /couldn't find a camera/],
    ["camera", "NotReadableError", /Another app may be using your camera/],
    ["screenShare", "NotAllowedError", /Sharing was cancelled or blocked/],
    ["microphone", "SomethingElse", /Try again in a moment/],
  ] as const)("gives %s / %s a next step", (device, name, expected) => {
    expect(deviceErrorHint(device, { name })).toMatch(expected);
  });

  it("titles each device's failure in plain words", () => {
    expect(DEVICE_ERROR_MESSAGE.microphone).toBe("Couldn't start your microphone");
    expect(DEVICE_ERROR_MESSAGE.screenShare).toBe("Couldn't share your screen");
  });
});
