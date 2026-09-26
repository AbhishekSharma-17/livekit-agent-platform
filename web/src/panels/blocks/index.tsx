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
 * matching component. The `document`, `table`, `video` and `markdown`
 * components are split out of the first load (`React.lazy`); pdf.js is a
 * further PDF-only split inside the document block.
 */
import type { BlockSpec } from "@/contracts/lkap-contracts";
import * as React from "react";
import { Suspense, lazy } from "react";

import type { PanelProps } from "@/panels/registry";
import type { BlockType } from "@/panels/composite/layout";
import { PanelEmpty } from "@/panels/generic/blocks";

import { ActivityBlock } from "./activity";
import { blockStateOf, blockTitle } from "./catalog";
import { ChecklistBlock } from "./checklist";
import { ChoicesBlock } from "./choices";
import { CustomBlock } from "./custom";
import { DetailsBlock } from "./details";
import { FormBlock } from "./form";
import { BlockFrame } from "./frame";
import { GalleryBlock } from "./gallery";
import { KbCitationsBlock } from "./kb_citations";
import { NotesBlock } from "./notes";
import { StatusBlock } from "./status";
import { StepsBlock } from "./steps";
import { TranscriptBlock } from "./transcript";
import type { BlockRenderProps } from "./types";

export { blockDomId, type BlockRenderProps } from "./types";

// `BlockRenderProps<S>` is only a narrowing of the same object for each block,
// so every component is stored under the widest signature.
type AnyBlockComponent = React.ComponentType<BlockRenderProps<never>>;

const DocumentBlock = lazy(() => import("./document"));
const TableBlock = lazy(() => import("./table"));
// The video block is the only one that needs LiveKit's React bindings; kept
// out of the first load so the console pages that render panels (session
// detail, the composer preview) don't pull livekit-client in.
const VideoBlock = lazy(() => import("./video"));
// `markdown` pulls in `streamdown`; kept out of the first load the same way,
// for a session whose panel has no `markdown` block (V5-12).
const MarkdownBlock = lazy(() => import("./markdown"));

/** A block type this web build has no renderer for yet. */
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
  // V5-15 added this type to the contract; its renderer comes with V5-17.
  consent: NotRenderedYetBlock,
};

/** Lazily-loaded block types (they suspend on first render). */
export const LAZY_BLOCK_TYPES: ReadonlySet<BlockType> = new Set<BlockType>(["document", "table", "video", "markdown"]);

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
