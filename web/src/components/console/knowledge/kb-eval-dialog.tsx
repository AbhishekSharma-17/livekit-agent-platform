"use client";

import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useKbDocuments, usePutKbEvals } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import type { KbEvalIn, KbEvalOut } from "@/contracts/lkap-contracts";

/** A row being edited; `key` is local-only (React identity), never sent to the api. */
interface DraftRow {
  key: string;
  question: string;
  expected_document_id: string | null;
  expected_text: string;
  tagsText: string;
}

let rowSeq = 0;
function nextKey() {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

function toDraft(row: KbEvalOut): DraftRow {
  return {
    key: nextKey(),
    question: row.question,
    expected_document_id: row.expected_document_id ?? null,
    expected_text: row.expected_text ?? "",
    tagsText: (row.tags ?? []).join(", "),
  };
}

function emptyDraft(): DraftRow {
  return { key: nextKey(), question: "", expected_document_id: null, expected_text: "", tagsText: "" };
}

/** A row needs a question and at least one of an expected document or expected text (contract `KbEvalIn`). */
function rowIssue(row: DraftRow): string | null {
  if (row.question.trim() === "") return "Needs a question.";
  if (!row.expected_document_id && row.expected_text.trim() === "") {
    return "Needs an expected document, expected text, or both.";
  }
  return null;
}

function toPayload(row: DraftRow): KbEvalIn {
  // `tags` is generated as a fixed-length tuple union (json-schema-to-typescript's
  // rendering of `maxItems: 20`), not `string[]` — the runtime shape is a plain
  // array either way, so this is a type-level cast, not a behavior change.
  const tags = row.tagsText
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0) as KbEvalIn["tags"];
  return {
    question: row.question.trim(),
    expected_document_id: row.expected_document_id,
    expected_text: row.expected_text.trim() === "" ? null : row.expected_text.trim(),
    tags,
  };
}

/**
 * `kb-eval-dialog.tsx` (docs/v5/PLAN-V5.md V5-10): edits the whole golden-question
 * set at once — `PUT .../evals` replaces it wholesale, so this dialog seeds
 * from the stored set, lets the builder add/edit/remove rows, and saves every
 * row back together. A question names either the document the right answer
 * comes from, a passage it contains, or both (`KbEvalIn`).
 */
export function KbEvalDialog({
  kbId,
  open,
  onOpenChange,
  evals,
}: {
  kbId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  evals: KbEvalOut[];
}) {
  const [rows, setRows] = React.useState<DraftRow[]>([]);
  const documentsQuery = useKbDocuments(kbId);
  const putEvals = usePutKbEvals(kbId);

  // Read through a ref, not a dependency: `evals` is a fresh array reference
  // on every render while its query is loading (`evalsQuery.data?.items ?? []`
  // in the caller) and again whenever react-query refetches it in the
  // background (e.g. `refetchOnWindowFocus`) — depending on it directly would
  // wipe whatever the admin is mid-typing every time that happens. Seed only
  // on the open transition (`false` → `true`), from whatever `evals` holds at
  // that moment.
  const evalsRef = React.useRef(evals);
  evalsRef.current = evals;
  React.useEffect(() => {
    if (open) {
      const current = evalsRef.current;
      setRows(current.length > 0 ? current.map(toDraft) : [emptyDraft()]);
    }
  }, [open]);

  const documents = documentsQuery.data?.items ?? [];
  const issues = rows.map(rowIssue);
  const hasIssues = issues.some((issue) => issue !== null);

  function updateRow(key: string, patch: Partial<DraftRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function removeRow(key: string) {
    setRows((current) => current.filter((row) => row.key !== key));
  }

  async function handleSave() {
    if (hasIssues) {
      toast.error("Fix the highlighted questions before saving.");
      return;
    }
    try {
      await putEvals.mutateAsync({ items: rows.map(toPayload) });
      toast.success(`Saved ${rows.length === 1 ? "1 question" : `${rows.length} questions`}.`);
      onOpenChange(false);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Evaluation questions</DialogTitle>
          <DialogDescription>
            Each question is found when the agent&rsquo;s top matches include the expected document, contain
            the expected text, or both. Saving replaces the whole list.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            {rows.map((row, index) => {
              const issue = issues[index];
              return (
                <div key={row.key} className="flex flex-col gap-3 rounded-lg border border-border p-3">
                  <div className="flex items-start justify-between gap-2">
                    <Field
                      label={`Question ${index + 1}`}
                      htmlFor={`${row.key}-question`}
                      className="min-w-0 flex-1"
                    >
                      <Input
                        id={`${row.key}-question`}
                        value={row.question}
                        onChange={(e) => updateRow(row.key, { question: e.target.value })}
                        placeholder="Is water damage from a burst pipe covered?"
                      />
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove question ${index + 1}`}
                      className="mt-6"
                      onClick={() => removeRow(row.key)}
                    >
                      <Icon as={Trash2Icon} size="sm" />
                    </Button>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field
                      label="Expected document"
                      htmlFor={`${row.key}-doc`}
                      hint="The right answer should come from this file."
                      optional
                    >
                      <Select
                        value={row.expected_document_id ?? "__none__"}
                        onValueChange={(value) =>
                          updateRow(row.key, { expected_document_id: value === "__none__" ? null : value })
                        }
                      >
                        <SelectTrigger id={`${row.key}-doc`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">Any document</SelectItem>
                          {documents.map((doc) => (
                            <SelectItem key={doc.id} value={doc.id}>
                              {doc.filename}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field
                      label="Tags"
                      htmlFor={`${row.key}-tags`}
                      hint="Comma-separated, e.g. hindi, water-damage."
                      optional
                    >
                      <Input
                        id={`${row.key}-tags`}
                        value={row.tagsText}
                        onChange={(e) => updateRow(row.key, { tagsText: e.target.value })}
                        placeholder="water-damage"
                      />
                    </Field>
                  </div>

                  <Field
                    label="Expected text"
                    htmlFor={`${row.key}-text`}
                    hint="A passage the right answer should contain."
                    optional
                    error={issue ?? undefined}
                  >
                    <Textarea
                      id={`${row.key}-text`}
                      rows={2}
                      value={row.expected_text}
                      onChange={(e) => updateRow(row.key, { expected_text: e.target.value })}
                      placeholder="A burst pipe is covered when the water loss was sudden and accidental."
                    />
                  </Field>
                </div>
              );
            })}

            <Button type="button" variant="outline" onClick={() => setRows((current) => [...current, emptyDraft()])}>
              <Icon as={PlusIcon} size="sm" /> Add question
            </Button>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={() => void handleSave()} disabled={putEvals.isPending || rows.length === 0}>
            {putEvals.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
