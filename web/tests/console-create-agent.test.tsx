import * as React from "react";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { CreateAgentDialog } from "@/components/console/agents/create/create-agent-dialog";
import {
  TEMPLATE_CHIP_META,
  flowFact,
  gatedDifferences,
  landingSection,
} from "@/components/console/agents/create/template-meta";
import { TemplateTile } from "@/components/console/agents/create/template-tile";
import { derivedFromPack } from "@/components/console/agents/create/use-templates";
import { RadioGroup as RadioGroupRoot } from "@/components/ui/radio-group";
import type { AgentConfig, AgentOut, CredentialPage, PacksResponse, ProvidersResponse } from "@/contracts/lkap-contracts";

import { TEMPLATES, templateById } from "./fixtures/templates";

/**
 * The New agent dialog (docs/v4/TEMPLATES.md §6, PLAN-V4 V4-02 acceptance):
 * gallery from `GET /v1/templates`, badges, keyboard selection, the preview,
 * step 2, the posted body, the post-create toast and navigation, the 422
 * issue list and the 404 fallback to packs.
 *
 * jsdom: Radix overlays position with floating-ui, whose `isTopLayer` calls
 * `matches(":popover-open")`/`(":modal")` — seconds per call in nwsapi. The
 * `beforeAll` answers them with `false` (see console-editor-shell.test.tsx).
 * `RadioGroupItem` needs a `ResizeObserver`.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const routerPush = vi.fn();
const routerReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  usePathname: () => "/console/agents",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const PROVIDERS: ProvidersResponse = {
  providers: [
    { id: "livekit-inference-stt", kind: "stt", label: "LiveKit Inference STT", vendor: "LiveKit", package: "", python_class: "" },
    { id: "livekit-inference-llm", kind: "llm", label: "LiveKit Inference LLM", vendor: "LiveKit", package: "", python_class: "" },
    { id: "livekit-inference-tts", kind: "tts", label: "LiveKit Inference TTS", vendor: "LiveKit", package: "", python_class: "" },
    {
      id: "google-image-gen",
      kind: "image_gen",
      label: "Google image generation",
      vendor: "Google",
      package: "",
      python_class: "",
      requires_credential: true,
    },
  ],
};

const NO_KEYS: CredentialPage = { items: [], total: 0 };
const GOOGLE_IMAGE_KEY: CredentialPage = {
  total: 1,
  items: [
    {
      id: "k-1",
      provider_id: "google-image-gen",
      label: "Google",
      fingerprint: "ab12",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  ],
};

const PACKS: PacksResponse = {
  items: [templateById("insurance_claim").pack, templateById("blank").pack].map((manifest) => ({ manifest })),
};

function createdAgent(templateId: string, config?: Partial<AgentConfig>): AgentOut {
  const template = templateById(templateId);
  return {
    id: "agent-new",
    name: template.template.name,
    slug: "agent-new",
    description: "",
    pack_id: template.pack.id,
    ui_panel_id: template.pack.ui_panel_id,
    published: false,
    config_version: 1,
    created_at: "2026-09-24T00:00:00Z",
    updated_at: "2026-09-24T00:00:00Z",
    config: { instructions: "Help.", pipeline: { mode: "cascaded" }, ...config },
  };
}

interface StubOptions {
  keys?: CredentialPage;
  /** `templates` answers 404 (an api older than the console). */
  templates404?: boolean;
  /** What `POST /v1/agents` answers. */
  create?: { status: number; body: unknown };
}

function stubFetch(options: StubOptions = {}) {
  const posts: unknown[] = [];
  const respond = (body: unknown, status = 200) =>
    ({ ok: status < 400, status, statusText: "", json: async () => body }) as Response;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.startsWith("/api/console/agents") && method === "POST") {
      posts.push(JSON.parse(init?.body as string));
      const create = options.create ?? { status: 201, body: createdAgent("blank") };
      return respond(create.body, create.status);
    }
    if (url.startsWith("/api/console/templates")) {
      if (options.templates404) return respond({ error: { code: "not_found", message: "Not Found" } }, 404);
      return respond(TEMPLATES);
    }
    if (url.startsWith("/api/console/packs")) return respond(PACKS);
    if (url.startsWith("/api/console/providers")) return respond(PROVIDERS);
    if (url.startsWith("/api/console/credentials")) return respond(options.keys ?? NO_KEYS);
    if (url.startsWith("/api/console/connections")) {
      return respond({ items: [{ id: "conn-1", name: "Cloud A", slug: "cloud-a", url: "wss://example.test", is_default: true }], total: 1 });
    }
    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { posts, fetchMock };
}

function renderDialog(options: StubOptions & { initialTemplateId?: string } = {}) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const stub = stubFetch(options);
  const onOpenChange = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <CreateAgentDialog open onOpenChange={onOpenChange} initialTemplateId={options.initialTemplateId} />
    </QueryClientProvider>,
  );
  return { ...view, ...stub, onOpenChange };
}

/** The gallery's tiles, in DOM order. */
function tiles(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-slot="template-tile"]'));
}

function tile(id: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-slot="template-tile"][data-template="${id}"]`);
  if (!el) throw new Error(`no tile ${id}`);
  return el;
}

function badgeKinds(id: string): string[] {
  return Array.from(tile(id).querySelectorAll<HTMLElement>('[data-slot="template-badge"]')).map(
    (badge) => badge.dataset.badge ?? "",
  );
}

function previewPane(): HTMLElement {
  return screen.getByRole("complementary", { name: "Starter preview" });
}

async function waitForGallery() {
  await waitFor(() => expect(tiles().length).toBeGreaterThan(0));
  // Preselection runs once the gallery arrives.
  await waitFor(() => expect(screen.getAllByRole("radio").some((r) => r.getAttribute("aria-checked") === "true")).toBe(true));
}

async function continueTo(templateId: string) {
  await waitForGallery();
  fireEvent.click(within(tile(templateId)).getByRole("radio"));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("textbox", { name: /^Name/ });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const GENERIC_IDS = [
  "blank",
  "knowledge_assistant",
  "receptionist",
  "vision_assistant",
  "phone_agent",
  "lead_qualification",
  "survey_intake",
];

describe("CreateAgentDialog — step 1, the gallery", () => {
  it("renders the eight starters in response order with Blank agent preselected", async () => {
    renderDialog();
    await waitForGallery();

    expect(screen.getByRole("dialog", { name: "New agent" })).toBeTruthy();
    expect(tiles().map((el) => el.dataset.template)).toEqual(TEMPLATES.items.map((item) => item.template.id));
    expect(tiles()).toHaveLength(8);

    const radios = screen.getAllByRole("radio");
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
    expect(radios[0].id).toBe("template-blank");
    // Named by the starter's name, not the whole card.
    expect(screen.getByRole("radio", { name: "Blank agent" })).toBe(radios[0]);
    // Insurance is last, under the "Advanced example" divider.
    expect(tiles().at(-1)?.dataset.template).toBe("insurance_claim");
    expect(screen.getByText("Advanced example")).toBeTruthy();
  });

  it("renders each tile's chips from TEMPLATE_CHIP_META", async () => {
    renderDialog();
    await waitForGallery();

    for (const item of TEMPLATES.items) {
      const chips = Array.from(tile(item.template.id).querySelectorAll<HTMLElement>('[data-slot="template-chip"]'));
      expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual(
        (item.template.chips ?? []).map((chip) => TEMPLATE_CHIP_META[chip].label),
      );
    }
    expect(within(tile("receptionist")).getByRole("listitem", { name: "HTTP tools" }).getAttribute("title")).toBe(
      TEMPLATE_CHIP_META.http_tools.help,
    );
  });

  it("badges phone, webhook and the example pack, and says Inference-only starters need no key", async () => {
    renderDialog();
    await waitForGallery();

    expect(badgeKinds("phone_agent")).toEqual(["phone_number"]);
    expect(within(tile("phone_agent")).getByText("Needs a phone number")).toBeTruthy();
    expect(badgeKinds("lead_qualification")).toEqual(["webhook"]);
    expect(within(tile("lead_qualification")).getByText("Needs a webhook")).toBeTruthy();
    expect(badgeKinds("insurance_claim")).toEqual(["optional_key", "example_pack"]);
    expect(within(tile("insurance_claim")).getByText("Optional key").getAttribute("title")).toMatch(/Google/);
    expect(within(tile("insurance_claim")).getByText("Example pack")).toBeTruthy();
    for (const id of ["blank", "knowledge_assistant", "receptionist", "vision_assistant", "survey_intake"]) {
      expect(badgeKinds(id)).toEqual([]);
    }

    for (const id of GENERIC_IDS) {
      expect(within(tile(id)).getByText("Runs on LiveKit Inference — no vendor key")).toBeTruthy();
    }
    expect(within(tile("insurance_claim")).queryByText("Runs on LiveKit Inference — no vendor key")).toBeNull();
  });

  it("shows Keys present on the insurance tile when the workspace holds the Google image key", async () => {
    renderDialog({ keys: GOOGLE_IMAGE_KEY });
    await waitForGallery();
    await waitFor(() => expect(badgeKinds("insurance_claim")).toEqual(["keys_present", "example_pack"]));
    expect(within(tile("insurance_claim")).getByText("Keys present")).toBeTruthy();
    expect(within(tile("insurance_claim")).queryByText("Optional key")).toBeNull();
  });

  it("moves the selection with the arrow keys", async () => {
    renderDialog();
    await waitForGallery();

    const [blank, knowledge] = screen.getAllByRole("radio");
    blank.focus();
    fireEvent.keyDown(blank, { key: "ArrowDown" });
    await waitFor(() => expect(knowledge.getAttribute("aria-checked")).toBe("true"));
    expect(blank.getAttribute("aria-checked")).toBe("false");
    await waitFor(() => expect(within(previewPane()).getByRole("heading", { name: "Knowledge assistant" })).toBeTruthy());
  });

  it("previews the selected starter: description, facts, Try saying and After creating", async () => {
    renderDialog();
    await waitForGallery();
    fireEvent.click(within(tile("receptionist")).getByRole("radio"));

    const receptionist = templateById("receptionist").template;
    const pane = within(previewPane());
    await waitFor(() => expect(pane.getByRole("heading", { name: "Receptionist" })).toBeTruthy());
    expect(pane.getByText(receptionist.description)).toBeTruthy();
    // 7 flow nodes, one of them the global node → 6 steps.
    expect(pane.getByText("6 nodes, 4 variables")).toBeTruthy();
    expect(pane.getByText("2 HTTP tools: check_availability, book_appointment")).toBeTruthy();
    expect(pane.getByText("Seeds 1 knowledge base")).toBeTruthy();
    for (const prompt of receptionist.sample_prompts ?? []) {
      expect(pane.getByText(`“${prompt}”`)).toBeTruthy();
    }
    for (const step of receptionist.next_steps ?? []) {
      expect(pane.getByText(step.label)).toBeTruthy();
    }

    // Knowledge assistant: the pack's composite panel blocks, labelled.
    fireEvent.click(within(tile("knowledge_assistant")).getByRole("radio"));
    await waitFor(() =>
      expect(within(previewPane()).getByText("Blocks: Status, Sources, Notes, Activity")).toBeTruthy(),
    );
    expect(within(previewPane()).getByText("Seeds 2 knowledge bases")).toBeTruthy();

    // Insurance: the pack's code tools and its own panel.
    fireEvent.click(within(tile("insurance_claim")).getByRole("radio"));
    await waitFor(() => expect(within(previewPane()).getByText("4 code tools")).toBeTruthy());
    expect(within(previewPane()).getByText("Claim notebook")).toBeTruthy();
  });

  it("falls back to the pack list when the api has no templates route (404), rendering derived tiles", async () => {
    const { posts } = renderDialog({ templates404: true });
    await waitForGallery();

    expect(tiles().map((el) => el.dataset.template)).toEqual(["pack:generic", "pack:insurance_claim"]);
    expect(screen.getByRole("radio", { name: "Blank agent" }).getAttribute("aria-checked")).toBe("true");
    expect(within(tile("pack:insurance_claim")).getByText("Insurance claim intake")).toBeTruthy();
    expect(screen.getByText(/lists packs rather than starters/)).toBeTruthy();

    // An older api doesn't know template_id: the fallback creates from the pack.
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("textbox", { name: /^Name/ });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ pack_id: "generic", config: null });
    expect(posts[0]).not.toHaveProperty("template_id");
  });

  it("preselects the deep link's starter", async () => {
    renderDialog({ initialTemplateId: "receptionist" });
    await waitForGallery();
    expect(screen.getByRole("radio", { name: "Receptionist" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("CreateAgentDialog — step 2 and creating", () => {
  it("Continue opens step 2 prefilled with the starter's name and tagline", async () => {
    renderDialog();
    await continueTo("knowledge_assistant");

    const template = templateById("knowledge_assistant").template;
    expect(screen.getByRole("heading", { name: "New agent · Knowledge assistant" })).toBeTruthy();
    expect((screen.getByRole("textbox", { name: /^Name/ }) as HTMLInputElement).value).toBe("Knowledge assistant");
    expect((screen.getByRole("textbox", { name: /^Description/ }) as HTMLTextAreaElement).value).toBe(template.tagline);
    // One connection in the workspace → no connection picker.
    expect(screen.queryByText("Connection")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Knowledge assistant" }).getAttribute("aria-checked")).toBe("true"));
  });

  it("requires a name", async () => {
    const { posts } = renderDialog();
    await continueTo("blank");
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("Name is required.")).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it("posts template_id (no pack_id), toasts and opens the editor on the starter's first section", async () => {
    const { posts, onOpenChange } = renderDialog({
      create: { status: 201, body: createdAgent("knowledge_assistant") },
    });
    await continueTo("knowledge_assistant");
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), { target: { value: "Support line" } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      name: "Support line",
      description: templateById("knowledge_assistant").template.tagline,
      template_id: "knowledge_assistant",
      config: null,
    });
    expect(posts[0]).not.toHaveProperty("pack_id");
    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith("/console/agents/agent-new?section=knowledge&from=knowledge_assistant"),
    );
    expect(toast.success).toHaveBeenCalledWith("Created from Knowledge assistant", undefined);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // TEMPLATES.md §6.4: `section=<first next_steps[].section ?? "providers">`. The
  // catalogue's blank starter lists "Write the instructions" first (§5.1), so it
  // lands on Instructions; Providers is the fallback for a starter whose steps
  // name no section (the PLAN-V4 card's "blank → providers" example predates
  // the catalogue — reported as a deviation).
  it("lands a blank agent on its first next step's section, and a sectionless starter on Providers", async () => {
    const { posts } = renderDialog({ create: { status: 201, body: createdAgent("blank") } });
    await continueTo("blank");
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/console/agents/agent-new?section=instructions&from=blank"));
    expect(toast.success).toHaveBeenCalledWith("Created from Blank agent", undefined);
    expect(landingSection({ ...templateById("blank").template, next_steps: [{ label: "Read the docs", href: "/x" }] })).toBe(
      "providers",
    );
  });

  it("names DTMF in the toast when seeding left it off for the phone agent", async () => {
    renderDialog({
      create: { status: 201, body: createdAgent("phone_agent", { capabilities: { dtmf: false } }) },
    });
    await continueTo("phone_agent");
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const [title, options] = vi.mocked(toast.success).mock.calls[0];
    expect(title).toBe("Created from Phone agent");
    expect((options as { description?: string }).description).toMatch(/DTMF is off until a SIP trunk is reachable/);
    // phone_agent's first next step is a page link; the first step with a section is Recording.
    expect(routerPush).toHaveBeenCalledWith("/console/agents/agent-new?section=recording&from=phone_agent");
  });

  it("renders a 422's issues under the form and keeps the dialog open", async () => {
    const { onOpenChange } = renderDialog({
      create: {
        status: 422,
        body: {
          error: {
            code: "unprocessable_entity",
            message: "agent configuration is invalid",
            details: {
              errors: ["pipeline.stt: needs LiveKit Inference"],
              warnings: [],
              issues: [
                {
                  path: "pipeline.stt",
                  message: "LiveKit Inference isn't available on self-hosted connections — add a key for a speech-to-text vendor.",
                  severity: "error",
                },
                { path: "voice", message: "a warning that is not shown", severity: "warning" },
              ],
            },
          },
        },
      },
    });
    await continueTo("knowledge_assistant");
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("This agent can't be created yet")).toBeTruthy();
    expect(within(alert).getByText(/LiveKit Inference isn't available on self-hosted connections/)).toBeTruthy();
    expect(within(alert).getByText("pipeline.stt")).toBeTruthy();
    expect(within(alert).queryByText("a warning that is not shown")).toBeNull();
    expect(within(alert).getByRole("link", { name: "Add a provider key" }).getAttribute("href")).toBe("/console/providers");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(routerPush).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Back" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("explains an unknown starter id with the ids the server knows", async () => {
    renderDialog({
      create: {
        status: 422,
        body: {
          error: {
            code: "unprocessable_entity",
            message: "unknown template 'x'",
            details: { template_id: "x", known: ["blank", "receptionist"] },
          },
        },
      },
    });
    await continueTo("blank");
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("This starter isn't available on this server")).toBeTruthy();
    expect(within(alert).getByText(/blank, receptionist/)).toBeTruthy();
  });
});

describe("template-meta helpers", () => {
  it("counts flow steps without the global node", () => {
    expect(flowFact(templateById("receptionist").template)).toBe("6 nodes, 4 variables");
    expect(flowFact(templateById("lead_qualification").template)).toBe("8 nodes, 7 variables");
    expect(flowFact(templateById("blank").template)).toBeNull();
  });

  it("lands on the first next step that names a section, else Providers", () => {
    expect(landingSection(templateById("knowledge_assistant").template)).toBe("knowledge");
    expect(landingSection(templateById("receptionist").template)).toBe("tools");
    expect(landingSection(templateById("lead_qualification").template)).toBe("flow");
    expect(landingSection({ ...templateById("blank").template, next_steps: [] })).toBe("providers");
  });

  it("reports gated fields by comparing the starter (and its pack) with the created config", () => {
    const phone = templateById("phone_agent");
    expect(gatedDifferences(phone, { instructions: "", pipeline: { mode: "cascaded" }, capabilities: { dtmf: true } })).toEqual([]);
    expect(gatedDifferences(phone, { instructions: "", pipeline: { mode: "cascaded" }, capabilities: { dtmf: false } })).toEqual([
      "DTMF is off until a SIP trunk is reachable on this connection.",
    ]);

    // The insurance pack recommends image generation; a keyless workspace drops it.
    const insurance = templateById("insurance_claim");
    const providers = new Map([["google-image-gen", { vendor: "Google", label: "Google image generation" }]]);
    const withoutImages = gatedDifferences(insurance, { instructions: "", pipeline: { mode: "cascaded" } }, providers);
    expect(withoutImages).toEqual(["Running on LiveKit Inference — add a Google key for incident sketches."]);

    // A realtime starter created as cascaded.
    const realtime = {
      ...phone,
      template: {
        ...phone.template,
        capabilities: null,
        pipeline: { mode: "realtime" as const, realtime: { provider_id: "google-realtime" } },
      },
      pack: { ...phone.pack, capabilities: { ...phone.pack.capabilities, dtmf: false } },
    };
    expect(gatedDifferences(realtime, { instructions: "", pipeline: { mode: "cascaded" } })[0]).toMatch(
      /^Running on LiveKit Inference — add a google-realtime key for realtime voice\.$/,
    );
  });

  it("derives a starter from a pack like the api's derived_template", () => {
    const derived = derivedFromPack(templateById("insurance_claim").pack, PROVIDERS.providers);
    expect(derived.template.id).toBe("pack:insurance_claim");
    expect(derived.template.category).toBe("example");
    expect(derived.template.chips).toEqual(["code_tools", "knowledge_seeds", "camera", "image_gen"]);
    expect(derived.template.requires?.provider_keys).toEqual([{ provider_id: "google-image-gen", optional: true }]);
    expect(derivedFromPack(templateById("blank").pack).template.name).toBe("Blank agent");
  });
});

describe("V4-02 file rules", () => {
  const web = path.resolve(__dirname, "..");
  const createDir = path.join(web, "src/components/console/agents/create");

  function walk(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = path.join(dir, name);
      return statSync(full).isDirectory() ? walk(full) : [full];
    });
  }

  it("uses no side sheet or drawer in the dialog, its parts or the summary rail", () => {
    const files = [...walk(createDir), path.join(web, "src/components/console/agents/editor/summary-rail.tsx")];
    for (const file of files) {
      expect(readFileSync(file, "utf8"), file).not.toMatch(/@\/components\/ui\/(sheet|drawer)|from "vaul"/);
    }
  });

  it("deletes the old page flow and pack card", () => {
    expect(existsSync(path.join(createDir, "pack-card.tsx"))).toBe(false);
    expect(existsSync(path.join(createDir, "create-agent-flow.tsx"))).toBe(false);
  });

  it("has no link to /console/agents/new outside the deep-link page itself", () => {
    const offenders = walk(path.join(web, "src"))
      .filter((file) => /\.(tsx?|mdx?)$/.test(file))
      .filter((file) => !file.endsWith(path.join("app", "console", "agents", "new", "page.tsx")))
      .filter((file) => /(href[=:]|push\(|replace\()\s*[{"'`]*\/console\/agents\/new/.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });
});

describe("TemplateTile — 'Keys present' through the credential home (V4-04, R-V4-7)", () => {
  // `keyProviderIds` is built from the raw credential list (`create-agent-dialog.tsx`),
  // whose rows the api stores under a provider's credential home — an
  // OpenRouter key added from any of its five slots is always stored as
  // `openrouter-llm` (V4-03, ask #13). A starter that requires an *aliased*
  // id (here `openrouter-stt`) must still read "Keys present" once that one
  // key exists; `TemplateTile` is the file this card asks to carry that
  // mapping (`template-meta.ts::templateBadges` itself stays a pure
  // function of the ids it's given).
  const openrouterLlm = { id: "openrouter-llm", kind: "llm", label: "OpenRouter", vendor: "OpenRouter", package: "", python_class: "" };
  const openrouterStt = {
    id: "openrouter-stt",
    kind: "stt",
    label: "OpenRouter (STT)",
    vendor: "OpenRouter",
    package: "",
    python_class: "",
    credential_provider: "openrouter-llm",
  };

  function templateRequiring(...providerIds: string[]): (typeof TEMPLATES)["items"][number] {
    const base = templateById("blank");
    return {
      ...base,
      template: {
        ...base.template,
        id: "openrouter_test_template",
        name: "OpenRouter test starter",
        requires: { ...base.template.requires, provider_keys: providerIds.map((provider_id) => ({ provider_id })) },
      },
    };
  }

  function renderTile(item: ReturnType<typeof templateRequiring>, keyProviderIds: ReadonlySet<string>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.startsWith("/api/console/providers")) {
          return { ok: true, status: 200, json: async () => ({ providers: [openrouterLlm, openrouterStt] }) } as Response;
        }
        throw new Error(`Unhandled fetch: ${url}`);
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <RadioGroupRoot value={item.template.id} onValueChange={() => {}}>
          <TemplateTile item={item} selected providers={new Map()} keyProviderIds={keyProviderIds} />
        </RadioGroupRoot>
      </QueryClientProvider>,
    );
  }

  it("reads 'Keys present' when the one home credential covers an aliased required key", async () => {
    const item = templateRequiring("openrouter-stt", "openrouter-llm");
    renderTile(item, new Set(["openrouter-llm"]));
    await waitFor(() => expect(screen.getByText("Keys present")).toBeTruthy());
    expect(screen.queryByText("Needs keys")).toBeNull();
  });

  it("still reads 'Needs keys' when the home credential is missing", async () => {
    const item = templateRequiring("openrouter-stt");
    renderTile(item, new Set());
    await waitFor(() => expect(screen.getByText("Needs keys")).toBeTruthy());
  });
});
