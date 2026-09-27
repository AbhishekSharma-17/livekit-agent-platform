"use client";

/**
 * `link` block — a checkout, e-sign or portal link the caller opens, and
 * whether they finished (`send_link` / the link hook, `LinkBlockState`,
 * V5-43 → V5-44, `docs/v5/_asks.md` #310).
 *
 * The worker already checks `url` against the block's `allowed_hosts` and
 * refuses anything but `https://` (`send_link`, `LinkBlockState._https`),
 * but this renders whatever `UiState` last carried with no further trip
 * through the api — so it re-checks here too (`checkedHttpsUrl`, `./types`)
 * before ever putting the link in an `href`, a QR code or an iframe `src`.
 * A link that fails the re-check (an unlisted host, `javascript:`, `data:`,
 * a bare `http:`) is never rendered as a link at all.
 *
 * States (`status`): `idle` (nothing sent, the empty state) → `pending`
 * (shown or texted) → `opened` (the caller opened it) → `completed` /
 * `failed` / `expired` (the outcome the business's own system reported
 * through the signed link hook, or the block's own expiry passing).
 * Opening the link posts `block_action {block_id, name: "opened"}` so the
 * worker can move `pending → opened`; it is fire-and-forget (never blocks
 * the browser's own navigation) and safe to send more than once.
 */
import * as React from "react";
import { ExternalLinkIcon, QrCodeIcon, SmartphoneIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { LinkBlockState } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import { checkedHttpsUrl, hostsOf } from "./types";
import type { BlockRenderProps } from "./types";

type LinkStatus = NonNullable<LinkBlockState["status"]>;

const STATUS_LABEL: Record<LinkStatus, string> = {
  idle: "Not sent",
  pending: "Waiting for you to open it",
  opened: "Opened",
  completed: "Completed",
  failed: "Didn't go through",
  expired: "Expired",
};

const STATUS_TONE: Record<LinkStatus, StatusTone> = {
  idle: "neutral",
  pending: "info",
  opened: "info",
  completed: "success",
  failed: "danger",
  expired: "warning",
};

function siteNameOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

interface LinkConfig {
  openIn: "new_tab" | "dialog";
  showQr: boolean;
}

function configOf(config: unknown): LinkConfig {
  const record = isRecord(config) ? config : {};
  return {
    openIn: record.open_in === "dialog" ? "dialog" : "new_tab",
    showQr: record.show_qr !== false,
  };
}

/**
 * The QR module matrix for `url`, loaded on demand (`qrcode-generator`, a
 * dependency-free ~50 KB module) so the link block's own bundle stays small
 * for the far more common case of a session with no `link` block or
 * `show_qr: false` — this `import()` is not reachable from the block index's
 * eager map, only from here, once a QR is actually needed.
 */
function useQrMatrix(url: string | null): boolean[][] | null {
  const [matrix, setMatrix] = React.useState<boolean[][] | null>(null);

  React.useEffect(() => {
    setMatrix(null);
    if (!url) return undefined;
    let cancelled = false;
    void import("qrcode-generator").then(({ default: qrcode }) => {
      if (cancelled) return;
      const qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      const count = qr.getModuleCount();
      const rows: boolean[][] = [];
      for (let row = 0; row < count; row += 1) {
        const cells: boolean[] = [];
        for (let col = 0; col < count; col += 1) cells.push(qr.isDark(row, col));
        rows.push(cells);
      }
      setMatrix(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return matrix;
}

/**
 * A QR code drawn as one `<path>` of unit squares — never `createSvgTag`'s
 * raw markup string injected into the DOM. Fixed black-on-white (not theme
 * tokens, deliberately: a QR code must keep full contrast in dark mode too
 * or a phone camera won't read it).
 */
function LinkQr({ url, label }: { url: string; label: string }) {
  const matrix = useQrMatrix(url);
  if (!matrix) {
    return <div aria-hidden="true" className="size-28 shrink-0 animate-pulse rounded-md bg-muted" />;
  }
  const size = matrix.length;
  let path = "";
  matrix.forEach((row, r) => {
    row.forEach((dark, c) => {
      if (dark) path += `M${c},${r}h1v1h-1z`;
    });
  });
  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      className="size-28 shrink-0 rounded-sm bg-white p-1.5"
    >
      <path d={path} fill="#000" />
    </svg>
  );
}

/**
 * `open_in="dialog"`: the link opens over the call instead of a new tab.
 * Many checkout/e-sign hosts refuse to be framed (`X-Frame-Options` /
 * `frame-ancestors`), so the dialog always keeps an "Open in a new tab"
 * fallback (flagged as an ask — `docs/v5/_asks.md` #316).
 */
function LinkDialog({
  url,
  label,
  open,
  onOpenChange,
}: {
  url: string;
  label: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
        </DialogHeader>
        <DialogBody className="p-0">
          <iframe
            src={url}
            title={label}
            sandbox="allow-forms allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer"
            className="h-[70vh] w-full border-0"
          />
        </DialogBody>
        <DialogFooter>
          <Button asChild variant="outline">
            <a href={url} target="_blank" rel="noopener noreferrer">
              <Icon as={ExternalLinkIcon} size="sm" />
              Open in a new tab
            </a>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function LinkBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<LinkBlockState>) {
  const { openIn, showQr } = configOf(spec.config);
  const allowedHosts = hostsOf(spec.config, "allowed_hosts");
  const status: LinkStatus = data.status ?? "idle";
  const safeUrl = checkedHttpsUrl(data.url, allowedHosts);
  const [dialogOpen, setDialogOpen] = React.useState(false);

  function notifyOpened() {
    void panel.perform({ action: "block_action", payload: { block_id: spec.id, name: "opened" } });
  }

  if (status === "idle" || !data.url) {
    return (
      <BlockFrame spec={spec} title={title} highlighted={highlighted}>
        <PanelEmpty>The agent will show a link here when there is one to open.</PanelEmpty>
      </BlockFrame>
    );
  }

  const site = safeUrl ? siteNameOf(safeUrl) : null;
  const label = data.label || site || "Open link";

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      <div data-slot="block-link" className="flex flex-col gap-3">
        {data.label && <p className="text-sm font-medium">{data.label}</p>}
        {!safeUrl ? (
          <p className="text-danger-text text-sm">This link can&apos;t be shown safely.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {openIn === "dialog" ? (
                <Button
                  type="button"
                  onClick={() => {
                    setDialogOpen(true);
                    notifyOpened();
                  }}
                >
                  <Icon as={ExternalLinkIcon} size="sm" />
                  {label}
                </Button>
              ) : (
                <Button asChild>
                  <a href={safeUrl} target="_blank" rel="noopener noreferrer" onClick={notifyOpened}>
                    <Icon as={ExternalLinkIcon} size="sm" />
                    {label}
                  </a>
                </Button>
              )}
              <span className="text-muted-foreground text-[0.8125rem]">{site}</span>
            </div>
            {data.channel === "sms" && (
              <p className="text-muted-foreground flex items-center gap-1.5 text-[0.8125rem]">
                <Icon as={SmartphoneIcon} size="sm" />
                Sent as a text message to your phone.
              </p>
            )}
            {showQr && (status === "pending" || status === "opened") ? (
              <div className="flex items-center gap-3">
                <LinkQr url={safeUrl} label={`QR code for ${site}`} />
                <p className="text-muted-foreground text-[0.8125rem]">
                  <Icon as={QrCodeIcon} size="sm" className="mb-0.5 inline" /> Scan with your phone to finish there.
                </p>
              </div>
            ) : null}
            {openIn === "dialog" ? (
              <LinkDialog url={safeUrl} label={label} open={dialogOpen} onOpenChange={setDialogOpen} />
            ) : null}
          </>
        )}
        <p role="status" aria-live="polite" className="flex flex-wrap items-center gap-1.5 text-[0.8125rem]">
          <StatusChip tone={STATUS_TONE[status]} size="sm" dot>
            {STATUS_LABEL[status]}
          </StatusChip>
          {data.expires_at ? (
            <span className="text-muted-foreground">Expires {formatDateTime(data.expires_at)}</span>
          ) : null}
        </p>
      </div>
    </BlockFrame>
  );
}

export default LinkBlock;
