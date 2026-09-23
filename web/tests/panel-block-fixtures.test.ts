import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { STATE_FIXTURES, FIXTURE_LAYOUT } from "@/panels/blocks/__fixtures__";

/**
 * The block fixtures (`src/panels/blocks/__fixtures__/*.json`) are checked
 * against the **generated contract schemas**
 * (`contracts/generated/schemas/<Type>BlockState.schema.json`, exported from
 * `lkap_contracts.ui_protocol`): every key must be a declared property, and
 * enum / type constraints on the top-level fields must hold. So a fixture
 * cannot drift from what the worker is allowed to publish.
 */
const SCHEMAS = path.resolve(__dirname, "../../contracts/generated/schemas");

const SCHEMA_FILE: Record<string, string> = {
  form: "FormBlockState",
  document: "DocumentBlockState",
  gallery: "GalleryBlockState",
  table: "TableBlockState",
  transcript: "TranscriptBlockState",
  video: "VideoBlockState",
  kb_citations: "KbCitationsBlockState",
};

type JsonSchema = {
  properties?: Record<string, { type?: string; enum?: unknown[]; anyOf?: { type?: string }[]; alias?: string }>;
};

function load(name: string): JsonSchema {
  return JSON.parse(readFileSync(path.join(SCHEMAS, `${name}.schema.json`), "utf8")) as JsonSchema;
}

function jsonType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value;
}

function allowedTypes(prop: { type?: string; anyOf?: { type?: string }[] }): string[] {
  const types = prop.type ? [prop.type] : (prop.anyOf ?? []).map((t) => t.type ?? "");
  return types.includes("number") ? [...types, "integer"] : types;
}

describe("block fixtures match the generated contract schemas", () => {
  for (const [stem, { type, state }] of Object.entries(STATE_FIXTURES)) {
    it(`${stem}.json fits ${SCHEMA_FILE[type]}`, () => {
      const schema = load(SCHEMA_FILE[type]);
      const properties = schema.properties ?? {};
      for (const [key, value] of Object.entries(state)) {
        expect(Object.keys(properties), `unknown key "${key}"`).toContain(key);
        const prop = properties[key];
        const types = allowedTypes(prop);
        if (types.length > 0 && !types.includes("")) expect(types, `type of "${key}"`).toContain(jsonType(value));
        if (prop.enum) expect(prop.enum).toContain(value);
      }
    });
  }

  it("the layout fixture fits PanelLayout/BlockSpec and has one block of every type", () => {
    const schema = JSON.parse(readFileSync(path.join(SCHEMAS, "PanelLayout.schema.json"), "utf8")) as {
      properties: Record<string, unknown>;
      $defs: { BlockSpec: { properties: Record<string, { enum?: string[] }> } };
    };
    expect(Object.keys(FIXTURE_LAYOUT).every((key) => key in schema.properties)).toBe(true);
    const blockProps = schema.$defs.BlockSpec.properties;
    for (const block of FIXTURE_LAYOUT.blocks) {
      for (const key of Object.keys(block)) expect(Object.keys(blockProps)).toContain(key);
    }
    expect(new Set(FIXTURE_LAYOUT.blocks.map((b) => b.type))).toEqual(new Set(blockProps.type.enum));
  });
});
