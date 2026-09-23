import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BLOCK_CATALOG, BLOCK_TYPES, VIDEO_SOURCES } from "@/panels/blocks/catalog";

/**
 * R-V2-17: `lkap_contracts.blocks` is the per-block config schema, exported as
 * `contracts/generated/schemas/BlockConfig_<type>.schema.json`. The composer's
 * config forms (`catalog.ts::configFields`) stay hand-written in Phase 1, so
 * this test keeps them equal: every block type's form edits exactly the keys
 * its schema declares — a key the schema lacks would 422 on save
 * (`panel.blocks[i].config.<key>`), and a declared key the form lacks could
 * never be set from the console.
 *
 * `custom` is the one exception: its schema's `kind` is pack-declared
 * (`"flow_progress"`, R-V2-14) and extra keys are allowed, so the composer
 * edits none of them.
 */
const SCHEMAS = path.resolve(__dirname, "../../contracts/generated/schemas");

type JsonSchema = {
  additionalProperties?: boolean;
  properties?: Record<string, { default?: unknown; pattern?: string; minimum?: number }>;
};

function load(type: string): JsonSchema {
  return JSON.parse(readFileSync(path.join(SCHEMAS, `BlockConfig_${type}.schema.json`), "utf8")) as JsonSchema;
}

/** Schema properties the composer deliberately does not edit, per block type. */
const NOT_IN_COMPOSER: Partial<Record<string, readonly string[]>> = { custom: ["kind"] };

describe("block config parity (R-V2-17)", () => {
  it.each(BLOCK_TYPES)("%s: configFields keys equal the exported schema's properties", (type) => {
    const schema = load(type);
    const schemaKeys = Object.keys(schema.properties ?? {})
      .filter((key) => !(NOT_IN_COMPOSER[type] ?? []).includes(key))
      .sort();
    const formKeys = BLOCK_CATALOG[type].configFields.map((field) => field.key).sort();
    expect(formKeys).toEqual(schemaKeys);
  });

  it.each(BLOCK_TYPES.filter((type) => type !== "custom"))("%s: unknown config keys are rejected", (type) => {
    expect(load(type).additionalProperties).toBe(false);
  });

  it("custom blocks allow pack-declared keys", () => {
    expect(load("custom").additionalProperties).toBe(true);
  });

  it("form defaults agree with the schema defaults", () => {
    for (const type of BLOCK_TYPES) {
      const properties = load(type).properties ?? {};
      for (const field of BLOCK_CATALOG[type].configFields) {
        const schemaDefault = properties[field.key]?.default;
        // The document form edits "no starting document" as "" where the schema says null.
        if (type === "document" && field.key === "url") {
          expect([null, ""]).toContain(schemaDefault ?? null);
          continue;
        }
        expect(field.default, `${type}.${field.key}`).toEqual(schemaDefault);
      }
    }
  });

  it("the video source options all satisfy the schema's pattern", () => {
    const pattern = load("video").properties?.source?.pattern;
    expect(pattern).toBeTruthy();
    const re = new RegExp(pattern as string);
    for (const option of VIDEO_SOURCES) expect(re.test(option.value), option.value).toBe(true);
  });

  it("the document page minimum matches the form's", () => {
    const field = BLOCK_CATALOG.document.configFields.find((f) => f.key === "page");
    expect(field && "min" in field ? field.min : undefined).toBe(load("document").properties?.page?.minimum);
  });
});
