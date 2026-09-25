/**
 * Block fixtures (V2-11 card: `web/src/panels/blocks/__fixtures__/*.json`).
 *
 * The JSON files are block **states** exactly as the worker publishes them
 * under `UiState.blocks[<id>]` (the V2-10 → V2-11 wire contract);
 * `tests/panel-block-fixtures.test.ts` checks every key against the
 * matching `contracts/generated/schemas/<Type>BlockState.schema.json`.
 * `layout.json` is a `PanelLayout` with one block of every type.
 *
 * This module adds what a state cannot carry — asset object URLs and a
 * transcript — so the preview route (`scene=blocks` / `scene=composite`),
 * the agent editor's live preview and the tests all render the same thing
 * without a room. Assets are inline `data:` URLs (a two-page PDF and three
 * SVG "photos"): no network, no `public/` files.
 */
import type { ReceivedMessage } from "@livekit/components-react";

import type { AssetRef, UiState, BlockSpec, PanelLayout } from "@/contracts/lkap-contracts";
import { normalizeUiState, type UiStateStore } from "@/lib/ui-state";
import type { BlockType } from "@/panels/composite/layout";

import genericUiState from "../../../../tests/fixtures/generic_ui_state.json";
import choicesState from "./choices.json";
import detailsState from "./details.json";
import documentState from "./document.json";
import formRequested from "./form.requested.json";
import formSubmitted from "./form.submitted.json";
import galleryState from "./gallery.json";
import kbCitationsState from "./kb_citations.json";
import layout from "./layout.json";
import markdownState from "./markdown.json";
import stepsState from "./steps.json";
import tableState from "./table.json";
import transcriptState from "./transcript.json";
import videoState from "./video.json";

/** A `PanelLayout` with one block of every type (ids as in `layout.json`). */
export const FIXTURE_LAYOUT = layout as PanelLayout & { blocks: BlockSpec[] };

/** Filled state per stateful block type (form: `requested`). */
export const BLOCK_FIXTURE_STATES: Partial<Record<BlockType, Record<string, unknown>>> = {
  form: formRequested,
  document: documentState,
  gallery: galleryState,
  table: tableState,
  transcript: transcriptState,
  video: videoState,
  kb_citations: kbCitationsState,
};

/** Every JSON state fixture by file stem, for the schema check. */
export const STATE_FIXTURES: Record<string, { type: BlockType; state: Record<string, unknown> }> = {
  "form.requested": { type: "form", state: formRequested },
  "form.submitted": { type: "form", state: formSubmitted },
  document: { type: "document", state: documentState },
  gallery: { type: "gallery", state: galleryState },
  table: { type: "table", state: tableState },
  transcript: { type: "transcript", state: transcriptState },
  video: { type: "video", state: videoState },
  kb_citations: { type: "kb_citations", state: kbCitationsState },
  // V5-08: the quartet's states (renderers and scenes come with V5-12).
  choices: { type: "choices", state: choicesState },
  details: { type: "details", state: detailsState },
  markdown: { type: "markdown", state: markdownState },
  steps: { type: "steps", state: stepsState },
};

export const FORM_SUBMITTED_STATE: Record<string, unknown> = formSubmitted;

const SAMPLE_PDF = "data:application/pdf;base64,JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFs0IDAgUiA2IDAgUl0gL0NvdW50IDIgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL0ZvbnQgL1N1YnR5cGUgL1R5cGUxIC9CYXNlRm9udCAvSGVsdmV0aWNhID4+CmVuZG9iago0IDAgb2JqCjw8IC9UeXBlIC9QYWdlIC9QYXJlbnQgMiAwIFIgL01lZGlhQm94IFswIDAgNTk1IDg0Ml0gL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgMyAwIFIgPj4gPj4gL0NvbnRlbnRzIDUgMCBSID4+CmVuZG9iago1IDAgb2JqCjw8IC9MZW5ndGggMzY5ID4+CnN0cmVhbQpCVCAvRjEgMjAgVGYgNTAgNzQwIFRkIChDbGFpbSBmb3JtIC0gcGFnZSAxKSBUaiBFVApCVCAvRjEgMTIgVGYgNTAgNzAwIFRkIChQb2xpY3kgbnVtYmVyOiBIMC00NDcyMSkgVGogRVQKQlQgL0YxIDEyIFRmIDUwIDY3OCBUZCAoRGF0ZSBvZiBsb3NzOiAyMDI2LTA5LTIxKSBUaiBFVApCVCAvRjEgMTIgVGYgNTAgNjU2IFRkIChDYXVzZToga2l0Y2hlbiBmaXJlLCBzdG92ZSB0b3ApIFRqIEVUCkJUIC9GMSAxMiBUZiA1MCA2MzQgVGQgKFJvb21zIGFmZmVjdGVkOiBraXRjaGVuLCBoYWxsd2F5KSBUaiBFVAowLjg1IGcgNTAgMzgwIDQ5NSAxMjAgcmUgZgowIGcgQlQgL0YxIDEyIFRmIDYwIDQ3MCBUZCAoU2lnbmF0dXJlIGFuZCBkYXRlKSBUaiBFVAplbmRzdHJlYW0KZW5kb2JqCjYgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCA1OTUgODQyXSAvUmVzb3VyY2VzIDw8IC9Gb250IDw8IC9GMSAzIDAgUiA+PiA+PiAvQ29udGVudHMgNyAwIFIgPj4KZW5kb2JqCjcgMCBvYmoKPDwgL0xlbmd0aCAzMDMgPj4Kc3RyZWFtCkJUIC9GMSAyMCBUZiA1MCA3NDAgVGQgKENsYWltIGZvcm0gLSBwYWdlIDIpIFRqIEVUCkJUIC9GMSAxMiBUZiA1MCA3MDAgVGQgKEVzdGltYXRlZCByZXBhaXI6IDQsMjAwIEVVUikgVGogRVQKQlQgL0YxIDEyIFRmIDUwIDY3OCBUZCAoQ29udHJhY3RvcjogQnJpZ2h0bGluZSBSZXBhaXJzKSBUaiBFVApCVCAvRjEgMTIgVGYgNTAgNjU2IFRkIChQaG90b3MgYXR0YWNoZWQ6IDMpIFRqIEVUCjAuODUgZyA1MCAzODAgNDk1IDEyMCByZSBmCjAgZyBCVCAvRjEgMTIgVGYgNjAgNDcwIFRkIChTaWduYXR1cmUgYW5kIGRhdGUpIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDgKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAwMDEyMSAwMDAwMCBuIAowMDAwMDAwMTkxIDAwMDAwIG4gCjAwMDAwMDAzMTcgMDAwMDAgbiAKMDAwMDAwMDczNiAwMDAwMCBuIAowMDAwMDAwODYyIDAwMDAwIG4gCnRyYWlsZXIKPDwgL1NpemUgOCAvUm9vdCAxIDAgUiA+PgpzdGFydHhyZWYKMTIxNQolJUVPRgo=";

function photo(label: string, hue: number, shape: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">` +
    `<stop offset="0" stop-color="hsl(${hue} 35% 38%)"/><stop offset="1" stop-color="hsl(${hue + 30} 30% 18%)"/>` +
    `</linearGradient></defs><rect width="400" height="300" fill="url(#g)"/>${shape}` +
    `<text x="20" y="280" font-family="sans-serif" font-size="18" fill="white" fill-opacity="0.85">${label}</text></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

/** The envelope's `assets` for the fixtures (gallery photos + the claim form). */
export const FIXTURE_ASSET_REFS: AssetRef[] = [
  { asset_id: "frame-stove", kind: "photo", mime: "image/jpeg", caption: "Scorched stove top", meta: {}, ts: 1777000100 },
  { asset_id: "frame-hallway", kind: "photo", mime: "image/jpeg", caption: "Smoke on the hallway ceiling", meta: {}, ts: 1777000160 },
  { asset_id: "frame-ceiling", kind: "photo", mime: "image/jpeg", caption: "Kitchen ceiling", meta: {}, ts: 1777000200 },
  { asset_id: "doc-claim-form", kind: "document", mime: "application/pdf", caption: "claim-form.pdf", meta: {}, ts: 1777000250 },
];

/** `asset_id` → URL, standing in for the `lkap.ui.asset` object URLs. */
export function fixtureAssetUrls(): Map<string, string> {
  return new Map([
    ["frame-stove", photo("Stove", 20, '<rect x="110" y="90" width="180" height="110" rx="10" fill="black" fill-opacity="0.45"/><circle cx="160" cy="145" r="28" fill="none" stroke="white" stroke-opacity="0.5" stroke-width="6"/><circle cx="240" cy="145" r="28" fill="none" stroke="white" stroke-opacity="0.5" stroke-width="6"/>')],
    ["frame-hallway", photo("Hallway", 210, '<rect x="150" y="40" width="100" height="200" fill="black" fill-opacity="0.35"/>')],
    ["frame-ceiling", photo("Ceiling", 35, '<ellipse cx="200" cy="120" rx="140" ry="50" fill="black" fill-opacity="0.4"/>')],
    ["doc-claim-form", SAMPLE_PDF],
  ]);
}

/** A short conversation for the transcript block (`useSessionMessages` shape). */
export const FIXTURE_TRANSCRIPT = [
  { id: "t1", timestamp: 1777000000000, type: "agentTranscript", message: "Hi, I'm Maya. Can you tell me what happened?" },
  { id: "t2", timestamp: 1777000020000, type: "userTranscript", message: "There was a small kitchen fire this morning." },
  { id: "t3", timestamp: 1777000090000, type: "agentTranscript", message: "I'm sorry. Could you show me the stove with your camera?" },
] as unknown as ReceivedMessage[];

/** Block states keyed by the ids in `layout.json`. */
export function fixtureBlocks(): Record<string, Record<string, unknown>> {
  return {
    status: {},
    intake: formRequested,
    items: tableState,
    claim_form: documentState,
    photos: galleryState,
    sources: kbCitationsState,
    notes: {},
    checklist: {},
    camera: videoState,
    transcript: transcriptState,
    activity: {},
    pack: {},
    injured: choicesState,
    claim: detailsState,
    recap: markdownState,
    progress: stepsState,
  };
}

/** The full envelope the composite fixture renders: generic fixture + assets + blocks. */
export function fixtureUiState(blocks: Record<string, unknown> = fixtureBlocks()): UiStateStore["state"] {
  const envelope = genericUiState as UiState;
  return normalizeUiState({
    ...envelope,
    v: 2,
    assets: FIXTURE_ASSET_REFS,
    custom: { claim_id: "CLM-20931", stage: "first_notice" },
    blocks,
  });
}
