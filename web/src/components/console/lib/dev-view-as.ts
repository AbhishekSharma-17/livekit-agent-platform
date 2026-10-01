"use client";

import * as React from "react";

import { BREAK_GLASS_USER_ID } from "@/components/console/lib/sign-out";
import type { WorkspaceMembership } from "@/contracts/lkap-contracts";

/**
 * The development-only "view as" role switch (decision O6, docs/ui/REPORT.md).
 *
 * The dev server runs with the admin bypass, so every page renders as an
 * owner. This lets a developer render the console as an admin, builder or
 * viewer to check what each role sees. It is **UI only**: it changes which
 * role the console renders for (`useActiveWorkspace`), never what the server
 * allows. Every request still carries the break-glass admin token, and the
 * api still answers as the owner.
 *
 * It is on only when both hold:
 * - the app runs in development (`process.env.NODE_ENV === "development"`,
 *   inlined at build time, so a production build folds every branch here to
 *   "off" and the switch's UI is dropped), and
 * - the console is signed in through the admin bypass, which `GET /v1/auth/me`
 *   reports as the synthetic break-glass user (`BREAK_GLASS_USER_ID`).
 *
 * The choice is kept per browser in `localStorage` under `DEV_VIEW_AS_KEY`
 * (the render check sets it directly). It only ever lowers the role: a
 * choice above the real role is ignored.
 */
export type Role = WorkspaceMembership["role"];

export const DEV_VIEW_AS_KEY = "lkap:dev:view-as";
export const DEV_VIEW_AS_ROLES: readonly Role[] = ["owner", "admin", "builder", "viewer"];

const RANK: Record<Role, number> = { viewer: 0, builder: 1, admin: 2, owner: 3 };
const CHANGE_EVENT = "lkap:dev:view-as-change";

/** Whether the switch is available: development, signed in through the admin bypass. */
export function devViewAsAllowed(userId: string | undefined): boolean {
  return process.env.NODE_ENV === "development" && userId === BREAK_GLASS_USER_ID;
}

function isRole(value: unknown): value is Role {
  return typeof value === "string" && value in RANK;
}

function readStored(): Role | null {
  if (process.env.NODE_ENV !== "development") return null;
  try {
    const value = window.localStorage.getItem(DEV_VIEW_AS_KEY);
    return isRole(value) ? value : null;
  } catch {
    return null;
  }
}

function subscribe(onChange: () => void): () => void {
  if (process.env.NODE_ENV !== "development") return () => undefined;
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

/** Save (or with `null`, clear) the role to view as. Does nothing outside development. */
export function setDevViewAs(role: Role | null): void {
  if (process.env.NODE_ENV !== "development") return;
  try {
    if (role) window.localStorage.setItem(DEV_VIEW_AS_KEY, role);
    else window.localStorage.removeItem(DEV_VIEW_AS_KEY);
  } catch {
    // Blocked storage: the switch simply doesn't stick.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

/** The stored choice, or `null` (none, outside development, or before hydration). */
export function useDevViewAsChoice(): Role | null {
  return React.useSyncExternalStore(subscribe, readStored, () => null);
}

/** The role the console renders for: the real role, lowered to the choice when the switch is allowed. */
export function viewAsRole(real: Role, choice: Role | null, allowed: boolean): Role {
  if (!allowed || !choice) return real;
  return RANK[choice] < RANK[real] ? choice : real;
}
