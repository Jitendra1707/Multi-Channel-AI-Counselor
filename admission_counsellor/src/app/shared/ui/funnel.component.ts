import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { FunnelStage } from '../../domain/models';
import { IconComponent } from './icon.component';
import { fmtInt } from '../util/format';

/** Admissions funnel — horizontal stage bars with count/%/drop-off/trend (§32.5). */
@Component({
  selector: 'va-funnel',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="funnel">
      @for (s of stages; track s.key; let i = $index) {
        <button class="stage" (click)="stageClick.emit(s)" [style.--w.%]="width(s)" [attr.title]="'View ' + s.label + ' candidates'">
          <span class="bar"><span class="bar-fill" [style.width.%]="width(s)"></span></span>
          <span class="meta">
            <span class="top">
              <span class="label">{{ s.label }}</span>
              <span class="count t-num">{{ fmt(s.count) }}</span>
            </span>
            <span class="bottom t-cap">
              <span class="conv t-muted">{{ conv(i) }}% of total</span>
              @if (i > 0) {
                <span class="drop" [class.up]="s.dropOffPct < 0">
                  <va-icon [name]="s.dropOffPct < 0 ? 'trending-up' : 'trending-down'" [size]="12"></va-icon>
                  {{ absDrop(s) }}% {{ s.dropOffPct < 0 ? 'gain' : 'drop-off' }}
                </span>
              }
              <span class="trend" [class.neg]="s.trendPct < 0">
                <va-icon [name]="s.trendPct < 0 ? 'arrow-down' : 'arrow-up'" [size]="11"></va-icon>{{ absTrend(s) }}%
              </span>
            </span>
          </span>
        </button>
      }
    </div>`,
  styles: [`
    .funnel { display: flex; flex-direction: column; gap: 6px; }
    .stage { position: relative; display: grid; grid-template-columns: 1fr; text-align: left; background: transparent;
      border: none; padding: 10px 12px; border-radius: var(--r-md); cursor: pointer; transition: background .15s ease; }
    .stage:hover { background: var(--color-surface-alt); }
    .bar { position: absolute; inset: 0; border-radius: var(--r-md); overflow: hidden; }
    .bar-fill { position: absolute; inset: 0; right: auto; background: var(--gradient-ai); opacity: .14; border-radius: var(--r-md);
      transition: width .6s cubic-bezier(.4,0,.2,1); }
    .meta { position: relative; display: flex; flex-direction: column; gap: 4px; z-index: 1; }
    .top { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .label { font-weight: 600; font-size: var(--text-sm); }
    .count { font-weight: 700; font-size: var(--text-h4); font-variant-numeric: tabular-nums; }
    .bottom { display: flex; align-items: center; gap: 14px; }
    .drop { display: inline-flex; align-items: center; gap: 3px; color: var(--color-danger); font-weight: 600; }
    .drop.up { color: var(--color-success); }
    .trend { display: inline-flex; align-items: center; gap: 2px; color: var(--color-success); font-weight: 600; }
    .trend.neg { color: var(--color-danger); }
  `],
})
export class FunnelComponent {
  @Input({ required: true }) stages: FunnelStage[] = [];
  @Output() stageClick = new EventEmitter<FunnelStage>();
  fmt = fmtInt;
  private maxCount() { return Math.max(1, ...this.stages.map(s => s.count)); }
  width(s: FunnelStage) { return Math.max(8, (s.count / this.maxCount()) * 100); }
  conv(i: number) { const top = this.stages[0]?.count || 1; return ((this.stages[i].count / top) * 100).toFixed(1); }
  absDrop(s: FunnelStage) { return Math.abs(s.dropOffPct).toFixed(1); }
  absTrend(s: FunnelStage) { return Math.abs(s.trendPct); }
}
