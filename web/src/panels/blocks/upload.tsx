"use client";

/**
 * `upload` block — the caller sends files or photos from their device
 * (`request_upload`, CONTRACTS-V2 §4.4, panels-and-capabilities.md B6,
 * V5-19 → V5-23).
 *
 * Renders from **state**, like every requestable block (`RequestableState`):
 * `status: "requested"` is the pending marker, `files`/`rejected` are
 * written by the worker as it receives and checks each file
 * (`agent/src/lkap_agent/ui/channel.py::receive_upload`) — a `block_submit`
 * never invents a file the worker has not verified. The picker's own file
 * list additionally shows what `useUploadQueue` (`composite/upload.ts`) is
 * still queuing or sending, and what it refused before a byte left the
 * browser (size and count only — the worker sniffs the real bytes for type,
 * so a client-side type guess would only ever be a false refusal,
 * `docs/v5/_asks.md` #131).
 *
 * The picked bytes go out through `sendUploadFile` (a `streamBytes` pump,
 * not `sendFile` — see `composite/upload.ts`'s docstring for why), one file
 * at a time. Once every file the caller wants to send appears in `files`,
 * `block_submit {values: {files: [asset_id, ...]}}` closes the request.
 *
 * A barge-in can cancel the block server-side (D-V5-34) while a pick or a
 * send is still in flight — the plain-words line for that
 * (`docs/v5/_asks.md` #130) is deliberately not "You dismissed this without
 * answering." (the caller didn't dismiss anything; the agent stopped
 * listening).
 *
 * This file also exports `FormFileField`, a form `file` field's control
 * (`blocks/form.tsx`'s `FormEditor` loads it lazily — `React.lazy(() =>
 * import("./upload"))` — precisely so that importing `form.tsx`, a common
 * block rendered on the composer preview and the console's read-only
 * snapshot, does not eagerly pull `@livekit/components-react` in; only a
 * `form` block that actually has a `file` field ever loads this chunk).
 * `useUploadQueue` (`composite/upload.ts`) itself needs no room-context
 * import at all — `room` is a plain parameter there — so only this file and
 * `blocks/index.tsx`'s own lazy `upload` entry carry the cost.
 */
import * as React from "react";
import { useEffect, useMemo, useRef } from "react";
import { useMaybeRoomContext } from "@livekit/components-react";
import { CameraIcon, PaperclipIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { AssetRef, UploadBlockState, UploadedFile, UploadRejection } from "@/contracts/lkap-contracts";
import { formatBytes } from "@/lib/format";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { DEFAULT_UPLOAD_ACCEPT, useUploadQueue, type UploadLimits, type UploadRoom } from "@/panels/composite/upload";
import { PanelEmpty } from "@/panels/generic/blocks";

import { FileTile } from "./gallery";
import { BlockFrame } from "./frame";
import type { FormFieldSpec } from "./form";
import type { BlockRenderProps } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `UploadBlockConfig` (`BlockSpec.config`), defensively — a block from an older save may lack a key. */
function uploadConfig(config: unknown): UploadLimits & { cameraCapture: boolean } {
  const raw = isRecord(config) ? config : {};
  const accept =
    Array.isArray(raw.accept) && raw.accept.every((a) => typeof a === "string") && raw.accept.length > 0
      ? (raw.accept as string[])
      : DEFAULT_UPLOAD_ACCEPT;
  const maxFiles = typeof raw.max_files === "number" && raw.max_files > 0 ? raw.max_files : 3;
  const maxBytes = typeof raw.max_bytes === "number" && raw.max_bytes > 0 ? raw.max_bytes : 10 * 1024 * 1024;
  return { accept, maxFiles, maxBytes, cameraCapture: raw.camera_capture === true };
}

/**
 * `{asset_id, mime}` → the `AssetRef` shape `FileTile` (`gallery.tsx`)
 * renders. `caption` stays `null` — every caller already shows the file's
 * name as its own text next to the thumbnail, and `AssetTile` would
 * otherwise repeat it a second time as a `<figcaption>`.
 */
function fileAsset(file: UploadedFile): AssetRef {
  return { asset_id: file.asset_id, kind: "upload", mime: file.mime, caption: null, meta: {}, ts: 0 };
}

/**
 * The picker, drop zone and per-file rows for a pending request. A fresh
 * instance per request (`UploadBlock`'s `requestKey`), like `FormEditor` and
 * `ChoicesEditor` — a new prompt never inherits a stale queue.
 */
function UploadPicker({
  blockId,
  config,
  files,
  rejected,
  panelAssets,
  perform,
}: {
  blockId: string;
  config: UploadLimits & { cameraCapture: boolean };
  files: UploadedFile[];
  rejected: UploadRejection[];
  panelAssets: Map<string, string>;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const room = useMaybeRoomContext() as UploadRoom | undefined;
  const { sending, error, submit, cancel: cancelRequest } = useBlockRequest(blockId, "requested", perform);
  const busy = sending !== null;
  const { rows: queueRows, addFiles, cancel: cancelQueue } = useUploadQueue({
    room,
    blockId,
    limits: config,
    acceptedCount: files.length,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  function onCancel() {
    if (busy) return;
    cancelQueue();
    void cancelRequest();
  }

  function onSend() {
    if (busy || files.length === 0) return;
    void submit({ files: files.map((f) => f.asset_id) });
  }

  return (
    <div
      data-slot="block-upload"
      className="flex flex-col gap-3"
      onDragOver={(event) => {
        if (!room) return;
        event.preventDefault();
      }}
      onDrop={(event) => {
        if (!room) return;
        event.preventDefault();
        if (event.dataTransfer.files.length > 0) addFiles(event.dataTransfer.files);
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={config.accept.join(",")}
        className="sr-only"
        aria-hidden="true"
        tabIndex={-1}
        onChange={(event) => {
          if (event.target.files && event.target.files.length > 0) addFiles(event.target.files);
          event.target.value = "";
        }}
      />
      {config.cameraCapture && (
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => {
            if (event.target.files && event.target.files.length > 0) addFiles(event.target.files);
            event.target.value = "";
          }}
        />
      )}
      <div
        data-slot="block-upload-dropzone"
        className="border-border bg-muted/20 flex flex-col items-center gap-2 rounded-md border border-dashed px-3 py-4 text-center"
      >
        <p className="text-muted-foreground text-[0.8125rem]">
          {room ? "Choose a file, or drag one here." : "Files can be sent during a call."}
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" size="sm" variant="outline" disabled={!room} onClick={() => fileInputRef.current?.click()}>
            <Icon as={PaperclipIcon} size="sm" />
            Choose a file
          </Button>
          {config.cameraCapture && (
            <Button type="button" size="sm" variant="outline" disabled={!room} onClick={() => cameraInputRef.current?.click()}>
              <Icon as={CameraIcon} size="sm" />
              Take a photo
            </Button>
          )}
        </div>
      </div>

      {(files.length > 0 || rejected.length > 0 || queueRows.length > 0) && (
        <ul data-slot="block-upload-files" className="flex flex-col gap-2">
          {files.map((file) => (
            <li key={file.asset_id} className="flex items-center gap-2.5">
              <div className="size-10 shrink-0">
                <FileTile asset={fileAsset(file)} url={panelAssets.get(file.asset_id)} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{file.name}</p>
                <p className="text-muted-foreground text-xs">{formatBytes(file.size)}</p>
              </div>
            </li>
          ))}
          {rejected.map((r, i) => (
            <li key={`server-${i}-${r.name}`} className="flex items-center gap-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{r.name}</p>
                <p className="text-danger-text text-xs">{r.message}</p>
              </div>
            </li>
          ))}
          {queueRows.map((row) => (
            <li key={row.key} className="flex items-center gap-2.5">
              {row.kind === "rejected" ? (
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{row.name}</p>
                  <p className="text-danger-text text-xs">{row.message}</p>
                </div>
              ) : row.kind === "active" ? (
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{row.name}</p>
                  <div className="bg-muted mt-1 h-1 w-full overflow-hidden rounded-full">
                    <div
                      className="bg-brand h-full origin-left rounded-full transition-transform duration-(--dur-2) ease-out"
                      style={{ transform: `scaleX(${Math.max(0.02, row.progress)})` }}
                    />
                  </div>
                </div>
              ) : (
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{row.name}</p>
                  <p className="text-muted-foreground text-xs">Waiting to send…</p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" onClick={onSend} disabled={busy || files.length === 0}>
          {sending === "submit" ? "Sending…" : "Send"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function SubmittedFiles({ files, panelAssets }: { files: UploadedFile[]; panelAssets: Map<string, string> }) {
  return (
    <div data-slot="block-upload-submitted" className="flex flex-col gap-2.5">
      <StatusChip tone="success" size="sm" dot>
        Sent
      </StatusChip>
      {files.length > 0 && (
        <ul className="grid grid-cols-3 gap-2">
          {files.map((file) => (
            <li key={file.asset_id} className="flex flex-col gap-1">
              <FileTile asset={fileAsset(file)} url={panelAssets.get(file.asset_id)} />
              <p className="text-muted-foreground truncate text-xs" title={file.name}>
                {file.name}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function UploadBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<UploadBlockState>) {
  const status = data.status ?? "idle";
  const files = Array.isArray(data.files) ? data.files : [];
  const rejected = Array.isArray(data.rejected) ? data.rejected : [];
  const config = uploadConfig(spec.config);
  // A new request (new prompt) remounts the picker with a fresh queue, like
  // `FormEditor`'s `requestKey` — `files`/`rejected` are deliberately not part
  // of the key: they grow *during* the same request as the worker verifies
  // what this picker just sent, and must not reset the in-flight queue.
  const requestKey = typeof data.prompt === "string" ? data.prompt : "";

  let body: React.ReactNode;
  if (status === "requested") {
    body = (
      <UploadPicker
        key={requestKey}
        blockId={spec.id}
        config={config}
        files={files}
        rejected={rejected}
        panelAssets={panel.assets}
        perform={panel.perform}
      />
    );
  } else if (status === "cancelled") {
    body = <PanelEmpty>The agent stopped waiting for this file.</PanelEmpty>;
  } else if (status === "submitted") {
    body = <SubmittedFiles files={files} panelAssets={panel.assets} />;
  } else {
    body = <PanelEmpty>The agent will ask for a file here when it needs one.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {typeof data.prompt === "string" && data.prompt && status === "requested" && (
        <p className="mb-2.5 text-sm font-medium">{data.prompt}</p>
      )}
      {body}
    </BlockFrame>
  );
}

export default UploadBlock;

/* -------------------------------------------------------------------------- */
/* Form `file` field — `blocks/form.tsx` loads this lazily                     */
/* -------------------------------------------------------------------------- */

/** The assets the worker has verified for this form's `file` field so far (`channel.py::_store_upload`'s `meta`). */
function receivedFieldAssets(assets: AssetRef[], blockId: string, fieldName: string): AssetRef[] {
  return assets.filter((asset) => asset.meta?.block_id === blockId && asset.meta?.field === fieldName);
}

export interface FormFileFieldProps {
  field: FormFieldSpec;
  blockId: string;
  assets: AssetRef[];
  panelAssets: Map<string, string>;
  disabled: boolean;
  onChange: (ids: string[]) => void;
}

/**
 * A form's `file` field: delegates to the same picker/sender the `upload`
 * block uses (`useUploadQueue`), with `field` in the stream's attributes so
 * the worker files it under this field instead of a block's own `files`
 * list. The field's *value* is never what the caller typed — it is
 * `receivedFieldAssets` (what the worker has actually stored for
 * `{blockId, field.name}`), pushed into the draft as soon as it changes.
 */
export function FormFileField({ field, blockId, assets, panelAssets, disabled, onChange }: FormFileFieldProps) {
  const room = useMaybeRoomContext() as UploadRoom | undefined;
  const received = useMemo(() => receivedFieldAssets(assets, blockId, field.name), [assets, blockId, field.name]);
  const receivedIds = useMemo(() => received.map((asset) => asset.asset_id), [received]);
  const lastSyncedRef = useRef<string>("");
  useEffect(() => {
    const key = receivedIds.join(",");
    if (lastSyncedRef.current !== key) {
      lastSyncedRef.current = key;
      onChange(receivedIds);
    }
  }, [receivedIds, onChange]);

  const limits: UploadLimits = field.upload ?? { accept: DEFAULT_UPLOAD_ACCEPT, maxFiles: 1, maxBytes: 10 * 1024 * 1024 };
  const { rows, addFiles } = useUploadQueue({ room, blockId, field: field.name, limits, acceptedCount: receivedIds.length });
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="flex flex-col gap-2">
      <input
        ref={inputRef}
        type="file"
        multiple={limits.maxFiles > 1}
        accept={limits.accept.join(",")}
        className="sr-only"
        aria-hidden="true"
        tabIndex={-1}
        disabled={disabled}
        onChange={(event) => {
          if (event.target.files && event.target.files.length > 0) addFiles(event.target.files);
          event.target.value = "";
        }}
      />
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="self-start"
        disabled={disabled || !room}
        onClick={() => inputRef.current?.click()}
      >
        <Icon as={PaperclipIcon} size="sm" />
        {room ? "Choose a file" : "Files can be sent during a call"}
      </Button>
      {(received.length > 0 || rows.length > 0) && (
        <ul className="flex flex-col gap-1.5">
          {received.map((asset) => (
            <li key={asset.asset_id} className="flex items-center gap-2 text-sm">
              <div className="size-8 shrink-0">
                {/* `caption: null` — the name is already shown as its own text next to the thumbnail, `fileAsset` above does the same. */}
                <FileTile asset={{ ...asset, caption: null }} url={panelAssets.get(asset.asset_id)} />
              </div>
              <span className="min-w-0 flex-1 truncate">{asset.caption ?? asset.asset_id}</span>
            </li>
          ))}
          {rows.map((row) => (
            <li key={row.key} className="text-muted-foreground text-xs">
              {row.kind === "rejected" ? (
                <span className="text-danger-text">{row.message}</span>
              ) : row.kind === "active" ? (
                `${row.name} — sending…`
              ) : (
                `${row.name} — waiting to send…`
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
