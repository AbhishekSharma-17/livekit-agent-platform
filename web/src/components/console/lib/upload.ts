import { ApiError } from "@/lib/api";
import type { DatasetOut, KbDocumentOut } from "@/contracts/lkap-contracts";

/**
 * Upload cap (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §WP-6: "Upload cap error
 * copy (25 MB)"). The api doesn't enforce this yet (that lands with the jobs
 * work in v2, docs/v2/PLAN-V2.md "KB upload cap (F-29)"), so the console
 * checks client-side before ever calling the endpoint, and also recognises a
 * `413` from the server (a future api, or a proxy in front of it) with the
 * same copy.
 */
export const KB_UPLOAD_MAX_BYTES = 25 * 1024 * 1024;
export const KB_UPLOAD_MAX_MB = 25;

/** True when `file` exceeds the 25 MB upload cap. */
export function isFileOverUploadCap(file: File): boolean {
  return file.size > KB_UPLOAD_MAX_BYTES;
}

/** The error copy shown for a file rejected by the 25 MB upload cap. */
export function kbUploadCapErrorMessage(filename: string): string {
  return `"${filename}" is larger than the 25 MB upload limit. Try a smaller file.`;
}

/**
 * `api.ts`'s `apiRequest` always JSON-encodes the body, which breaks
 * multipart uploads (it would stringify the `FormData` object and set
 * `Content-Type: application/json`). The console proxy at
 * `src/app/api/console/[...path]/route.ts` forwards the raw request body
 * either way, so a plain `fetch` with a `FormData` body (browser sets the
 * multipart boundary header itself) reaches `lkap_api` untouched.
 *
 * Callers should check `isFileOverUploadCap` before calling this — it is not
 * re-checked here so the function keeps doing exactly one thing (the actual
 * request), but a `413` from the server is still translated to the same
 * cap copy defensively.
 */
export async function uploadKbDocument(kbId: string, file: File): Promise<KbDocumentOut> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`/api/console/knowledge-bases/${kbId}/documents`, {
    method: "POST",
    body: formData,
  });

  if (response.status === 413) {
    throw new ApiError(413, "payload_too_large", kbUploadCapErrorMessage(file.name));
  }

  const payload: unknown = await response.json().catch(() => undefined);

  if (!response.ok) {
    if (
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof (payload as { error?: unknown }).error === "object"
    ) {
      const err = (payload as { error: { code: string; message: string; details?: unknown } }).error;
      throw new ApiError(response.status, err.code, err.message, err.details);
    }
    throw new ApiError(response.status, "unknown_error", response.statusText);
  }

  return payload as KbDocumentOut;
}

/**
 * The largest file a lookup table (V6-16 dataset, D-V6-27) is made from
 * (`lkap_contracts.datasets.MAX_DATASET_BYTES`) — checked client-side first,
 * same rationale as {@link KB_UPLOAD_MAX_BYTES}.
 */
export const DATASET_UPLOAD_MAX_BYTES = 5 * 1024 * 1024;
export const DATASET_UPLOAD_MAX_MB = 5;

/** True when `file` exceeds the 5 MB lookup-table upload cap. */
export function isDatasetFileOverUploadCap(file: File): boolean {
  return file.size > DATASET_UPLOAD_MAX_BYTES;
}

/** The error copy shown for a file rejected by the 5 MB lookup-table upload cap. */
export function datasetUploadCapErrorMessage(filename: string): string {
  return `"${filename}" is larger than the 5 MB upload limit. Try a smaller file.`;
}

/**
 * `POST /v1/datasets` (V6-19): multipart, like {@link uploadKbDocument} — a
 * plain `fetch` with `FormData` so the browser sets its own boundary header,
 * carrying `file`, `name` and `key_columns` (a JSON string of `{column:
 * type}`, `lkap_api.routers.datasets.upload_dataset`).
 */
export async function uploadDataset(
  name: string,
  keyColumns: Record<string, string>,
  file: File,
): Promise<DatasetOut> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("name", name);
  formData.append("key_columns", JSON.stringify(keyColumns));

  const response = await fetch("/api/console/datasets", {
    method: "POST",
    body: formData,
  });

  if (response.status === 413) {
    throw new ApiError(413, "payload_too_large", datasetUploadCapErrorMessage(file.name));
  }

  const payload: unknown = await response.json().catch(() => undefined);

  if (!response.ok) {
    if (
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof (payload as { error?: unknown }).error === "object"
    ) {
      const err = (payload as { error: { code: string; message: string; details?: unknown } }).error;
      throw new ApiError(response.status, err.code, err.message, err.details);
    }
    throw new ApiError(response.status, "unknown_error", response.statusText);
  }

  return payload as DatasetOut;
}
