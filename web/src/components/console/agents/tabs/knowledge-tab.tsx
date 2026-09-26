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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useKbs } from "@/components/console/lib/api-hooks";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import { DetailsDisclosure } from "@/components/console/sessions/details-disclosure";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { pluralize } from "@/lib/format";

/** `KnowledgeConfig.rerank` is `Literal["none","local"] | str`; everything else (a `connection:<id>` reranker, V5-24) reads as "local" here until that package adds its own picker. */
function rerankOnServer(value: string | undefined): boolean {
  return value === "local" || (value !== undefined && value !== "none");
}

/**
 * "Knowledge (section)" (docs/UI_UX_SPEC.md §4.7, §7.6). Unchanged by the v2
 * amendments. Uploading/searching documents happens on the dedicated
 * `/console/knowledge` pages; this tab only attaches existing knowledge
 * bases to the agent and sets retrieval behaviour.
 */
export function KnowledgeTab() {
  const topKId = React.useId();
  const minScoreId = React.useId();
  const maxTokensId = React.useId();
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const kbIds = watch("config.knowledge.kb_ids");
  const minScore = watch("config.knowledge.min_score");
  const rerank = watch("config.knowledge.rerank");
  const prefetch = watch("config.knowledge.prefetch");
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

        <SectionRow className="flex flex-col gap-1.5">
          <span className="text-sm font-medium leading-5">Search mode</span>
          <Controller
            control={control}
            name="config.knowledge.mode"
            render={({ field }) => (
              <RadioGroup
                value={field.value ?? "hybrid"}
                onValueChange={field.onChange}
                aria-label="Search mode"
                className="gap-2.5"
                data-issue-path="knowledge.mode"
              >
                <label htmlFor="knowledge-mode-hybrid" className="flex items-start gap-2 text-sm">
                  <RadioGroupItem id="knowledge-mode-hybrid" value="hybrid" className="mt-0.5" />
                  <span>
                    Hybrid
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      Also matches exact words, like policy numbers or form names.
                    </span>
                  </span>
                </label>
                <label htmlFor="knowledge-mode-vector" className="flex items-start gap-2 text-sm">
                  <RadioGroupItem id="knowledge-mode-vector" value="vector" className="mt-0.5" />
                  <span>
                    Vector only
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      Matches by meaning only, even when the wording is different.
                    </span>
                  </span>
                </label>
              </RadioGroup>
            )}
          />
        </SectionRow>

        <SectionRow className="flex flex-col gap-2">
          <Field
            inline
            label="Only use strong matches"
            htmlFor="knowledge-min-score-toggle"
            hint="Hides weaker matches instead of sending everything the agent found."
          >
            <Switch
              id="knowledge-min-score-toggle"
              checked={minScore !== null && minScore !== undefined}
              onCheckedChange={(checked) => setValue("config.knowledge.min_score", checked ? 0.5 : null, { shouldDirty: true })}
              data-issue-path="knowledge.min_score"
            />
          </Field>
          {minScore !== null && minScore !== undefined ? (
            <div className="flex items-center gap-3 pl-0.5">
              <Controller
                control={control}
                name="config.knowledge.min_score"
                render={({ field }) => (
                  <Slider
                    id={minScoreId}
                    aria-label="Minimum match strength"
                    className="max-w-xs"
                    min={0}
                    max={1}
                    step={0.05}
                    value={[field.value ?? 0.5]}
                    onValueChange={([next]) => field.onChange(next)}
                    data-issue-path="knowledge.min_score"
                  />
                )}
              />
              <span className="w-10 shrink-0 text-right text-sm tabular-nums text-muted-foreground">
                {Math.round((minScore ?? 0) * 100)}%
              </span>
            </div>
          ) : null}
        </SectionRow>

        <SectionRow>
          <Field
            inline
            label="Prepare answers early"
            htmlFor="knowledge-prefetch"
            hint="Starts searching while the caller is still talking, so the answer is usually ready by the time they finish."
          >
            <Controller
              control={control}
              name="config.knowledge.prefetch"
              render={({ field }) => (
                <Switch
                  id="knowledge-prefetch"
                  checked={field.value ?? true}
                  onCheckedChange={field.onChange}
                  data-issue-path="knowledge.prefetch"
                />
              )}
            />
          </Field>
        </SectionRow>

        <SectionRow className="flex flex-col gap-1.5">
          <Field
            label="Re-rank results"
            htmlFor="knowledge-rerank"
            hint={
              prefetch ?? true
                ? "Double-checks the closest matches more carefully before answering."
                : "Double-checks the closest matches more carefully — adds a short delay to every reply unless Prepare answers early is on."
            }
          >
            <Select
              value={rerankOnServer(rerank) ? "local" : "none"}
              onValueChange={(value) => setValue("config.knowledge.rerank", value, { shouldDirty: true })}
            >
              <SelectTrigger id="knowledge-rerank" className="w-full sm:w-64" data-issue-path="knowledge.rerank">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Off</SelectItem>
                <SelectItem value="local">On this server</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </SectionRow>

        <SectionRow className="flex flex-col gap-1.5">
          <span className="text-sm font-medium leading-5">Context for follow-ups</span>
          <Controller
            control={control}
            name="config.knowledge.query_mode"
            render={({ field }) => (
              <RadioGroup
                value={field.value ?? "conversation"}
                onValueChange={field.onChange}
                aria-label="Context used to search on follow-up questions"
                className="gap-2.5"
                data-issue-path="knowledge.query_mode"
              >
                <label htmlFor="knowledge-query-mode-conversation" className="flex items-start gap-2 text-sm">
                  <RadioGroupItem id="knowledge-query-mode-conversation" value="conversation" className="mt-0.5" />
                  <span>
                    This message and what came right before
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      Better for short follow-ups like &quot;and what about theft?&quot;
                    </span>
                  </span>
                </label>
                <label htmlFor="knowledge-query-mode-last-turn" className="flex items-start gap-2 text-sm">
                  <RadioGroupItem id="knowledge-query-mode-last-turn" value="last_turn" className="mt-0.5" />
                  <span>
                    Just this message
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      Simpler, but can miss the point of a short follow-up.
                    </span>
                  </span>
                </label>
              </RadioGroup>
            )}
          />
        </SectionRow>

        <SectionRow>
          <Field
            inline
            label="Skip short replies"
            htmlFor="knowledge-skip-short-turns"
            hint={'Don’t search for things like "yes", "okay" or a lone number.'}
          >
            <Controller
              control={control}
              name="config.knowledge.skip_short_turns"
              render={({ field }) => (
                <Switch
                  id="knowledge-skip-short-turns"
                  checked={field.value ?? true}
                  onCheckedChange={field.onChange}
                  data-issue-path="knowledge.skip_short_turns"
                />
              )}
            />
          </Field>
        </SectionRow>

        <SectionRow>
          <Field
            label="Most text to include"
            htmlFor={maxTokensId}
            hint="Upper limit on how much retrieved text is added to one reply (in tokens); higher can find more but costs more."
          >
            <Controller
              control={control}
              name="config.knowledge.max_inject_tokens"
              render={({ field }) => (
                <Input
                  id={maxTokensId}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={100}
                  className="w-28"
                  value={field.value ?? 1200}
                  onChange={(event) => field.onChange(Number(event.target.value))}
                  data-issue-path="knowledge.max_inject_tokens"
                />
              )}
            />
          </Field>
        </SectionRow>

        <SectionRow>
          <DetailsDisclosure label="How this works">
            <p className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
              Hybrid mode fuses a keyword search with an embedding (meaning) search and ranks the
              combined list. Turning on re-ranking rescores the closest matches with a second, more
              careful model before the minimum-match floor is applied. The floor compares each
              match&rsquo;s final score (0–1) after whichever of those stages ran.
            </p>
          </DetailsDisclosure>
        </SectionRow>
      </Section>
    </div>
  );
}
