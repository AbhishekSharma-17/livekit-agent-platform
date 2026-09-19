/**
 * Formatting helpers shared by the console and the session surface
 * (docs/UI_UX_SPEC.md §2.7). Pure functions; no React, no console imports, so
 * the session bundle can use them.
 *
 * Dates render in the viewer's local time zone unless `timeZone` is passed
 * (tests pass `"UTC"` for stable output). The locale is pinned to `en-GB`
 * so dates read "19 Sep, 03:04" everywhere (24-hour clock, day before month).
 */

const LOCALE = "en-GB";

export interface DateFormatOptions {
  /** Include seconds. Default `false` — lists never show seconds (§1.1). */
  seconds?: boolean;
  /** IANA zone; defaults to the viewer's zone. */
  timeZone?: string;
}

/**
 * Normalise a timestamp to epoch milliseconds.
 *
 * - ISO string → `Date.parse`.
 * - Numeric string → treated as a number.
 * - Number < 1e11 → epoch **seconds** (× 1000); otherwise epoch milliseconds.
 *
 * Returns `NaN` for anything unparseable.
 */
export function toMillis(ts: string | number): number {
  if (typeof ts === "number") {
    if (!Number.isFinite(ts)) return Number.NaN;
    return ts < 1e11 ? ts * 1000 : ts;
  }
  const trimmed = ts.trim();
  if (trimmed === "") return Number.NaN;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return toMillis(Number(trimmed));
  return Date.parse(trimmed);
}

function toDate(ts: string | number): Date | null {
  const ms = toMillis(ts);
  return Number.isNaN(ms) ? null : new Date(ms);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Calendar parts of `date` in `timeZone` (the viewer's zone when omitted). */
function calendarParts(date: Date, timeZone?: string): { day: number; month: number; year: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "numeric",
    year: "numeric",
    timeZone,
  }).formatToParts(date);
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? Number.NaN);
  return { day: get("day"), month: get("month"), year: get("year") };
}

/**
 * "19 Sep, 03:04" (adds the year when it isn't the current year). Month
 * abbreviations are fixed English three-letter forms (ICU's en-GB "Sept"
 * would otherwise vary by runtime).
 */
export function formatDateTime(ts: string | number, options: DateFormatOptions = {}): string {
  const date = toDate(ts);
  if (!date) return "—";
  const { seconds = false, timeZone } = options;
  const { day, month, year } = calendarParts(date, timeZone);
  const currentYear = calendarParts(new Date(), timeZone).year;
  const datePart = `${day} ${MONTHS[month - 1]}${year === currentYear ? "" : ` ${year}`}`;
  return `${datePart}, ${formatTime(date.getTime(), { seconds, timeZone })}`;
}

/** "03:04" (or "03:04:05" with `seconds`). */
export function formatTime(ts: string | number, options: DateFormatOptions = {}): string {
  const date = toDate(ts);
  if (!date) return "—";
  const { seconds = false, timeZone } = options;
  return new Intl.DateTimeFormat(LOCALE, {
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    hourCycle: "h23",
    timeZone,
  }).format(date);
}

/**
 * "42s", "1m 42s", "1h 5m". Negative, `NaN` or non-finite input → "—".
 * Sub-second durations round down to "0s".
 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

/** "0 B", "512 B", "1.5 KB", "3.2 MB", "1.1 GB" (binary units, 1 decimal ≥ 1 KB). */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  if (unit === 0) return `${Math.round(value)} B`;
  const rounded = value >= 100 ? Math.round(value).toString() : value.toFixed(1).replace(/\.0$/, "");
  return `${rounded} ${units[unit]}`;
}

/** `pluralize(3, "chunk", "chunks")` → "3 chunks"; `pluralize(1, …)` → "1 chunk". */
export function pluralize(n: number, one: string, many: string): string {
  return `${n.toLocaleString(LOCALE)} ${n === 1 ? one : many}`;
}

/**
 * Relative time used by `RelativeTime`: "just now", "4 min ago", "3 h ago",
 * "yesterday", "5 days ago"; older than 7 days falls back to
 * `formatDateTime`. Future times read "in 4 min".
 */
export function formatRelative(
  ts: string | number,
  now: number = Date.now(),
  options: DateFormatOptions = {},
): string {
  const ms = toMillis(ts);
  if (Number.isNaN(ms)) return "—";
  const diff = now - ms;
  const future = diff < 0;
  const abs = Math.abs(diff);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const phrase = (value: string) => (future ? `in ${value}` : `${value} ago`);

  if (abs < 45_000) return "just now";
  if (abs < hour) return phrase(`${Math.max(1, Math.floor(abs / minute))} min`);
  if (abs < day) return phrase(`${Math.floor(abs / hour)} h`);
  const days = Math.round(abs / day);
  if (days === 1) return future ? "tomorrow" : "yesterday";
  if (days <= 7) return phrase(`${days} days`);
  return formatDateTime(ms, options);
}
