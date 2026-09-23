import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BLOCK_TOOLS, BUILTIN_TOOLS } from "@/components/console/lib/constants";
import { VISION_TOOL_NAMES } from "@/components/console/flow/tool-options";
import { BLOCK_TOOL_TYPES, UPDATABLE_BLOCK_TYPES } from "@/panels/blocks/catalog";

/**
 * V2-16-3 / V2-19B-3: `lkap_contracts.tools` is the single source of the
 * built-in and block tool names, exported as
 * `contracts/generated/builtin_tools.json`. The web keeps typed copies (the
 * web image builds from `web/` alone, so it cannot import that file at
 * runtime); this test pins every copy to the export, the
 * `block-config-parity.test.ts` pattern.
 */
const EXPORT = path.resolve(__dirname, "../../contracts/generated/builtin_tools.json");

interface BuiltinToolsExport {
  builtin_tool_names: string[];
  vision_tool_names: string[];
  block_tool_names: string[];
  block_tool_types: Record<string, string[]>;
}

const exported = JSON.parse(readFileSync(EXPORT, "utf8")) as BuiltinToolsExport;
const sorted = (values: Iterable<string>) => [...values].sort();

describe("tool names parity with lkap_contracts.tools", () => {
  it("the console's built-in toggles plus http_request are exactly the contract's built-ins", () => {
    // `http_request` is switched by `http_request_enabled`, not a toggle row.
    expect(sorted([...BUILTIN_TOOLS.map((tool) => tool.name), "http_request"])).toEqual(
      sorted(exported.builtin_tool_names),
    );
  });

  it("the vision tools match", () => {
    expect(sorted(VISION_TOOL_NAMES)).toEqual(sorted(exported.vision_tool_names));
  });

  it("the block tool toggles match", () => {
    expect(sorted(BLOCK_TOOLS.map((tool) => tool.name))).toEqual(sorted(exported.block_tool_names));
  });

  it("each block tool registers for the same block types", () => {
    expect(sorted(Object.keys(BLOCK_TOOL_TYPES))).toEqual(sorted(Object.keys(exported.block_tool_types)));
    for (const [name, types] of Object.entries(exported.block_tool_types)) {
      expect(sorted(BLOCK_TOOL_TYPES[name as keyof typeof BLOCK_TOOL_TYPES]), name).toEqual(sorted(types));
    }
  });

  it("update_block writes exactly the updatable block types", () => {
    expect(sorted(UPDATABLE_BLOCK_TYPES)).toEqual(sorted(exported.block_tool_types.update_block ?? []));
  });
});
