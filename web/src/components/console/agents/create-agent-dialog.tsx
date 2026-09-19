"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateAgent, usePacks } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";

/**
 * "agents list (create from pack, ...)" — `POST /v1/agents` with
 * `config: null` seeds `AgentConfig` from the chosen pack's manifest
 * (docs/CONTRACTS.md §8 seeding rule; W1-API-CORE applies it server-side).
 */
export function CreateAgentDialog() {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [packId, setPackId] = React.useState("");
  const packsQuery = usePacks();
  const createAgent = useCreateAgent();
  const router = useRouter();

  const packs = React.useMemo(() => packsQuery.data?.items ?? [], [packsQuery.data]);

  React.useEffect(() => {
    if (!packId && packs.length > 0) {
      setPackId(packs[0].manifest.id);
    }
  }, [packId, packs]);

  function reset() {
    setName("");
    setDescription("");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    if (packId === "") {
      toast.error("Choose a pack.");
      return;
    }
    try {
      const agent = await createAgent.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        pack_id: packId,
        config: null,
      });
      reset();
      setOpen(false);
      toast.success(`Agent "${agent.name}" created.`);
      router.push(`/console/agents/${agent.id}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button">
          <PlusIcon className="size-4" /> New agent
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create an agent</DialogTitle>
            <DialogDescription>Starts from a pack&apos;s recommended pipeline and instructions.</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div>
              <label htmlFor={`${uid}-name`} className="mb-1 block text-sm font-medium">
                Name
              </label>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Claims intake"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-description`} className="mb-1 block text-sm font-medium">
                Description
              </label>
              <Textarea
                id={`${uid}-description`}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What this agent is for (optional)"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-pack`} className="mb-1 block text-sm font-medium">
                Pack
              </label>
              {packsQuery.isError ? (
                <p className="text-sm text-destructive">Could not load packs: {errorMessage(packsQuery.error)}</p>
              ) : (
                <Select value={packId} onValueChange={setPackId} disabled={packsQuery.isLoading}>
                  <SelectTrigger id={`${uid}-pack`} className="w-full">
                    <SelectValue placeholder="Choose a pack" />
                  </SelectTrigger>
                  <SelectContent>
                    {packs.map((pack) => (
                      <SelectItem key={pack.manifest.id} value={pack.manifest.id}>
                        {pack.manifest.name} ({pack.manifest.id})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={createAgent.isPending}>
              {createAgent.isPending ? "Creating…" : "Create agent"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
