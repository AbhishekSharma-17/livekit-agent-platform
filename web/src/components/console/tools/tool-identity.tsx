import * as React from "react";
import { GlobeIcon, PlugIcon, Table2Icon, type LucideIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { VendorMark } from "@/components/shared/vendor-mark";
import { vendorKey, vendorMarkFor } from "@/components/shared/vendor-marks";
import { MCP_PRESETS } from "@/components/console/tools/mcp-presets";
import { cn } from "@/lib/utils";
import type { ToolOut } from "@/contracts/lkap-contracts";

/**
 * How a tool reads in a list: a human title, the service it belongs to and
 * its leading mark. The technical `name` is what the model sees; lists show
 * it small and in mono beside the title, and the edit dialog shows it whole.
 */

/** Words kept upper case when a name is humanised ("get_order_id" → "Get order ID"). */
const ACRONYMS: Record<string, string> = {
  ai: "AI",
  api: "API",
  crm: "CRM",
  csv: "CSV",
  faq: "FAQ",
  http: "HTTP",
  id: "ID",
  ids: "IDs",
  json: "JSON",
  llm: "LLM",
  mcp: "MCP",
  otp: "OTP",
  pdf: "PDF",
  sms: "SMS",
  sql: "SQL",
  url: "URL",
  urls: "URLs",
};

/**
 * A technical tool name as a sentence-case title: drops a leading app prefix
 * ("gmail_send_email" with prefix "gmail" → "Send email"), splits on `_`, `-`,
 * `.` and camelCase ("customerLookup" → "Customer lookup") and keeps common
 * acronyms upper case. A name that already reads as words ("Policy lookup")
 * or a brand-cased single word ("GitHub") is returned as it is.
 */
export function humanizeToolName(name: string, prefixes: readonly (string | null | undefined)[] = []): string {
  const raw = name.trim();
  if (!raw) return name;
  const technical = /[_\-.]/.test(raw) || /^[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)*$/.test(raw);
  if (/\s/.test(raw) || !technical) return raw;
  let rest = raw;
  for (const prefix of prefixes) {
    if (!prefix) continue;
    const key = vendorKey(prefix);
    const match = rest.match(/^([A-Za-z0-9]+)[_\-.](.+)$/);
    if (key && match && vendorKey(match[1]) === key) {
      rest = match[2];
      break;
    }
  }
  const words = rest
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[\s_\-.]+/)
    .filter(Boolean)
    .map((word) => word.toLowerCase());
  if (words.length === 0) return raw;
  return words
    .map((word, index) => ACRONYMS[word] ?? (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");
}

function isProviderTool(tool: ToolOut): boolean {
  return "tool_slug" in tool.definition;
}

function hostOf(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** A brand from a host: the second-level label, then the label plus its TLD ("mcp.linear.app" → linear; "api.cal.com" → calcom). */
function brandFromHost(host: string | null): string | null {
  if (!host) return null;
  const labels = host.split(".").filter(Boolean);
  if (labels.length < 2) return null;
  const sld = labels[labels.length - 2];
  const tld = labels[labels.length - 1];
  if (vendorMarkFor(sld)) return sld;
  if (vendorMarkFor(`${sld}${tld}`)) return `${sld}${tld}`;
  return null;
}

/** The service a tool belongs to, as a vendor label for `VendorMark`, or `null` (a generic kind icon). */
export function toolService(tool: ToolOut, appName?: string | null): string | null {
  if (isProviderTool(tool)) {
    const definition = tool.definition as { toolkit?: string };
    return appName ?? definition.toolkit ?? null;
  }
  if (tool.kind === "dataset") return null;
  const url = "url" in tool.definition ? (tool.definition as { url?: string }).url : undefined;
  const host = hostOf(url);
  if (tool.kind === "mcp" || tool.definition.kind === "mcp") {
    const preset = host ? MCP_PRESETS.find((p) => hostOf(p.url) === host) : undefined;
    if (preset) return preset.name;
    if (vendorMarkFor(tool.name)) return tool.name;
  }
  return brandFromHost(host);
}

/** The list title for a tool: its humanised name, without the app prefix for an app action. */
export function toolTitle(tool: ToolOut, appName?: string | null): string {
  const toolkit = isProviderTool(tool) ? (tool.definition as { toolkit?: string }).toolkit : undefined;
  return humanizeToolName(tool.name, [toolkit, appName]);
}

const TILE: Record<"sm" | "md", string> = { sm: "size-5", md: "size-6" };

/**
 * The leading mark on a tool row: the app's or server's real mark when the
 * console knows the service (`vendor-marks.ts`), else a kind icon: a table for
 * a lookup tool, a globe for HTTP and a plug for an MCP server. An app with no
 * known mark keeps its monogram. Decorative: the row's text names the tool.
 */
export function ToolMark({
  tool,
  appName,
  size = "md",
  className,
}: {
  tool: ToolOut;
  appName?: string | null;
  size?: "sm" | "md";
  className?: string;
}) {
  const service = toolService(tool, appName);
  if (service && (isProviderTool(tool) || vendorMarkFor(service))) {
    return <VendorMark vendor={service} size={size} className={className} />;
  }
  const icon: LucideIcon = tool.kind === "dataset" ? Table2Icon : tool.kind === "http" ? GlobeIcon : PlugIcon;
  return (
    <span
      aria-hidden="true"
      data-slot="tool-mark"
      data-icon={tool.kind === "dataset" ? "table" : tool.kind === "http" ? "globe" : "plug"}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-sm bg-muted-strong text-text-secondary",
        TILE[size],
        className,
      )}
    >
      <Icon as={icon} size="sm" />
    </span>
  );
}
