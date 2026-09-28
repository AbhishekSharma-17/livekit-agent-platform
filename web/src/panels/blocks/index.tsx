"use client";

/**
 * Panel v2 blocks (CONTRACTS-V2 §4.4): one component per `BlockSpec.type`,
 * and `<Block>` — the one entry point the composite panel and any custom
 * pack panel use to render a block.
 *
 * ```tsx
 * // inside a custom PanelDefinition's Component (props: PanelProps)
 * <Block spec={{ id: "sources", type: "kb_citations", title: "Sources" }} {...props} />
 * ```
 *
 * `<Block>` picks the block's state out of `props.state.blocks[spec.id]`
 * (over the type's initial state), resolves the heading, and renders the
 * matching component. The `document`, `table`, `video`, `markdown`,
 * `upload`, `captions`, `transcript` and `activity` components are split out
 * of the first load (`React.lazy`); pdf.js is a further PDF-only split
 * inside the document block, and `link`'s QR encoder (`qrcode-generator`) is
 * a further dynamic `import()` inside `link.tsx` itself.
 */
import type { BlockSpec } from "@/contracts/lkap-contracts";
import * as React from "react";
import { Suspense, lazy } from "react";

import type { PanelProps } from "@/panels/registry";
import type { BlockType } from "@/panels/composite/layout";
import { PanelEmpty } from "@/panels/generic/blocks";

import { blockStateOf, blockTitle } from "./catalog";
import { ChecklistBlock } from "./checklist";
import { ChoicesBlock } from "./choices";
import { ConsentBlock } from "./consent";
import { CustomBlock } from "./custom";
import { DetailsBlock } from "./details";
import { FormBlock } from "./form";
import { BlockFrame } from "./frame";
import { GalleryBlock } from "./gallery";
import { HandoffBlock } from "./handoff";
import { KbCitationsBlock } from "./kb_citations";
import { LinkBlock } from "./link";
import { NotesBlock } from "./notes";
import { SlotsBlock } from "./slots";
import { StatusBlock } from "./status";
import { StepsBlock } from "./steps";
import { CardsBlock } from "./cards";
import type { BlockRenderProps } from "./types";

export { blockDomId, type BlockRenderProps } from "./types";

// `BlockRenderProps<S>` is only a narrowing of the same object for each block,
// so every component is stored under the widest signature.
type AnyBlockComponent = React.ComponentType<BlockRenderProps<never>>;

// `activity` reads the room's live `agent_state` for its "working on" line
// (V5-44) through `@livekit/components-react`, the same reason `video`,
// `upload`, `captions` and `transcript` are split out below — kept out of
// the first load for a session whose panel has no room-bound need for it.
const ActivityBlock = lazy(() => import("./activity"));
const DocumentBlock = lazy(() => import("./document"));
const TableBlock = lazy(() => import("./table"));
// The video block needs LiveKit's React bindings; kept out of the first load
// so the console pages that render panels (session detail, the composer
// preview) don't pull livekit-client in.
const VideoBlock = lazy(() => import("./video"));
// `markdown` pulls in `streamdown`; kept out of the first load the same way,
// for a session whose panel has no `markdown` block (V5-12).
const MarkdownBlock = lazy(() => import("./markdown"));
// `upload` also needs LiveKit's React bindings (`useMaybeRoomContext`, for
// its byte-stream sender) — the same reason `video` is split out (V5-23).
const UploadBlock = lazy(() => import("./upload"));
// `captions` also needs LiveKit's React bindings (`useMaybeRoomContext`, through
// `composite/captions-stream.ts`'s `useCaptionsStream`) — the same reason
// `video`/`upload` are split out (V5-35).
const CaptionsBlock = lazy(() => import("./captions"));
// V5-35: the transcript block's language chip reads `useCaptionsStream` too
// (matching a stored turn to its caption segment), so it now needs LiveKit's
// React bindings the same way — split out for the same reason, even though
// `transcript` is one of the more commonly-placed blocks (a brief Suspense
// fallback there is the traded cost).
const TranscriptBlock = lazy(() => import("./transcript"));

/** A block type this web build has no renderer for yet (V6-08's two; V6-10 renders them). */
function NotRenderedYetBlock({ spec, title, highlighted }: BlockRenderProps) {
  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      <PanelEmpty>This block is not shown here yet.</PanelEmpty>
    </BlockFrame>
  );
}

/** Block type → component. Every `BlockType` has one (`tests/panel-blocks.test.tsx`). */
export const BLOCK_COMPONENTS: Record<BlockType, AnyBlockComponent> = {
  status: StatusBlock,
  notes: NotesBlock,
  checklist: ChecklistBlock,
  activity: ActivityBlock,
  form: FormBlock as AnyBlockComponent,
  document: DocumentBlock as AnyBlockComponent,
  gallery: GalleryBlock as AnyBlockComponent,
  table: TableBlock as AnyBlockComponent,
  transcript: TranscriptBlock as AnyBlockComponent,
  video: VideoBlock as AnyBlockComponent,
  kb_citations: KbCitationsBlock as AnyBlockComponent,
  custom: CustomBlock,
  choices: ChoicesBlock as AnyBlockComponent,
  details: DetailsBlock as AnyBlockComponent,
  markdown: MarkdownBlock as AnyBlockComponent,
  steps: StepsBlock as AnyBlockComponent,
  consent: ConsentBlock as AnyBlockComponent,
  upload: UploadBlock as AnyBlockComponent,
  captions: CaptionsBlock as AnyBlockComponent,
  handoff: HandoffBlock as AnyBlockComponent,
  link: LinkBlock as AnyBlockComponent,
  slots: SlotsBlock as AnyBlockComponent,
  cards: CardsBlock as AnyBlockComponent,
  // V6-08 added these types to the contract; their renderers come with V6-10.
  notebook: NotRenderedYetBlock,
  layout: NotRenderedYetBlock,
};

/** Lazily-loaded block types (they suspend on first render). */
export const LAZY_BLOCK_TYPES: ReadonlySet<BlockType> = new Set<BlockType>([
  "activity",
  "document",
  "table",
  "video",
  "markdown",
  "upload",
  "captions",
  "transcript",
]);

export interface BlockProps extends PanelProps {
  spec: BlockSpec;
  /** `show_block` / a `form` request just pointed here (brand ring). */
  highlighted?: boolean;
}

function BlockFallback({ spec, title }: { spec: BlockSpec; title: string | null }) {
  return (
    <BlockFrame spec={spec} title={title} loading>
      <PanelEmpty>Loading…</PanelEmpty>
    </BlockFrame>
  );
}

/** Render one platform block from a `BlockSpec` and the panel's props. */
export function Block({ spec, highlighted, ...panel }: BlockProps) {
  const Component = BLOCK_COMPONENTS[spec.type] ?? CustomBlock;
  const title = blockTitle(spec);
  const data = blockStateOf(spec, panel.state.blocks);
  const node = (
    <Component
      spec={spec}
      data={data as never}
      panel={panel}
      title={title}
      highlighted={highlighted}
    />
  );
  if (!LAZY_BLOCK_TYPES.has(spec.type)) return node;
  return <Suspense fallback={<BlockFallback spec={spec} title={title} />}>{node}</Suspense>;
}
