import { describe, expect, it } from "vitest";

import {
  AGENT_KEY_EXPIRY_OPTIONS,
  AGENT_KEY_PRESETS,
  CALLS_WRITE_SCOPE,
  claudeCodeSnippet,
  codexSnippet,
  cursorSnippet,
  expiresAtIso,
  presetById,
  skillInstallLine,
  snippetClientFor,
  TRANSCRIPT_ACK_LABEL,
  TRANSCRIPT_WARNING,
  type SnippetContext,
} from "@/components/console/settings/snippets";

/**
 * Snippet correctness for the "Connect an AI agent" dialog (docs/v3/PLAN-V3.md
 * V3-04 acceptance): the Codex snippets must be valid TOML and the
 * Cursor/generic ones valid JSON. No `toml` parser is a project dependency
 * (adding one would touch `package.json`, outside this card's exclusive
 * files) — `parseMiniToml` below is a deliberately minimal parser that
 * understands exactly the subset `snippets.ts` emits (`[section.path]`
 * headers, `key = "string"` and `key = [ "a", "b" ]`), enough to prove the
 * generated text parses and has the expected shape.
 */
type TomlValue = string | TomlValue[] | TomlTable;
interface TomlTable {
  [key: string]: TomlValue;
}

function parseTomlValue(raw: string): TomlValue {
  const value = raw.trim();
  if (value.startsWith('"') && value.endsWith('"')) return value.slice(1, -1);
  if (value.startsWith("[") && value.endsWith("]")) {
    const inner = value.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(",").map((item) => parseTomlValue(item));
  }
  throw new Error(`parseMiniToml: unsupported value '${raw}'`);
}

function parseMiniToml(text: string): TomlTable {
  const root: TomlTable = {};
  let cursor: TomlTable = root;
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const section = line.match(/^\[([\w.-]+)\]$/);
    if (section) {
      cursor = root;
      for (const part of section[1].split(".")) {
        const next = (cursor[part] ??= {});
        if (typeof next !== "object" || Array.isArray(next)) {
          throw new Error(`parseMiniToml: '${part}' is not a table`);
        }
        cursor = next;
      }
      continue;
    }
    const kv = line.match(/^([\w-]+)\s*=\s*(.+)$/);
    if (!kv) throw new Error(`parseMiniToml: invalid line '${line}'`);
    cursor[kv[1]] = parseTomlValue(kv[2]);
  }
  return root;
}

const CTX: SnippetContext = {
  apiOrigin: "http://127.0.0.1:8080",
  key: "lkap_testkey123456",
  remote: false,
};

const REMOTE_CTX: SnippetContext = {
  ...CTX,
  remote: true,
  publicMcpUrl: "https://api.example.test/mcp",
};

describe("claudeCodeSnippet", () => {
  it("local: stdio, the key, the api origin and -s user", () => {
    const snippet = claudeCodeSnippet(CTX);
    expect(snippet).toContain("-s user");
    expect(snippet).toContain("--transport stdio");
    expect(snippet).toContain(CTX.apiOrigin);
    expect(snippet).toContain(CTX.key);
    expect(snippet).toContain("uv run --project <checkout>/mcp lkap-mcp");
  });

  it("remote: --transport http, the public url and the bearer header", () => {
    const snippet = claudeCodeSnippet(REMOTE_CTX);
    expect(snippet).toContain("--transport http");
    expect(snippet).toContain(REMOTE_CTX.publicMcpUrl);
    expect(snippet).toContain(`Authorization: Bearer ${REMOTE_CTX.key}`);
    expect(snippet).not.toContain("stdio");
  });
});

/** Narrows a `TomlValue` to a table, so a test can chain `.` access without `any`. */
function asTable(value: TomlValue | undefined): TomlTable {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`expected a TOML table, got ${JSON.stringify(value)}`);
  }
  return value;
}

describe("codexSnippet", () => {
  it("local: valid TOML with command/args/env", () => {
    const parsed = parseMiniToml(codexSnippet(CTX));
    const lkap = asTable(asTable(parsed.mcp_servers).lkap);
    expect(lkap.command).toBe("uv");
    expect(lkap.args).toEqual(["run", "--project", "<checkout>/mcp", "lkap-mcp"]);
    const env = asTable(lkap.env);
    expect(env.LKAP_API_URL).toBe(CTX.apiOrigin);
    expect(env.LKAP_API_KEY).toBe(CTX.key);
  });

  it("remote: valid TOML with url and a bearer header", () => {
    const parsed = parseMiniToml(codexSnippet(REMOTE_CTX));
    const lkap = asTable(asTable(parsed.mcp_servers).lkap);
    expect(lkap.url).toBe(REMOTE_CTX.publicMcpUrl);
    expect(asTable(lkap.http_headers).Authorization).toBe(`Bearer ${REMOTE_CTX.key}`);
  });
});

describe("cursorSnippet", () => {
  it("local: valid JSON with command/args/env", () => {
    const parsed = JSON.parse(cursorSnippet(CTX));
    expect(parsed.mcpServers.lkap.command).toBe("uv");
    expect(parsed.mcpServers.lkap.args).toEqual(["run", "--project", "<checkout>/mcp", "lkap-mcp"]);
    expect(parsed.mcpServers.lkap.env).toEqual({ LKAP_API_URL: CTX.apiOrigin, LKAP_API_KEY: CTX.key });
  });

  it("remote: valid JSON with url and headers", () => {
    const parsed = JSON.parse(cursorSnippet(REMOTE_CTX));
    expect(parsed.mcpServers.lkap.url).toBe(REMOTE_CTX.publicMcpUrl);
    expect(parsed.mcpServers.lkap.headers).toEqual({ Authorization: `Bearer ${REMOTE_CTX.key}` });
  });
});

describe("AGENT_KEY_PRESETS", () => {
  it("read-only ⊆ builder ⊆ operator, and calls:write is in none of them", () => {
    const readOnly = presetById("read_only").scopes;
    const builder = presetById("builder").scopes;
    const operator = presetById("operator").scopes;
    for (const scope of readOnly) expect(builder).toContain(scope);
    for (const scope of builder) expect(operator).toContain(scope);
    for (const preset of AGENT_KEY_PRESETS) expect(preset.scopes).not.toContain(CALLS_WRITE_SCOPE);
  });

  it("never offers the '*' scope (R-V3-9)", () => {
    for (const preset of AGENT_KEY_PRESETS) expect(preset.scopes).not.toContain("*");
  });
});

describe("expiresAtIso", () => {
  it("adds the given number of days to the reference date", () => {
    const from = new Date("2026-01-01T00:00:00.000Z");
    expect(expiresAtIso(30, from)).toBe("2026-01-31T00:00:00.000Z");
    expect(expiresAtIso(7, from)).toBe("2026-01-08T00:00:00.000Z");
    expect(expiresAtIso(1, from)).toBe("2026-01-02T00:00:00.000Z");
  });
});

describe("AGENT_KEY_EXPIRY_OPTIONS", () => {
  it("offers 1, 7, 30, 90 and 365 days, shortest first (asks #35)", () => {
    expect(AGENT_KEY_EXPIRY_OPTIONS).toEqual([
      { days: 1, label: "1 day" },
      { days: 7, label: "7 days" },
      { days: 30, label: "30 days" },
      { days: 90, label: "90 days" },
      { days: 365, label: "365 days" },
    ]);
  });
});

describe("snippetClientFor", () => {
  it("maps 'other' to the generic Cursor/JSON tab and leaves the rest unchanged", () => {
    expect(snippetClientFor("other")).toBe("cursor");
    expect(snippetClientFor("claude-code")).toBe("claude-code");
    expect(snippetClientFor("codex")).toBe("codex");
    expect(snippetClientFor("cursor")).toBe("cursor");
  });
});

describe("skillInstallLine", () => {
  it("uses the <checkout> placeholder when no path is given", () => {
    expect(skillInstallLine()).toBe("<checkout>/scripts/install_claude_skill.sh");
  });
});

/**
 * R-V3-40 (V3-07-4, verdict C1): the warning and the acknowledgement label
 * widened to name the client's own hooks and plugins, and the warning leads
 * with the by-reference form before it accepts an inline paste.
 */
describe("TRANSCRIPT_WARNING", () => {
  it("names hooks and plugins, not just the transcript", () => {
    expect(TRANSCRIPT_WARNING).toContain("hooks");
    expect(TRANSCRIPT_WARNING).toContain("plugins");
  });

  it("leads with the by-reference form (env:/file:) before it accepts pasting", () => {
    const referenceAt = TRANSCRIPT_WARNING.indexOf("env:NAME");
    const fileRefAt = TRANSCRIPT_WARNING.indexOf("file:~/.config/lkap/dev.env#NAME");
    const pasteAt = TRANSCRIPT_WARNING.indexOf("Paste a secret only if");
    expect(referenceAt).toBeGreaterThan(-1);
    expect(fileRefAt).toBeGreaterThan(-1);
    expect(pasteAt).toBeGreaterThan(-1);
    expect(referenceAt).toBeLessThan(pasteAt);
    expect(fileRefAt).toBeLessThan(pasteAt);
  });

  it("never carries a literal agent key", () => {
    expect(TRANSCRIPT_WARNING).not.toMatch(/lkap_/);
  });
});

describe("TRANSCRIPT_ACK_LABEL", () => {
  it("names the transcript, hooks and plugins", () => {
    expect(TRANSCRIPT_ACK_LABEL).toContain("hooks and plugins");
    expect(TRANSCRIPT_ACK_LABEL).toContain("transcript");
  });
});
