import { describe, expect, it } from "vitest";

import {
  BARE_TOKEN_ID_REASON,
  DEFAULT_MODEL_ID_RULES,
  ID_LIKE_FIELD_NAMES,
  isIdLikeField,
  isSendableModelId,
  modelIdPath,
  SECRET_LOOKING_REASON,
  validateModelId,
} from "@/lib/model-ids";
import type { ModelIdRules, ProviderSpec } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * The console mirror of the model-id rule (V4-09; R-V4-21, R-V4-31, R-V4-32).
 * Every case of `contracts/tests/test_model_ids.py` is repeated here, run
 * with the rules `providers.json` publishes (`model_id_rules`), so the mirror
 * and the contract cannot drift apart silently.
 */

const DOC = providersJson as unknown as { providers: ProviderSpec[]; model_id_rules: Required<ModelIdRules> };
const RULES = DOC.model_id_rules;
const MAX_LEN = RULES.max_len;
const BARE_MIN = RULES.bare_token_min_len;

const model = (value: string) => validateModelId(value, { field: "model", rules: RULES });
const idField = (value: string) => validateModelId(value, { field: "id", rules: RULES });

/** `_registry_ids()` over the JSON: every model id, default and id-like field value the registry carries. */
function registryIds(): string[] {
  const ids = new Set<string>();
  for (const spec of DOC.providers) {
    for (const m of spec.models ?? []) ids.add(m.id);
    if (spec.default_model) ids.add(spec.default_model);
    for (const field of spec.fields ?? []) {
      if (field.type === "model" || field.type === "catalog" || isIdLikeField(field.name)) {
        if (typeof field.default === "string" && field.default) ids.add(field.default);
        if (field.type !== "enum" || isIdLikeField(field.name)) for (const option of field.options ?? []) ids.add(option);
      }
    }
    for (const voice of spec.capabilities?.voices ?? []) ids.add(voice);
  }
  return [...ids].sort();
}

function fakeSecret(prefix: string): string {
  return `${prefix}Zq9WkX7vRt3LmN8pYb2HcJ5d`;
}

function runs(value: string, size = 4): string[] {
  const out: string[] = [];
  for (let i = 0; i + size <= value.length; i += 1) out.push(value.slice(i, i + size));
  return out;
}

const HEX32 = "0123456789abcdef0123456789abcdef";
const PADDED_BASE64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo+YWJjZA==";

describe("the mirror uses the published rules", () => {
  it("DEFAULT_MODEL_ID_RULES equals providers.json's model_id_rules", () => {
    expect(DEFAULT_MODEL_ID_RULES).toEqual(RULES);
  });

  it("the published pattern compiles as a JavaScript regular expression", () => {
    expect(() => new RegExp(RULES.pattern)).not.toThrow();
    expect(RULES.pattern.startsWith("^") && RULES.pattern.endsWith("$")).toBe(true);
  });

  it("ID_LIKE_FIELD_NAMES matches the contract's eight names (not part of model_id_rules; pinned here)", () => {
    expect([...ID_LIKE_FIELD_NAMES].sort()).toEqual(
      ["avatar_id", "emotion_id", "face_id", "pal_id", "persona_id", "voice", "voice_id", "voice_name"].sort(),
    );
  });
});

describe("validateModelId — field: model (validate_model_id)", () => {
  it.each(registryIds())("every id the registry carries passes: %s", (id) => {
    expect(model(id)).toBeNull();
  });

  it.each([
    "amazon.nova-2-lite-v1:0",
    "openai/gpt-4o-mini:free",
    "BAAI/bge-small-en-v1.5",
    "cartesia/sonic-3:a0e99841-438c-4a64-b679-ae501e7d6091",
    "accounts/fireworks/models/llama-v3p1-8b-instruct",
    "ft:gpt-4o-mini:org::abc",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "models@2025+beta",
    "x-".repeat(MAX_LEN / 2),
    "a".repeat(BARE_MIN - 1),
  ])("real-world id shape passes: %s", (id) => {
    expect(model(id)).toBeNull();
    expect(new RegExp(RULES.pattern).test(id)).toBe(true);
  });

  it.each([
    ["", "empty"],
    ["x-".repeat(MAX_LEN / 2) + "x", "longer than"],
    ["gpt 4", "whitespace"],
    ["gpt-4\t", "whitespace"],
    ["gpt\u0000", "control"],
    ["gpt-4ö", "non-ASCII"],
    ["https://example.com/model", "URL"],
    ["httpbin", "URL"],
    ["Http-model", "URL"],
    ["vendor://model", "URL"],
    ["model?x=1", "character"],
    ["model#frag", "character"],
    ["a&b", "character"],
    ["a=b", "character"],
    ["<script>", "character"],
    ['"quoted"', "character"],
    ["'quoted'", "character"],
    ["back`tick", "character"],
  ])("syntax rejection %j names a reason containing %j", (value, fragment) => {
    const issue = model(value);
    expect(issue?.severity).toBe("error");
    expect(issue?.reason).toContain(fragment);
    expect(new RegExp(RULES.pattern).test(value)).toBe(false);
  });

  it.each(RULES.secret_prefixes)("secret prefix %s is refused with a value-free reason", (prefix) => {
    const value = fakeSecret(prefix);
    const issue = model(value);
    expect(issue).toEqual({ severity: "error", reason: SECRET_LOOKING_REASON });
    const tail = value.slice(prefix.length);
    for (const size of [6, 4]) for (const run of runs(tail, size)) expect(issue?.reason).not.toContain(run);
    expect(issue?.reason).not.toContain(prefix);
  });

  it.each([HEX32, "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", PADDED_BASE64])("a bare token is refused as a secret: %s", (value) => {
    expect(model(value)).toEqual({ severity: "error", reason: SECRET_LOOKING_REASON });
  });

  it("a long id with separators is not a bare token", () => {
    expect(model("gemini-2-5-flash-native-audio-preview-12-2025-extra")).toBeNull();
    expect(model("a".repeat(20) + "_" + "b".repeat(20))).toBeNull();
  });

  it("the secret check runs before the syntax check", () => {
    expect(model("sk-abc=def")?.reason).toBe(SECRET_LOOKING_REASON);
  });

  it("a key with surrounding whitespace is still recognised as a key (the prefix check strips, like Python)", () => {
    expect(model("  sk-or-v1-abc  ")?.reason).toBe(SECRET_LOOKING_REASON);
  });
});

describe("validateModelId — field: id (validate_id_value, R-V4-31)", () => {
  it("a 32-hex value is a model-id error but an id-field warning", () => {
    expect(model(HEX32)).toEqual({ severity: "error", reason: SECRET_LOOKING_REASON });
    expect(idField(HEX32)).toEqual({ severity: "warning", reason: BARE_TOKEN_ID_REASON });
  });

  it.each(RULES.secret_prefixes)("a prefixed key (%s) is an error from both rules", (prefix) => {
    const value = fakeSecret(prefix);
    expect(model(value)?.severity).toBe("error");
    expect(idField(value)).toEqual({ severity: "error", reason: SECRET_LOOKING_REASON });
  });

  it.each(["3f2b8c1e-9a4d-4e6b-8c2a-1d5e7f9a0b3c", "r79e1c033f", "aura-2-thalia-en"])("a uuid or ordinary id passes both: %s", (value) => {
    expect(model(value)).toBeNull();
    expect(idField(value)).toBeNull();
  });

  it("a padded base64 key stays an error on an id field (syntax runs before the bare-token warning)", () => {
    expect(idField(PADDED_BASE64)?.severity).toBe("error");
  });

  it.each(["has space", "http://x", "a?b", "x".repeat(201), ""])("a syntax failure is an error on an id field: %j", (value) => {
    expect(idField(value)?.severity).toBe("error");
  });

  it.each([HEX32, fakeSecret("sk-"), fakeSecret("xi-"), PADDED_BASE64])("no reason of either rule carries a run of the value", (value) => {
    for (const reason of [model(value)?.reason ?? "", idField(value)?.reason ?? ""]) {
      expect(runs(value).filter((run) => reason.includes(run))).toEqual([]);
    }
  });
});

describe("helpers", () => {
  it.each([
    ["voice", true],
    ["voice_id", true],
    ["simli_config.face_id", true],
    ["simli_config.emotion_id", true],
    ["model", false],
    ["language", false],
    ["base_url", false],
  ])("isIdLikeField(%s) is %s", (name, expected) => {
    expect(isIdLikeField(name)).toBe(expected);
  });

  it("isSendableModelId is false for empty, key-like and malformed ids", () => {
    expect(isSendableModelId("openai/gpt-4.1-mini")).toBe(true);
    expect(isSendableModelId("")).toBe(false);
    expect(isSendableModelId(null)).toBe(false);
    expect(isSendableModelId(fakeSecret("sk-or-"))).toBe(false);
    expect(isSendableModelId(HEX32)).toBe(false);
    expect(isSendableModelId("gpt 4")).toBe(false);
  });

  it("modelIdPath keeps slashes and escapes the rest per segment", () => {
    expect(modelIdPath("openai/gpt-4.1-mini")).toBe("openai/gpt-4.1-mini");
    expect(modelIdPath("ft:gpt-4o-mini:org::abc")).toBe("ft%3Agpt-4o-mini%3Aorg%3A%3Aabc");
    expect(modelIdPath("a%b/c@d")).toBe("a%25b/c%40d");
  });
});
