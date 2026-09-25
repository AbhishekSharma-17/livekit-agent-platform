import type { ConnectionOut, ModelSpec, ProviderOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * Registry helpers shared by the provider slot editor, the credential dialog,
 * the connections/providers pages and the agent editor's providers section
 * (docs/UI_UX_SPEC.md §4.4/§4.5, WP-4; UI_UX_SPEC-V2-AMENDMENTS §2.2/§2.3).
 *
 * R-V2-2 (docs/v2/PLAN-V2.md §8) migrates this gate off the `status` alias
 * (R-V2-1: `availability === "available" && worker_image === "slim"`) onto
 * the real signals a `ProviderOut` carries once a workspace and its
 * connections exist: `availability`, `enabled` (workspace setting) and
 * `installed_on` (which connections' workers report the id — the api already
 * applies the `worker_image ≤ connection.worker_image` fallback rule server
 * side, so the client never re-derives it, see `routers/providers.py`). A
 * bare `ProviderSpec` (no workspace context, e.g. a v1 fixture) is treated as
 * enabled with `installed_on: []`.
 *
 * `verification` remains informational only — it never gates anything here
 * (R-V2-1's second half, unchanged by R-V2-2).
 */

export type ProviderKind = ProviderSpec["kind"];

export type SlotAvailability =
  /** Offered in pickers. */
  | "selectable"
  /** `availability: "deferred"` — on the roadmap. */
  | "coming-soon"
  /** `availability: "available"` but not installed on the connection in view (or on any connection, with none in view). */
  | "not-installed"
  /** The workspace disabled this provider (`ProviderOut.enabled === false`). */
  | "disabled"
  /** `capabilities.cloud_only` on a self-hosted connection — needs LiveKit Cloud + Inference. */
  | "cloud-only"
  /** `availability: "incompatible"` — known not to work with this platform. */
  | "incompatible"
  /** `availability: "removed"` — hidden everywhere. */
  | "removed";

/** The connection a slot picker is scoped to, when one is bound. Omit for a connection-agnostic listing (credential dialog, the Providers catalog's "all providers" view). */
export interface SlotConnectionContext {
  connection?: Pick<ConnectionOut, "id" | "name" | "deployment_type"> | null;
}

function enabledFor(spec: ProviderSpec): boolean {
  return (spec as ProviderOut).enabled !== false;
}

/**
 * `undefined` (the field is absent, e.g. a bare `ProviderSpec` fixture with
 * no workspace context — tests, or `contracts/generated/providers.json`
 * itself) is distinct from `[]` (a real `ProviderOut` the api checked and
 * found on no connection): only the latter means "known not installed".
 */
function installedOn(spec: ProviderSpec): string[] | undefined {
  return (spec as ProviderOut).installed_on;
}

/**
 * @deprecated R-V2-1's `status` alias, kept only for payloads that still
 * carry it and for anything not yet migrated to `slotAvailability`. New code
 * should use `slotAvailability`/`isSelectableForCredentials` instead
 * (R-V2-2) — `status` never reflects workspace enablement or per-connection
 * install state.
 */
export function isMvp(spec: ProviderSpec): boolean {
  if (spec.status !== undefined) return spec.status === "mvp";
  return (spec.availability ?? "available") === "available" && (spec.worker_image ?? "slim") === "slim";
}

/**
 * R-V2-2 gate. **Without `ctx.connection`** (the credential dialog, the
 * Providers catalog's kind list, and every existing WP-4 caller that has no
 * single connection to test against) this falls back to R-V2-1's alias
 * exactly (`isMvp`) rather than consulting `installed_on` at all: the static
 * registry export (`contracts/generated/providers.json`, built with zero
 * connections) always reports `installed_on: []`, so treating an empty list
 * as "not installed" with no connection in view would make every provider
 * unselectable the moment a live `ProviderOut` (which also defaults
 * `installed_on` to `[]` before any connection exists) replaces the bare
 * `ProviderSpec` — not just in this static fixture. **With `ctx.connection`**
 * — the one case R-V2-2 actually changes — a provider is `selectable` iff it
 * is enabled for the workspace, isn't `cloud_only` on a self-hosted
 * connection, and `installed_on` includes that connection's id. This is
 * where the providers section's slot cards gate for real, via
 * `connectionDisabledReason`.
 */
export function slotAvailability(spec: ProviderSpec, ctx: SlotConnectionContext = {}): SlotAvailability {
  switch (spec.availability) {
    case "removed":
      return "removed";
    case "incompatible":
      return "incompatible";
    case "deferred":
      return "coming-soon";
    default:
      break; // "available" (or undefined — v1 fixtures)
  }
  if (!enabledFor(spec)) return "disabled";
  const connection = ctx.connection;
  if (!connection) {
    return isMvp(spec) ? "selectable" : "not-installed";
  }
  if (spec.capabilities?.cloud_only && connection.deployment_type === "self_hosted") {
    return "cloud-only";
  }
  const installed = installedOn(spec) ?? [];
  if (!installed.includes(connection.id)) return "not-installed";
  return "selectable";
}

/** Short chip text and a one-sentence reason (with a fix where there is one). */
export function unavailableCopy(spec: ProviderSpec, ctx: SlotConnectionContext = {}): { chip: string; reason: string } {
  switch (slotAvailability(spec, ctx)) {
    case "disabled":
      return { chip: "Disabled", reason: `${spec.label} is disabled for this workspace — enable it on the Providers page.` };
    case "cloud-only":
      return {
        chip: "Cloud only",
        reason:
          "Not available on self-hosted connections — LiveKit Inference needs LiveKit Cloud. Use your own STT/LLM/TTS keys.",
      };
    case "not-installed":
      return {
        chip: "Not installed",
        reason: ctx.connection
          ? `Not installed on “${ctx.connection.name}”. Use LiveKit Inference for this slot, or give the connection the full worker image.`
          : "Not installed on any of your connections yet.",
      };
    case "incompatible":
      return { chip: "Not available", reason: spec.notes || "Known not to work with this platform." };
    case "removed":
      return { chip: "Removed", reason: spec.notes || "No longer offered." };
    default:
      return { chip: "Coming soon", reason: spec.notes || "Planned for a later release." };
  }
}

/**
 * A sentence (with a fix) for why `spec` can't be picked on `connection`, or
 * `null` when it can — for `ProviderSlotEditor`'s `constraints.disabledReason`
 * (composed on top of the registry's own connection-agnostic gate above, so
 * a provider installed nowhere is already caught before this runs).
 */
export function connectionDisabledReason(
  spec: ProviderSpec,
  connection: Pick<ConnectionOut, "id" | "name" | "deployment_type"> | null | undefined,
): string | null {
  if (!connection) return null;
  const availability = slotAvailability(spec, { connection });
  if (availability === "selectable") return null;
  return unavailableCopy(spec, { connection }).reason;
}

/** Available and enabled for the workspace, ignoring per-connection install state (credential dialog: "which vendors take a key"). */
export function isSelectableForCredentials(spec: ProviderSpec): boolean {
  return (spec.availability ?? "available") === "available" && enabledFor(spec);
}

/**
 * The provider id whose credential row `spec`'s key is stored under
 * (R-V4-7 / `OPENROUTER.md` D-V4-10 — mirrors the contracts-side
 * `lkap_contracts.providers.credential_home`): `spec.credential_provider`
 * when set (an alias, e.g. `openrouter-stt` → `openrouter-llm`), else the
 * id itself. Accepts a bare id plus the registry for callers that only have
 * the id (a template's `RequiredKey.provider_id`); an id not found in the
 * registry is returned unchanged (its own home, as far as this client
 * knows).
 */
export function credentialHome(specOrId: Pick<ProviderSpec, "id" | "credential_provider"> | string, registry: ProviderSpec[] = []): string {
  const spec = typeof specOrId === "string" ? registry.find((p) => p.id === specOrId) : specOrId;
  if (!spec) return specOrId as string;
  return spec.credential_provider ?? spec.id;
}

export function isVerified(spec: ProviderSpec): boolean {
  return spec.verification === "verified";
}

/** The vendor name for a shared key's title: `spec.vendor`, else the label with a trailing "(Kind)"-style suffix stripped. */
function vendorTitle(spec: Pick<ProviderSpec, "vendor" | "label">): string {
  return spec.vendor?.trim() || spec.label.replace(/\s*\([^()]*\)\s*$/, "").trim();
}

export interface CredentialDisplay {
  /** The vendor name for a shared credential home ("OpenRouter"); the spec's own label otherwise. */
  title: string;
  /** Human kind labels this key covers, in `KIND_ORDER` — the home's own kind plus every aliased entry's. A single entry for a spec that isn't a credential home. */
  usedBy: string[];
}

/**
 * How a stored key should read everywhere it appears — the credentials
 * list, the key dialog, the picker, the providers catalog (R-V4-7's
 * follow-up: a key added from an aliased slot, e.g. `openrouter-stt`, read
 * as LLM-only because the console showed the credential *home*'s own
 * label/kind, e.g. "OpenRouter" filed under "Language models", with no sign
 * it also covers STT/TTS/embeddings/image generation).
 *
 * Accepts either the home spec or any alias — both resolve to the same
 * answer. When the resolved home is genuinely shared (another registry
 * entry names it as `credential_provider`, or `spec` itself is an alias),
 * the title becomes the vendor name and `usedBy` lists every kind it
 * covers, home included, in `KIND_ORDER`. Otherwise (an ordinary
 * single-kind provider, e.g. Deepgram) today's label and kind are
 * unchanged — `usedBy` is just that one kind.
 *
 * `registry` is optional: without it (e.g. `CredentialPicker`, which only
 * has its own `spec`, not the full list), a spec that is itself an alias
 * (`credential_provider` set) still resolves to the vendor title from its
 * own `vendor` field — just with a `usedBy` limited to what's knowable
 * without the registry (its own kind). Pass the full registry wherever it's
 * already in hand (the credentials list, the dialog, the catalog) for the
 * complete `usedBy` list.
 */
export function credentialDisplay(
  spec: Pick<ProviderSpec, "id" | "label" | "vendor" | "kind" | "credential_provider">,
  registry: ProviderSpec[] = [],
): CredentialDisplay {
  const homeId = spec.credential_provider ?? spec.id;
  const home = registry.find((p) => p.id === homeId) ?? (homeId === spec.id ? spec : undefined);
  const aliases = home ? registry.filter((p) => p.credential_provider === home.id) : [];
  const isShared = Boolean(spec.credential_provider) || aliases.length > 0;

  if (!isShared) {
    return { title: spec.label, usedBy: [KIND_LABEL[spec.kind]] };
  }

  const baseline = home ?? spec;
  const kinds = new Set<ProviderKind>([baseline.kind, spec.kind, ...aliases.map((a) => a.kind)]);
  const ordered = Array.from(kinds).sort((a, b) => kindRank(a) - kindRank(b));
  return { title: vendorTitle(baseline), usedBy: ordered.map((k) => KIND_LABEL[k]) };
}

/** LiveKit Inference entries (`livekit-inference-stt|llm|tts`): no key, billed through LiveKit Cloud. */
export function isInferenceProvider(spec: ProviderSpec): boolean {
  return spec.id.startsWith("livekit-inference-");
}

export function inferenceProviderFor(kind: ProviderKind, providers: ProviderSpec[]): ProviderSpec | undefined {
  return providers.find((p) => p.kind === kind && isInferenceProvider(p) && isSelectableForCredentials(p));
}

export const KIND_LABEL: Record<ProviderKind, string> = {
  realtime: "Realtime",
  stt: "Speech-to-text",
  llm: "Language models",
  tts: "Text-to-speech",
  avatar: "Avatars",
  image_gen: "Image generation",
  embedding: "Embeddings",
  secret_bag: "Tool secrets",
  tool_provider: "Connected apps",
  vad: "Voice activity detection",
  turn_detection: "Turn detection",
  noise_cancellation: "Noise cancellation",
};

/** Display order for kinds (credentials page, vendor pickers). */
export const KIND_ORDER: ProviderKind[] = [
  "realtime",
  "stt",
  "llm",
  "tts",
  "avatar",
  "image_gen",
  "embedding",
  "secret_bag",
  "tool_provider",
  "vad",
  "turn_detection",
  "noise_cancellation",
];

export function kindRank(kind: ProviderKind): number {
  const index = KIND_ORDER.indexOf(kind);
  return index === -1 ? KIND_ORDER.length : index;
}

/** The model the slot actually runs: the explicit one, else the provider's default. */
export function effectiveModelId(spec: ProviderSpec | undefined, model: string | null | undefined): string | null {
  const trimmed = model?.trim();
  if (trimmed) return trimmed;
  return spec?.default_model ?? null;
}

export function findModel(spec: ProviderSpec | undefined, modelId: string | null | undefined): ModelSpec | undefined {
  if (!spec || !modelId) return undefined;
  return (spec.models ?? []).find((m) => m.id === modelId);
}

/**
 * Whether the configured cascaded LLM is known to ignore image parts
 * (DECISIONS-W2 D-W2-10): `true` only when the model is in the provider's
 * suggestion list without `supports_video`; unknown/free-text models never
 * trigger it. Mirrors `lkap_contracts.providers.vision_support` and the copy
 * in `agents/tabs/panel-tab.tsx` (WP-5's, not exported there).
 */
export function isKnownTextOnlyLlm(
  providers: ProviderSpec[],
  llm: { provider_id?: string; model?: string | null } | null | undefined,
): boolean {
  if (!llm?.provider_id) return false;
  const spec = providers.find((p) => p.id === llm.provider_id);
  if (!spec) return false;
  const model = findModel(spec, effectiveModelId(spec, llm.model));
  return model ? model.supports_video !== true : false;
}

/** "<Vendor> key · <Month yyyy>" (§4.5 suggested label). */
export function suggestedCredentialLabel(spec: Pick<ProviderSpec, "vendor">, now: Date = new Date()): string {
  const month = now.toLocaleString("en-GB", { month: "long", year: "numeric" });
  return `${spec.vendor} key · ${month}`;
}
