"use client";

import * as React from "react";
import { ChevronsUpDownIcon, CircleSlashIcon, PencilLineIcon, SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { useCredentials, useProviderModels, useProviders } from "@/components/console/lib/api-hooks";
import { formatUsdPerMin, usePriceQuotes } from "@/components/console/lib/cost-hooks";
import { catalogSaysVision } from "@/components/console/registry/model-capabilities";
import { TestedChip, testedStateFor } from "@/components/console/registry/model-test-panel";
import { CATALOG_FULL_LIMIT, useCatalog } from "@/hooks/useCatalog";
import { idIssueSentence, isSendableModelId, validateModelId } from "@/lib/model-ids";
import { cn } from "@/lib/utils";
import type { CatalogItem, CatalogSpec, ModelIdRules, ModelSpec, ProviderModelOut, ProviderSpec } from "@/contracts/lkap-contracts";

/** Most catalog rows rendered at once; typing narrows the rest (a 1000-item list stays fast). */
const CATALOG_RENDER_CAP = 100;

/** The provider context the combobox needs for its live groups (all optional: without it, it is today's suggestions-only picker). */
export type ModelComboboxProvider = Pick<ProviderSpec, "id" | "vendor" | "probe" | "requires_credential"> & {
  catalog?: CatalogSpec | null;
};

export interface ModelComboboxProps {
  /** Forwarded to the trigger so a caller's `<label htmlFor>` / `Field` resolves. */
  id?: string;
  /** *Suggested*: the registry's `models` (or a `type="model"` field's `options`). */
  models: ModelSpec[];
  /** The stored model id; `""` means "use the provider default". */
  value: string;
  /** `ProviderSpec.default_model`; marked "Default" in the list. */
  defaultModel?: string | null;
  onChange: (value: string) => void;
  /** Controlled open state (the vision note opens the list pre-filtered). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Start with the list filtered to vision models (`supports_video`, or catalog `image` input). */
  visionOnly?: boolean;
  disabled?: boolean;
  className?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  /** Enables *Catalog* (live vendor list), the vendor search row and *Your custom models*. */
  provider?: ModelComboboxProvider;
  /** The slot's key: the catalog is listed with it, and the chips compare its fingerprint. */
  credentialId?: string | null;
  /** `ProvidersResponse.model_id_rules`; read from the providers cache when omitted. */
  rules?: ModelIdRules | null;
}

/** Whether a provider has a live model list (`catalog.kinds` includes `models`). */
export function hasModelCatalog(catalog: CatalogSpec | null | undefined): boolean {
  return Boolean(catalog?.kinds?.includes("models"));
}

/**
 * The vendor's live model list for a slot, fetched once (limit 1000) and
 * filtered locally — typing never reaches the vendor (D-V4-25). Shared by the
 * combobox, the slot editor and the slot card through the query cache.
 */
export function useModelCatalog(
  providerId: string | undefined,
  catalog: CatalogSpec | null | undefined,
  credentialId: string | null | undefined,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useCatalog(providerId, "models", credentialId ?? null, {
    params: { limit: CATALOG_FULL_LIMIT },
    enabled: Boolean(providerId) && hasModelCatalog(catalog) && enabled,
  });
}

/** An id outside *Suggested* and the live *Catalog* — shown with the `Custom` badge (D-V4-23). */
export function isCustomModelId(modelId: string, models: ModelSpec[], catalogItems: CatalogItem[] = []): boolean {
  if (!modelId) return false;
  return !models.some((m) => m.id === modelId) && !catalogItems.some((item) => item.id === modelId);
}

/** The model-id rule the api publishes, else the contract defaults (`lib/model-ids.ts`). */
export function useModelIdRules(rules?: ModelIdRules | null): ModelIdRules | null | undefined {
  const cached = useProviders({ enabled: false }).data?.model_id_rules;
  return rules ?? cached;
}

function matches(id: string, label: string, needle: string): boolean {
  if (needle === "") return true;
  return label.toLowerCase().includes(needle) || id.toLowerCase().includes(needle);
}

function recordSaysVision(record: ProviderModelOut): boolean {
  return record.declared?.vision === true || (record.declared?.vision == null && record.detected?.vision === true);
}

/** OpenRouter is the one vendor with a server-side search (`search_vendor`, R-V4-28). */
function vendorSearchName(provider: ModelComboboxProvider | undefined): string | null {
  const adapter = provider?.catalog?.adapter ?? "";
  return adapter.startsWith("openrouter_") ? "OpenRouter" : null;
}

/**
 * Model picker (docs/UI_UX_SPEC.md §4.4 step 3; docs/v4/CUSTOM-MODELS.md
 * D-V4-23, §3): a `Command` in a `Popover` with four groups —
 *   - *Suggested* — the registry's models, the default marked;
 *   - *Catalog* — the vendor's live list for the chosen key, filtered here as
 *     you type; for OpenRouter a "Search OpenRouter for …" row asks the vendor;
 *   - *Your custom models* — ids this workspace has tested or declared, with
 *     their tested chip;
 *   - "Use custom model: <typed>" — free text is first-class; an id outside
 *     the lists is a validation warning, never an error.
 *
 * The typed text is checked by the model-id rule first: a value that looks
 * like a key (or breaks the syntax) disables the custom row with the reason,
 * is never repeated anywhere, and triggers no request (R-V4-32).
 */
export function ModelCombobox(props: ModelComboboxProps) {
  // The live groups need the query client; a bare suggestions picker (a
  // `type="model"` option field) stays query-free.
  return props.provider ? <LiveModelCombobox {...props} provider={props.provider} /> : <ModelComboboxView {...props} live={NO_LIVE_DATA} />;
}

/** What the live groups feed the view. */
interface LiveData {
  catalogItems: CatalogItem[];
  vendorItems: CatalogItem[];
  vendorSearching: boolean;
  records: ProviderModelOut[];
  fingerprint: string | null | undefined;
  searchVendor: string | null;
  vendorQuery: string | null;
  setVendorQuery: (query: string | null) => void;
  rules: ModelIdRules | null | undefined;
  /** `model id -> "≈ $0.004/min"`, from the one `POST /v1/pricing/quotes` this open fired (docs/v4/COSTS.md §5 item 3). */
  priceQuotes: Map<string, string>;
}

const NO_LIVE_DATA: LiveData = {
  catalogItems: [],
  vendorItems: [],
  vendorSearching: false,
  records: [],
  fingerprint: undefined,
  searchVendor: null,
  vendorQuery: null,
  setVendorQuery: () => {},
  rules: undefined,
  priceQuotes: new Map(),
};

/** Most pairs a `POST /v1/pricing/quotes` call is asked for at once (the route's own cap). */
const PRICE_QUOTE_CAP = 100;

function LiveModelCombobox(props: ModelComboboxProps & { provider: ModelComboboxProvider }) {
  const { provider, credentialId = null, rules: rulesProp } = props;
  const [openState, setOpenState] = React.useState(false);
  const open = props.open ?? openState;
  const [vendorQuery, setVendorQuery] = React.useState<string | null>(null);
  const rules = useModelIdRules(rulesProp);

  // Live groups: the list is fetched once (limit 1000) and searched locally.
  const catalogQuery = useModelCatalog(provider.id, provider.catalog, credentialId);
  const searchVendor = vendorSearchName(provider);
  const vendorSearchQuery = useCatalog(provider.id, "models", credentialId, {
    params: { q: vendorQuery ?? undefined, search_vendor: true, limit: CATALOG_FULL_LIMIT },
    enabled: Boolean(searchVendor && vendorQuery && isSendableModelId(vendorQuery, rules)),
  });
  const customRecords = useProviderModels(provider.id, { custom: true }, { enabled: open });
  const credentials = useCredentials();
  const needsKey = provider.requires_credential !== false && !provider.id.startsWith("livekit-inference-");
  const fingerprint = !needsKey
    ? null
    : credentialId
      ? credentials.data?.items.find((item) => item.id === credentialId)?.fingerprint
      : undefined;

  // The visible ids as of this open (suggested first, then the catalog page):
  // one batched request per open, never re-fired while typing narrows the list (D-V4-47).
  const quoteIds = React.useMemo(() => {
    const ids = [...props.models.map((m) => m.id), ...(catalogQuery.data?.items ?? []).map((item) => item.id)];
    return Array.from(new Set(ids)).slice(0, PRICE_QUOTE_CAP);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `props.models` is a fresh array per render; its content is what matters
  }, [catalogQuery.data, provider.id]);
  const quotesQuery = usePriceQuotes(
    quoteIds.map((id) => ({ provider_id: provider.id, model: id })),
    // Wait for the catalog's first load to settle before quoting: firing as
    // soon as `open` flips true (before the catalog page has arrived) would
    // quote only the suggested ids, then quote again once the catalog data
    // changes `quoteIds` — two requests for one open, not one.
    { enabled: open && quoteIds.length > 0 && !catalogQuery.isLoading },
  );
  const priceQuotes = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const item of quotesQuery.data?.items ?? []) {
      if (!item.model) continue;
      map.set(item.model, formatUsdPerMin(item.per_minute_usd) ?? "no price");
    }
    return map;
  }, [quotesQuery.data]);

  const live: LiveData = {
    catalogItems: catalogQuery.data?.items ?? [],
    vendorItems: vendorQuery ? (vendorSearchQuery.data?.items ?? []) : [],
    vendorSearching: Boolean(vendorQuery) && vendorSearchQuery.isFetching,
    records: customRecords.data?.items ?? [],
    fingerprint,
    searchVendor,
    vendorQuery,
    setVendorQuery,
    rules,
    priceQuotes,
  };
  return (
    <ModelComboboxView
      {...props}
      open={open}
      onOpenChange={(next) => {
        if (!next) setVendorQuery(null);
        setOpenState(next);
        props.onOpenChange?.(next);
      }}
      live={live}
    />
  );
}

function ModelComboboxView({
  id,
  models,
  value,
  defaultModel,
  onChange,
  open: openProp,
  onOpenChange,
  visionOnly: visionOnlyProp = false,
  disabled,
  className,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
  provider,
  rules: rulesProp,
  live,
}: ModelComboboxProps & { live: LiveData }) {
  const [openState, setOpenState] = React.useState(false);
  const open = openProp ?? openState;
  const [query, setQuery] = React.useState("");
  const [visionOnly, setVisionOnly] = React.useState(visionOnlyProp);
  const listId = React.useId();
  const rules = live.rules ?? rulesProp;
  const { searchVendor, vendorQuery, setVendorQuery, fingerprint } = live;

  React.useEffect(() => {
    if (open) setVisionOnly(visionOnlyProp);
  }, [open, visionOnlyProp]);

  function setOpen(next: boolean) {
    if (!next) setQuery("");
    setOpenState(next);
    onOpenChange?.(next);
  }

  function choose(next: string) {
    onChange(next);
    setOpen(false);
  }

  const catalogItems = live.catalogItems;
  const effective = value.trim() || defaultModel || "";
  const suggestedIds = new Set(models.map((m) => m.id));
  const liveItems = dedupe([...live.vendorItems, ...catalogItems]).filter((item) => !suggestedIds.has(item.id));
  const knownIds = new Set([...suggestedIds, ...liveItems.map((item) => item.id)]);
  const records = live.records.filter((record) => !knownIds.has(record.model_id));

  const catalogSelected = liveItems.find((item) => item.id === effective);
  const selected: ModelSpec | undefined =
    models.find((m) => m.id === effective) ?? (catalogSelected ? { id: catalogSelected.id, label: catalogSelected.label } : undefined);
  const custom = value.trim() !== "" && isCustomModelId(value.trim(), models, liveItems);

  const needle = query.trim().toLowerCase();
  const hasVision =
    models.some((m) => m.supports_video) || liveItems.some((item) => catalogSaysVision(item.meta) === true);
  const visibleSuggested = models.filter((m) => matches(m.id, m.label, needle) && (!visionOnly || m.supports_video));
  const visibleCatalogAll = liveItems.filter(
    (item) => matches(item.id, item.label, needle) && (!visionOnly || catalogSaysVision(item.meta) === true),
  );
  const visibleCatalog = visibleCatalogAll.slice(0, CATALOG_RENDER_CAP);
  const visibleRecords = records.filter(
    (record) => matches(record.model_id, record.model_id, needle) && (!visionOnly || recordSaysVision(record)),
  );

  const typed = query.trim();
  const typedIssue = typed === "" ? null : validateModelId(typed, { field: "model", rules });
  const typedIsListed = typed !== "" && (knownIds.has(typed) || records.some((record) => record.model_id === typed));
  const offerCustom = typed !== "" && !typedIsListed;
  const offerVendorSearch = Boolean(searchVendor) && typed !== "" && typedIssue === null && vendorQuery !== typed;
  const nothingVisible =
    visibleSuggested.length === 0 && visibleCatalog.length === 0 && visibleRecords.length === 0 && !offerCustom;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          disabled={disabled}
          className={cn("h-auto min-h-9 w-full justify-between gap-2 px-3 py-1.5 text-left font-normal", className)}
        >
          <span className="flex min-w-0 items-center gap-2">
            <ModelSummary
              model={selected}
              modelId={effective}
              isDefault={value.trim() === "" && Boolean(defaultModel)}
              // The dark outline trigger lightens on hover/open; the muted id would drop below 4.5:1 there.
              idClassName="dark:text-foreground/80"
            />
            {custom ? <CapabilityBadge kind="custom" /> : null}
          </span>
          <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-72 p-0" aria-label="Choose a model">
        <Command shouldFilter={false} label="Models">
          <CommandInput value={query} onValueChange={setQuery} placeholder="Search models or type an id" />
          {hasVision ? (
            <div className="flex items-center gap-2 px-3 pt-2 text-xs text-muted-foreground">
              <button
                type="button"
                aria-pressed={visionOnly}
                onClick={() => setVisionOnly((v) => !v)}
                className={cn(
                  "rounded-xs px-1.5 py-0.5 font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  visionOnly ? "bg-brand-soft text-brand-text" : "bg-muted text-muted-foreground hover:text-foreground",
                )}
              >
                Vision only
              </button>
              {visionOnly ? <span>Showing models that can see images.</span> : null}
            </div>
          ) : null}
          <CommandList id={listId} className="max-h-[min(22rem,50dvh)]">
            {nothingVisible ? <CommandEmpty>No models match.</CommandEmpty> : null}
            {visibleSuggested.length > 0 ? (
              <CommandGroup heading="Suggested">
                {visibleSuggested.map((model) => (
                  <CommandItem
                    key={model.id}
                    value={`suggested:${model.id}`}
                    onSelect={() => choose(model.id)}
                    data-checked={model.id === effective ? "true" : undefined}
                    className="items-start"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex flex-wrap items-center justify-between gap-1.5">
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <span className="text-sm text-foreground">{model.label}</span>
                          {model.id === defaultModel ? (
                            <span className="rounded-xs bg-muted px-1 text-[0.6875rem] leading-4 font-medium text-muted-foreground">
                              Default
                            </span>
                          ) : null}
                          {model.supports_video ? <CapabilityBadge kind="vision" /> : null}
                        </span>
                        <ModelPriceHint priceQuotes={live.priceQuotes} modelId={model.id} />
                      </span>
                      <span className="truncate font-mono text-xs text-muted-foreground">{model.id}</span>
                      {model.note ? <span className="text-xs text-muted-foreground">{model.note}</span> : null}
                    </div>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}
            {visibleCatalog.length > 0 || live.vendorSearching ? (
              <CommandGroup
                heading={
                  <span className="flex items-center justify-between gap-2">
                    <span>Catalog</span>
                    <span className="font-normal tabular-nums">
                      {visibleCatalogAll.length > CATALOG_RENDER_CAP
                        ? `${CATALOG_RENDER_CAP} of ${visibleCatalogAll.length} — keep typing`
                        : visibleCatalogAll.length}
                    </span>
                  </span>
                }
              >
                {visibleCatalog.map((item) => (
                  <CommandItem
                    key={item.id}
                    value={`catalog:${item.id}`}
                    onSelect={() => choose(item.id)}
                    data-checked={item.id === effective ? "true" : undefined}
                    className="items-start"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex flex-wrap items-center justify-between gap-1.5">
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <span className="min-w-0 truncate text-sm text-foreground" title={item.label}>
                            {item.label}
                          </span>
                          {catalogSaysVision(item.meta) === true ? <CapabilityBadge kind="vision" /> : null}
                        </span>
                        <ModelPriceHint priceQuotes={live.priceQuotes} modelId={item.id} />
                      </span>
                      <span className="truncate font-mono text-xs text-muted-foreground" title={item.id}>
                        {item.id}
                      </span>
                    </div>
                  </CommandItem>
                ))}
                {live.vendorSearching ? (
                  <p className="px-2 py-1.5 text-xs text-muted-foreground">Searching {searchVendor}…</p>
                ) : null}
              </CommandGroup>
            ) : null}
            {visibleRecords.length > 0 ? (
              <CommandGroup heading="Your custom models">
                {visibleRecords.map((record) => (
                  <CommandItem
                    key={record.id || record.model_id}
                    value={`record:${record.model_id}`}
                    onSelect={() => choose(record.model_id)}
                    data-checked={record.model_id === effective ? "true" : undefined}
                    className="items-start"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="truncate font-mono text-xs text-foreground" title={record.model_id}>
                        {record.model_id}
                      </span>
                      {provider ? (
                        <TestedChip state={testedStateFor({ spec: provider, record, fingerprint })} className="max-w-full" />
                      ) : null}
                    </div>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}
            {offerVendorSearch || offerCustom ? (
              <CommandGroup heading="Custom">
                {offerVendorSearch ? (
                  <CommandItem value={`__search__:${typed}`} onSelect={() => setVendorQuery(typed)}>
                    <SearchIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                    <span className="min-w-0 truncate">
                      Search {searchVendor} for <span className="font-mono">&ldquo;{typed}&rdquo;</span>
                    </span>
                  </CommandItem>
                ) : null}
                {offerCustom ? (
                  typedIssue?.severity === "error" ? (
                    <CommandItem value="__custom__:invalid" disabled data-invalid="" className="items-start">
                      <CircleSlashIcon className="mt-0.5 size-4 text-danger-text" aria-hidden="true" />
                      <span className="min-w-0 text-pretty text-danger-text">{idIssueSentence(typedIssue, "this as a model id")}</span>
                    </CommandItem>
                  ) : (
                    <CommandItem value={`__custom__:${typed}`} onSelect={() => choose(typed)}>
                      <PencilLineIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                      <span className="min-w-0 truncate">
                        Use custom model: <span className="font-mono">{typed}</span>
                      </span>
                    </CommandItem>
                  )
                ) : null}
              </CommandGroup>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function dedupe(items: CatalogItem[]): CatalogItem[] {
  const seen = new Set<string>();
  const out: CatalogItem[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

/**
 * A row's own "≈ $0.004/min" (docs/v4/COSTS.md §5 item 3), muted "no price"
 * when the one batched quote for this open came back with nothing priced,
 * and nothing at all when there is no quote in view yet (no `provider`, or
 * the request hasn't answered) — never a flash of "no price" while loading.
 */
function ModelPriceHint({ priceQuotes, modelId }: { priceQuotes: Map<string, string>; modelId: string }) {
  const usd = priceQuotes.get(modelId);
  if (!usd) return null;
  return <span className="shrink-0 font-mono text-xs text-muted-foreground">{usd}</span>;
}

/** Label first, id second (mono); custom ids show "Custom model". */
export function ModelSummary({
  model,
  modelId,
  isDefault = false,
  idClassName,
}: {
  model: ModelSpec | undefined;
  modelId: string;
  isDefault?: boolean;
  idClassName?: string;
}) {
  if (!modelId) {
    return <span className="text-sm text-muted-foreground">Choose a model</span>;
  }
  return (
    <span className="flex min-w-0 flex-col">
      <span className="flex items-center gap-1.5 truncate text-sm text-foreground">
        {model ? model.label : "Custom model"}
        {isDefault ? <span className="text-xs text-muted-foreground">· Default</span> : null}
      </span>
      <span className={cn("truncate font-mono text-xs text-muted-foreground", idClassName)}>{modelId}</span>
    </span>
  );
}
