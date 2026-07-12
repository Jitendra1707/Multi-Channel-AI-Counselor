import { ChangeDetectionStrategy, Component, Input, computed, signal } from '@angular/core';
import { BarDatum } from '../../domain/models';
import { fmtInt } from '../util/format';

/** Tiny inline sparkline (theme-aware, accessible label). */
@Component({
  selector: 'va-sparkline',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.viewBox]="'0 0 100 ' + height" preserveAspectRatio="none" class="spark" role="img" [attr.aria-label]="'trend'">
      <defs>
        <linearGradient [attr.id]="gid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" [attr.stop-color]="color" stop-opacity="0.22"/>
          <stop offset="100%" [attr.stop-color]="color" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path [attr.d]="area()" [attr.fill]="'url(#' + gid + ')'"/>
      <path [attr.d]="line()" fill="none" [attr.stroke]="color" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>`,
  styles: [`.spark { width: 100%; height: 100%; display: block; } :host { display: block; width: 100%; height: 100%; }`],
})
export class SparklineComponent {
  @Input({ required: true }) set data(v: number[]) { this._d.set(v ?? []); }
  @Input() color = 'var(--color-primary)';
  @Input() height = 36;
  private _d = signal<number[]>([]);
  gid = 'sg-' + Math.floor(Math.random() * 1e6);

  private pts = computed(() => {
    const d = this._d(); if (d.length < 2) return [] as [number, number][];
    const min = Math.min(...d), max = Math.max(...d), span = max - min || 1;
    return d.map((v, i) => [(i / (d.length - 1)) * 100, this.height - ((v - min) / span) * (this.height - 4) - 2] as [number, number]);
  });
  line = computed(() => this.pts().map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(2) + ' ' + p[1].toFixed(2)).join(' '));
  area = computed(() => { const p = this.pts(); if (!p.length) return ''; return this.line() + ` L100 ${this.height} L0 ${this.height} Z`; });
}

/** Horizontal bar list — for lead-source / course-demand. */
@Component({
  selector: 'va-bar-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="bars">
      @for (b of data; track b.label) {
        <div class="barrow">
          <div class="barlabel"><span class="truncate">{{ b.label }}</span>
            @if (b.sub) { <span class="t-cap t-muted">{{ b.sub }}</span> }
          </div>
          <div class="bartrack">
            <div class="barfill" [attr.data-tone]="b.tone || ''" [style.width.%]="pct(b.value)"></div>
          </div>
          <div class="barval t-num">{{ fmt(b.value) }}</div>
        </div>
      }
    </div>`,
  styles: [`
    .bars { display: flex; flex-direction: column; gap: 12px; }
    .barrow { display: grid; grid-template-columns: 140px 1fr 56px; align-items: center; gap: 12px; }
    .barlabel { display: flex; flex-direction: column; gap: 1px; font-size: var(--text-sm); font-weight: 500; min-width: 0; }
    .bartrack { height: 10px; background: var(--color-surface-alt); border-radius: 999px; overflow: hidden; }
    .barfill { height: 100%; border-radius: 999px; background: var(--color-primary); transition: width .5s ease; }
    .barfill[data-tone='low']  { background: var(--band-low); }
    .barfill[data-tone='med']  { background: var(--band-med); }
    .barfill[data-tone='high'] { background: var(--band-high); }
    .barfill[data-tone='ai']   { background: var(--gradient-ai); }
    .barval { font-size: var(--text-sm); font-weight: 700; text-align: right; }
    @media (max-width: 560px) { .barrow { grid-template-columns: 96px 1fr 48px; } }
  `],
})
export class BarListComponent {
  @Input({ required: true }) data: BarDatum[] = [];
  private max = () => Math.max(1, ...this.data.map(d => d.value));
  pct(v: number) { return Math.max(3, (v / this.max()) * 100); }
  fmt = fmtInt;
}

/** Donut / ring chart for distributions. */
@Component({
  selector: 'va-donut',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="donut">
      <svg viewBox="0 0 42 42" class="ring">
        <circle class="bg" cx="21" cy="21" r="15.915" fill="none" stroke="var(--color-surface-alt)" stroke-width="5"/>
        @for (s of segs(); track s.label) {
          <circle cx="21" cy="21" r="15.915" fill="none" [attr.stroke]="s.color" stroke-width="5"
            [attr.stroke-dasharray]="s.len + ' ' + (100 - s.len)" [attr.stroke-dashoffset]="s.off" stroke-linecap="butt"/>
        }
      </svg>
      <div class="center"><div class="t-h3 t-num">{{ total() }}</div><div class="t-cap t-muted">{{ centerLabel }}</div></div>
    </div>
    <div class="legend">
      @for (s of segs(); track s.label) {
        <div class="leg"><span class="sw" [style.background]="s.color"></span><span class="t-sm">{{ s.label }}</span><span class="t-sm t-num t-muted">{{ s.value }}</span></div>
      }
    </div>`,
  styles: [`
    :host { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
    .donut { position: relative; width: 132px; height: 132px; flex: none; }
    .ring { transform: rotate(-90deg); width: 100%; height: 100%; }
    .ring circle { transition: stroke-dasharray .6s ease; }
    .center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
    .legend { display: flex; flex-direction: column; gap: 8px; }
    .leg { display: flex; align-items: center; gap: 8px; }
    .sw { width: 11px; height: 11px; border-radius: 3px; flex: none; }
    .leg .t-num { margin-left: auto; }
  `],
})
export class DonutComponent {
  @Input({ required: true }) set data(v: BarDatum[]) { this._d.set(v ?? []); }
  @Input() centerLabel = 'total';
  private _d = signal<BarDatum[]>([]);
  private palette = ['var(--band-low)', 'var(--band-med)', 'var(--band-high)', 'var(--color-accent-2)', 'var(--color-primary)'];
  total = computed(() => fmtInt(this._d().reduce((a, b) => a + b.value, 0)));
  segs = computed(() => {
    const d = this._d(); const sum = d.reduce((a, b) => a + b.value, 0) || 1; let acc = 0;
    return d.map((x, i) => {
      const len = (x.value / sum) * 100; const off = 100 - acc + 25; acc += len;
      const color = x.tone === 'low' ? 'var(--band-low)' : x.tone === 'med' ? 'var(--band-med)' : x.tone === 'high' ? 'var(--band-high)' : this.palette[i % this.palette.length];
      return { label: x.label, value: fmtInt(x.value), len, off, color };
    });
  });
}
