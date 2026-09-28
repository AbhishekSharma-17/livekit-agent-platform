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
import type { DocumentBlockState, FormBlockState, GalleryBlockState, HandoffBlockState, KbCitationsBlockState, TableBlockState, TableColumn, TranscriptBlockState, VideoBlockState, BlockSpec } from "@/contracts/lkap-contracts";

import type { BlockType } from "@/panels/composite/layout";

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
  "choices",
  "details",
  "markdown",
  "steps",
  "consent",
  "upload",
  "captions",
  "handoff",
  "link",
  "slots",
  "cards",
  "notebook",
  "layout",
  "canvas",
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
export type BlockToolName =
  | "update_block"
  | "show_document"
  | "table_append"
  | "request_form"
  | "request_choice"
  | "resolve_choice"
  | "set_details"
  | "show_text"
  | "set_steps"
  | "request_consent"
  | "record_consent"
  | "request_upload"
  | "describe_panel"
  | "send_link"
  | "request_slot"
  | "resolve_slot"
  | "show_cards"
  | "set_checklist"
  | "check_item"
  | "notebook_write"
  | "notebook_check"
  | "draw_on_canvas"
  | "clear_canvas"
  | "read_canvas";

/** Block types `update_block` may write (agent `UPDATABLE_BLOCK_TYPES`). */
export const UPDATABLE_BLOCK_TYPES: ReadonlySet<BlockType> = new Set<BlockType>([
  "document",
  "gallery",
  "table",
  "transcript",
  "video",
  "kb_citations",
  "custom",
  "details",
  "markdown",
  "steps",
  // V5-43
  "cards",
]);

/**
 * When the worker registers each block tool (agent
 * `build_builtin_tools`): `update_block` for any updatable block, the others
 * for a block of their own type (`set_steps` only for a `steps` block that does
 * not follow the flow). `builtin_disabled` still turns them off.
 */
export const BLOCK_TOOL_TYPES: Record<BlockToolName, ReadonlySet<BlockType>> = {
  update_block: UPDATABLE_BLOCK_TYPES,
  show_document: new Set<BlockType>(["document"]),
  table_append: new Set<BlockType>(["table"]),
  request_form: new Set<BlockType>(["form"]),
  request_choice: new Set<BlockType>(["choices"]),
  resolve_choice: new Set<BlockType>(["choices"]),
  set_details: new Set<BlockType>(["details"]),
  show_text: new Set<BlockType>(["markdown"]),
  set_steps: new Set<BlockType>(["steps"]),
  // V5-15: `record_consent` is also registered without a consent block when
  // `recording.require_consent` is on (a spoken answer on any channel).
  request_consent: new Set<BlockType>(["consent"]),
  record_consent: new Set<BlockType>(["consent"]),
  // V5-19
  request_upload: new Set<BlockType>(["upload"]),
  // V5-43: `describe_panel` is registered whenever the panel has any block.
  describe_panel: new Set<BlockType>(BLOCK_TYPES),
  send_link: new Set<BlockType>(["link"]),
  request_slot: new Set<BlockType>(["slots"]),
  resolve_slot: new Set<BlockType>(["slots"]),
  show_cards: new Set<BlockType>(["cards"]),
  // V6-06
  set_checklist: new Set<BlockType>(["checklist"]),
  check_item: new Set<BlockType>(["checklist"]),
  // V6-08
  notebook_write: new Set<BlockType>(["notebook"]),
  notebook_check: new Set<BlockType>(["notebook"]),
  // V6-12
  draw_on_canvas: new Set<BlockType>(["canvas"]),
  clear_canvas: new Set<BlockType>(["canvas"]),
  read_canvas: new Set<BlockType>(["canvas"]),
};

/** One field of a block's config form. */
export type BlockConfigField =
  | {
      key: string;
      label: string;
      kind: "boolean";
      hint?: string;
      default: boolean;
      /** Shown, switched off, greyed (`notebook.caller_can_draw` until V6-12's drawing board). */
      disabled?: boolean;
    }
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
  | { key: "columns"; label: string; kind: "columns"; hint?: string; default: TableColumn[] }
  | {
      key: string;
      label: string;
      /** Free text (`consent.text`, V5-15); empty means "use the default". */
      kind: "text";
      hint?: string;
      default: string;
      maxLength: number;
    }
  | {
      key: string;
      label: string;
      /** A list of small objects (`details.fields`, `steps.steps`); edited by `block-config-form.tsx::ListEditor`. */
      kind: "list";
      hint?: string;
      default: Record<string, unknown>[];
      itemKeys: readonly string[];
    }
  | {
      key: "sections";
      label: string;
      /**
       * A `notebook`'s sections (V6-10): id, title, kind (`text` / `checklist`
       * / `details` / `ink`) — edited by `block-config-form.tsx::NotebookSectionsEditor`.
       */
      kind: "sections";
      hint?: string;
      default: { id: string; title: string; kind: string }[];
    }
  | {
      key: "children";
      label: string;
      /**
       * A `layout`'s children (V6-10): another block of the panel plus an
       * optional tab label — edited by `block-config-form.tsx::LayoutChildrenEditor`.
       */
      kind: "children";
      hint?: string;
      default: { block_id: string; label: string | null }[];
    }
  | {
      key: string;
      label: string;
      /** Several values picked from `options` (`upload.accept`, V5-19); the editor comes with V5-23. */
      kind: "multiselect";
      hint?: string;
      default: string[];
      options: readonly { value: string; label: string }[];
    }
  | {
      key: string;
      label: string;
      /** A language code or none (`captions.target_language`, V5-31); the editor comes with V5-35. */
      kind: "language";
      hint?: string;
      default: string | null;
    }
  | {
      key: string;
      label: string;
      /**
       * Site names such as `example.com` or `*.example.com` (`link.allowed_hosts`,
       * `cards.image_hosts`, V5-43); the editor comes with V5-44.
       */
      kind: "hosts";
      hint?: string;
      default: string[];
    };

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

export const CHOICE_LAYOUTS = [
  { value: "buttons", label: "Buttons" },
  { value: "list", label: "List" },
  { value: "chips", label: "Chips" },
] as const;

export const STEPS_SOURCES = [
  { value: "manual", label: "The agent" },
  { value: "flow", label: "The flow" },
] as const;

export const CONSENT_KINDS = [
  { value: "recording", label: "Recording the call" },
  { value: "ai_disclosure", label: "Talking to an AI" },
  { value: "terms", label: "Terms" },
  { value: "custom", label: "Something else" },
] as const;

export const CONSENT_DECLINE_ACTIONS = [
  { value: "continue", label: "Carry on without it" },
  { value: "end_call", label: "Say goodbye and end the call" },
] as const;

/** `captions.position` values (V5-31). */
export const CAPTIONS_POSITIONS = [
  { value: "block", label: "In the panel" },
  { value: "bottom", label: "Over the video" },
] as const;

/**
 * A short, curated language list (V5-35): the same set
 * `agents/tabs/instructions-tab.tsx`'s `voice.language` picker offers, so the
 * console never shows two different language lists. Used by the Languages
 * card (`voice.languages`) and the `captions` block's `target_language`
 * field editor (`block-config-form.tsx`) — both take an IANA-ish two-letter
 * code (`lkap_contracts.providers.LANGUAGE_CODE_PATTERN`).
 */
export const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "hi", label: "Hindi" },
  { value: "ja", label: "Japanese" },
] as const;

/** A language code's label, or the code itself for one outside the curated list. */
export function languageLabel(code: string): string {
  return LANGUAGE_OPTIONS.find((option) => option.value === code)?.label ?? code;
}

/** `upload.accept` values (`lkap_contracts.blocks.UPLOAD_MIME_TYPES` plus `image/*`); never HTML or SVG. */
export const UPLOAD_ACCEPT_OPTIONS = [
  { value: "image/*", label: "Any photo" },
  { value: "image/jpeg", label: "JPEG" },
  { value: "image/png", label: "PNG" },
  { value: "image/webp", label: "WebP" },
  { value: "image/gif", label: "GIF" },
  { value: "image/heic", label: "HEIC" },
  { value: "image/heif", label: "HEIF" },
  { value: "application/pdf", label: "PDF" },
] as const;

/** `link.open_in` values (V5-43). */
export const LINK_OPEN_IN = [
  { value: "new_tab", label: "In a new tab" },
  { value: "dialog", label: "In a window over the call" },
] as const;

/** `slots.timezone_mode` values (V5-43). */
export const SLOTS_TIMEZONE_MODES = [
  { value: "caller", label: "The caller's time zone" },
  { value: "agent", label: "The business time zone" },
] as const;

/** `cards.layout` values (V5-43). */
export const CARDS_LAYOUTS = [
  { value: "carousel", label: "Carousel" },
  { value: "grid", label: "Grid" },
  { value: "list", label: "List" },
] as const;

/** `notebook.paper` values (V6-08). */
export const NOTEBOOK_PAPERS = [
  { value: "plain", label: "Plain" },
  { value: "ruled", label: "Ruled" },
  { value: "grid", label: "Grid" },
  { value: "legal", label: "Legal pad" },
] as const;

/** `notebook.font` values (V6-08): typed notes in a handwriting font are a look, not real handwriting. */
export const NOTEBOOK_FONTS = [
  { value: "print", label: "Print" },
  { value: "handwritten", label: "Handwriting" },
] as const;

/** `canvas.background` values (V6-12): what the drawing board starts on. */
export const CANVAS_BACKGROUNDS = [
  { value: "none", label: "Blank" },
  { value: "asset", label: "A picture the agent puts on it" },
  { value: "live_camera", label: "The caller's camera" },
] as const;

/** `canvas.tools` the caller may be offered (V6-12; `text` is reserved and not offered). */
export const CANVAS_TOOL_OPTIONS = [
  { value: "pen", label: "Pen" },
  { value: "highlighter", label: "Highlighter" },
  { value: "eraser", label: "Eraser" },
  { value: "box", label: "Box" },
  { value: "arrow", label: "Arrow" },
] as const;

/** `layout.kind` values (V6-08). */
export const LAYOUT_KINDS = [
  { value: "tabs", label: "Tabs" },
  { value: "columns", label: "Side by side" },
] as const;

/** `notebook.sections[].kind` values (V6-08 → V6-10; `NotebookSectionKind`). */
export const NOTEBOOK_SECTION_KINDS = [
  { value: "text", label: "Notes" },
  { value: "checklist", label: "Checklist" },
  { value: "details", label: "Summary" },
  { value: "ink", label: "Drawing board (coming soon)" },
] as const;

/** `DetailsItem.type` (`contracts/generated/schemas/BlockConfig_details.schema.json`). */
export const DETAILS_FIELD_TYPES = ["string", "number", "date", "money", "phone", "email", "badge"] as const;

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
    configFields: [
      {
        key: "caller_can_edit",
        label: "The caller can tick items",
        kind: "boolean",
        hint: "The agent is told when the caller ticks an item.",
        default: false,
      },
    ],
    filledBy: "set_checklist",
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
  // V5-08: minimal entries (PLAN-V5 §0.1); the renderers, previews and composer
  // editors for these four come with V5-12.
  choices: {
    type: "choices",
    label: "Choices",
    description: "Options the caller taps or answers by voice.",
    defaultTitle: null,
    idStem: "choices",
    configFields: [
      { key: "multi", label: "Allow more than one answer", kind: "boolean", default: false },
      { key: "layout", label: "Layout", kind: "select", default: "buttons", options: CHOICE_LAYOUTS },
      { key: "max_options", label: "Most options", kind: "integer", default: 8, min: 2 },
    ],
    filledBy: "request_choice",
  },
  details: {
    type: "details",
    label: "Details",
    description: "A card of facts collected so far, such as a claim number and dates.",
    defaultTitle: "Details",
    idStem: "details",
    configFields: [
      { key: "columns", label: "Columns", kind: "integer", default: 1, min: 1 },
      {
        key: "fields",
        label: "Starting rows",
        kind: "list",
        hint: "Rows the card starts with. The agent fills them in and may add more.",
        default: [],
        itemKeys: ["key", "label", "type"],
      },
      {
        key: "caller_can_edit",
        label: "The caller can change values",
        kind: "boolean",
        hint: "The agent is told when the caller changes a value.",
        default: false,
      },
    ],
    filledBy: "set_details",
  },
  markdown: {
    type: "markdown",
    label: "Text",
    description: "Longer text on screen, such as a recap or instructions.",
    defaultTitle: null,
    idStem: "text",
    configFields: [
      { key: "max_chars", label: "Longest text (characters)", kind: "integer", default: 8000, min: 200 },
      { key: "allow_links", label: "Allow links", kind: "boolean", default: false },
    ],
    filledBy: "show_text",
  },
  steps: {
    type: "steps",
    label: "Steps",
    description: "Where the caller is in the process.",
    defaultTitle: "Progress",
    idStem: "steps",
    configFields: [
      {
        key: "steps",
        label: "Steps",
        kind: "list",
        hint: "The steps to show. When the block follows the flow, use the flow's step ids.",
        default: [],
        itemKeys: ["id", "label"],
      },
      { key: "source", label: "Driven by", kind: "select", default: "manual", options: STEPS_SOURCES },
      { key: "show_notes", label: "Show notes under steps", kind: "boolean", default: true },
    ],
    filledBy: "set_steps or the flow",
  },
  // V5-15: minimal entry (PLAN-V5 §0.1, R-V5-7); the renderer, the banner and the
  // composer editor come with V5-17.
  consent: {
    type: "consent",
    label: "Consent",
    description: "Asks the caller to agree, for example before the call is recorded.",
    defaultTitle: null,
    idStem: "consent",
    configFields: [
      { key: "kind", label: "Asks about", kind: "select", default: "recording", options: CONSENT_KINDS },
      {
        key: "text",
        label: "Wording",
        kind: "text",
        hint: "Leave empty to use the workspace's wording (Settings, Compliance).",
        default: "",
        maxLength: 2000,
      },
      { key: "required", label: "Required", kind: "boolean", default: true },
      {
        key: "decline_action",
        label: "If the caller says no",
        kind: "select",
        default: "continue",
        options: CONSENT_DECLINE_ACTIONS,
      },
      { key: "show_banner", label: "Show the AI assistant banner", kind: "boolean", default: true },
    ],
    filledBy: "request_consent",
  },
  // V5-19: minimal entry (PLAN-V5 §0.1, R-V5-7); the renderer, the sender and the
  // `multiselect` editor come with V5-23.
  upload: {
    type: "upload",
    label: "Upload",
    description: "Lets the caller send photos or documents from their device.",
    defaultTitle: "Send a file",
    idStem: "upload",
    configFields: [
      {
        key: "accept",
        label: "File types",
        kind: "multiselect",
        default: ["image/*", "application/pdf"],
        options: UPLOAD_ACCEPT_OPTIONS,
      },
      { key: "max_files", label: "Most files at once", kind: "integer", default: 3, min: 1 },
      {
        key: "max_bytes",
        label: "Largest file (bytes)",
        kind: "integer",
        hint: "Up to 25 MB (26214400 bytes).",
        default: 10485760,
        min: 1,
      },
      { key: "camera_capture", label: "Open the camera on phones", kind: "boolean", default: false },
    ],
    filledBy: "request_upload",
  },
  // V5-31: minimal entry (PLAN-V5 §0.1, R-V5-7); the renderer, the `lkap.captions` reader and
  // the `language` field editor come with V5-35.
  captions: {
    type: "captions",
    label: "Captions",
    description: "Large live captions of what the caller and the agent are saying.",
    defaultTitle: "Captions",
    idStem: "captions",
    configFields: [
      { key: "show_user", label: "Show what the caller says", kind: "boolean", default: true },
      { key: "show_agent", label: "Show what the agent says", kind: "boolean", default: true },
      { key: "position", label: "Where", kind: "select", default: "block", options: CAPTIONS_POSITIONS },
      {
        key: "target_language",
        label: "Translate into",
        kind: "language",
        hint: "Translated captions are not available yet.",
        default: null,
      },
    ],
    filledBy: "the conversation",
  },
  // V5-32: minimal entry (PLAN-V5 §0.1, R-V5-7); the renderer and previews come with V5-36.
  handoff: {
    type: "handoff",
    label: "Handoff",
    description: "Shows the caller when they are being put through to a person, and when that person joins.",
    defaultTitle: "Talking to a person",
    idStem: "handoff",
    configFields: [
      { key: "show_queue", label: "Show the caller's place in the queue", kind: "boolean", default: true },
      { key: "show_agent_name", label: "Show the person's name", kind: "boolean", default: true },
    ],
    filledBy: "transfer_call",
  },
  // V5-43: minimal entries (PLAN-V5 §0.1, R-V5-7); the renderers, the `hosts` editor and
  // previews come with V5-44.
  link: {
    type: "link",
    label: "Link",
    description: "A payment, signing or portal link the caller opens, and whether they finished.",
    defaultTitle: "Link",
    idStem: "link",
    configFields: [
      {
        key: "allowed_hosts",
        label: "Sites links may go to",
        kind: "hosts",
        hint: "At least one, like example.com or *.example.com. Only https links are shown.",
        default: [],
      },
      { key: "open_in", label: "Open the link", kind: "select", default: "new_tab", options: LINK_OPEN_IN },
      { key: "show_qr", label: "Show a QR code for phones", kind: "boolean", default: true },
    ],
    filledBy: "send_link",
  },
  slots: {
    type: "slots",
    label: "Times",
    description: "Times the caller can book, to tap or say.",
    defaultTitle: "Pick a time",
    idStem: "slots",
    configFields: [
      { key: "timezone_mode", label: "Show times in", kind: "select", default: "caller", options: SLOTS_TIMEZONE_MODES },
      { key: "days_visible", label: "Days shown", kind: "integer", default: 7, min: 1 },
      { key: "allow_custom", label: "Let the caller ask for another time", kind: "boolean", default: false },
    ],
    filledBy: "request_slot",
  },
  cards: {
    type: "cards",
    label: "Cards",
    description: "Options side by side, like plans or repair shops, with buttons.",
    defaultTitle: null,
    idStem: "cards",
    configFields: [
      { key: "layout", label: "Layout", kind: "select", default: "carousel", options: CARDS_LAYOUTS },
      { key: "selectable", label: "Let the caller pick one", kind: "boolean", default: true },
      { key: "max_cards", label: "Most cards", kind: "integer", default: 10, min: 1 },
      {
        key: "image_hosts",
        label: "Sites pictures may come from",
        kind: "hosts",
        hint: "Leave empty to show only pictures from the call.",
        default: [],
      },
    ],
    filledBy: "show_cards",
  },
  // V6-08: minimal entries (PLAN-V5 §0.1, R-V5-7); the renderers, the section and child
  // editors and previews come with V6-10.
  notebook: {
    type: "notebook",
    label: "Notebook",
    description: "A notebook the agent writes in as the call goes: notes, a checklist, a summary and a drawing board.",
    defaultTitle: "Notebook",
    idStem: "notebook",
    configFields: [
      { key: "paper", label: "Paper", kind: "select", default: "ruled", options: NOTEBOOK_PAPERS },
      { key: "font", label: "Writing", kind: "select", default: "print", options: NOTEBOOK_FONTS },
      {
        key: "sections",
        label: "Sections",
        kind: "sections",
        hint: "What the notebook holds, in order: notes, a checklist, a summary card or a drawing board.",
        default: [{ id: "notes", title: "Notes", kind: "text" }],
      },
      {
        key: "caller_can_write",
        label: "The caller can write in it",
        kind: "boolean",
        hint: "The caller can add notes, tick items and change values. The agent is told about each change.",
        default: false,
      },
      {
        key: "caller_can_draw",
        label: "The caller can draw",
        kind: "boolean",
        hint: "The caller can draw on the boards of the drawing sections.",
        default: false,
      },
    ],
    filledBy: "notebook_write",
  },
  layout: {
    type: "layout",
    label: "Tabs or columns",
    description: "Shows other blocks of the panel as tabs or side by side.",
    defaultTitle: null,
    idStem: "layout",
    configFields: [
      { key: "kind", label: "Show as", kind: "select", default: "tabs", options: LAYOUT_KINDS },
      {
        key: "children",
        label: "Blocks inside",
        kind: "children",
        hint: "Other blocks of this panel to show here. Each block can be inside one of these only.",
        default: [],
      },
      { key: "columns", label: "Columns", kind: "integer", hint: "Used when shown side by side.", default: 2, min: 2 },
    ],
    filledBy: "the blocks you put in it",
  },
  // V6-12: a minimal entry (PLAN-V5 §0.1, R-V5-7); the drawing board itself, the tools picker
  // and previews come with V6-14.
  canvas: {
    type: "canvas",
    label: "Drawing board",
    description: "A board the caller can write or sketch on by hand, and the agent can mark up and read.",
    defaultTitle: "Drawing board",
    idStem: "board",
    configFields: [
      {
        key: "caller_can_draw",
        label: "The caller can draw",
        kind: "boolean",
        hint: "The caller can write and sketch on the board. The agent can read what they wrote.",
        default: false,
      },
      {
        key: "tools",
        label: "Tools",
        kind: "multiselect",
        default: ["pen", "highlighter", "eraser"],
        options: CANVAS_TOOL_OPTIONS,
      },
      { key: "background", label: "Starts on", kind: "select", default: "none", options: CANVAS_BACKGROUNDS },
      {
        key: "max_strokes",
        label: "Most strokes",
        kind: "integer",
        hint: "The board says it is full after this many strokes (up to 2,000).",
        default: 500,
        min: 1,
      },
      {
        key: "signature_mode",
        label: "Signature board",
        kind: "boolean",
        hint: "Kept for signatures; not used yet.",
        default: false,
      },
    ],
    filledBy: "draw_on_canvas",
  },
};

/** The heading a block shows: its title, else the type's default (`null` = none). */
export function blockTitle(spec: BlockSpec): string | null {
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
  handoff: Required<HandoffBlockState>;
}

const STATE_DEFAULTS: { [K in keyof BlockStateByType]: () => BlockStateByType[K] } = {
  form: () => ({ schema: {}, values: {}, status: "idle", submitted_at: null }),
  document: () => ({ asset_id: null, url: null, page: 1, highlights: [] }),
  gallery: () => ({ asset_ids: [], selected: null }),
  table: () => ({ columns: [], rows: [], selected_row: null }),
  transcript: () => ({ show_tools: false }),
  video: () => ({ source: "agent_avatar", muted: false }),
  kb_citations: () => ({ items: [] }),
  // V5-32/36: the worker seeds `handoff` the same way (`ui/blocks.py::BLOCK_STATE_MODELS`,
  // docs/v5/_asks.md #210) — idle, nothing filled in yet.
  handoff: () => ({ status: "idle", mode: null, target: null, queue_position: null, agent_name: null, reason: null }),
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * A `canvas`'s pre-call state (V6-12, ask #93(c)): mirrors the worker's
 * `initial_block_state` — every `CanvasBlockState` field at its model default
 * except `background`, which starts on `"live_camera"` only when the config
 * asks for it; a config `background: "asset"` still starts blank (`"none"`)
 * — the picture is a per-session value the agent puts on later, never a
 * per-agent config value.
 */
function canvasInitialState(spec: BlockSpec): Record<string, unknown> {
  const config = isRecord(spec.config) ? spec.config : {};
  const background = config.background === "live_camera" ? "live_camera" : "none";
  return {
    width: 1600,
    height: 1200,
    background,
    strokes: [],
    shapes: [],
    snapshot_asset_id: null,
    limit_reached: false,
    updated_at: null,
  };
}

/**
 * A notebook section's empty content by kind (mirrors the worker's
 * `ui.blocks._empty_section`). `canvasBlockId` (V6-12): an `ink` section's
 * board, carried straight from its config — `null` while it has none yet.
 */
function emptyNotebookSection(kind: unknown, canvasBlockId: string | null): Record<string, unknown> {
  switch (kind) {
    case "checklist":
      return { kind: "checklist", items: [] };
    case "details":
      return { kind: "details", items: [] };
    case "ink":
      return { kind: "ink", canvas_block_id: canvasBlockId };
    default:
      return { kind: "text", entries: [] };
  }
}

/**
 * `config.sections` as `{id, kind, canvasBlockId}` triples, one `notes` text
 * section when the config carries none (ask #60, mirrors
 * `NotebookBlockConfig.sections`'s own default of one `notes` text section).
 */
function notebookSectionIds(config: Record<string, unknown>): { id: string; kind: unknown; canvasBlockId: string | null }[] {
  const sections = config.sections;
  if (Array.isArray(sections) && sections.length > 0) {
    return sections
      .filter((section): section is Record<string, unknown> => isRecord(section) && typeof section.id === "string")
      .map((section) => ({
        id: section.id as string,
        kind: section.kind,
        canvasBlockId: typeof section.canvas_block_id === "string" ? section.canvas_block_id : null,
      }));
  }
  return [{ id: "notes", kind: "text", canvasBlockId: null }];
}

/**
 * A notebook's pre-call state (ask #60): mirrors the worker's
 * `ui.blocks.empty_notebook_sections` — one empty section per `config.sections`
 * entry, keyed by id, content shaped by kind. Not a plain key copy (the
 * generic rule in `initialBlockState` below): `config.sections` and
 * `state.sections` have different shapes.
 */
function notebookInitialState(spec: BlockSpec): Record<string, unknown> {
  const config = isRecord(spec.config) ? spec.config : {};
  const sections: Record<string, unknown> = {};
  for (const { id, kind, canvasBlockId } of notebookSectionIds(config)) sections[id] = emptyNotebookSection(kind, canvasBlockId);
  return { sections, updated_at: null };
}

/**
 * The state a block renders before the worker's snapshot arrives (pre-call,
 * connecting, the console preview): the type's defaults, seeded by any
 * `BlockSpec.config` key that names a state field — the same rule as the
 * worker's `initial_block_state`. Envelope, `layout` and custom blocks are
 * `{}` (a `layout` has no state of its own, D-V6-18); `notebook` (V6-08) and
 * `canvas` (V6-12) have their own rules (`notebookInitialState`,
 * `canvasInitialState`) — a plain key copy would be wrong for both.
 */
export function initialBlockState(spec: BlockSpec): Record<string, unknown> {
  if (spec.type === "notebook") return notebookInitialState(spec);
  if (spec.type === "canvas") return canvasInitialState(spec);
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
export function blockStateOf(spec: BlockSpec, blocks: Record<string, unknown> | undefined): Record<string, unknown> {
  const initial = initialBlockState(spec);
  const live = blocks?.[spec.id];
  return isRecord(live) ? { ...initial, ...live } : initial;
}
