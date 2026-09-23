import { describe, expect, it } from "vitest";

import { roleAtLeast, writeAccessReason } from "@/components/console/lib/roles";

/**
 * Role-gating helpers (docs/v2/_asks.md V2-20-5): the pure logic behind
 * `useWriteAccess`, kept separate from `useActiveWorkspace`'s network/cookie
 * plumbing so the ordering rule itself (`viewer < builder < admin < owner`)
 * is trivially testable.
 */
describe("roleAtLeast", () => {
  it("orders roles viewer < builder < admin < owner", () => {
    expect(roleAtLeast("viewer", "builder")).toBe(false);
    expect(roleAtLeast("builder", "builder")).toBe(true);
    expect(roleAtLeast("admin", "builder")).toBe(true);
    expect(roleAtLeast("owner", "builder")).toBe(true);

    expect(roleAtLeast("builder", "admin")).toBe(false);
    expect(roleAtLeast("admin", "admin")).toBe(true);
    expect(roleAtLeast("owner", "admin")).toBe(true);
  });

  it("treats an unresolved (loading or signed-out) role as no access", () => {
    expect(roleAtLeast(undefined, "builder")).toBe(false);
    expect(roleAtLeast(undefined, "admin")).toBe(false);
  });

  it("a role always meets its own floor", () => {
    for (const role of ["viewer", "builder", "admin", "owner"] as const) {
      expect(roleAtLeast(role, role)).toBe(true);
    }
  });
});

describe("writeAccessReason", () => {
  it("names the admin floor explicitly", () => {
    expect(writeAccessReason("admin")).toMatch(/admin/i);
  });

  it("defaults to the builder-floor explanation", () => {
    expect(writeAccessReason()).toMatch(/viewer/i);
    expect(writeAccessReason("builder")).toMatch(/viewer/i);
  });
});
