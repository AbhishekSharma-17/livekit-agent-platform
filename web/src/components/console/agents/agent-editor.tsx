"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormProvider, useForm, type FieldErrors } from "react-hook-form";
import { toast } from "sonner";
import { ExternalLinkIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgent, useUpdateAgent, useValidateAgent, useDeleteAgent } from "@/components/console/lib/api-hooks";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { DEFAULT_CAPABILITIES, DEFAULT_KNOWLEDGE, DEFAULT_TOOLS, DEFAULT_VOICE } from "@/components/console/agents/defaults";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { ValidationBanner } from "@/components/console/shared/validation-banner";
import { firstErrorMessage } from "@/components/console/lib/form-errors";
import { ProvidersTab } from "@/components/console/agents/tabs/providers-tab";
import { InstructionsTab } from "@/components/console/agents/tabs/instructions-tab";
import { PanelTab } from "@/components/console/agents/tabs/panel-tab";
import { ToolsTab } from "@/components/console/agents/tabs/tools-tab";
import { KnowledgeTab } from "@/components/console/agents/tabs/knowledge-tab";
import type { AgentOut, ValidationResult } from "@/contracts/lkap-contracts";

function toFormValues(agent: AgentOut): AgentEditorForm {
  return {
    name: agent.name,
    description: agent.description,
    ui_panel_id: agent.ui_panel_id,
    config: {
      instructions: agent.config.instructions,
      pipeline: { ...agent.config.pipeline, mode: agent.config.pipeline.mode ?? "cascaded" },
      voice: { ...DEFAULT_VOICE, ...agent.config.voice },
      capabilities: { ...DEFAULT_CAPABILITIES, ...agent.config.capabilities },
      tools: { ...DEFAULT_TOOLS, ...agent.config.tools },
      knowledge: { ...DEFAULT_KNOWLEDGE, ...agent.config.knowledge },
      pack_settings: agent.config.pack_settings ?? {},
      timezone: agent.config.timezone ?? "UTC",
    },
  };
}

export function AgentEditor({ agentId }: { agentId: string }) {
  const { data: agent, isLoading, isError, error, refetch } = useAgent(agentId);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (isError || !agent) {
    return <ErrorBanner message={`Could not load this agent: ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  return <AgentEditorFormBody agent={agent} />;
}

function AgentEditorFormBody({ agent }: { agent: AgentOut }) {
  const router = useRouter();
  const updateAgent = useUpdateAgent(agent.id);
  const validateAgent = useValidateAgent(agent.id);
  const deleteAgent = useDeleteAgent();
  const [validation, setValidation] = React.useState<ValidationResult | null>(null);

  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(agent),
  });

  React.useEffect(() => {
    form.reset(toFormValues(agent));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, agent.config_version]);

  async function onSubmit(values: AgentEditorForm) {
    // The inactive mode's slots (e.g. leftover `stt`/`llm`/`tts` after
    // switching to realtime) stay in form state so a user can switch back
    // without re-configuring them, but the api validates every non-null
    // `ProviderRef` at save (docs/CONTRACTS.md §6) — a half-configured
    // provider left over from the other mode would fail validation with no
    // way for the user to see why. Null them out only in the payload we
    // send, not in the form itself.
    const pipeline = { ...values.config.pipeline };
    if (pipeline.mode === "cascaded") {
      pipeline.realtime = null;
    } else {
      pipeline.stt = null;
      pipeline.llm = null;
      pipeline.tts = null;
    }

    try {
      const updated = await updateAgent.mutateAsync({
        name: values.name,
        description: values.description,
        ui_panel_id: values.ui_panel_id,
        config: { v: 1, ...values.config, pipeline },
      });
      form.reset(toFormValues(updated));
      toast.success("Saved.");
      try {
        const result = await validateAgent.mutateAsync();
        setValidation(result);
      } catch {
        // validation is best-effort; a failed validate call shouldn't hide a successful save
      }
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  function onInvalid(errors: FieldErrors<AgentEditorForm>) {
    toast.error(firstErrorMessage(errors) ?? "Fix the highlighted fields before saving.");
  }

  async function togglePublished(next: boolean) {
    try {
      const updated = await updateAgent.mutateAsync({ published: next });
      form.setValue("name", updated.name);
      toast.success(next ? "Published." : "Unpublished.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(onSubmit, onInvalid)}>
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <Input
              {...form.register("name")}
              className="h-auto border-none bg-transparent px-0 text-xl font-semibold shadow-none focus-visible:ring-0"
              aria-label="Agent name"
            />
            {form.formState.errors.name ? (
              <p className="text-xs text-destructive">{form.formState.errors.name.message}</p>
            ) : null}
            <p className="text-sm text-muted-foreground">/{agent.slug}</p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={agent.published} onCheckedChange={togglePublished} />
              {agent.published ? "Published" : "Draft"}
            </label>
            <Button asChild variant="outline" size="sm">
              <Link href={`/s/${agent.slug}?mode=test`} target="_blank" rel="noopener noreferrer">
                Test call <ExternalLinkIcon className="size-3.5" />
              </Link>
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" variant="ghost" size="icon-sm" aria-label="Delete agent">
                  <Trash2Icon className="size-3.5" />
                </Button>
              }
              title={`Delete "${agent.name}"?`}
              description="This permanently deletes the agent and its private tool bindings."
              confirmLabel="Delete"
              onConfirm={async () => {
                try {
                  await deleteAgent.mutateAsync(agent.id);
                  toast.success("Agent deleted.");
                  router.push("/console");
                } catch (error) {
                  toast.error(errorMessage(error));
                }
              }}
            />
            <Button type="submit" disabled={updateAgent.isPending}>
              {updateAgent.isPending ? "Saving…" : "Save & validate"}
            </Button>
          </div>
        </div>

        <div className="mb-1 text-xs text-muted-foreground">
          Description
          <Input
            {...form.register("description")}
            className="mt-1 h-8"
            placeholder="What this agent is for (optional)"
          />
        </div>

        {validation ? (
          <div className="my-4">
            <ValidationBanner result={validation} />
          </div>
        ) : null}

        <Tabs defaultValue="providers" className="mt-4">
          <TabsList>
            <TabsTrigger value="providers">Providers</TabsTrigger>
            <TabsTrigger value="instructions">Instructions &amp; voice</TabsTrigger>
            <TabsTrigger value="panel">Panel</TabsTrigger>
            <TabsTrigger value="tools">Tools</TabsTrigger>
            <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
          </TabsList>
          <TabsContent value="providers" className="mt-4">
            <ProvidersTab />
          </TabsContent>
          <TabsContent value="instructions" className="mt-4">
            <InstructionsTab />
          </TabsContent>
          <TabsContent value="panel" className="mt-4">
            <PanelTab />
          </TabsContent>
          <TabsContent value="tools" className="mt-4">
            <ToolsTab agent={agent} />
          </TabsContent>
          <TabsContent value="knowledge" className="mt-4">
            <KnowledgeTab />
          </TabsContent>
        </Tabs>
      </form>
    </FormProvider>
  );
}
