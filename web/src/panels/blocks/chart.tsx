"use client";

/**
 * `chart` block — numbers the agent shows as one big number, bars, a line, a
 * pie or a gauge (`show_chart`, V6-23, D-V6-20, ask #215). Inline SVG the
 * console owns — no chart library. The SVG is decorative (`aria-hidden`):
 * an accessible `<table>` underneath (`table.tsx`'s own pattern) carries the
 * same numbers for a screen reader, always, whether or not `config.show_table`
 * also puts it on screen.
 *
 * Colours come from the app's own tone tokens (never a raw `emerald-*` /
 * `sky-*` literal, per `panels/generic/blocks.tsx`'s house rule), cycled
 * across series — there are 6 distinct series tones and `MAX_CHART_SERIES`
 * is 8, so the 7th and 8th series repeat a tone; a rare edge the contract
 * does not otherwise resolve.
 */
import * as React from "react";
import { useMemo } from "react";

import type { ChartBlockState, ChartPoint } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const WIDTH = 400;
const HEIGHT = 200;
const PAD = { top: 12, right: 12, bottom: 24, left: 12 };

/** Categorical series colours, from the app's own tone tokens (no raw hex, no Tailwind colour literals). */
const SERIES_COLORS = [
  "var(--color-brand)",
  "var(--color-info)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-danger)",
  "var(--color-stage)",
] as const;

function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

function formatValue(value: number, unit: string | null): string {
  const text = Number.isInteger(value) ? value.toLocaleString("en-US") : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return unit ? `${text} ${unit}` : text;
}

/** Distinct series names, in first-appearance order; `null` (no series) becomes one implicit series. */
function seriesOf(points: readonly ChartPoint[]): (string | null)[] {
  const seen: (string | null)[] = [];
  for (const point of points) {
    const key = point.series ?? null;
    if (!seen.includes(key)) seen.push(key);
  }
  return seen.length > 0 ? seen : [null];
}

/** Distinct labels, in first-appearance order (the x-axis / pie slices / bar groups). */
function labelsOf(points: readonly ChartPoint[]): string[] {
  const seen: string[] = [];
  for (const point of points) if (!seen.includes(point.label)) seen.push(point.label);
  return seen;
}

/** The always-present accessible table under the chart (`table.tsx`'s own markup). */
function ChartTable({ points, unit, visible }: { points: readonly ChartPoint[]; unit: string | null; visible: boolean }) {
  const hasSeries = points.some((p) => p.series != null);
  return (
    <div
      data-slot="chart-table"
      className={cn(
        "border-border -mx-1 overflow-x-auto rounded-md border",
        visible ? "mt-3" : "sr-only",
      )}
    >
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-muted/50">
            <th scope="col" className="text-muted-foreground px-2.5 py-1.5 text-left text-xs font-medium">
              Label
            </th>
            {hasSeries && (
              <th scope="col" className="text-muted-foreground px-2.5 py-1.5 text-left text-xs font-medium">
                Series
              </th>
            )}
            <th scope="col" className="text-muted-foreground px-2.5 py-1.5 text-right text-xs font-medium">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {points.map((point, index) => (
            <tr key={index} className="border-border border-t">
              <td className="px-2.5 py-1.5">{point.label}</td>
              {hasSeries && <td className="px-2.5 py-1.5">{point.series ?? "—"}</td>}
              <td className="px-2.5 py-1.5 text-right tabular-nums">{formatValue(point.value, unit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NumberChart({ point, unit }: { point: ChartPoint | undefined; unit: string | null }) {
  return (
    <div data-slot="chart-number" className="flex flex-col items-center justify-center gap-1 py-4">
      <span className="text-foreground text-4xl font-semibold tabular-nums">
        {point ? formatValue(point.value, unit) : "—"}
      </span>
    </div>
  );
}

/**
 * A semicircular gauge, `min`..`max` swept left-to-right through the top
 * (a math angle of `π` at the left point, increasing to `2π` at the right
 * point — always a clockwise sweep on screen, so `sweep-flag` is always `1`;
 * the foreground arc's span never exceeds `π` radians, so `large-arc-flag`
 * is always `0`).
 */
function GaugeChart({ point, min, max, unit }: { point: ChartPoint | undefined; min: number; max: number; unit: string | null }) {
  const value = point?.value ?? min;
  const clamped = Math.min(max, Math.max(min, value));
  const fraction = max > min ? (clamped - min) / (max - min) : 0;
  const cx = WIDTH / 2;
  const cy = HEIGHT - 30;
  const r = 110;
  const startAngle = Math.PI;
  const endAngle = Math.PI + fraction * Math.PI;
  const point0: [number, number] = [cx + r * Math.cos(startAngle), cy + r * Math.sin(startAngle)];
  const point1: [number, number] = [cx + r * Math.cos(endAngle), cy + r * Math.sin(endAngle)];
  const trackEnd: [number, number] = [cx + r, cy];
  return (
    <div data-slot="chart-gauge" className="flex flex-col items-center gap-2">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false" className="w-full" style={{ maxHeight: HEIGHT }}>
        <path
          d={`M ${point0[0]} ${point0[1]} A ${r} ${r} 0 1 1 ${trackEnd[0]} ${trackEnd[1]}`}
          fill="none"
          stroke="var(--color-muted)"
          strokeWidth={18}
          strokeLinecap="round"
        />
        {fraction > 0 && (
          <path
            d={`M ${point0[0]} ${point0[1]} A ${r} ${r} 0 0 1 ${point1[0]} ${point1[1]}`}
            fill="none"
            stroke="var(--color-brand)"
            strokeWidth={18}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div className="-mt-8 flex w-full max-w-xs items-end justify-between px-4 text-xs">
        <span className="text-muted-foreground">{formatValue(min, unit)}</span>
        <span className="text-foreground text-xl font-semibold tabular-nums">{formatValue(clamped, unit)}</span>
        <span className="text-muted-foreground">{formatValue(max, unit)}</span>
      </div>
    </div>
  );
}

function BarChart({ points }: { points: readonly ChartPoint[] }) {
  const labels = useMemo(() => labelsOf(points), [points]);
  const series = useMemo(() => seriesOf(points), [points]);
  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const values = points.map((p) => p.value);
  const maxValue = Math.max(0, ...values);
  const minValue = Math.min(0, ...values);
  const domain = Math.max(maxValue - minValue, 1);
  const zeroY = PAD.top + innerH * (maxValue / domain);
  const yOf = (value: number) => PAD.top + innerH * ((maxValue - value) / domain);

  const groupW = innerW / Math.max(labels.length, 1);
  const barGap = 4;
  const barW = Math.max(2, (groupW - barGap * 2) / Math.max(series.length, 1));

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false" className="w-full" style={{ maxHeight: HEIGHT }}>
      <line x1={PAD.left} y1={zeroY} x2={WIDTH - PAD.right} y2={zeroY} stroke="var(--color-border)" strokeWidth={1} />
      {labels.map((label, labelIndex) => {
        const groupX = PAD.left + labelIndex * groupW;
        return (
          <g key={label}>
            {series.map((seriesName, seriesIndex) => {
              const point = points.find((p) => p.label === label && (p.series ?? null) === seriesName);
              if (!point) return null;
              const barX = groupX + barGap + seriesIndex * barW;
              const y = yOf(point.value);
              const barY = Math.min(y, zeroY);
              const barH = Math.max(1, Math.abs(zeroY - y));
              return <rect key={seriesName ?? "_"} x={barX} y={barY} width={Math.max(1, barW - 2)} height={barH} fill={seriesColor(seriesIndex)} rx={1.5} />;
            })}
            <text x={groupX + groupW / 2} y={HEIGHT - PAD.bottom + 14} textAnchor="middle" fontSize={10} fill="var(--color-muted-foreground)">
              {label.length > 8 ? `${label.slice(0, 7)}…` : label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function LineChart({ points }: { points: readonly ChartPoint[] }) {
  const labels = useMemo(() => labelsOf(points), [points]);
  const series = useMemo(() => seriesOf(points), [points]);
  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const values = points.map((p) => p.value);
  const maxValue = Math.max(...values, 0);
  const minValue = Math.min(...values, 0);
  const domain = Math.max(maxValue - minValue, 1);
  const yOf = (value: number) => PAD.top + innerH * ((maxValue - value) / domain);
  const xOf = (labelIndex: number) => PAD.left + (labels.length > 1 ? (innerW * labelIndex) / (labels.length - 1) : innerW / 2);

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false" className="w-full" style={{ maxHeight: HEIGHT }}>
      {series.map((seriesName, seriesIndex) => {
        const seriesPoints = labels
          .map((label, labelIndex) => {
            const point = points.find((p) => p.label === label && (p.series ?? null) === seriesName);
            return point ? ([xOf(labelIndex), yOf(point.value)] as [number, number]) : null;
          })
          .filter((p): p is [number, number] => p !== null);
        if (seriesPoints.length === 0) return null;
        const d = seriesPoints.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
        const color = seriesColor(seriesIndex);
        return (
          <g key={seriesName ?? "_"}>
            <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {seriesPoints.map(([x, y], i) => (
              <circle key={i} cx={x} cy={y} r={3} fill={color} />
            ))}
          </g>
        );
      })}
      {labels.map((label, labelIndex) => (
        <text key={label} x={xOf(labelIndex)} y={HEIGHT - PAD.bottom + 14} textAnchor="middle" fontSize={10} fill="var(--color-muted-foreground)">
          {label.length > 8 ? `${label.slice(0, 7)}…` : label}
        </text>
      ))}
    </svg>
  );
}

function polarPoint(cx: number, cy: number, r: number, angle: number): [number, number] {
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

function PieChart({ points }: { points: readonly ChartPoint[] }) {
  const total = points.reduce((sum, p) => sum + p.value, 0);
  const cx = WIDTH / 2;
  const cy = HEIGHT / 2;
  const r = Math.min(WIDTH, HEIGHT) / 2 - 20;
  if (total <= 0) {
    return (
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false" className="w-full" style={{ maxHeight: HEIGHT }}>
        <circle cx={cx} cy={cy} r={r} fill="var(--color-muted)" />
      </svg>
    );
  }
  let angle = -Math.PI / 2;
  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false" className="w-full" style={{ maxHeight: HEIGHT }}>
      {points.map((point, index) => {
        const sweep = (point.value / total) * Math.PI * 2;
        const start = angle;
        const end = angle + sweep;
        angle = end;
        const [x0, y0] = polarPoint(cx, cy, r, start);
        const [x1, y1] = polarPoint(cx, cy, r, end);
        const largeArc = sweep > Math.PI ? 1 : 0;
        const d = sweep >= Math.PI * 2 - 0.0001
          ? `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`
          : `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${largeArc} 1 ${x1} ${y1} Z`;
        return <path key={index} d={d} fill={seriesColor(index)} stroke="var(--color-card)" strokeWidth={1} />;
      })}
    </svg>
  );
}

export function ChartBlock({ spec, data, title, highlighted }: BlockRenderProps<ChartBlockState>) {
  const points = useMemo(() => (Array.isArray(data.points) ? data.points : []), [data.points]);
  const kind = data.kind ?? "bar";
  const unit = typeof data.unit === "string" ? data.unit : null;
  const showTable = (spec.config as { show_table?: unknown } | null)?.show_table === true;

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {points.length === 0 ? (
        <PanelEmpty>No numbers yet.</PanelEmpty>
      ) : (
        <div data-slot="block-chart" className="flex flex-col gap-1">
          {typeof data.title === "string" && data.title && <h4 className="text-sm font-medium">{data.title}</h4>}
          {kind === "number" && <NumberChart point={points[0]} unit={unit} />}
          {kind === "gauge" && <GaugeChart point={points[0]} min={data.gauge_min ?? 0} max={data.gauge_max ?? 100} unit={unit} />}
          {kind === "bar" && <BarChart points={points} />}
          {kind === "line" && <LineChart points={points} />}
          {kind === "pie" && <PieChart points={points} />}
          {typeof data.caption === "string" && data.caption && <p className="text-muted-foreground text-[0.8125rem]">{data.caption}</p>}
          <ChartTable points={points} unit={unit} visible={showTable} />
        </div>
      )}
    </BlockFrame>
  );
}

export default ChartBlock;
