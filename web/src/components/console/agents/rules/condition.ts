/**
 * V6-15 (`docs/v6/PLAN-V6.md` D-V6-25, `contracts/src/lkap_contracts/rules_expr.py`): a
 * console-side port of the rule condition grammar's **tokenizer and parser only** (no
 * evaluator — the worker is the only place a condition ever runs). This lets the Rules
 * editor validate a condition inline, with the api's exact wording and 1-based character
 * position, before a save round-trips to find out. The api's `parse_condition` stays the
 * single source of truth: `lkap_contracts.rules.Rule._parses` calls it inside a Pydantic
 * `field_validator`, so a bad condition is refused before `rule_issues` ever runs — the
 * only way "an invalid condition shows the api's message inline and the save is blocked"
 * (V6-15 acceptance) holds without a round trip is for this parser to refuse the exact
 * same inputs with the exact same message.
 *
 * One deliberate divergence from a byte-for-byte port: `_check_pattern`'s `re.compile`
 * call is Python's regex engine, not JavaScript's — a pattern the browser's `RegExp`
 * can't compile (a Python-only construct such as `(?P<name>...)`) may still be perfectly
 * valid on the worker. Mirroring `guardrails/pattern-tester.tsx`'s `hasNestedRepeat`
 * precedent, a `RegExp` compile failure here is silently allowed through rather than
 * reported as invalid; length and nested-repeat are checked exactly as the api does,
 * since both are pure string checks.
 *
 * `hasNestedRepeat` below is a second copy of `guardrails/pattern-tester.tsx`'s function
 * of the same name (itself a copy of `config_service.py::nested_repeat`, which V6-13's
 * ask #75 consolidated with `rules_expr.nested_repeat` on the Python side) rather than an
 * import of it: that file carries `"use client"`, and this module is imported from
 * `lib/schemas.ts`, which is not itself client-scoped.
 */

//: Longest condition (`rules_expr.MAX_CONDITION_CHARS`).
export const MAX_CONDITION_CHARS = 200;
//: Deepest nesting of parentheses and `not` (`rules_expr.MAX_DEPTH`).
export const MAX_DEPTH = 8;
//: Most predicates in one condition (`rules_expr.MAX_PREDICATES`).
export const MAX_PREDICATES = 12;
//: Longest string literal or pattern (`rules_expr.MAX_LITERAL_CHARS`).
export const MAX_LITERAL_CHARS = 100;

export type CompareOp = "==" | "!=" | ">=" | "<=" | ">" | "<";
export type Literal = string | number | boolean;

export class ConditionError extends Error {
  /** 0-based offset into the condition text, or `null` when the message names no position. */
  readonly position: number | null;
  readonly reason: string;

  constructor(message: string, position: number | null = null) {
    super(position === null ? message : `${message} (at character ${position + 1})`);
    this.reason = message;
    this.position = position;
    this.name = "ConditionError";
  }
}

// --------------------------------------------------------------------------- the tree

export interface IsSet {
  kind: "is_set";
  name: string;
}
export interface Compare {
  kind: "compare";
  name: string;
  op: CompareOp;
  value: Literal;
}
export interface Matches {
  kind: "matches";
  name: string;
  pattern: string;
  ignoreCase: boolean;
}
export interface ToolOutcome {
  kind: "tool_outcome";
  name: string;
  outcome: "ok" | "failed";
}
export interface Not {
  kind: "not";
  item: Expr;
}
export interface And {
  kind: "and";
  items: Expr[];
}
export interface Or {
  kind: "or";
  items: Expr[];
}

export type Expr = IsSet | Compare | Matches | ToolOutcome | Not | And | Or;

// --------------------------------------------------------------------------- tokens

type TokenKind = "lparen" | "rparen" | "op" | "var" | "tool" | "string" | "number" | "regex" | "word" | "end";

interface Token {
  kind: TokenKind;
  text: string;
  position: number;
  value?: unknown;
}

const VAR_RE = /^var\.([a-z][a-z0-9_]{0,63})(?![A-Za-z0-9_.])/;
const TOOL_RE = /^tool\.([A-Za-z_][A-Za-z0-9_]{0,63})\.(ok|failed)(?![A-Za-z0-9_.])/;
const NUMBER_RE = /^-?\d{1,15}(?:\.\d{1,15})?(?![A-Za-z0-9_.])/;
const WORD_RE = /^[A-Za-z_][A-Za-z0-9_]*/;
const OPS: readonly CompareOp[] = ["==", "!=", ">=", "<=", ">", "<"];
const KEYWORDS = new Set(["and", "or", "not", "is", "set", "empty", "matches", "true", "false"]);

function readString(text: string, start: number): { value: string; end: number } {
  const quote = text[start];
  let index = start + 1;
  const out: string[] = [];
  while (index < text.length) {
    const char = text[index];
    if (char === "\\" && index + 1 < text.length && (text[index + 1] === quote || text[index + 1] === "\\")) {
      out.push(text[index + 1]);
      index += 2;
      continue;
    }
    if (char === quote) {
      const value = out.join("");
      if (value.length > MAX_LITERAL_CHARS) {
        throw new ConditionError(`text in quotes is longer than ${MAX_LITERAL_CHARS} characters`, start);
      }
      return { value, end: index + 1 };
    }
    out.push(char);
    index += 1;
  }
  throw new ConditionError("text in quotes is not closed", start);
}

function readRegex(text: string, start: number): { pattern: string; ignoreCase: boolean; end: number } {
  let index = start + 1;
  while (index < text.length) {
    const char = text[index];
    if (char === "\\") {
      index += 2;
      continue;
    }
    if (char === "/") {
      const pattern = text.slice(start + 1, index);
      index += 1;
      const flags = WORD_RE.exec(text.slice(index));
      let ignoreCase = false;
      if (flags) {
        if (flags[0] !== "i") {
          throw new ConditionError("the only pattern flag is 'i' (ignore case)", index);
        }
        ignoreCase = true;
        index += flags[0].length;
      }
      return { pattern, ignoreCase, end: index };
    }
    index += 1;
  }
  throw new ConditionError("a pattern must end with '/'", start);
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let index = 0;
  while (index < text.length) {
    const char = text[index];
    if (/\s/.test(char)) {
      index += 1;
      continue;
    }
    if (char === "(") {
      tokens.push({ kind: "lparen", text: char, position: index });
      index += 1;
      continue;
    }
    if (char === ")") {
      tokens.push({ kind: "rparen", text: char, position: index });
      index += 1;
      continue;
    }
    if (char === '"' || char === "'") {
      const { value, end } = readString(text, index);
      tokens.push({ kind: "string", text: text.slice(index, end), position: index, value });
      index = end;
      continue;
    }
    if (char === "/") {
      const previous = tokens[tokens.length - 1];
      if (!previous || previous.kind !== "word" || previous.text.toLowerCase() !== "matches") {
        throw new ConditionError("a /pattern/ may only follow 'matches'", index);
      }
      const { pattern, ignoreCase, end } = readRegex(text, index);
      tokens.push({ kind: "regex", text: text.slice(index, end), position: index, value: { pattern, ignoreCase } });
      index = end;
      continue;
    }
    const op = OPS.find((candidate) => text.startsWith(candidate, index));
    if (op) {
      tokens.push({ kind: "op", text: op, position: index });
      index += op.length;
      continue;
    }
    if (text.startsWith("var.", index)) {
      const match = VAR_RE.exec(text.slice(index));
      if (!match) {
        throw new ConditionError(
          "a variable is written var.<name>: lower-case letters, digits and '_', nothing after it",
          index,
        );
      }
      tokens.push({ kind: "var", text: match[0], position: index, value: match[1] });
      index += match[0].length;
      continue;
    }
    if (text.startsWith("tool.", index)) {
      const match = TOOL_RE.exec(text.slice(index));
      if (!match) {
        throw new ConditionError("a tool outcome is written tool.<name>.ok or tool.<name>.failed", index);
      }
      tokens.push({ kind: "tool", text: match[0], position: index, value: [match[1], match[2]] });
      index += match[0].length;
      continue;
    }
    const numberMatch = NUMBER_RE.exec(text.slice(index));
    if (numberMatch && (/\d/.test(char) || char === "-")) {
      tokens.push({ kind: "number", text: numberMatch[0], position: index, value: Number(numberMatch[0]) });
      index += numberMatch[0].length;
      continue;
    }
    const wordMatch = WORD_RE.exec(text.slice(index));
    if (wordMatch) {
      if (!KEYWORDS.has(wordMatch[0].toLowerCase())) {
        throw new ConditionError(
          `'${wordMatch[0]}' is not something a condition understands; name a variable as var.<name> and put text in quotes`,
          index,
        );
      }
      const wordEnd = index + wordMatch[0].length;
      if (text[wordEnd] === ".") {
        throw new ConditionError("conditions cannot read attributes or call anything", wordEnd);
      }
      tokens.push({ kind: "word", text: wordMatch[0], position: index });
      index = wordEnd;
      continue;
    }
    throw new ConditionError(`unexpected character '${char}'`, index);
  }
  tokens.push({ kind: "end", text: "", position: text.length });
  return tokens;
}

// --------------------------------------------------------------------------- parser

class Parser {
  private readonly tokens: Token[];
  private index = 0;
  private predicates = 0;

  constructor(text: string) {
    this.tokens = tokenize(text);
  }

  private get current(): Token {
    return this.tokens[this.index];
  }

  private at(kind: TokenKind): boolean {
    return this.current.kind === kind;
  }

  private word(...words: string[]): boolean {
    const token = this.current;
    return token.kind === "word" && words.includes(token.text.toLowerCase());
  }

  private advance(): Token {
    const token = this.current;
    this.index += 1;
    return token;
  }

  parse(): Expr {
    const expr = this.or(0);
    if (this.current.kind !== "end") {
      throw new ConditionError(`unexpected '${this.current.text}'`, this.current.position);
    }
    return expr;
  }

  private or(depth: number): Expr {
    const items = [this.and(depth)];
    while (this.word("or")) {
      this.advance();
      items.push(this.and(depth));
    }
    return items.length === 1 ? items[0] : { kind: "or", items };
  }

  private and(depth: number): Expr {
    const items = [this.unary(depth)];
    while (this.word("and")) {
      this.advance();
      items.push(this.unary(depth));
    }
    return items.length === 1 ? items[0] : { kind: "and", items };
  }

  private unary(depth: number): Expr {
    if (depth >= MAX_DEPTH) {
      throw new ConditionError(`conditions nest at most ${MAX_DEPTH} levels deep`, this.current.position);
    }
    if (this.word("not")) {
      this.advance();
      return { kind: "not", item: this.unary(depth + 1) };
    }
    if (this.at("lparen")) {
      const opening = this.advance();
      const inner = this.or(depth + 1);
      if (!this.at("rparen")) {
        throw new ConditionError("a '(' is not closed", opening.position);
      }
      this.advance();
      return inner;
    }
    return this.predicate();
  }

  private count(position: number): void {
    this.predicates += 1;
    if (this.predicates > MAX_PREDICATES) {
      throw new ConditionError(`a condition has at most ${MAX_PREDICATES} checks`, position);
    }
  }

  private predicate(): Expr {
    const token = this.current;
    if (token.kind === "tool") {
      this.advance();
      this.count(token.position);
      const [name, outcome] = token.value as [string, "ok" | "failed"];
      return { kind: "tool_outcome", name, outcome };
    }
    if (token.kind !== "var") {
      if (token.kind === "end") throw new ConditionError("the condition ends too early", token.position);
      throw new ConditionError(`expected var.<name> or tool.<name>.ok, found '${token.text}'`, token.position);
    }
    this.advance();
    this.count(token.position);
    const name = token.value as string;
    const follow = this.current;
    if (this.word("is")) {
      this.advance();
      let negate = false;
      if (this.word("not")) {
        this.advance();
        negate = true;
      }
      if (this.word("set")) {
        this.advance();
        const expr: Expr = { kind: "is_set", name };
        return negate ? { kind: "not", item: expr } : expr;
      }
      if (this.word("empty")) {
        this.advance();
        const isSet: Expr = { kind: "is_set", name };
        return negate ? isSet : { kind: "not", item: isSet };
      }
      throw new ConditionError("after 'is' write 'set', 'empty', 'not set' or 'not empty'", follow.position);
    }
    if (this.word("matches")) {
      this.advance();
      const regex = this.current;
      if (regex.kind !== "regex") {
        throw new ConditionError("after 'matches' write a /pattern/", regex.position);
      }
      this.advance();
      const { pattern, ignoreCase } = regex.value as { pattern: string; ignoreCase: boolean };
      checkPattern(pattern, regex.position);
      return { kind: "matches", name, pattern, ignoreCase };
    }
    if (follow.kind === "op") {
      const op = follow.text as CompareOp;
      this.advance();
      const literal = this.current;
      if (op === "==" || op === "!=") {
        if (literal.kind === "string" || literal.kind === "number") {
          this.advance();
          return { kind: "compare", name, op, value: literal.value as Literal };
        }
        if (this.word("true", "false")) {
          this.advance();
          return { kind: "compare", name, op, value: literal.text.toLowerCase() === "true" };
        }
        throw new ConditionError(`after '${op}' write text in quotes, a number, true or false`, literal.position);
      }
      if (literal.kind !== "number") {
        throw new ConditionError(`after '${op}' write a number`, literal.position);
      }
      this.advance();
      return { kind: "compare", name, op, value: literal.value as number };
    }
    throw new ConditionError(
      "after a variable write 'is set', 'is empty', '==', '!=', '>=', '<=', '>', '<' or 'matches'",
      follow.position,
    );
  }
}

/**
 * Whether `pattern` repeats a group that itself holds an unbounded repeat (`(a+)+`,
 * `(\w*\s)*`) — mirrors `rules_expr.nested_repeat` / `guardrails/pattern-tester.tsx`'s
 * `hasNestedRepeat` (see the file header for why this is a second copy, not an import).
 */
function hasNestedRepeat(pattern: string): boolean {
  const unbounded = /[+*]|\{\d*,\}/y;
  const stack: boolean[] = [];
  let index = 0;
  let inClass = false;
  while (index < pattern.length) {
    const char = pattern[index];
    if (char === "\\") {
      index += 2;
      continue;
    }
    if (inClass) {
      inClass = char !== "]";
    } else if (char === "[") {
      inClass = true;
    } else if (char === "(") {
      stack.push(false);
    } else if (char === ")" && stack.length > 0) {
      const inner = stack.pop() as boolean;
      const next = pattern.slice(index + 1, index + 2);
      if (inner && (next === "+" || next === "*" || next === "{")) return true;
      if (stack.length > 0) stack[stack.length - 1] = stack[stack.length - 1] || inner;
    } else if (stack.length > 0) {
      unbounded.lastIndex = index;
      if (unbounded.test(pattern)) stack[stack.length - 1] = true;
    }
    index += 1;
  }
  return false;
}

/**
 * Length and nested-repeat are pure string checks, mirrored exactly
 * (`rules_expr._check_pattern`); a `RegExp` compile failure is not reported here — see the
 * file header.
 */
function checkPattern(pattern: string, position: number): void {
  if (!pattern) throw new ConditionError("the pattern is empty", position);
  if (pattern.length > MAX_LITERAL_CHARS) {
    throw new ConditionError(`a pattern is at most ${MAX_LITERAL_CHARS} characters`, position);
  }
  if (hasNestedRepeat(pattern)) {
    throw new ConditionError(
      "this pattern repeats a group that already repeats, which can take very long; simplify it",
      position,
    );
  }
}

/**
 * Parse one condition (`rules_expr.parse_condition`).
 *
 * @throws ConditionError It does not parse or breaks a bound; `.message` reads the way an
 *   admin can act on, with a 1-based character position when one applies.
 */
export function parseCondition(text: string): Expr {
  if (!text || !text.trim()) throw new ConditionError("the condition is empty");
  if (text.length > MAX_CONDITION_CHARS) {
    throw new ConditionError(`a condition is at most ${MAX_CONDITION_CHARS} characters`);
  }
  return new Parser(text).parse();
}

/** The exact prefix `lkap_contracts.rules.Rule._parses` puts in front of a `ConditionError`. */
export const CONDITION_ERROR_PREFIX = "the condition does not read: ";

/** `"the condition does not read: …"` — what the api's `field_validator` raises, verbatim. */
export function conditionErrorMessage(error: ConditionError): string {
  return `${CONDITION_ERROR_PREFIX}${error.message}`;
}

/** `null` when `text` parses; the api-style message otherwise. Never throws. */
export function conditionIssue(text: string): string | null {
  try {
    parseCondition(text);
    return null;
  } catch (error) {
    if (error instanceof ConditionError) return conditionErrorMessage(error);
    throw error;
  }
}

// --------------------------------------------------------------------------- analysis

export function referencedVariables(expr: Expr): Set<string> {
  switch (expr.kind) {
    case "is_set":
    case "compare":
    case "matches":
      return new Set([expr.name]);
    case "tool_outcome":
      return new Set();
    case "not":
      return referencedVariables(expr.item);
    case "and":
    case "or":
      return expr.items.reduce((acc, item) => {
        for (const name of referencedVariables(item)) acc.add(name);
        return acc;
      }, new Set<string>());
  }
}

export function referencedTools(expr: Expr): Set<string> {
  switch (expr.kind) {
    case "tool_outcome":
      return new Set([expr.name]);
    case "not":
      return referencedTools(expr.item);
    case "and":
    case "or":
      return expr.items.reduce((acc, item) => {
        for (const name of referencedTools(item)) acc.add(name);
        return acc;
      }, new Set<string>());
    default:
      return new Set();
  }
}

// --------------------------------------------------------------------------- the builder <-> text bridge

/** One row of the condition builder: a plain-words predicate over a variable or a tool call. */
export interface ConditionRow {
  subject: "var" | "tool";
  name: string;
  op: "is_set" | "is_empty" | "eq" | "neq" | "gte" | "lte" | "gt" | "lt" | "matches" | "tool_ok" | "tool_failed";
  /** Unused for `is_set`/`is_empty`/`tool_ok`/`tool_failed`. */
  value?: string;
  /** `matches` only. */
  ignoreCase?: boolean;
}

const COMPARE_OP_OF: Record<"eq" | "neq" | "gte" | "lte" | "gt" | "lt", CompareOp> = {
  eq: "==",
  neq: "!=",
  gte: ">=",
  lte: "<=",
  gt: ">",
  lt: "<",
};
const ROW_OP_OF: Record<CompareOp, "eq" | "neq" | "gte" | "lte" | "gt" | "lt"> = {
  "==": "eq",
  "!=": "neq",
  ">=": "gte",
  "<=": "lte",
  ">": "gt",
  "<": "lt",
};

function quote(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** Whether a builder value, taken literally, reads as a number (the grammar's `NUMBER`). */
export function looksNumeric(value: string): boolean {
  return /^-?\d{1,15}(?:\.\d{1,15})?$/.test(value.trim());
}

/** One row → its condition text (`var.x == "y"`, `tool.lookup.ok`, …). */
export function rowToText(row: ConditionRow): string {
  if (row.subject === "tool") {
    return `tool.${row.name}.${row.op === "tool_failed" ? "failed" : "ok"}`;
  }
  const name = `var.${row.name}`;
  switch (row.op) {
    case "is_set":
      return `${name} is set`;
    case "is_empty":
      return `${name} is empty`;
    case "matches":
      return `${name} matches /${row.value ?? ""}/${row.ignoreCase ? "i" : ""}`;
    case "eq":
    case "neq":
    case "gte":
    case "lte":
    case "gt":
    case "lt": {
      const op = COMPARE_OP_OF[row.op];
      const raw = row.value ?? "";
      if (op === "==" || op === "!=") {
        const trimmed = raw.trim().toLowerCase();
        if (trimmed === "true" || trimmed === "false") return `${name} ${op} ${trimmed}`;
        if (looksNumeric(raw)) return `${name} ${op} ${raw.trim()}`;
        return `${name} ${op} ${quote(raw)}`;
      }
      return `${name} ${op} ${raw.trim()}`;
    }
    default:
      // Unreachable: `subject === "var"` rules out the tool-only ops above.
      return `${name} is set`;
  }
}

/** Rows joined as "all of these" (`and`) — the only combinator the builder writes. */
export function rowsToCondition(rows: readonly ConditionRow[]): string {
  return rows.map(rowToText).join(" and ");
}

function rowFromExpr(expr: Expr): ConditionRow | null {
  switch (expr.kind) {
    case "is_set":
      return { subject: "var", name: expr.name, op: "is_set" };
    case "matches":
      return { subject: "var", name: expr.name, op: "matches", value: expr.pattern, ignoreCase: expr.ignoreCase };
    case "compare":
      return {
        subject: "var",
        name: expr.name,
        op: ROW_OP_OF[expr.op],
        value: typeof expr.value === "string" ? expr.value : String(expr.value),
      };
    case "tool_outcome":
      return { subject: "tool", name: expr.name, op: expr.outcome === "ok" ? "tool_ok" : "tool_failed" };
    case "not":
      return expr.item.kind === "is_set" ? { subject: "var", name: expr.item.name, op: "is_empty" } : null;
    default:
      return null;
  }
}

/**
 * Decompose a condition into builder rows, only when it is a single predicate or a plain
 * `and` of predicates (no `or`, no other `not`, no parentheses) — everything the builder
 * itself can produce. `null` means "keep editing this as text": the raw-text escape hatch
 * is one-way past that point, same as this file's header explains.
 */
export function conditionToRows(expr: Expr): ConditionRow[] | null {
  const items = expr.kind === "and" ? expr.items : [expr];
  const rows = items.map(rowFromExpr);
  return rows.every((row): row is ConditionRow => row !== null) ? rows : null;
}
