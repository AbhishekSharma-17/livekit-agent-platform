"use client";

import * as React from "react";
import { toast } from "sonner";
import { PlusIcon, UploadIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useUploadDataset } from "@/components/console/lib/api-hooks";
import { DATASET_UPLOAD_MAX_MB, datasetUploadCapErrorMessage, isDatasetFileOverUploadCap } from "@/components/console/lib/upload";
import { errorMessage } from "@/components/console/shared/error-banner";
import { datasetFormatFromFilename, sniffDatasetFileColumns } from "@/components/console/datasets/dataset-columns";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { LoadingRow } from "@/components/shared/loading-state";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { useWriteGate } from "@/components/console/tools/write-gate";

const ACCEPT_ATTR = ".csv,.tsv,.json";

/**
 * `lkap_contracts.datasets.DatasetKeyType` — not its own exported TS type (only inlined into
 * `DatasetColumn.type`/`DatasetKeyColumn.type`), so mirrored here the way `tool-context.ts`
 * mirrors other contract literals.
 */
type DatasetKeyType = "string" | "phone" | "email" | "number";

const KEY_TYPE_LABEL: Record<DatasetKeyType, string> = {
  string: "Text",
  phone: "Phone number",
  email: "Email",
  number: "Number",
};

interface ColumnDraft {
  name: string;
  matchOn: boolean;
  type: DatasetKeyType;
}

/**
 * "Upload a lookup table" (V6-16 dataset, V6-19's console): a file, a name, then which
 * columns lookups match on and how each is compared — the api's `key_columns` (ask #104).
 * The column list is sniffed client-side from the file's header row (`dataset-columns.ts`);
 * the api still decides for real when it parses the upload.
 */
export function UploadDatasetDialog({ variant = "primary" }: { variant?: "primary" | "secondary" } = {}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [file, setFile] = React.useState<File | null>(null);
  const [name, setName] = React.useState("");
  const [columns, setColumns] = React.useState<ColumnDraft[]>([]);
  const [sniffing, setSniffing] = React.useState(false);
  const [fileError, setFileError] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const upload = useUploadDataset();
  const gate = useWriteGate();

  function reset() {
    setFile(null);
    setName("");
    setColumns([]);
    setFileError(null);
  }

  async function handleFile(picked: File | null) {
    setFileError(null);
    if (!picked) {
      setFile(null);
      setColumns([]);
      return;
    }
    if (isDatasetFileOverUploadCap(picked)) {
      setFileError(datasetUploadCapErrorMessage(picked.name));
      setFile(null);
      setColumns([]);
      return;
    }
    if (!datasetFormatFromFilename(picked.name)) {
      setFileError(`"${picked.name}" isn't a .csv, .tsv or .json file.`);
      setFile(null);
      setColumns([]);
      return;
    }
    setFile(picked);
    if (name.trim() === "") setName(picked.name.replace(/\.(csv|tsv|json)$/i, ""));
    setSniffing(true);
    try {
      const headers = await sniffDatasetFileColumns(picked);
      setColumns(headers.map((header) => ({ name: header, matchOn: false, type: "string" })));
      if (headers.length === 0) {
        setFileError("Couldn't read this file's columns — check it has a header row.");
      }
    } finally {
      setSniffing(false);
    }
  }

  function toggleMatchOn(name_: string, matchOn: boolean) {
    setColumns((cols) => cols.map((c) => (c.name === name_ ? { ...c, matchOn } : c)));
  }

  function setType(name_: string, type: DatasetKeyType) {
    setColumns((cols) => cols.map((c) => (c.name === name_ ? { ...c, type } : c)));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      setFileError("Choose a file to upload.");
      return;
    }
    const keyColumns = Object.fromEntries(columns.filter((c) => c.matchOn).map((c) => [c.name, c.type]));
    if (Object.keys(keyColumns).length === 0) {
      toast.error("Pick at least one column lookups can match on.");
      return;
    }
    try {
      const dataset = await upload.mutateAsync({ name: name.trim() || file.name, keyColumns, file });
      setOpen(false);
      reset();
      toast.success(`"${dataset.name}" is importing.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  // Viewers get a read-only note in place of the page's primary (decision D12).
  if (!gate.show) return <ReadOnlyNote>{readOnlyCopy("builder", "upload lookup tables")}</ReadOnlyNote>;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant={variant} disabled={gate.pending}>
          <PlusIcon aria-hidden="true" /> Upload a lookup table
        </Button>
      </DialogTrigger>
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader>
            <DialogTitle>Upload a lookup table</DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              A .csv, .tsv or .json file, at most {DATASET_UPLOAD_MAX_MB} MB. Nothing in a cell is ever run — it&rsquo;s read as
              plain text.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="gap-5">
            <div className="flex flex-col gap-1.5">
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT_ATTR}
                className="hidden"
                onChange={(event) => void handleFile(event.target.files?.[0] ?? null)}
              />
              <div
                role="button"
                tabIndex={0}
                aria-label="Choose a file to upload"
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
                className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-6 text-center transition-colors duration-(--duration-fast) hover:bg-muted"
              >
                <span className="flex size-10 items-center justify-center rounded bg-muted text-text-secondary">
                  <Icon as={UploadIcon} size="tile" />
                </span>
                <p className="text-body text-foreground">{file ? file.name : "Choose a .csv, .tsv or .json file"}</p>
              </div>
              {fileError ? <p className="text-label text-destructive-text">{fileError}</p> : null}
            </div>

            <Field label="Name" htmlFor={`${uid}-name`} required>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Policy directory"
              />
            </Field>

            {sniffing ? (
              <LoadingRow label="Reading the file’s columns…" />
            ) : columns.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                <span className="text-body font-medium text-foreground">Columns</span>
                <p className="text-label text-text-secondary">
                  Pick which columns a lookup must match on, and how each is compared.
                </p>
                <div className="flex flex-col gap-1 rounded border border-border p-2">
                  {columns.map((column) => (
                    <div key={column.name} className="flex flex-wrap items-center gap-3 py-1">
                      <label className="flex min-w-0 flex-1 items-center gap-2 text-body">
                        <input
                          type="checkbox"
                          className="size-4 shrink-0"
                          checked={column.matchOn}
                          onChange={(e) => toggleMatchOn(column.name, e.target.checked)}
                        />
                        <span className="truncate font-mono text-label">{column.name}</span>
                      </label>
                      {column.matchOn ? (
                        <Select value={column.type} onValueChange={(next) => setType(column.name, next as DatasetKeyType)}>
                          <SelectTrigger className="w-40 shrink-0 max-sm:w-full" aria-label={`${column.name} — type`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(Object.keys(KEY_TYPE_LABEL) as DatasetKeyType[]).map((type) => (
                              <SelectItem key={type} value={type}>
                                {KEY_TYPE_LABEL[type]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!file || sniffing} busy={upload.isPending} busyLabel="Uploading…">
              Upload
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
