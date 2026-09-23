"use client";

import * as React from "react";
import Link from "next/link";
import { Controller, useFormContext } from "react-hook-form";
import { BookOpenIcon, SearchIcon } from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useKbs } from "@/components/console/lib/api-hooks";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { pluralize } from "@/lib/format";

/**
 * "Knowledge (section)" (docs/UI_UX_SPEC.md §4.7, §7.6). Unchanged by the v2
 * amendments. Uploading/searching documents happens on the dedicated
 * `/console/knowledge` pages; this tab only attaches existing knowledge
 * bases to the agent and sets retrieval behaviour.
 */
export function KnowledgeTab() {
  const topKId = React.useId();
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const kbIds = watch("config.knowledge.kb_ids");
  const kbsQuery = useKbs();
  const [query, setQuery] = React.useState("");

  function toggle(id: string, attached: boolean) {
    const current = kbIds ?? [];
    setValue(
      "config.knowledge.kb_ids",
      attached ? Array.from(new Set([...current, id])) : current.filter((existing) => existing !== id),
      { shouldDirty: true },
    );
  }

  const allKbs = kbsQuery.data?.items ?? [];
  const filteredKbs =
    query.trim() === ""
      ? allKbs
      : allKbs.filter((kb) => kb.name.toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="knowledge-attached"
        title="Attached knowledge bases"
        description="The agent can search these during a call."
      >
        {allKbs.length > 0 ? (
          <SectionRow>
            <InputGroup className="max-w-sm">
              <InputGroupAddon>
                <Icon as={SearchIcon} size="sm" />
              </InputGroupAddon>
              <InputGroupInput
                placeholder="Search knowledge bases"
                aria-label="Search knowledge bases"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </InputGroup>
          </SectionRow>
        ) : null}

        {kbsQuery.isLoading ? (
          <SectionRow>
            <Skeleton className="h-10 w-full" />
          </SectionRow>
        ) : kbsQuery.isError ? (
          <SectionRow>
            <ErrorBanner message={errorMessage(kbsQuery.error)} onRetry={() => kbsQuery.refetch()} />
          </SectionRow>
        ) : allKbs.length === 0 ? (
          <SectionRow>
            <EmptyState
              icon={BookOpenIcon}
              title="No knowledge bases yet"
              description="Create one under Knowledge, upload documents, then attach it here."
              action={
                <Link href="/console/knowledge" className="text-sm font-medium text-brand-text hover:underline">
                  Go to Knowledge
                </Link>
              }
            />
          </SectionRow>
        ) : filteredKbs.length === 0 ? (
          <SectionRow>
            <p className="text-sm text-muted-foreground">No knowledge bases match &quot;{query}&quot;.</p>
          </SectionRow>
        ) : (
          filteredKbs.map((kb) => (
            <SectionRow key={kb.id}>
              <Field
                inline
                label={kb.name}
                htmlFor={`kb-${kb.id}`}
                hint={`${pluralize(kb.document_count, "document", "documents")} · ${pluralize(kb.chunk_count, "chunk", "chunks")} · ${embedderLabel(kb.embedder_id)}`}
              >
                <Switch
                  id={`kb-${kb.id}`}
                  checked={(kbIds ?? []).includes(kb.id)}
                  onCheckedChange={(checked) => toggle(kb.id, checked)}
                  data-issue-path="knowledge.kb_ids"
                />
              </Field>
            </SectionRow>
          ))
        )}

        <SectionRow>
          <Link
            href="/console/knowledge"
            className="text-[0.8125rem] font-medium text-muted-foreground hover:text-foreground hover:underline"
          >
            Manage knowledge bases
          </Link>
        </SectionRow>
      </Section>

      <Section
        id="knowledge-retrieval"
        title="Retrieval"
        description="How attached knowledge is used during a call."
      >
        <SectionRow>
          <Field
            inline
            label="Add the best matches to every turn"
            htmlFor="knowledge-auto-inject"
            hint="Sends the closest matches with each reply, in addition to the explicit search tool."
          >
            <Controller
              control={control}
              name="config.knowledge.auto_inject"
              render={({ field }) => (
                <Switch
                  id="knowledge-auto-inject"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  data-issue-path="knowledge.auto_inject"
                />
              )}
            />
          </Field>
        </SectionRow>
        <SectionRow>
          <Field label="Matches per turn" htmlFor={topKId} hint="Higher finds more but costs tokens.">
            <Controller
              control={control}
              name="config.knowledge.top_k"
              render={({ field }) => (
                <Input
                  id={topKId}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={10}
                  className="w-24"
                  value={field.value}
                  onChange={(event) => field.onChange(Number(event.target.value))}
                  data-issue-path="knowledge.top_k"
                />
              )}
            />
          </Field>
        </SectionRow>
      </Section>
    </div>
  );
}
