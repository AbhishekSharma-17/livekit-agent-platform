"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import type {
  PackManifest,
  PacksResponse,
  PipelineConfig,
  ProviderSpec,
  ProvidersResponse,
  RequiredKey,
  StarterTemplate,
  TemplateOut,
  TemplatesResponse,
} from "@/contracts/lkap-contracts";

/**
 * Starter templates for the New agent dialog (docs/v4/TEMPLATES.md §6): `GET
 * /v1/templates`, and `GET /v1/templates/{id}` for the editor's "Next steps"
 * card. Kept beside the dialog rather than in `lib/api-hooks.ts` (V4-02's
 * choice per the package card).
 *
 * An api older than this console has no `templates` route; a 404 falls back
 * to `GET /v1/packs` rendered as derived tiles (`derivedFromPack`), so the
 * console can ship before the api is upgraded.
 */

export const templateKeys = {
  all: ["templates"] as const,
  detail: (id: string) => ["templates", id] as const,
};

export interface TemplateGallery {
  items: TemplateOut[];
  /**
   * `templates`: the api's catalogue (create with `template_id`).
   * `packs`: the fallback for an older api (create with `pack_id`).
   */
  source: "templates" | "packs";
}

export const GENERIC_PACK_ID = "generic";
export const DERIVED_PREFIX = "pack:";

const TAGLINE_MAX = 90;
const NAME_MAX = 48;

/** Slots whose provider needs a key, by pipeline mode (api `REQUIRED_SLOTS`). */
const MODE_SLOTS: Record<NonNullable<PipelineConfig["mode"]>, (keyof PipelineConfig)[]> = {
  cascaded: ["stt", "llm", "tts"],
  realtime: ["realtime"],
  half_cascade: ["realtime", "tts"],
};
const EXTRA_SLOTS: (keyof PipelineConfig)[] = ["avatar", "image_gen", "workflow_llm"];
const OPTIONAL_SLOTS = new Set<keyof PipelineConfig>(["avatar", "image_gen"]);

/** Mirror of the api's `requirements_from_pipeline`, for derived tiles only. */
export function requirementsFromPipeline(pipeline: PipelineConfig, providers: ProviderSpec[]): RequiredKey[] {
  const specs = new Map(providers.map((spec) => [spec.id, spec]));
  const keys = new Map<string, RequiredKey>();
  for (const slot of [...MODE_SLOTS[pipeline.mode ?? "cascaded"], ...EXTRA_SLOTS]) {
    const ref = pipeline[slot];
    if (!ref || typeof ref !== "object" || !("provider_id" in ref) || typeof ref.provider_id !== "string") continue;
    const spec = specs.get(ref.provider_id);
    if (!spec?.requires_credential) continue;
    const optional = OPTIONAL_SLOTS.has(slot);
    const existing = keys.get(spec.id);
    if (!existing || (existing.optional && !optional)) keys.set(spec.id, { provider_id: spec.id, optional });
  }
  return [...keys.values()];
}

function tagline(description: string): string {
  const first = description.trim().split(". ")[0].trim().replace(/\.$/, "");
  return first.length <= TAGLINE_MAX ? first : `${first.slice(0, TAGLINE_MAX - 1).trimEnd()}…`;
}

function derivedChips(manifest: PackManifest): NonNullable<StarterTemplate["chips"]> {
  const chips: NonNullable<StarterTemplate["chips"]> = [];
  if (manifest.tool_names.length > 0) chips.push("code_tools");
  if ((manifest.kb_seeds ?? []).length > 0) chips.push("knowledge_seeds");
  if (manifest.capabilities.camera) chips.push("camera");
  if (manifest.capabilities.screen_share) chips.push("screen_share");
  if (manifest.capabilities.dtmf) chips.push("dtmf");
  if (manifest.recommended_pipeline.image_gen) chips.push("image_gen");
  return chips;
}

/**
 * A pack as a derived starter (api `catalog.derived_template`, D-V4-4): every
 * overlay field unset, so the manifest is the whole configuration. The
 * `generic` pack is labelled "Blank agent" and sorted first, as the old pack
 * list did.
 */
export function derivedFromPack(manifest: PackManifest, providers: ProviderSpec[] = []): TemplateOut {
  const isBlank = manifest.id === GENERIC_PACK_ID;
  const template: StarterTemplate = {
    id: `${DERIVED_PREFIX}${manifest.id}`,
    name: (isBlank ? "Blank agent" : manifest.name).slice(0, NAME_MAX),
    tagline: tagline(manifest.description),
    description: manifest.description,
    category: isBlank ? "blank" : "example",
    chips: derivedChips(manifest),
    requires: {
      provider_keys: requirementsFromPipeline(manifest.recommended_pipeline, providers),
      telephony: false,
      webhook_endpoint: false,
      storage: false,
    },
    order: isBlank ? 0 : 1000,
    pack_id: manifest.id,
  };
  return { template, pack: manifest, derived: true };
}

async function fetchGallery(): Promise<TemplateGallery> {
  try {
    const response = await api.get<TemplatesResponse>("templates");
    return { items: response.items, source: "templates" };
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
  }
  const [packs, providers] = await Promise.all([
    api.get<PacksResponse>("packs"),
    api.get<ProvidersResponse>("providers").catch(() => ({ providers: [] }) as ProvidersResponse),
  ]);
  const items = packs.items
    .map((pack) => derivedFromPack(pack.manifest, providers.providers))
    .sort((a, b) => (a.template.order ?? 100) - (b.template.order ?? 100));
  return { items, source: "packs" };
}

/** The gallery in response order (the api's `order`, then derived packs). */
export function useTemplates(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: templateKeys.all,
    queryFn: fetchGallery,
    staleTime: 5 * 60_000,
    enabled: options?.enabled ?? true,
  });
}

/**
 * One starter by id, or `null` when the api doesn't know it (404) — the
 * "Next steps" card then simply doesn't render.
 */
export function useTemplate(id: string | null | undefined) {
  return useQuery({
    queryKey: templateKeys.detail(id ?? ""),
    queryFn: async () => {
      try {
        return await api.get<TemplateOut>(`templates/${encodeURIComponent(id ?? "")}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(id),
    staleTime: 5 * 60_000,
    retry: false,
  });
}
