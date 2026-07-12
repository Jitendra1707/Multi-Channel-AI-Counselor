import { ChangeDetectionStrategy, Component, Input, computed, signal } from '@angular/core';
import { Band, CandidateStage, Sentiment } from '../../domain/models';
import { IconComponent } from './icon.component';
import { band, SENTI_LABEL } from '../util/format';

/** Lifecycle status pill (§13.2) — color grouped by phase. */
@Component({
  selector: 'va-status-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="status" [attr.data-group]="group()">{{ status }}</span>`,
  styles: [`
    .status { display: inline-flex; align-items: center; font-size: var(--text-cap); font-weight: 600;
      padding: 4px 9px; border-radius: var(--r-pill); white-space: nowrap; border: 1px solid transparent; }
    .status[data-group='new']    { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .status[data-group='active'] { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); border-color: rgba(var(--color-primary-rgb), .18); }
    .status[data-group='engaged']{ background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .status[data-group='convert']{ background: var(--color-success-soft); color: var(--color-success); }
    .status[data-group='lost']   { background: var(--color-danger-soft); color: var(--color-danger); }
  `],
})
export class StatusBadgeComponent {
  @Input({ required: true }) set status(v: CandidateStage | string) { this._s.set(v); }
  get status() { return this._s(); }
  private _s = signal<string>('');
  group = computed(() => {
    const s = this._s();
    if (/Lost|Disqualified|Not Interested|Deferred|Closed/.test(s)) return 'lost';
    if (/Admitted|Fee Paid|Submitted|Offered|Registered|Converted/.test(s)) return 'convert';
    if (/Interested|Counseling|V-Con|Parent|Registration|Application|Schedul|Escalated|Delegated/.test(s)) return 'engaged';
    if (/Contacted|Validated|Needs|Welcomed|Call|Follow/.test(s)) return 'active';
    return 'new';
  });
}

@Component({
  selector: 'va-sentiment-badge',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="senti" [attr.data-s]="value" [title]="label()">
      <va-icon [name]="icon()" [size]="14"></va-icon>
      @if (showLabel) { <span>{{ label() }}</span> }
    </span>`,
  styles: [`
    .senti { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; }
    .senti[data-s='very-neg'] { color: var(--senti-very-neg); }
    .senti[data-s='neg']      { color: var(--senti-neg); }
    .senti[data-s='neutral']  { color: var(--senti-neutral); }
    .senti[data-s='pos']      { color: var(--senti-pos); }
    .senti[data-s='very-pos'] { color: var(--senti-very-pos); }
  `],
})
export class SentimentBadgeComponent {
  @Input({ required: true }) value!: Sentiment;
  @Input() showLabel = false;
  label = () => SENTI_LABEL[this.value];
  icon = () => (this.value === 'pos' || this.value === 'very-pos') ? 'smile' : this.value === 'neutral' ? 'meh' : 'frown';
}

/** Probability / confidence band bar (§34.1). */
@Component({
  selector: 'va-probability-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="prob" [attr.data-band]="b()" [class.ai]="ai">
      <span class="track"><span class="fill" [style.width.%]="value"></span></span>
      <span class="num t-num">{{ value }}%</span>
    </span>`,
  styles: [`
    .prob { display: inline-flex; align-items: center; gap: 8px; min-width: 110px; }
    .track { flex: 1; height: 6px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; min-width: 54px; }
    .fill { display: block; height: 100%; border-radius: 999px; transition: width .4s ease; }
    .num { font-size: var(--text-cap); font-weight: 700; min-width: 34px; text-align: right; }
    .prob[data-band='low']  .fill { background: var(--band-low); }
    .prob[data-band='med']  .fill { background: var(--band-med); }
    .prob[data-band='high'] .fill { background: var(--band-high); }
    .prob[data-band='low']  .num { color: var(--band-low); }
    .prob[data-band='med']  .num { color: var(--band-med); }
    .prob[data-band='high'] .num { color: var(--band-high); }
    .prob.ai[data-band='high'] .fill { background: var(--gradient-ai); }
    .prob.ai[data-band='high'] .num { color: var(--color-accent); }
  `],
})
export class ProbabilityBadgeComponent {
  @Input({ required: true }) value = 0;
  @Input() ai = false;
  b = () => band(this.value);
}

/** Risk / generic band chip. */
@Component({
  selector: 'va-band-chip',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="band" [attr.data-band]="band">{{ labelText() }}</span>`,
  styles: [`
    .band { display: inline-flex; align-items: center; font-size: var(--text-cap); font-weight: 600; padding: 3px 8px; border-radius: var(--r-pill); }
    .band[data-band='low']  { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .band[data-band='med']  { background: var(--color-warning-soft); color: var(--color-warning); }
    .band[data-band='high'] { background: var(--color-danger-soft); color: var(--color-danger); }
  `],
})
export class BandChipComponent {
  @Input({ required: true }) band: Band = 'low';
  @Input() label?: string;
  labelText() { return this.label ?? this.band[0].toUpperCase() + this.band.slice(1); }
}

/** Approval-state chip used across config + KMS. */
@Component({
  selector: 'va-approval-chip',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="ac" [attr.data-s]="state"><va-icon [name]="icon()" [size]="12"></va-icon>{{ labelText() }}</span>`,
  styles: [`
    .ac { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: var(--r-pill); text-transform: capitalize; }
    .ac[data-s='approved'] { background: var(--color-success-soft); color: var(--color-success); }
    .ac[data-s='pending']  { background: var(--color-warning-soft); color: var(--color-warning); }
    .ac[data-s='draft']    { background: var(--color-surface-alt); color: var(--color-text-muted); }
  `],
})
export class ApprovalChipComponent {
  @Input({ required: true }) state: 'approved' | 'pending' | 'draft' = 'draft';
  icon() { return this.state === 'approved' ? 'check' : this.state === 'pending' ? 'clock' : 'edit'; }
  labelText() { return this.state === 'pending' ? 'Pending approval' : this.state; }
}
