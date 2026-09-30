import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KbEvalDialog } from "@/components/console/knowledge/kb-eval-dialog";
import { KbEvalsCard } from "@/components/console/knowledge/kb-evals-card";
import type { KbEvalOut, KbEvalRunOut, KbEvalSetOut } from "@/contracts/lkap-contracts";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function evalRow(overrides: Partial<KbEvalOut> = {}): KbEvalOut {
  return {
    id: "eval-1",
    question: "Is water damage from a burst pipe covered?",
    expected_document_id: null,
    expected_text: "A burst pipe is covered when the water loss was sudden and accidental.",
    tags: [],
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function runResult(overrides: Partial<KbEvalRunOut> = {}): KbEvalRunOut {
  return {
    job_id: "job-1",
    kb_id: "kb-1",
    status: "done",
    options: { mode: "hybrid", rerank: "none", min_score: null, k: 4 },
    result: {
      kb_id: "kb-1",
      mode: "hybrid",
      rerank: "none",
      min_score: null,
      k: 4,
      total: 1,
      scored: 1,
      found: 1,
      skipped: 0,
      recall_at_k: 1,
      recall_at_1: 1,
      mrr: 1,
      by_tag: [],
      warnings: [],
      items: [
        {
          eval_id: "eval-1",
          question: "Is water damage from a burst pipe covered?",
          status: "found",
          rank: 1,
          reciprocal_rank: 1,
          top_hits: [],
        },
      ],
      started_at: "2026-09-20T00:00:00Z",
      finished_at: "2026-09-20T00:00:02Z",
    },
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:02Z",
    ...overrides,
  };
}

beforeEach(() => {
  // Radix `Select` ("Expected document") needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

/**
 * `GET /auth/me` for a builder: Edit questions and Run are write controls,
 * not rendered for a viewer (decision D12), so the runner tests sign in as
 * someone who may use them.
 */
function asBuilder(url: string): Response | undefined {
  if (url.includes("/auth/me")) {
    return jsonResponse({ user: { id: "u1", email: "b@example.test" }, workspaces: [{ id: "ws1", name: "W", slug: "w", role: "builder" }] });
  }
  return undefined;
}

describe("KbEvalsCard — eval runner (docs/v5/PLAN-V5.md V5-10)", () => {
  it("shows 'No golden questions yet' and disables Run when the set is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const me = asBuilder(url);
        if (me) return me;
        if (url.includes("/evaluate/latest")) {
          return jsonResponse({ error: { code: "not_found", message: "none" } }, 404);
        }
        if (url.includes("/evals")) return jsonResponse({ items: [], total: 0 } satisfies KbEvalSetOut);
        if (url.includes("/documents")) return jsonResponse({ items: [], total: 0 });
        return jsonResponse({});
      }),
    );

    renderWithClient(<KbEvalsCard kbId="kb-1" />);

    expect(await screen.findByText("No golden questions yet")).toBeTruthy();
    const run = await screen.findByRole("button", { name: /run/i });
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit questions" })).toHaveProperty("disabled", false));
    expect(run).toHaveProperty("disabled", true);
  });

  it("doesn't offer Edit questions or Run to a viewer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/me")) {
          return jsonResponse({ user: { id: "u1", email: "v@example.test" }, workspaces: [{ id: "ws1", name: "W", slug: "w", role: "viewer" }] });
        }
        if (url.includes("/evaluate/latest")) return jsonResponse({ error: { code: "not_found", message: "none" } }, 404);
        if (url.includes("/evals")) return jsonResponse({ items: [evalRow()], total: 1 } satisfies KbEvalSetOut);
        return jsonResponse({});
      }),
    );

    renderWithClient(<KbEvalsCard kbId="kb-1" />);
    await screen.findByText("1 golden question");
    await waitFor(() => expect(screen.queryByRole("button", { name: /run/i })).toBeNull());
    expect(screen.queryByRole("button", { name: "Edit questions" })).toBeNull();
  });

  it("edits the question set through the dialog and saves it with PUT .../evals", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const me = asBuilder(url);
      if (me) return me;
      if (url.includes("/evaluate/latest")) return jsonResponse({ error: { code: "not_found", message: "none" } }, 404);
      if (method === "GET" && url.includes("/evals")) return jsonResponse({ items: [], total: 0 } satisfies KbEvalSetOut);
      if (method === "PUT" && url.includes("/evals")) {
        const body = JSON.parse(String(init?.body));
        return jsonResponse({ items: body.items.map((item: object, i: number) => ({ id: `e-${i}`, created_at: "2026-09-20T00:00:00Z", ...item })), total: body.items.length });
      }
      if (url.includes("/documents")) return jsonResponse({ items: [], total: 0 });
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<KbEvalsCard kbId="kb-1" />);
    await screen.findByText("No golden questions yet");

    const edit = await screen.findByRole("button", { name: "Edit questions" });
    await waitFor(() => expect(edit).toHaveProperty("disabled", false));
    fireEvent.click(edit);
    fireEvent.change(await screen.findByLabelText("Question 1"), {
      target: { value: "Is water damage from a burst pipe covered?" },
    });
    fireEvent.change(screen.getByLabelText("Expected text"), {
      target: { value: "A burst pipe is covered when the water loss was sudden and accidental." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([url, init]) => (init?.method ?? "GET") === "PUT" && String(url).includes("/evals"),
      );
      expect(putCall).toBeTruthy();
    });
    const [, putInit] = fetchMock.mock.calls.find(
      ([url, init]) => (init?.method ?? "GET") === "PUT" && String(url).includes("/evals"),
    )!;
    const body = JSON.parse(String(putInit?.body));
    expect(body.items).toEqual([
      {
        question: "Is water damage from a burst pipe covered?",
        expected_document_id: null,
        expected_text: "A burst pipe is covered when the water loss was sudden and accidental.",
        tags: [],
      },
    ]);
  });

  it("running evaluates the set and shows recall@1, recall@k and MRR", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        const me = asBuilder(url);
        if (me) return me;
        if (url.includes("/evaluate/latest")) return jsonResponse({ error: { code: "not_found", message: "none" } }, 404);
        if (method === "POST" && url.includes("/evaluate")) {
          return jsonResponse(runResult({ status: "pending", result: null }), 202);
        }
        if (method === "GET" && url.includes("/evaluate/job-1")) {
          return jsonResponse(runResult());
        }
        if (url.includes("/evals")) return jsonResponse({ items: [evalRow()], total: 1 } satisfies KbEvalSetOut);
        if (url.includes("/documents")) return jsonResponse({ items: [], total: 0 });
        return jsonResponse({});
      }),
    );

    renderWithClient(<KbEvalsCard kbId="kb-1" />);
    await screen.findByText("1 golden question");

    const run = await screen.findByRole("button", { name: /run/i });
    await waitFor(() => expect(run).toHaveProperty("disabled", false));
    fireEvent.click(run);

    expect(await screen.findByText("Recall@1")).toBeTruthy();
    expect(screen.getAllByText("100%")).toHaveLength(2); // recall@1 and recall@k
    expect(screen.getByText("MRR")).toBeTruthy();
    expect(screen.getByText("1.00")).toBeTruthy();
  });

  it("doesn't wipe a mid-edit question when its parent re-renders with a new (but equal) evals array", async () => {
    // Simulates react-query handing back a fresh array reference for the same
    // data — a background refetch (e.g. refetchOnWindowFocus), or simply the
    // `evalsQuery.data?.items ?? []` fallback the card uses while loading —
    // which must not re-seed the dialog's draft rows out from under typing.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/documents")) return jsonResponse({ items: [], total: 0 });
        return jsonResponse({});
      }),
    );

    function Harness({ evals }: { evals: KbEvalOut[] }) {
      return <KbEvalDialog kbId="kb-1" open onOpenChange={() => {}} evals={evals} />;
    }

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <Harness evals={[]} />
      </QueryClientProvider>,
    );

    fireEvent.change(await screen.findByLabelText("Question 1"), {
      target: { value: "Still typing this one…" },
    });

    // A brand new array, same (empty) content — not the same reference.
    rerender(
      <QueryClientProvider client={client}>
        <Harness evals={[]} />
      </QueryClientProvider>,
    );

    expect((screen.getByLabelText("Question 1") as HTMLInputElement).value).toBe("Still typing this one…");
  });
});
