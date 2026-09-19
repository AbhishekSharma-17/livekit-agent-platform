"use client";

import * as React from "react";
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
import { useCreateKb, useProviders } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";

export function CreateKbDialog() {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [embedderId, setEmbedderId] = React.useState("fastembed-embedding");
  const providersQuery = useProviders();
  const createKb = useCreateKb();

  const embedders = (providersQuery.data?.providers ?? []).filter((p) => p.kind === "embedding" && p.status === "mvp");

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    try {
      const kb = await createKb.mutateAsync({ name: name.trim(), description: description.trim(), embedder_id: embedderId });
      setName("");
      setDescription("");
      setOpen(false);
      toast.success(`Knowledge base "${kb.name}" created.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button">
          <PlusIcon className="size-4" /> New knowledge base
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>New knowledge base</DialogTitle>
            <DialogDescription>Documents are chunked and embedded on upload.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label htmlFor={`${uid}-name`} className="mb-1 block text-sm font-medium">
                Name
              </label>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Policy handbook"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-description`} className="mb-1 block text-sm font-medium">
                Description
              </label>
              <Textarea id={`${uid}-description`} value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <div>
              <label htmlFor={`${uid}-embedder`} className="mb-1 block text-sm font-medium">
                Embedder
              </label>
              <Select value={embedderId} onValueChange={setEmbedderId}>
                <SelectTrigger id={`${uid}-embedder`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {embedders.map((embedder) => (
                    <SelectItem key={embedder.id} value={embedder.id}>
                      {embedder.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={createKb.isPending}>
              {createKb.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
