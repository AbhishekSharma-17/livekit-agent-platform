import { describe, expect, it } from "vitest";

import { asSentence, codeMessage, friendlyError, isPresentable, rawErrorDetail, statusMessage } from "@/lib/friendly-error";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ApiError } from "@/lib/api";

/** docs/ui/DESIGN-SYSTEM.md sections 1, 3 and 8.4: plain words plus a next step, never raw errors. */
describe("friendlyError", () => {
  it.each([
    ["fetch rejection", new TypeError("Failed to fetch"), "We couldn't reach the server.", "check-connection"],
    ["node fetch rejection", new TypeError("fetch failed"), "We couldn't reach the server.", "check-connection"],
    ["status 0", new ApiError(0, "unknown_error", ""), "We couldn't reach the server.", "check-connection"],
    ["401", new ApiError(401, "unauthorized", "missing bearer token"), "Your session has ended.", "sign-in"],
    ["403", new ApiError(403, "forbidden", "role viewer below builder"), "You don't have permission to do this.", "ask-admin"],
    ["404", new ApiError(404, "not_found", "agent 5f0c2e1a-0000-4000-8000-000000000000 not found"), "We couldn't find that.", "refresh"],
    ["500 status text", new ApiError(500, "unknown_error", "Internal Server Error"), "Something went wrong on our side.", "retry"],
    ["502", new ApiError(502, "unknown_error", "Bad Gateway"), "The service is unavailable right now.", "retry"],
    ["429", new ApiError(429, "rate_limited", "slow down"), "There are too many requests right now.", "retry"],
    ["409 not live", new ApiError(409, "not_live", "the session is not live"), "This session isn't live any more.", "refresh"],
    ["name in use", new ApiError(409, "agent_name_in_use", "name taken"), "That name is already taken.", "fix-input"],
    ["vendor error", new ApiError(502, "livekit_error", "twirp error: unavailable: dial tcp"), "LiveKit didn't accept the request.", "retry"],
    ["abort", Object.assign(new Error("The user aborted a request."), { name: "AbortError" }), "The request was cancelled.", "retry"],
  ])("maps a %s to plain copy", (_label, error, detail, action) => {
    const friendly = friendlyError(error);
    expect(friendly.detail).toBe(detail);
    expect(friendly.action).toBe(action);
    expect(friendly.message.startsWith(detail)).toBe(true);
  });

  it("keeps the api's authored validation sentence, as a sentence", () => {
    const friendly = friendlyError(new ApiError(409, "conflict", "credential is still used by 2 agents"));
    expect(friendly.message).toBe("Credential is still used by 2 agents.");
    expect(friendly.action).toBe("refresh");
  });

  it("falls back to status copy when the authored message is technical", () => {
    const leaky = [
      "ValueError: invalid literal for int() with base 10",
      "agent 5f0c2e1a-9b7d-4c3e-8a1f-2b6d9e0c4a7f is archived",
      "upstream said {\"detail\": \"bad\"}",
      "see https://example.com/errors/42",
      "Traceback (most recent call last)",
      "key agk_9f8e7d6c5b4a39281706 is invalid",
    ];
    for (const message of leaky) {
      const friendly = friendlyError(new ApiError(422, "unprocessable_entity", message));
      expect(friendly.message, message).toBe("Some of the details aren't valid. Check them and try again.");
    }
  });

  it("never renders the raw text but keeps it for logs", () => {
    const error = new ApiError(500, "internal_error", "psycopg.OperationalError: connection refused", { trace: "x" });
    const friendly = friendlyError(error);
    expect(friendly.message).not.toMatch(/psycopg|refused|500/);
    expect(friendly.raw).toContain("psycopg.OperationalError");
    expect(rawErrorDetail(error)).toContain("ApiError 500 internal_error");
  });

  it("passes console-authored Error messages through as sentences", () => {
    expect(friendlyError(new Error("pick a file first")).message).toBe("Pick a file first.");
    expect(friendlyError(new Error("TypeError: x is undefined")).message).toBe("Something went wrong. Try again in a moment.");
    expect(friendlyError("nope").message).toBe("Nope.");
    expect(friendlyError({ weird: true }).message).toBe("Something went wrong. Try again in a moment.");
  });

  it("builds a title from the context", () => {
    const friendly = friendlyError(new TypeError("Failed to fetch"), { action: "load agents" });
    expect(friendly.title).toBe("Couldn't load agents");
    expect(friendly.retryable).toBe(true);
    expect(friendlyError(new ApiError(403, "forbidden", "")).retryable).toBe(false);
  });

  it("records status and code", () => {
    const friendly = friendlyError(new ApiError(409, "calls_busy", "all lines busy"));
    expect(friendly.status).toBe(409);
    expect(friendly.code).toBe("calls_busy");
  });
});

describe("errorMessage", () => {
  it("returns the friendly message", () => {
    expect(errorMessage(new TypeError("Failed to fetch"))).toBe(
      "We couldn't reach the server. Check your connection and try again.",
    );
    expect(errorMessage(new ApiError(503, "unknown_error", "Service Unavailable"))).toBe(
      "The service is unavailable right now. Try again in a moment.",
    );
  });
});

describe("isPresentable / asSentence", () => {
  it("accepts plain sentences and rejects technical text", () => {
    expect(isPresentable("That name is already taken")).toBe(true);
    expect(isPresentable("Internal Server Error")).toBe(false);
    expect(isPresentable("x".repeat(300))).toBe(false);
    expect(isPresentable("value is undefined")).toBe(false);
  });

  it("capitalises and full-stops", () => {
    expect(asSentence("  the session ended ")).toBe("The session ended.");
    expect(asSentence("Done!")).toBe("Done!");
  });
});

describe("codeMessage / statusMessage (single sentences for the caller page)", () => {
  it("returns the same words friendlyError shows", () => {
    expect(codeMessage("calls_busy")).toBe(friendlyError(new ApiError(409, "calls_busy", "cap reached")).message);
    expect(statusMessage(500)).toBe(friendlyError(new ApiError(500, "internal_error", "boom")).message);
    expect(codeMessage("calls_busy")).toBe("All lines are busy right now. Try again in a moment.");
    expect(statusMessage(500)).toBe("Something went wrong on our side. Try again in a moment.");
  });

  it("is null for an unknown code or a status without fixed copy", () => {
    expect(codeMessage("no_such_code")).toBeNull();
    expect(statusMessage(418)).toBeNull();
  });
});
