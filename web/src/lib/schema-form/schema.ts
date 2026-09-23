/**
 * JSON Schema → field descriptors (V2-16, `@rjsf`-free).
 *
 * Understands the subset Pydantic emits for the flow node models
 * (`GET /v1/flows/node-specs`): `type` string/integer/number/boolean/array/object,
 * `enum`, `const`, `anyOf` with `null` (optional fields), `$ref` into `$defs`,
 * and tuples (`prefixItems`). Anything it cannot map to a plain control becomes a
 * `json` field — never silently dropped, so a new contract field always shows up.
 */

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  items?: JsonSchema | false;
  prefixItems?: JsonSchema[];
  properties?: Record<string, JsonSchema>;
  additionalProperties?: JsonSchema | boolean;
  propertyNames?: JsonSchema;
  required?: string[];
  pattern?: string;
  minimum?: number;
  maximum?: number;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
}

export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "integer"
  | "boolean"
  | "enum"
  | "string-list"
  | "const"
  | "json";

export interface FieldDescriptor {
  /** Property name in the value object. */
  name: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  /** `null` is a valid value (Pydantic `X | None`). */
  nullable: boolean;
  description?: string;
  default?: unknown;
  /** `enum` options (stringified). */
  options?: string[];
  pattern?: string;
  /** The resolved property schema. */
  schema: JsonSchema;
}

export interface FieldsFromSchemaOptions {
  /** String properties rendered as a multi-line textarea. */
  multiline?: readonly string[];
}

/** `max_turns` → `Max turns`; `kb_ids` → `Kb ids` (callers override labels they care about). */
export function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

/** Resolve a local `#/$defs/Name` reference against the root schema. */
export function resolveRef(schema: JsonSchema, root: JsonSchema): JsonSchema {
  let current = schema;
  for (let depth = 0; current.$ref && depth < 8; depth += 1) {
    const match = /^#\/(?:\$defs|definitions)\/(.+)$/.exec(current.$ref);
    const target = match ? root.$defs?.[match[1]] : undefined;
    if (!target) return current;
    const rest: JsonSchema = { ...current };
    delete rest.$ref;
    current = { ...target, ...rest };
  }
  return current;
}

function typesOf(schema: JsonSchema): string[] {
  if (Array.isArray(schema.type)) return schema.type;
  return schema.type ? [schema.type] : [];
}

/** Split `X | None` (`anyOf: [X, {type: "null"}]`) into `X` and a nullable flag. */
export function unwrapNullable(schema: JsonSchema, root: JsonSchema): { schema: JsonSchema; nullable: boolean } {
  const resolved = resolveRef(schema, root);
  const branches = resolved.anyOf ?? resolved.oneOf;
  if (branches) {
    const nonNull = branches.filter((branch) => !typesOf(branch).includes("null"));
    const nullable = nonNull.length < branches.length;
    if (nonNull.length === 1) {
      const rest: JsonSchema = { ...resolved };
      delete rest.anyOf;
      delete rest.oneOf;
      return { schema: { ...resolveRef(nonNull[0], root), ...rest }, nullable };
    }
    return { schema: resolved, nullable };
  }
  const types = typesOf(resolved);
  if (types.includes("null") && types.length > 1) {
    return { schema: { ...resolved, type: types.filter((t) => t !== "null") }, nullable: true };
  }
  return { schema: resolved, nullable: false };
}

function kindOf(name: string, schema: JsonSchema, root: JsonSchema, options: FieldsFromSchemaOptions): FieldKind {
  if (schema.const !== undefined) return "const";
  if (schema.enum && schema.enum.length > 0) return "enum";
  const types = typesOf(schema);
  if (types.length !== 1) return "json";
  switch (types[0]) {
    case "string":
      return options.multiline?.includes(name) ? "textarea" : "text";
    case "integer":
      return "integer";
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "array": {
      if (schema.prefixItems || !schema.items) return "json";
      const item = unwrapNullable(schema.items, root).schema;
      return typesOf(item).length === 1 && typesOf(item)[0] === "string" && !item.enum ? "string-list" : "json";
    }
    default:
      return "json";
  }
}

/**
 * One descriptor per property of an object schema, in declaration order.
 *
 * @param schema - An object schema (a node spec's `json_schema`).
 * @param options - Presentation hints.
 */
export function fieldsFromSchema(schema: JsonSchema, options: FieldsFromSchemaOptions = {}): FieldDescriptor[] {
  const root = schema;
  const object = resolveRef(schema, root);
  const required = new Set(object.required ?? []);
  return Object.entries(object.properties ?? {}).map(([name, property]) => {
    const { schema: resolved, nullable } = unwrapNullable(property, root);
    const kind = kindOf(name, resolved, root, options);
    const descriptor: FieldDescriptor = {
      name,
      label: humanize(name),
      kind,
      required: required.has(name),
      nullable,
      schema: resolved,
    };
    const description = property.description ?? resolved.description;
    if (description) descriptor.description = description;
    const fallback = property.default !== undefined ? property.default : resolved.default;
    if (fallback !== undefined) descriptor.default = fallback;
    if (resolved.enum) descriptor.options = resolved.enum.map((option) => String(option));
    if (resolved.pattern) descriptor.pattern = resolved.pattern;
    return descriptor;
  });
}

/** The value a field shows: the object's own value, else the schema default. */
export function fieldValue(field: FieldDescriptor, value: Record<string, unknown>): unknown {
  return Object.prototype.hasOwnProperty.call(value, field.name) ? value[field.name] : field.default;
}
