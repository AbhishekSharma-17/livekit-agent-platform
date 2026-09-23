import { api } from "@/lib/api";

/** The break-glass admin token's synthetic user (`GET /v1/auth/me`). */
export const BREAK_GLASS_USER_ID = "break-glass";

/**
 * `POST /v1/auth/logout`, then a hard navigation to `/login` so the
 * middleware sees the cleared cookie (V2-14). Navigates even when the call
 * fails: a stale session must never trap the user in the console.
 */
export async function signOut(): Promise<void> {
  try {
    await api.post("auth/logout");
  } finally {
    window.location.assign("/login");
  }
}
