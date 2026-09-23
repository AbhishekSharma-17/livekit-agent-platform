/**
 * The block catalog: one entry per `BlockSpec.type` (CONTRACTS-V2 §4.4).
 *
 * Pure data — no React — so the session bundle (default titles, initial
 * state) and the console's panel composer (palette copy, config forms, block
 * tools) read the same source.
 *
 * ### Config fields
 *
 * `configFields` is the block's config schema, and the composer generates its
 * per-block form from it. The keys are exactly the ones the worker honours
 * today: `initial_block_state` (agent `ui/blocks.py`) seeds a block's state
 * from every `BlockSpec.config` key that names a field of the block's state
 * model, and ignores everything else. So a table's `columns`, a document's
 * `url`/`page`, a transcript's `show_tools` and a video's `source`/`muted`
 * are configurable, and nothing else is — `BlockSpec.config` is public
 * (R-V2-7), so it never carries anything but these.
 */
import type {
  DocumentBlockState,
  FormBlockState,
  GalleryBlockState,
  KbCitationsBlockState,
  TableBlockState,
  TableColumn,
  TranscriptBlockState,
  VideoBlockState,
} from "@/contracts/lkap-contracts";

import type { BlockSpecV2, BlockType } from "@/panels/composite/layout";

/** Every block type, in palette order. */
export const BLOCK_TYPES: readonly BlockType[] = [
  "status",
  "notes",
  "checklist",
  "activity",
  "form",
  "table",
  "document",
  "gallery",
  "kb_citations",
  "transcript",
  "video",
  "custom",
];

/** Types whose state is the envelope (`status`, `notes`, …) and hold `{}`. */
export const ENVELOPE_BLOCK_TYPES: ReadonlySet<BlockType> = new Set<BlockType>([
  "status",
  "notes",
  "checklist",
  "activity",
  "custom",
]);

/** Built-in block tools (agent `tools/builtin/__init__.py::BLOCK_TOOL_NAMES`). */
export type BlockToolName = "update_block" | "show_document" | "table_append" | "request_form";

/** Block types `update_block` may write (agent `UPDATABLE_BLOCK_TYPES`). */
export const UPDATABLE_BLOCK_TYPES: ReadonlySet<BlockType> = new Set<BlockType>([
  "document",
  "gallery",
  "table",
  "transcript",
  "video",
  "kb_citations",
  "custom",
]);

/**
 * When the worker registers each block tool (agent
 * `build_builtin_tools`): `update_block` for any updatable block, the other
 * three for a block of their own type. `builtin_disabled` still turns them off.
 */
export const BLOCK_TOOL_TYPES: Record<BlockToolName, ReadonlySet<BlockType>> = {
  update_block: UPDATABLE_BLOCK_TYPES,
  show_document: new Set<BlockType>(["document"]),
  table_append: new Set<BlockType>(["table"]),
  request_form: new Set<BlockType>(["form"]),
};

/** One field of a block's config form. */
export type BlockConfigField =
  | { key: string; label: string; kind: "boolean"; hint?: string; default: boolean }
  | { key: string; label: string; kind: "integer"; hint?: string; default: number; min?: number }
  | { key: string; label: string; kind: "url"; hint?: string; default: string }
  | {
      key: string;
      label: string;
      kind: "select";
      hint?: string;
      default: string;
      options: readonly { value: string; label: string }[];
    }
  | { key: "columns"; label: string; kind: "columns"; hint?: string; default: TableColumn[] };

export interface BlockCatalogEntry {
  type: BlockType;
  /** Palette label, sentence case. */
  label: string;
  /** One line for the add-block palette. */
  description: string;
  /** The heading shown when `BlockSpec.title` is empty; `null` = no heading. */
  defaultTitle: string | null;
  /** Default id stem for a new block of this type (`table`, `table_2`, …). */
  idStem: string;
  configFields: readonly BlockConfigField[];
  /** The agent tool that fills this block, for the composer's hint. */
  filledBy: string;
}

export const TABLE_COLUMN_TYPES: readonly TableColumn["type"][] = ["string", "number", "boolean", "date"];

export const VIDEO_SOURCES = [
  { value: "agent_avatar", label: "The agent's avatar" },
  { value: "user_camera", label: "The caller's camera" },
  { value: "user_screen", label: "The caller's shared screen" },
] as const;

export const BLOCK_CATALOG: Record<BlockType, BlockCatalogEntry> = {
  status: {
    type: "status",
    label: "Status",
    description: "The status stamp and progress bar the agent sets.",
    defaultTitle: null,
    idStem: "status",
    configFields: [],
    filledBy: "set_status",
  },
  notes: {
    type: "notes",
    label: "Notes",
    description: "What the agent has written down during the call.",
    defaultTitle: "Notes",
    idStem: "notes",
    configFields: [],
    filledBy: "push_note",
  },
  checklist: {
    type: "checklist",
    label: "Checklist",
    description: "What the agent still needs from the caller.",
    defaultTitle: "Still needed",
    idStem: "checklist",
    configFields: [],
    filledBy: "the pack's checklist",
  },
  activity: {
    type: "activity",
    label: "Activity",
    description: "The agent's tool calls, newest first.",
    defaultTitle: "Activity",
    idStem: "activity",
    configFields: [],
    filledBy: "every tool call",
  },
  form: {
    type: "form",
    label: "Form",
    description: "A form the agent asks the caller to fill in.",
    defaultTitle: "Your details",
    idStem: "form",
    configFields: [],
    filledBy: "request_form",
  },
  table: {
    type: "table",
    label: "Table",
    description: "Rows the agent adds as it collects them.",
    defaultTitle: "Table",
    idStem: "table",
    configFields: [
      {
        key: "columns",
        label: "Columns",
        kind: "columns",
        hint: "Starting columns. The agent adds a column when a row has a new field.",
        default: [],
      },
    ],
    filledBy: "table_append",
  },
  document: {
    type: "document",
    label: "Document",
    description: "A PDF, image or Markdown file, page by page, with highlights.",
    defaultTitle: "Document",
    idStem: "document",
    configFields: [
      {
        key: "url",
        label: "Starting document",
        kind: "url",
        hint: "Optional https:// link shown before the agent opens anything.",
        default: "",
      },
      { key: "page", label: "Starting page", kind: "integer", default: 1, min: 1 },
    ],
    filledBy: "show_document",
  },
  gallery: {
    type: "gallery",
    label: "Gallery",
    description: "Photos the agent captures, such as pinned camera frames.",
    defaultTitle: "Photos",
    idStem: "gallery",
    configFields: [],
    filledBy: "pin_frame",
  },
  kb_citations: {
    type: "kb_citations",
    label: "Sources",
    description: "The knowledge-base passages behind the agent's last answer.",
    defaultTitle: "Sources",
    idStem: "sources",
    configFields: [],
    filledBy: "search_knowledge",
  },
  transcript: {
    type: "transcript",
    label: "Transcript",
    description: "The conversation so far, optionally with tool calls.",
    defaultTitle: "Transcript",
    idStem: "transcript",
    configFields: [
      {
        key: "show_tools",
        label: "Show tool calls",
        kind: "boolean",
        hint: "Interleave the agent's tool calls with the turns.",
        default: false,
      },
    ],
    filledBy: "the conversation",
  },
  video: {
    type: "video",
    label: "Video",
    description: "The avatar, the caller's camera or a shared screen.",
    defaultTitle: "Video",
    idStem: "video",
    configFields: [
      { key: "source", label: "Source", kind: "select", default: "agent_avatar", options: VIDEO_SOURCES },
      { key: "muted", label: "Start muted", kind: "boolean", default: false },
    ],
    filledBy: "update_block",
  },
  custom: {
    type: "custom",
    label: "Pack block",
    description: "A block the agent's pack renders itself.",
    defaultTitle: "Pack data",
    idStem: "custom",
    configFields: [],
    filledBy: "the pack",
  },
};

/** The heading a block shows: its title, else the type's default (`null` = none). */
export function blockTitle(spec: BlockSpecV2): string | null {
  const title = typeof spec.title === "string" ? spec.title.trim() : "";
  return title || BLOCK_CATALOG[spec.type]?.defaultTitle || null;
}

/* -------------------------------------------------------------------------- */
/* Initial state — mirrors the worker's `initial_block_state`                  */
/* -------------------------------------------------------------------------- */

export interface BlockStateByType {
  form: Required<Pick<FormBlockState, "schema" | "values" | "status">> & FormBlockState;
  document: Required<Pick<DocumentBlockState, "page" | "highlights">> & DocumentBlockState;
  gallery: Required<Pick<GalleryBlockState, "asset_ids">> & GalleryBlockState;
  table: Required<Pick<TableBlockState, "columns" | "rows">> & TableBlockState;
  transcript: Required<TranscriptBlockState>;
  video: Required<VideoBlockState>;
  kb_citations: Required<KbCitationsBlockState>;
}

const STATE_DEFAULTS: { [K in keyof BlockStateByType]: () => BlockStateByType[K] } = {
  form: () => ({ schema: {}, values: {}, status: "idle", submitted_at: null }),
  document: () => ({ asset_id: null, url: null, page: 1, highlights: [] }),
  gallery: () => ({ asset_ids: [], selected: null }),
  table: () => ({ columns: [], rows: [], selected_row: null }),
  transcript: () => ({ show_tools: false }),
  video: () => ({ source: "agent_avatar", muted: false }),
  kb_citations: () => ({ items: [] }),
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The state a block renders before the worker's snapshot arrives (pre-call,
 * connecting, the console preview): the type's defaults, seeded by any
 * `BlockSpec.config` key that names a state field — the same rule as the
 * worker's `initial_block_state`. Envelope and custom blocks are `{}`.
 */
export function initialBlockState(spec: BlockSpecV2): Record<string, unknown> {
  const factory = STATE_DEFAULTS[spec.type as keyof BlockStateByType];
  if (!factory) return {};
  const state: Record<string, unknown> = { ...factory() };
  const config = isRecord(spec.config) ? spec.config : {};
  for (const key of Object.keys(state)) {
    if (key in config) state[key] = config[key];
  }
  return state;
}

/**
 * A block's live state: the wire value from `UiState.blocks[id]` laid over
 * the initial state, so a renderer never sees a missing field.
 */
export function blockStateOf(spec: BlockSpecV2, blocks: Record<string, unknown> | undefined): Record<string, unknown> {
  const initial = initialBlockState(spec);
  const live = blocks?.[spec.id];
  return isRecord(live) ? { ...initial, ...live } : initial;
}
