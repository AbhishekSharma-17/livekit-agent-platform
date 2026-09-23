import { test } from "@playwright/test";

/**
 * V2-18 acceptance: "Playwright: text chat round trip with the fake worker."
 *
 * Blocked: `web/e2e/` has no fake/mock LiveKit worker or server today (see
 * `e2e/README.md` — only `login-console-smoke.spec.ts` exists, and it never
 * opens a real room). A genuine text-session round trip needs a LiveKit
 * server plus something dispatching and running `lkap_agent` against it;
 * V2-18's hard rules forbid starting a LiveKit worker or connecting to the
 * shared `lkap-agent`/`other-project-agent` project from an agent session, and
 * there is no lightweight in-process fake standing in for either piece the
 * way `agent/tests/fakes/fake_room.py` does for the Python unit tests.
 *
 * Building that fake (a mock LiveKit signaling/media server, or a
 * `livekit-server --dev` + a scripted worker double the browser can actually
 * connect to) is its own infrastructure project, not a V2-18-sized addition,
 * and is exactly what PLAN-V2 §6 stage L12c ("text chat with rewind on
 * `cloud-a`") and V2-19/V2-20's live verification are for.
 *
 * Logged in `docs/v2/_asks.md` ("Open — left by V2-18"). Once a fake worker
 * exists (V2-19's `testing/lkap_testing` package is the natural home, per its
 * card's "shared fakes … used by agent and packs tests" — extending that to
 * a browser-facing e2e fake is a reasonable follow-up), this spec should:
 * 1. Log in (or use the admin-token bypass) and open an agent's editor.
 * 2. Open "Test chat" from the Test call menu.
 * 3. Type a message, see the fake worker's scripted reply appear.
 * 4. Edit an earlier turn, replay it, and assert the transcript shows the
 *    edited text with a single fresh reply (not two).
 */
test.fixme(
  "text chat round trip with a fake worker",
  async () => {
    // See the file docstring: no fake worker exists yet to round-trip against.
  },
);
