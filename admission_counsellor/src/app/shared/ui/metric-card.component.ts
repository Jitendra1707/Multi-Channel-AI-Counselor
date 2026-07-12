import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { Metric } from '../../domain/models';
import { IconComponent } from './icon.component';
import { SparklineComponent } from './charts.component';
import { fmtMetric } from '../util/format';

/** KPI card: value, label, delta vs prior period, sparkline, click-to-drill (§35). */
@Component({
  selector: 'va-metric-card',
  standalone: true,
  imports: [IconComponent, SparklineComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button class="mc" [attr.data-tone]="metric.tone || 'default'" [class.clickable]="!!metric.drillTo"
            (click)="drill.emit(metric)" [disabled]="!metric.drillTo">
      <div class="mc-head">
        <span class="mc-label">{{ metric.label }}</span>
        @if (metric.tone === 'ai') { <span class="ai-dot" title="AI metric"></span> }
      </div>
      <div class="mc-value t-num">{{ value() }}</div>
      <div class="mc-foot">
        <span class="delta" [class.neg]="metric.deltaPct < 0">
          <va-icon [name]="metric.deltaPct < 0 ? 'arrow-down' : 'arrow-up'" [size]="13"></va-icon>
          {{ absDelta() }}%
        </span>
        <span class="mc-spark"><va-sparkline [data]="metric.trend" [color]="sparkColor()" [height]="32"></va-sparkline></span>
      </div>
    </button>`,
  styles: [`
    .mc { position: relative; width: 100%; text-align: left; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-lg); padding: 16px; box-shadow: var(--e1);
      display: flex; flex-direction: column; gap: 8px; transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; overflow: hidden; }
    .mc::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--color-border); opacity: 0; transition: opacity .15s; }
    .mc.clickable { cursor: pointer; }
    .mc.clickable:hover { transform: translateY(-2px); box-shadow: var(--e2); border-color: var(--color-border-strong); }
    .mc.clickable:hover::before { opacity: 1; }
    .mc[data-tone='success']::before { background: var(--color-success); }
    .mc[data-tone='warning']::before { background: var(--color-warning); }
    .mc[data-tone='danger']::before  { background: var(--color-danger); }
    .mc[data-tone='ai']::before      { background: var(--gradient-ai); }
    .mc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .mc-label { font-size: var(--text-sm); color: var(--color-text-muted); font-weight: 500; }
    .ai-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gradient-ai); flex: none; }
    .mc-value { font-size: 1.9rem; font-weight: 700; line-height: 1.1; letter-spacing: -.01em; }
    .mc-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 2px; }
    .delta { display: inline-flex; align-items: center; gap: 2px; font-size: var(--text-cap); font-weight: 700; color: var(--color-success);
      background: var(--color-success-soft); padding: 3px 7px; border-radius: var(--r-pill); }
    .delta.neg { color: var(--color-danger); background: var(--color-danger-soft); }
    .mc-spark { width: 92px; height: 32px; flex: none; opacity: .9; }
  `],
})
export class MetricCardComponent {
  @Input({ required: true }) metric!: Metric;
  @Output() drill = new EventEmitter<Metric>();
  value() { return this.metric.display ?? fmtMetric(this.metric.value, this.metric.format); }
  absDelta() { return Math.abs(this.metric.deltaPct).toFixed(1); }
  sparkColor() {
    switch (this.metric.tone) {
      case 'success': return 'var(--color-success)';
      case 'warning': return 'var(--color-warning)';
      case 'danger': return 'var(--color-danger)';
      case 'ai': return 'var(--color-accent)';
      default: return 'var(--color-primary)';
    }
  }
}
