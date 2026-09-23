"use client";

/**
 * One PDF page on a canvas, via pdf.js — imported only here, and this module
 * is itself imported only by the document block when the document is a PDF,
 * so pdf.js and its worker never reach the session's first load.
 */
import * as React from "react";
import { useEffect, useRef, useState } from "react";

export interface PdfPageProps {
  url: string;
  page: number;
  /** Reports the document's page count once it has loaded. */
  onPageCount?: (count: number) => void;
  /** Rendered over the canvas, in page coordinates (0..1). */
  children?: React.ReactNode;
}

type PdfJs = typeof import("pdfjs-dist");
type PdfDocument = Awaited<ReturnType<PdfJs["getDocument"]>["promise"]>;

let pdfjsPromise: Promise<PdfJs> | null = null;

function loadPdfJs(): Promise<PdfJs> {
  pdfjsPromise ??= import("pdfjs-dist").then((pdfjs) => {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
    return pdfjs;
  });
  return pdfjsPromise;
}

export function PdfPage({ url, page, onPageCount, children }: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<PdfDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ratio, setRatio] = useState<number | null>(null);
  const onPageCountRef = useRef(onPageCount);
  onPageCountRef.current = onPageCount;

  useEffect(() => {
    let cancelled = false;
    let loaded: PdfDocument | null = null;
    setDoc(null);
    setError(null);
    loadPdfJs()
      .then((pdfjs) => pdfjs.getDocument({ url }).promise)
      .then((document) => {
        loaded = document;
        if (cancelled) {
          void document.destroy();
          return;
        }
        setDoc(document);
        onPageCountRef.current?.(document.numPages);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
      if (loaded) void loaded.destroy();
    };
  }, [url]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!doc || !canvas) return;
    let cancelled = false;
    let task: { cancel: () => void; promise: Promise<void> } | null = null;
    const pageNumber = Math.min(Math.max(1, page), doc.numPages);
    void doc
      .getPage(pageNumber)
      .then((pdfPage) => {
        if (cancelled) return;
        const width = canvas.parentElement?.clientWidth || 360;
        const base = pdfPage.getViewport({ scale: 1 });
        const scale = (width / base.width) * (window.devicePixelRatio || 1);
        const viewport = pdfPage.getViewport({ scale });
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        setRatio(base.height / base.width);
        task = pdfPage.render({ canvas, viewport });
        return task.promise;
      })
      .catch((cause: unknown) => {
        if (!cancelled && !(cause instanceof Error && cause.name === "RenderingCancelledException")) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, page]);

  if (error) {
    return (
      <p role="alert" className="text-danger-text text-sm">
        Couldn&rsquo;t open this PDF ({error}).
      </p>
    );
  }
  return (
    <div
      data-slot="block-document-pdf"
      className="bg-muted/40 relative w-full overflow-hidden rounded-md"
      style={{ aspectRatio: ratio ? `1 / ${ratio}` : "3 / 4" }}
    >
      <canvas ref={canvasRef} aria-label={`Page ${page}`} role="img" className="size-full" />
      {doc ? children : <span className="text-muted-foreground absolute inset-0 flex items-center justify-center text-sm">Loading…</span>}
    </div>
  );
}

export default PdfPage;
