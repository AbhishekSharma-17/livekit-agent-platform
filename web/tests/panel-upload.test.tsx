import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { RoomContext } from "@livekit/components-react";
import type { Room } from "livekit-client";

import { SessionAssetsCard, sessionAssetUrls } from "@/components/console/sessions/session-panel-tab";
import type { AgentPublicOut, BlockSpec, PanelLayout, SessionAssetOut } from "@/contracts/lkap-contracts";
import { emptyUiState } from "@/lib/ui-state";
import { Block } from "@/panels/blocks";
import {
  BLOCK_FIXTURE_STATES,
  FIXTURE_LAYOUT,
  fixtureAssetUrls,
  fixtureUiState,
} from "@/panels/blocks/__fixtures__";
import { coerceForm, formFields } from "@/panels/blocks/form";
import { isPreviewableImage } from "@/panels/blocks/gallery";
import {
  checkFileClientSide,
  sendUploadFile,
  useUploadQueue,
  type UploadRoom,
} from "@/panels/composite/upload";
import type { PanelProps } from "@/panels/registry";

/**
 * V5-23: the `upload` block's picker and sender, the new `form` field kinds
 * (`phone`, `textarea`, `file`), the gallery's HEIC/HEIF fallback (ask
 * #134), and the barge-in cancel wording (ask #130). `panel-blocks.test.tsx`
 * covers the fixture render and the empty state, like every other block;
 * this file covers the byte-stream sender and the room-dependent behaviour
 * that needs a `RoomContext` to exercise at all.
 *
 * jsdom implements `File`/`Blob` (`BlobImpl`, `jsdom/living/file-api`) but
 * not `Blob.prototype.arrayBuffer` — every browser this code actually runs
 * in has it (`sendUploadFile` reads a file with `slice().arrayBuffer()`,
 * `composite/upload.ts`'s own docstring). It does implement `FileReader`,
 * so this file polyfills `arrayBuffer` on top of that for the test
 * environment only; production code is untouched.
 */
if (typeof Blob !== "undefined" && typeof Blob.prototype.arrayBuffer !== "function") {
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error ?? new Error("FileReader failed"));
      reader.readAsArrayBuffer(this);
    });
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function agent(panel?: PanelLayout): AgentPublicOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Maya",
    description: "",
    pipeline_mode: "cascaded",
    ui_panel_id: "composite",
    capabilities: {},
    panel: (panel ?? FIXTURE_LAYOUT) as AgentPublicOut["panel"],
  };
}

function panelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: fixtureUiState(),
    assets: fixtureAssetUrls(),
    agent: agent(),
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

function specOf(type: BlockSpec["type"]): BlockSpec {
  const spec = FIXTURE_LAYOUT.blocks.find((b) => b.type === type);
  if (!spec) throw new Error(type);
  return spec;
}

interface FakeWriter {
  write: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
}

/** A room whose `localParticipant.streamBytes` records every chunk it is asked to write. */
function fakeRoom() {
  const writes: Uint8Array[] = [];
  const writer: FakeWriter = {
    write: vi.fn(async (chunk: Uint8Array) => {
      writes.push(chunk);
    }),
    close: vi.fn(async () => {}),
  };
  const streamBytes = vi.fn(async () => writer);
  const room: UploadRoom = { localParticipant: { streamBytes } };
  return { room, streamBytes, writer, writes };
}

function bytesOf(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((n, c) => n + c.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

/* -------------------------------------------------------------------------- */

describe("checkFileClientSide", () => {
  const limits = { accept: ["image/*"], maxFiles: 2, maxBytes: 1000 };

  it("refuses a file over max_bytes with the plain-words size line", () => {
    const file = new File([new Uint8Array(2000)], "big.jpg", { type: "image/jpeg" });
    expect(checkFileClientSide(file, limits, 0)).toEqual({
      name: "big.jpg",
      reason: "too_large",
      message: "This file is too large. Send one up to 1 MB.",
    });
  });

  it("refuses once max_files is already reached", () => {
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });
    expect(checkFileClientSide(file, limits, 2)).toMatchObject({ reason: "too_many_files" });
  });

  it("refuses an empty file", () => {
    const file = new File([], "empty.jpg", { type: "image/jpeg" });
    expect(checkFileClientSide(file, limits, 0)).toMatchObject({ reason: "empty" });
  });

  it("never checks the declared type — the worker sniffs the real bytes", () => {
    const file = new File([new Uint8Array(10)], "a.html", { type: "text/html" });
    expect(checkFileClientSide(file, limits, 0)).toBeNull();
  });

  it("accepts a file within every limit", () => {
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });
    expect(checkFileClientSide(file, limits, 0)).toBeNull();
  });
});

describe("sendUploadFile (streamBytes, not sendFile — see composite/upload.ts's docstring)", () => {
  it("streams every byte in order, with block_id/name attributes and totalSize, and reports progress up to 1", async () => {
    const { room, streamBytes, writer, writes } = fakeRoom();
    const bytes = new Uint8Array(150_000); // several chunks at the sender's 64 KiB chunk size
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = i % 256;
    const file = new File([bytes], "photo.jpg", { type: "image/jpeg" });
    const progress: number[] = [];

    await sendUploadFile(room, file, { blockId: "documents", onProgress: (p) => progress.push(p) });

    expect(streamBytes).toHaveBeenCalledWith({
      topic: "lkap.ui.upload",
      name: "photo.jpg",
      mimeType: "image/jpeg",
      totalSize: bytes.length,
      attributes: { block_id: "documents", name: "photo.jpg" },
    });
    expect(Array.from(bytesOf(writes))).toEqual(Array.from(bytes));
    expect(writes.length).toBeGreaterThan(1);
    expect(progress.at(-1)).toBe(1);
    expect(progress.every((p, i) => i === 0 || p >= progress[i - 1])).toBe(true);
    expect(writer.close).toHaveBeenCalledTimes(1);
  });

  it("carries `field` for a form file field", async () => {
    const { room, streamBytes } = fakeRoom();
    const file = new File([new Uint8Array(10)], "id.jpg", { type: "image/jpeg" });
    await sendUploadFile(room, file, { blockId: "intake", field: "id_photo" });
    expect(streamBytes).toHaveBeenCalledWith(
      expect.objectContaining({ attributes: { block_id: "intake", name: "id.jpg", field: "id_photo" } }),
    );
  });

  it("reports one completion event for a zero-byte file without ever writing a chunk", async () => {
    const { room, writer } = fakeRoom();
    const file = new File([], "empty.jpg", { type: "image/jpeg" });
    const progress: number[] = [];
    await sendUploadFile(room, file, { blockId: "documents", onProgress: (p) => progress.push(p) });
    expect(writer.write).not.toHaveBeenCalled();
    expect(progress).toEqual([1]);
  });

  it("stops mid-stream once isCancelled turns true, and still closes the writer", async () => {
    const { room, writer } = fakeRoom();
    const bytes = new Uint8Array(300_000);
    const file = new File([bytes], "big.jpg", { type: "image/jpeg" });
    let cancelled = false;
    let writeCount = 0;
    writer.write.mockImplementation(async () => {
      writeCount += 1;
      if (writeCount === 1) cancelled = true;
    });

    await expect(
      sendUploadFile(room, file, { blockId: "documents", isCancelled: () => cancelled }),
    ).rejects.toThrow();
    expect(writer.close).toHaveBeenCalledTimes(1);
    expect(writeCount).toBeLessThan(Math.ceil(bytes.length / (64 * 1024)));
  });
});

describe("useUploadQueue", () => {
  const limits = { accept: ["image/*"], maxFiles: 3, maxBytes: 1_000_000 };

  it("sends a queued file and clears it once the transport is done", async () => {
    const { room } = fakeRoom();
    const { result } = renderHook(() => useUploadQueue({ room, blockId: "documents", limits, acceptedCount: 0 }));
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });

    act(() => result.current.addFiles([file]));
    await waitFor(() => expect(result.current.rows).toHaveLength(0));
  });

  it("refuses an over-limit file before it ever reaches the room", () => {
    const { room, streamBytes } = fakeRoom();
    const { result } = renderHook(() =>
      useUploadQueue({ room, blockId: "documents", limits: { ...limits, maxFiles: 1 }, acceptedCount: 1 }),
    );
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });

    act(() => result.current.addFiles([file]));
    expect(result.current.rows).toEqual([expect.objectContaining({ kind: "rejected", name: "a.jpg" })]);
    expect(streamBytes).not.toHaveBeenCalled();
  });

  it("cancel() refuses any further pick, silently — nothing queues and nothing streams", () => {
    const { room, streamBytes } = fakeRoom();
    const { result } = renderHook(() => useUploadQueue({ room, blockId: "documents", limits, acceptedCount: 0 }));
    act(() => result.current.cancel());
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });
    act(() => result.current.addFiles([file]));
    expect(result.current.rows).toHaveLength(0);
    expect(streamBytes).not.toHaveBeenCalled();
  });

  it("without a room, a picked file is never sent (queued, never active)", () => {
    const { result } = renderHook(() => useUploadQueue({ room: undefined, blockId: "documents", limits, acceptedCount: 0 }));
    const file = new File([new Uint8Array(10)], "a.jpg", { type: "image/jpeg" });
    act(() => result.current.addFiles([file]));
    expect(result.current.rows).toEqual([{ key: expect.any(String), kind: "queued", name: "a.jpg" }]);
  });
});

/* -------------------------------------------------------------------------- */

describe("upload block (V5-23)", () => {
  const spec = specOf("upload");

  /**
   * `upload` is lazy (it needs `@livekit/components-react`, like `video`):
   * the `Suspense` fallback and the resolved block are two different DOM
   * nodes (both `data-testid="block-upload"`, `panel-blocks.test.tsx`'s own
   * pattern), so this re-queries on each check rather than trusting a
   * reference captured before the swap.
   */
  async function findUploadEl(): Promise<HTMLElement> {
    await waitFor(() => expect(screen.getByTestId("block-upload").getAttribute("data-loading")).toBeNull());
    return screen.getByTestId("block-upload");
  }

  it("without a room (preview, the console's read-only snapshot), the picker is disabled and never throws", async () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findUploadEl();
    expect(within(el).getByText("Files can be sent during a call.")).toBeTruthy();
    expect(within(el).getByRole("button", { name: /Choose a file/ }).hasAttribute("disabled")).toBe(true);
  });

  it("sends a picked file with the block's own attributes; once the worker confirms it, Send is enabled", async () => {
    const { room, streamBytes } = fakeRoom();
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    const requested = fixtureUiState({ documents: { ...BLOCK_FIXTURE_STATES.upload, files: [], rejected: [] } });

    const { rerender } = render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={spec} {...panelProps({ perform, state: requested })} />
      </RoomContext.Provider>,
    );

    const el = await findUploadEl();
    const fileInput = el.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;
    const file = new File([new Uint8Array(10)], "damage.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });

    await waitFor(() => expect(streamBytes).toHaveBeenCalled());
    expect(streamBytes).toHaveBeenCalledWith(
      expect.objectContaining({ attributes: { block_id: "documents", name: "damage.jpg" } }),
    );
    expect(within(el).getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(true);

    // The worker verifies the file and appends it to `files` — a state
    // patch, simulated here by re-rendering with the new snapshot.
    const confirmed = fixtureUiState({
      documents: {
        ...BLOCK_FIXTURE_STATES.upload,
        files: [{ asset_id: "up-1", name: "damage.jpg", mime: "image/jpeg", size: 10 }],
        rejected: [],
      },
    });
    rerender(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={spec} {...panelProps({ perform, state: confirmed })} />
      </RoomContext.Provider>,
    );

    const sendButton = await screen.findByRole("button", { name: "Send" });
    expect(sendButton.hasAttribute("disabled")).toBe(false);
    fireEvent.click(sendButton);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "documents", values: { files: ["up-1"] } },
      }),
    );
  });

  it("refuses an over-sized file client-side, without ever streaming it (docs/v5/_asks.md #131)", async () => {
    const { room, streamBytes } = fakeRoom();
    const requested = fixtureUiState({ documents: { ...BLOCK_FIXTURE_STATES.upload, files: [], rejected: [] } });
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={spec} {...panelProps({ state: requested })} />
      </RoomContext.Provider>,
    );
    const el = await findUploadEl();
    const fileInput = el.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;
    // The fixture's "documents" block config sets no `max_bytes`, so the 10 MB default applies.
    const big = new File([new Uint8Array(11 * 1024 * 1024)], "huge.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [big] } });
    });
    expect(await within(el).findByText(/too large/)).toBeTruthy();
    expect(streamBytes).not.toHaveBeenCalled();
  });

  it("the camera input carries capture=\"environment\" when the block turns camera_capture on", async () => {
    // `layout.json`'s "documents" upload block sets `camera_capture: true`.
    render(<Block spec={spec} {...panelProps()} />);
    const el = await findUploadEl();
    const cameraInput = el.querySelector('input[capture="environment"]');
    expect(cameraInput).toBeTruthy();
    expect(cameraInput?.getAttribute("accept")).toBe("image/*");
  });

  it("shows the barge-in cancel wording, not the caller-dismissal one (ask #130)", async () => {
    const state = fixtureUiState({ documents: { ...BLOCK_FIXTURE_STATES.upload, status: "cancelled" } });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const el = await findUploadEl();
    expect(within(el).getByText("The agent stopped waiting for this file.")).toBeTruthy();
    expect(within(el).queryByText(/dismissed this without answering/)).toBeNull();
  });

  it("has an idle empty state distinct from every other requestable block's", async () => {
    render(<Block spec={{ id: "documents", type: "upload", config: {} }} {...panelProps({ state: emptyUiState(), assets: new Map() })} />);
    const el = await findUploadEl();
    expect(within(el).getByText("The agent will ask for a file here when it needs one.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */

describe("gallery block: HEIC/HEIF preview (ask #134)", () => {
  it("never renders a broken <img> for a HEIC/HEIF asset — a plain 'Preview not available' line instead", () => {
    expect(isPreviewableImage("image/heic")).toBe(false);
    expect(isPreviewableImage("image/heif")).toBe(false);
    expect(isPreviewableImage("image/jpeg")).toBe(true);

    const state = fixtureUiState({ photos: { asset_ids: ["heic-1"], selected: null } });
    const withHeic = {
      ...state,
      assets: [...state.assets, { asset_id: "heic-1", kind: "upload", mime: "image/heic", caption: "IMG_0001.HEIC", meta: {}, ts: 0 }],
    };
    render(<Block spec={specOf("gallery")} {...panelProps({ state: withHeic })} />);
    expect(screen.getByText("Preview not available")).toBeTruthy();
    expect(screen.queryByAltText("IMG_0001.HEIC")).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */

describe("form block: phone, textarea and file fields (V5-23)", () => {
  const schema = {
    properties: {
      full_name: { title: "Full name", type: "string" },
      phone: { title: "Phone", type: "string", format: "phone" },
      notes: { title: "Notes", type: "string", "x-lkap-widget": "textarea" },
      id_photo: {
        title: "ID photo",
        type: "array",
        items: { type: "string" },
        "x-lkap-widget": "file",
        "x-lkap-upload": { accept: ["image/*"], max_files: 1, max_bytes: 5_000_000 },
      },
    },
    required: ["full_name", "id_photo"],
  };

  it("parses phone (format) and textarea/file (x-lkap-widget) into their own field kinds", () => {
    const fields = formFields(schema);
    expect(fields.map((f) => [f.name, f.kind])).toEqual([
      ["full_name", "text"],
      ["phone", "phone"],
      ["notes", "textarea"],
      ["id_photo", "file"],
    ]);
    expect(fields.find((f) => f.name === "id_photo")?.upload).toEqual({
      accept: ["image/*"],
      maxFiles: 1,
      maxBytes: 5_000_000,
    });
  });

  it("requires at least one file for a required file field, and never invents one from typed text", () => {
    const fields = formFields(schema);
    const empty = coerceForm(fields, { full_name: "Priya", phone: "", notes: "", id_photo: [] });
    expect(empty.errors.id_photo).toBe("Attach id photo.");
    const filled = coerceForm(fields, { full_name: "Priya", phone: "+91 98765 43210", notes: "", id_photo: ["up-9"] });
    expect(filled.values).toEqual({ full_name: "Priya", phone: "+91 98765 43210", id_photo: ["up-9"] });
  });

  it("renders a tel input for phone and a multi-line control for textarea", async () => {
    const layout: PanelLayout = {
      panel_id: "composite",
      layout: "side",
      blocks: [{ id: "intake", type: "form", title: "Details", config: {}, order: 0 }],
    };
    const state = {
      ...emptyUiState(),
      blocks: { intake: { schema, values: {}, status: "requested", submitted_at: null } },
    };
    render(<Block spec={layout.blocks![0]} {...panelProps({ agent: agent(layout), state: state as never })} />);
    expect((screen.getByLabelText("Phone") as HTMLInputElement).type).toBe("tel");
    expect(screen.getByLabelText("Notes").tagName).toBe("TEXTAREA");
    // The schema's `file` field renders lazily too (`FormFileField`) — let it
    // settle before the test ends, so its resolution never lands outside
    // `act`. No `RoomContext` here, so the button reads "Files can be sent
    // during a call" (disabled), not "Choose a file".
    await screen.findByRole("button", { name: /Files can be sent during a call/ });
  });

  it("a file field sends through the room and shows what the worker has verified for that field, not a text control", async () => {
    const { room, streamBytes } = fakeRoom();
    const layout: PanelLayout = {
      panel_id: "composite",
      layout: "side",
      blocks: [{ id: "intake", type: "form", title: "Details", config: {}, order: 0 }],
    };
    // `max_files: 2` here (unlike the shared `schema`'s 1): the test picks a
    // *second* file on top of the one already verified below, so `useUploadQueue`
    // must not refuse it as `too_many_files` before it ever reaches the room.
    const twoFileSchema = {
      ...schema,
      properties: { ...schema.properties, id_photo: { ...schema.properties.id_photo, "x-lkap-upload": { ...schema.properties.id_photo["x-lkap-upload"], max_files: 2 } } },
    };
    const state = {
      ...emptyUiState(),
      blocks: { intake: { schema: twoFileSchema, values: {}, status: "requested", submitted_at: null } },
      assets: [{ asset_id: "up-9", kind: "upload", mime: "image/jpeg", caption: "id.jpg", meta: { block_id: "intake", field: "id_photo" }, ts: 0 }],
    };
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={layout.blocks![0]} {...panelProps({ agent: agent(layout), state: state as never })} />
      </RoomContext.Provider>,
    );
    // `FormFileField` is itself lazy (`form.tsx` loads it from `./upload` on
    // demand, precisely so the common `form` block stays livekit-free) —
    // wait past its own Suspense fallback before querying its content.
    // Already-verified for this field, from `state.assets` — not typed by the caller.
    expect(await screen.findByText("id.jpg")).toBeTruthy();
    expect(screen.queryByLabelText("ID photo")).toBeNull();

    const fileInput = document.querySelector('fieldset input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(10)], "second.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    await waitFor(() =>
      expect(streamBytes).toHaveBeenCalledWith(
        expect.objectContaining({ attributes: { block_id: "intake", name: "second.jpg", field: "id_photo" } }),
      ),
    );
  });
});

/* -------------------------------------------------------------------------- */

describe("multiselect config field parity (upload.accept, V5-23)", () => {
  it("block-config-parity.test.ts already pins the schema/catalog shape; this only pins the option list", async () => {
    const { UPLOAD_ACCEPT_OPTIONS, BLOCK_CATALOG } = await import("@/panels/blocks/catalog");
    const field = BLOCK_CATALOG.upload.configFields.find((f) => f.key === "accept");
    expect(field?.kind).toBe("multiselect");
    expect(field && "options" in field ? field.options : null).toBe(UPLOAD_ACCEPT_OPTIONS);
  });
});

/* -------------------------------------------------------------------------- */

function sessionAsset(overrides: Partial<SessionAssetOut> = {}): SessionAssetOut {
  return {
    id: "a1",
    session_id: "s-1",
    kind: "upload",
    name: "damage.jpg",
    mime: "image/jpeg",
    size: 204_800,
    sha256: "9f2c4a1e6b0d8c3f5a7e9b1d2c4f6a8e0b3d5f7a9c1e3b5d7f9a2c4e6b8d0f1a",
    created_at: "2026-09-26T10:00:00Z",
    url: "https://cdn.test/sessions/s-1/assets/a1",
    ...overrides,
  };
}

describe("session detail: stored files through the signed URL (V5-23, docs/v5/_asks.md #131)", () => {
  it("sessionAssetUrls keeps only rows with a (not yet expired) url, keyed by asset id", () => {
    const map = sessionAssetUrls([sessionAsset({ id: "a1" }), sessionAsset({ id: "a2", url: null })]);
    expect(map.get("a1")).toBe("https://cdn.test/sessions/s-1/assets/a1");
    expect(map.has("a2")).toBe(false);
  });

  it("SessionAssetsCard lists name, type, size and a download link", () => {
    render(<SessionAssetsCard items={[sessionAsset()]} isLoading={false} />);
    expect(screen.getByText("damage.jpg")).toBeTruthy();
    expect(screen.getByText("image/jpeg · 200 KB")).toBeTruthy();
    const link = screen.getByRole("link", { name: /Download/ });
    expect(link.getAttribute("href")).toBe("https://cdn.test/sessions/s-1/assets/a1");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("says the link expired for a signed url that has lapsed, instead of a dead download link", () => {
    render(<SessionAssetsCard items={[sessionAsset({ url: null })]} isLoading={false} />);
    expect(screen.getByText("Link expired")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Download/ })).toBeNull();
  });

  it("renders nothing for a session with no stored files, and a skeleton while loading", () => {
    const { container, rerender } = render(<SessionAssetsCard items={[]} isLoading={false} />);
    expect(container.innerHTML).toBe("");
    rerender(<SessionAssetsCard items={undefined} isLoading />);
    expect(container.querySelector("[data-slot=skeleton]")).toBeTruthy();
  });
});
