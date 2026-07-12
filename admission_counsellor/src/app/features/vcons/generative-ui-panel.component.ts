import {
  ChangeDetectionStrategy, Component, computed, input, output,
} from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { BarListComponent, DonutComponent, SparklineComponent } from '../../shared/ui/charts.component';
import { BarDatum } from '../../domain/models';
import {
  UiDirective, UiBlock, KpiBlock, BarBlock, LineBlock, DonutBlock, TableBlock, ReportBlock,
} from './ui-directive';

/**
 * GenerativeUiPanelComponent — renders a director-agent UiDirective received over
 * the WebRTC data channel. Charts reuse the app's existing SVG primitives
 * (va-bar-list / va-donut / va-sparkline) — no chart library. Angular port of
 * web-app/src/components/generative-ui/*.
 */
@Component({
  selector: 'va-generative-ui-panel',
  standalone: true,
  imports: [IconComponent, BarListComponent, DonutComponent, SparklineComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="gp">
      <div class="gp-head">
        <div class="gp-title">
          <span class="chip ai-chip"><va-icon name="sparkles" [size]="13"></va-icon> AI report</span>
          <h2 class="t-h3">{{ directive().title }}</h2>
          @if (directive().narration) { <p class="t-sm t-muted">{{ directive().narration }}</p> }
        </div>
        <button class="btn btn-ghost btn-sm" (click)="dismiss.emit()">
          <va-icon name="x" [size]="16"></va-icon> Back
        </button>
      </div>

      <div class="gp-body scroll-y">
        @for (b of directive().blocks; track $index) {
          @switch (b.kind) {

            @case ('kpi') {
              <div class="kpi-grid">
                @for (it of asKpi(b).items; track it.label) {
                  <div class="tile">
                    <span class="tl">{{ it.label }}</span>
                    <span class="tv">{{ it.value }}</span>
                    @if (it.delta) {
                      <span class="trend" [attr.data-t]="it.trend || 'flat'">
                        <va-icon [name]="trendIcon(it.trend)" [size]="12"></va-icon>{{ it.delta }}
                      </span>
                    }
                  </div>
                }
              </div>
            }

            @case ('bar') {
              <section class="block">
                <h4 class="t-h4">{{ asBar(b).title }}</h4>
                <va-bar-list [data]="toBars(asBar(b).series)"></va-bar-list>
              </section>
            }

            @case ('line') {
              <section class="block">
                <h4 class="t-h4">{{ asLine(b).title }}</h4>
                <div class="spark-wrap">
                  <va-sparkline [data]="toNums(asLine(b).series)" color="var(--color-accent)" [height]="64"></va-sparkline>
                </div>
                <div class="spark-axis t-cap t-muted">
                  <span>{{ firstX(asLine(b)) }}</span><span>{{ lastX(asLine(b)) }}</span>
                </div>
              </section>
            }

            @case ('donut') {
              <section class="block">
                <h4 class="t-h4">{{ asDonut(b).title }}</h4>
                <va-donut [data]="toBars(asDonut(b).series)" [centerLabel]="asDonut(b).title"></va-donut>
              </section>
            }

            @case ('table') {
              <section class="block">
                <h4 class="t-h4">{{ asTable(b).title }}</h4>
                <div class="table-wrap">
                  <table class="va-table">
                    <thead><tr>@for (c of asTable(b).columns; track c) { <th>{{ c }}</th> }</tr></thead>
                    <tbody>
                      @for (row of asTable(b).rows; track $index) {
                        <tr>@for (cell of row; track $index) { <td>{{ cell }}</td> }</tr>
                      }
                    </tbody>
                  </table>
                </div>
              </section>
            }

            @case ('report') {
              <section class="block">
                <h4 class="t-h4">{{ asReport(b).title }}</h4>
                <p class="report-body t-sm">{{ asReport(b).body }}</p>
              </section>
            }
          }
        }
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .gp { display: flex; flex-direction: column; height: 100%;
      background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e2); overflow: hidden; }
    .gp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 16px 18px; border-bottom: 1px solid var(--color-border); }
    .gp-title { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .gp-title p { margin: 0; max-width: 70ch; }
    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; align-self: flex-start; }
    .gp-head .btn { flex: none; }

    .gp-body { flex: 1; min-height: 0; padding: 18px; padding-right: var(--gp-pad-right, 18px); display: flex; flex-direction: column; gap: 20px; }
    .block { display: flex; flex-direction: column; gap: 12px; }

    .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
    .trend { display: inline-flex; align-items: center; gap: 3px; font-size: var(--text-cap); font-weight: 700; margin-top: 2px; }
    .trend[data-t='up'] { color: var(--color-success); }
    .trend[data-t='down'] { color: var(--color-danger); }
    .trend[data-t='flat'] { color: var(--color-text-muted); }

    .spark-wrap { height: 64px; }
    .spark-axis { display: flex; justify-content: space-between; }

    .table-wrap { border: 1px solid var(--color-border); border-radius: var(--r-md); overflow: hidden; }
    .report-body { line-height: 1.55; white-space: pre-line;
      background: var(--color-surface-alt); border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 14px; margin: 0; }
  `],
})
export class GenerativeUiPanelComponent {
  readonly directive = input.required<UiDirective>();
  readonly dismiss = output<void>();

  // Narrowing helpers — strict templates can't narrow a union by `.kind`, so the
  // @switch case guarantees the kind and these casts give the typed shape.
  asKpi = (b: UiBlock) => b as KpiBlock;
  asBar = (b: UiBlock) => b as BarBlock;
  asLine = (b: UiBlock) => b as LineBlock;
  asDonut = (b: UiBlock) => b as DonutBlock;
  asTable = (b: UiBlock) => b as TableBlock;
  asReport = (b: UiBlock) => b as ReportBlock;

  toBars(series: { x: string; y: number }[]): BarDatum[] {
    return series.map(p => ({ label: p.x, value: p.y }));
  }
  toNums(series: { x: string; y: number }[]): number[] {
    return series.map(p => p.y);
  }
  firstX(b: LineBlock): string { return b.series[0]?.x ?? ''; }
  lastX(b: LineBlock): string { return b.series[b.series.length - 1]?.x ?? ''; }

  trendIcon(trend?: string | null): string {
    return trend === 'up' ? 'arrow-up' : trend === 'down' ? 'arrow-down' : 'minus';
  }
}
