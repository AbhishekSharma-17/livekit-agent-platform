import { ApiError } from "@/lib/api";

/**
 * `api.ts`'s `apiRequest` always JSON-encodes the body, which breaks
 * multipart uploads (it would stringify the `FormData` object and set
 * `Content-Type: application/json`). The console proxy at
 * `src/app/api/console/[...path]/route.ts` forwards the raw request body
 * either way, so a plain `fetch` with a `FormData` body (browser sets the
 * multipart boundary header itself) reaches `lkap_api` untouched.
 */
export async function uploadKbDocument(kbId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`/api/console/knowledge-bases/${kbId}/documents`, {
    method: "POST",
    body: formData,
  });

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

  return payload;
}
