"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RadioGroup } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/shared";
import { useCreateAgent, usePacks, useProviders } from "@/components/console/lib/api-hooks";
import { errorMessage, ErrorBanner } from "@/components/console/shared/error-banner";
import { PackCard } from "@/components/console/agents/create/pack-card";
import type { PackManifest } from "@/contracts/lkap-contracts";

const GENERIC_PACK_ID = "generic";

/** `generic` ("Blank agent") sits first (§4.2); the rest keep the api's order. */
function orderPacks(manifests: PackManifest[]): PackManifest[] {
  return [...manifests].sort((a, b) => {
    if (a.id === GENERIC_PACK_ID) return -1;
    if (b.id === GENERIC_PACK_ID) return 1;
    return 0;
  });
}

/**
 * `/console/agents/new` (docs/UI_UX_SPEC.md §4.2): a page, not a modal. Step 1
 * picks a pack template, step 2 names the agent, then `POST /v1/agents` with
 * `config: null` seeds the config from the pack manifest server-side.
 */
export function CreateAgentFlow() {
  const router = useRouter();
  const uid = React.useId();
  const packsQuery = usePacks();
  const providersQuery = useProviders();
  const createAgent = useCreateAgent();

  const [packId, setPackId] = React.useState("");
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [nameTouched, setNameTouched] = React.useState(false);

  const packs = React.useMemo(() => orderPacks(packsQuery.data?.items.map((item) => item.manifest) ?? []), [
    packsQuery.data,
  ]);
  const vendors = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const spec of providersQuery.data?.providers ?? []) map.set(spec.id, spec.vendor);
    return map;
  }, [providersQuery.data]);

  React.useEffect(() => {
    if (!packId && packs.length > 0) setPackId(packs[0].id);
  }, [packId, packs]);

  const selectedPack = packs.find((pack) => pack.id === packId);
  const nameError = nameTouched && name.trim().length === 0 ? "Name is required." : null;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setNameTouched(true);
    if (name.trim().length === 0) return;
    if (!packId) {
      toast.error("Choose a pack to start from.");
      return;
    }
    try {
      const agent = await createAgent.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        pack_id: packId,
        config: null,
      });
      toast.success(`Created from ${selectedPack?.name ?? packId}.`);
      router.push(`/console/agents/${agent.id}?section=providers`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (packsQuery.isError) {
    return (
      <ErrorBanner message={`Could not load packs: ${errorMessage(packsQuery.error)}`} onRetry={() => packsQuery.refetch()} />
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex max-w-[720px] flex-col gap-8">
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em]">Start from a pack</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Each pack seeds a working pipeline, instructions and tools. You can change everything afterwards.
          </p>
        </div>
        {packsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <RadioGroup value={packId} onValueChange={setPackId} className="gap-3" aria-label="Pack template">
            {packs.map((manifest) => (
              <PackCard key={manifest.id} manifest={manifest} vendors={vendors} selected={manifest.id === packId} />
            ))}
          </RadioGroup>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em]">Name it</h2>
        </div>
        <Field label="Name" htmlFor={`${uid}-name`} required error={nameError}>
          <Input
            id={`${uid}-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            onBlur={() => setNameTouched(true)}
            placeholder="Claims intake"
            autoComplete="off"
          />
        </Field>
        <Field
          label="Description"
          htmlFor={`${uid}-description`}
          optional
          hint="Shown to callers on the call page."
        >
          <Textarea
            id={`${uid}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What this agent is for"
            rows={3}
          />
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={createAgent.isPending || !packId}>
          {createAgent.isPending ? "Creating…" : "Create agent"}
        </Button>
      </div>
    </form>
  );
}
