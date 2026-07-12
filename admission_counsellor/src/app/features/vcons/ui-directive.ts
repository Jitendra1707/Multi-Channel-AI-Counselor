/**
 * UiDirective — TS mirror of the backend Pydantic schema
 * (AegisBackend/aegis_backend/schemas/ui_directive.py), ported from
 * web-app/src/lib/uiDirective.ts.
 *
 * The director-briefing agent emits one of these over the WebRTC data channel
 * ({ type: 'ui_directive', directive: UiDirective }); GenerativeUiPanelComponent
 * renders each block by its `kind`. Keep in sync with the Python schema.
 */

export interface KpiItem {
  label: string;
  value: string;
  delta?: string | null;
  trend?: 'up' | 'down' | 'flat' | null;
}

export interface SeriesPoint {
  x: string;
  y: number;
}

export interface KpiBlock {
  kind: 'kpi';
  title?: string | null;
  items: KpiItem[];
}

export interface BarBlock {
  kind: 'bar';
  title: string;
  x_label?: string | null;
  y_label?: string | null;
  series: SeriesPoint[];
}

export interface LineBlock {
  kind: 'line';
  title: string;
  x_label?: string | null;
  y_label?: string | null;
  series: SeriesPoint[];
}

export interface DonutBlock {
  kind: 'donut';
  title: string;
  series: SeriesPoint[];
}

export interface TableBlock {
  kind: 'table';
  title: string;
  columns: string[];
  rows: string[][];
}

export interface ReportBlock {
  kind: 'report';
  title: string;
  body: string;
}

export type UiBlock =
  | KpiBlock
  | BarBlock
  | LineBlock
  | DonutBlock
  | TableBlock
  | ReportBlock;

export interface UiDirective {
  version: number;
  title: string;
  blocks: UiBlock[];
  narration?: string | null;
}

/** Narrow an unknown data-channel payload to a UiDirective (defensive). */
export function parseUiDirective(input: unknown): UiDirective | null {
  if (!input || typeof input !== 'object') return null;
  const d = input as Partial<UiDirective>;
  if (typeof d.title !== 'string' || !Array.isArray(d.blocks) || d.blocks.length === 0) {
    return null;
  }
  return d as UiDirective;
}
