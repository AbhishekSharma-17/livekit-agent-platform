"use client";

/**
 * The `upload` block's byte-stream sender (CONTRACTS-V2 §4.4,
 * `panels-and-capabilities.md` B6, V5-19 → V5-23): streams a picked file to
 * the worker on `lkap.ui.upload`, carrying the attributes
 * `agent/src/lkap_agent/ui/channel.py::receive_upload` reads off the stream:
 * `block_id`, `name`, and `field` for a form `file` field.
 *
 * **Deviation from the plan text** (an ask is filed for this): the plan and
 * `docs/v5/_asks.md` #131 both write `room.localParticipant.sendFile(file,
 * {topic, attributes: {block_id, name}})`, but the installed `livekit-client`
 * (2.22.3) does not thread `attributes` through `sendFile`/`SendFileOptions`
 * at all — its `_sendFile` builds the stream header from `id/name/mimeType/
 * topic/timestamp/size/encryptionType` only (`SendFileOptions` is `Pick<
 * StreamBytesOptions, 'topic'|'mimeType'|'destinationIdentities'>`, which
 * drops `attributes` and `name`). Sent that way, the worker would see
 * `attributes.block_id` empty and refuse every file as `not_requested`.
 * `streamBytes` **does** carry `attributes` (and `name`, and `totalSize`,
 * which is what lets the worker refuse a declared-oversize file before
 * reading a single byte — `channel.py::_check_and_store`'s `declared >
 * target.max_bytes` check), so this sender pumps the file through
 * `streamBytes` in fixed-size slices instead of `sendFile`. `streamBytes`
 * never compresses (only `sendFile`'s one-shot path does), so the worker's
 * magic-byte sniff (`sniff_mime`) sees the file's real bytes.
 *
 * Only the shape actually used is typed here (`UploadRoom`), not the real
 * `Room`/`LocalParticipant` from `livekit-client` — a real `Room` satisfies
 * it structurally, and a test can inject a fake without loading the SDK.
 *
 * `useUploadQueue` is the picker logic shared by `blocks/upload.tsx` (an
 * `upload` block) and `blocks/form.tsx` (a form `file` field): one file at a
 * time (mirroring the worker's own `_upload_lock`), client-side pre-checks
 * before a file joins the queue, and a merged row list a renderer can map
 * straight to list items.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/** The slice of `Room`/`LocalParticipant` this sender needs. */
export interface UploadWriter {
  write(chunk: Uint8Array): Promise<void>;
  close(): Promise<void>;
}

export interface UploadStreamOptions {
  topic?: string;
  name?: string;
  mimeType?: string;
  totalSize?: number;
  attributes?: Record<string, string>;
}

export interface UploadRoom {
  localParticipant: {
    streamBytes(options: UploadStreamOptions): Promise<UploadWriter>;
  };
}

/** The `lkap.ui.upload` byte-stream topic (CONTRACTS-V2 §4.4, `channel.py::TOPIC` pair with `lkap.ui.asset`). */
export const TOPIC_UI_UPLOAD = "lkap.ui.upload";

/** `accept` of an upload block (or a form file field) that sets none (`lkap_contracts.blocks.DEFAULT_UPLOAD_ACCEPT`). */
export const DEFAULT_UPLOAD_ACCEPT: readonly string[] = ["image/*", "application/pdf"];

/** Bytes moved per `streamBytes` write — small enough to report smooth progress, large enough not to chat. */
export const UPLOAD_CHUNK_BYTES = 64 * 1024;

/** The limits an `upload` block or a form `file` field enforces (`UploadBlockConfig` / `FormUploadSpec`). */
export interface UploadLimits {
  accept: readonly string[];
  maxFiles: number;
  maxBytes: number;
}

/** Why the sender itself refused a file, and what to show — mirrors `channel.py::rejection_message`'s wording. */
export type ClientUploadRejectReason = "too_large" | "too_many_files" | "empty";

export interface ClientUploadRejection {
  name: string;
  reason: ClientUploadRejectReason;
  message: string;
}

function megabytes(bytes: number): number {
  return Math.max(1, Math.floor(bytes / (1024 * 1024)));
}

/**
 * The client-side pre-checks the plan calls for ("client checks only save a
 * round trip" — `docs/v5/_asks.md` #131): size and count only. Type is
 * deliberately not checked here — `file.type` is the browser's *declared*
 * type (empty for some HEIC captures), the worker sniffs the real bytes
 * (`sniff_mime`) and is the only source of truth for `type_not_allowed`, so a
 * client-side type guess would only ever produce a false refusal.
 */
export function checkFileClientSide(
  file: File,
  limits: UploadLimits,
  alreadyCount: number,
): ClientUploadRejection | null {
  if (alreadyCount >= limits.maxFiles) {
    return {
      name: file.name,
      reason: "too_many_files",
      message: `You can send up to ${limits.maxFiles} file${limits.maxFiles === 1 ? "" : "s"} here.`,
    };
  }
  if (file.size > limits.maxBytes) {
    return {
      name: file.name,
      reason: "too_large",
      message: `This file is too large. Send one up to ${megabytes(limits.maxBytes)} MB.`,
    };
  }
  if (file.size === 0) {
    return { name: file.name, reason: "empty", message: "This file is empty." };
  }
  return null;
}

export interface SendUploadOptions {
  /** The requested block (`upload`, or the `form` block a `file` field belongs to). */
  blockId: string;
  /** The form field's name, for a form `file` field; omitted for an `upload` block. */
  field?: string;
  /** `0..1`, called as bytes leave the browser (not the worker's own receive progress). */
  onProgress?: (fraction: number) => void;
  /** Checked between chunks; throws `"aborted"` so a cancelled send stops promptly. */
  isCancelled?: () => boolean;
  chunkBytes?: number;
}

/**
 * Stream one file to the worker on `lkap.ui.upload`. Resolves once every byte
 * has been handed to the transport and the stream is closed — this says
 * nothing about whether the worker accepted the file; that arrives later as a
 * `files`/`rejected` patch on the block's state.
 */
export async function sendUploadFile(room: UploadRoom, file: File, options: SendUploadOptions): Promise<void> {
  const chunkBytes = options.chunkBytes ?? UPLOAD_CHUNK_BYTES;
  const attributes: Record<string, string> = { block_id: options.blockId, name: file.name };
  if (options.field) attributes.field = options.field;

  const writer = await room.localParticipant.streamBytes({
    topic: TOPIC_UI_UPLOAD,
    name: file.name,
    mimeType: file.type || "application/octet-stream",
    totalSize: file.size,
    attributes,
  });

  let offset = 0;
  try {
    // `file.size === 0` still needs one report at 1 so a caller waiting on
    // `onProgress` sees completion.
    if (file.size === 0) {
      options.onProgress?.(1);
    }
    while (offset < file.size) {
      if (options.isCancelled?.()) throw new Error("aborted");
      const slice = file.slice(offset, offset + chunkBytes);
      const buffer = await slice.arrayBuffer();
      await writer.write(new Uint8Array(buffer));
      offset += buffer.byteLength;
      options.onProgress?.(Math.min(1, offset / file.size));
    }
  } finally {
    await writer.close();
  }
}

/** One row of the picker's file list: a file still queued, the one currently sending, or a client-refused one. */
export type UploadQueueRow =
  | { key: string; kind: "queued"; name: string }
  | { key: string; kind: "active"; name: string; progress: number }
  | { key: string; kind: "rejected"; name: string; message: string };

export interface UseUploadQueueOptions {
  /** `undefined` outside a room (preview, the console's read-only snapshot, a test with no `RoomContext`) — the queue then never sends. */
  room: UploadRoom | undefined;
  blockId: string;
  /** The form field's name, for a form `file` field. */
  field?: string;
  limits: UploadLimits;
  /** How many files the worker has already accepted for this target — `UploadBlockState.files.length`, or the count of matching `assets` for a form field. */
  acceptedCount: number;
}

export interface UseUploadQueueResult {
  rows: UploadQueueRow[];
  /** Client-side checked (`checkFileClientSide`) and, if they pass, queued to send in order. */
  addFiles: (files: FileList | File[]) => void;
  /** Stops the queue from starting any further file; does not recall one already handed to the transport. */
  cancel: () => void;
}

/**
 * The upload picker's queue: one file at a time, mirroring the worker's own
 * `_upload_lock` (`channel.py`). `addFiles` runs `checkFileClientSide`
 * against `limits`/`acceptedCount` *before* a file is queued, so an
 * over-`max_bytes` or extra file never touches the transport — the worker
 * still re-checks everything it receives (this is a UX shortcut, not a
 * security boundary).
 */
export function useUploadQueue(options: UseUploadQueueOptions): UseUploadQueueResult {
  const { room, blockId, field, limits, acceptedCount } = options;
  const [queue, setQueue] = useState<{ key: number; file: File }[]>([]);
  const [active, setActive] = useState<{ key: number; name: string; progress: number } | null>(null);
  const [rejections, setRejections] = useState<{ key: number; name: string; message: string }[]>([]);
  const cancelledRef = useRef(false);
  const nextKey = useRef(0);

  const addFiles = useCallback(
    (picked: FileList | File[]) => {
      if (cancelledRef.current) return;
      let pending = acceptedCount + queue.length + (active ? 1 : 0);
      const accepted: { key: number; file: File }[] = [];
      const newRejections: { key: number; name: string; message: string }[] = [];
      for (const file of Array.from(picked)) {
        const rejection = checkFileClientSide(file, limits, pending);
        if (rejection) {
          newRejections.push({ key: nextKey.current++, name: rejection.name, message: rejection.message });
          continue;
        }
        pending += 1;
        accepted.push({ key: nextKey.current++, file });
      }
      if (newRejections.length > 0) setRejections((prev) => [...prev, ...newRejections]);
      if (accepted.length > 0) setQueue((prev) => [...prev, ...accepted]);
    },
    [acceptedCount, active, limits, queue.length],
  );

  useEffect(() => {
    if (active || queue.length === 0 || !room || cancelledRef.current) return;
    const [head, ...rest] = queue;
    setQueue(rest);
    setActive({ key: head.key, name: head.file.name, progress: 0 });
    // Guarded by `current.key === head.key`, not an effect-cleanup flag: this
    // effect's own `setQueue`/`setActive` calls are themselves its
    // dependencies, so a cleanup tied to that re-run would fire the instant
    // the send starts. `cancelledRef` (the caller's `cancel()`) is the only
    // real abort signal, read live by `isCancelled`.
    void sendUploadFile(room, head.file, {
      blockId,
      field,
      onProgress: (fraction) =>
        setActive((current) => (current && current.key === head.key ? { ...current, progress: fraction } : current)),
      isCancelled: () => cancelledRef.current,
    })
      .catch(() => {
        setRejections((prev) => [
          ...prev,
          { key: nextKey.current++, name: head.file.name, message: "This file could not be sent. Try again." },
        ]);
      })
      .finally(() => {
        setActive((current) => (current && current.key === head.key ? null : current));
      });
  }, [active, queue, room, blockId, field]);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    setQueue([]);
  }, []);

  const rows: UploadQueueRow[] = [
    ...queue.map((entry): UploadQueueRow => ({ key: `queued-${entry.key}`, kind: "queued", name: entry.file.name })),
    ...(active ? [{ key: `active-${active.key}`, kind: "active", name: active.name, progress: active.progress } as const] : []),
    ...rejections.map((r): UploadQueueRow => ({ key: `rejected-${r.key}`, kind: "rejected", name: r.name, message: r.message })),
  ];

  return { rows, addFiles, cancel };
}
