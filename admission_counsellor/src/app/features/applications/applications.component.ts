import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { FunnelComponent } from '../../shared/ui/funnel.component';
import { MetricCardComponent } from '../../shared/ui/metric-card.component';
import { PageHeaderComponent, SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { StatusBadgeComponent } from '../../shared/ui/badges.component';
import { AvatarComponent } from '../../shared/ui/avatar.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Application, FunnelStage, Metric } from '../../domain/models';

type FeeFilter = 'all' | 'Not started' | 'Pending' | 'Paid';

@Component({
  selector: 'va-applications',
  standalone: true,
  imports: [
    RouterLink, IconComponent, FunnelComponent, MetricCardComponent,
    PageHeaderComponent, SectionCardComponent, EmptyStateComponent,
    StatusBadgeComponent, AvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header
      title="Applications & Registrations"
      subtitle="Every stage to admission — {{ auth.institution().name }} · {{ auth.admissionCycle() }}">
      <span class="chip ai-chip"><va-icon name="shield-check" [size]="12"></va-icon> Approved-knowledge follow-ups</span>
      <button class="btn btn-ghost" (click)="exportPipeline()">
        <va-icon name="download" [size]="16"></va-icon><span class="hide-xs">Export</span>
      </button>
      <button class="btn btn-primary" (click)="go('/app/crm')">
        <va-icon name="users" [size]="16"></va-icon> Open CRM
      </button>
    </va-page-header>

    <!-- AI guardrail banner -->
    <div class="banner ai gd-banner">
      <va-icon name="sparkles" [size]="18"></va-icon>
      <span>Aisha nudges incomplete applicants from <b>approved knowledge only</b> — fee figures, deadlines and document
        checklists come from signed-off documents, never invented. Unsure cases escalate to a human counselor.</span>
      <button class="btn btn-sm btn-ghost" (click)="go('/app/kms')">Knowledge base <va-icon name="arrow-right" [size]="14"></va-icon></button>
    </div>

    <!-- KPI row -->
    <section class="kpis">
      @for (m of metrics(); track m.key) {
        <va-metric-card [metric]="m" (drill)="drill($event)"></va-metric-card>
      }
    </section>

    <!-- Funnel + drop-off rail -->
    <div class="cols">
      <va-section-card title="Registration → admission funnel" hint="Click any stage to view those candidates in CRM">
        <button actions class="btn btn-sm btn-ghost" (click)="go('/app/analytics')">Full analytics <va-icon name="arrow-up-right" [size]="14"></va-icon></button>
        <va-funnel [stages]="funnel" (stageClick)="drillStage($event)"></va-funnel>
      </va-section-card>

      <va-section-card title="Where applicants stall" hint="Biggest drop-off between stages" [flush]="true">
        <div class="drops">
          @for (d of dropOffs(); track d.from) {
            <div class="drop">
              <div class="drop-top">
                <span class="drop-stages truncate">{{ d.from }} <va-icon name="arrow-right" [size]="12"></va-icon> {{ d.to }}</span>
                <span class="drop-pct t-num">-{{ d.pct }}%</span>
              </div>
              <div class="drop-track"><span class="drop-fill" [style.width.%]="d.pct"></span></div>
              <span class="t-cap t-muted">{{ d.lost }} candidates lost at this step</span>
            </div>
          }
        </div>
      </va-section-card>
    </div>

    <!-- Two action lists -->
    <div class="cols">
      <va-section-card title="High-intent incomplete applications" hint="Conversion probability over 70% — fee not yet paid" [flush]="true">
        <span actions class="chip warn-chip"><va-icon name="flame" [size]="12"></va-icon> {{ highIntent().length }} at risk</span>
        @if (highIntent().length) {
          <div class="act-list">
            @for (a of highIntent(); track a.applicationId) {
              <div class="act-row">
                <a class="act-main" [routerLink]="['/app/crm/candidate', a.candidateId]">
                  <va-avatar [name]="a.candidateName" [hue]="hueFor(a.candidateId)" [size]="36"></va-avatar>
                  <div class="act-id">
                    <span class="act-name truncate">{{ a.candidateName }}</span>
                    <span class="t-cap t-muted truncate">{{ a.course }}</span>
                  </div>
                </a>
                <div class="act-meta">
                  <va-status-badge [status]="a.stage"></va-status-badge>
                  <span class="fee" [attr.data-fee]="a.feeStatus"><va-icon name="dollar-sign" [size]="12"></va-icon>{{ a.feeStatus }}</span>
                </div>
                <div class="act-next" [title]="a.nextAction">
                  <va-icon name="sparkles" [size]="13"></va-icon>
                  <span class="truncate">{{ a.nextAction }}</span>
                </div>
                <button class="btn btn-sm btn-accent" (click)="followUp(a)">
                  <va-icon name="send" [size]="14"></va-icon> Follow up
                </button>
              </div>
            }
          </div>
        } @else {
          <va-empty icon="check-circle" title="No high-intent stragglers"
            message="Every high-intent applicant has paid their fee or moved forward. Aisha will flag new ones as they appear."></va-empty>
        }
      </va-section-card>

      <va-section-card title="Pending documents & payment" hint="Applicants blocked on a fee or a missing document" [flush]="true">
        <span actions class="chip warn-chip"><va-icon name="clock" [size]="12"></va-icon> {{ pending().length }} pending</span>
        @if (pending().length) {
          <div class="act-list">
            @for (a of pending(); track a.applicationId) {
              <div class="act-row">
                <a class="act-main" [routerLink]="['/app/crm/candidate', a.candidateId]">
                  <va-avatar [name]="a.candidateName" [hue]="hueFor(a.candidateId)" [size]="36"></va-avatar>
                  <div class="act-id">
                    <span class="act-name truncate">{{ a.candidateName }}</span>
                    <span class="t-cap t-muted truncate">{{ a.course }}</span>
                  </div>
                </a>
                <div class="act-meta blockers">
                  @if (a.feeStatus !== 'Paid') {
                    <span class="chip block fee-block"><va-icon name="dollar-sign" [size]="12"></va-icon>{{ a.feeStatus === 'Pending' ? 'Fee pending' : 'Fee not started' }}</span>
                  }
                  @for (doc of a.missingDocs; track doc) {
                    <span class="chip block doc-block"><va-icon name="file-text" [size]="12"></va-icon>{{ doc }}</span>
                  }
                </div>
                <button class="btn btn-sm btn-ghost" (click)="nudge(a)">
                  <va-icon name="message-circle" [size]="14"></va-icon> Remind
                </button>
              </div>
            }
          </div>
        } @else {
          <va-empty icon="file-check" title="Nothing pending"
            message="No applications are blocked on a fee or a missing document right now."></va-empty>
        }
      </va-section-card>
    </div>

    <!-- Full applications table -->
    <va-section-card title="All applications" [hint]="filtered().length + ' of ' + applications().length + ' applications'" [flush]="true">
      <div actions class="seg">
        @for (f of feeFilters; track f.k) {
          <button [class.active]="feeFilter() === f.k" (click)="feeFilter.set(f.k)">{{ f.l }}</button>
        }
      </div>

      @if (filtered().length) {
        <div class="scroll-x">
          <table class="va-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Course</th>
                <th>Stage</th>
                <th>Fee status</th>
                <th>Missing documents</th>
                <th>Next action</th>
                <th class="num">Open</th>
              </tr>
            </thead>
            <tbody>
              @for (a of filtered(); track a.applicationId) {
                <tr [routerLink]="['/app/crm/candidate', a.candidateId]" [title]="'Open ' + a.candidateName">
                  <td>
                    <div class="cell-cand">
                      <va-avatar [name]="a.candidateName" [hue]="hueFor(a.candidateId)" [size]="32"></va-avatar>
                      <div class="cand-id">
                        <span class="truncate">{{ a.candidateName }}</span>
                        @if (a.highIntent) { <span class="chip hi-chip"><va-icon name="flame" [size]="11"></va-icon> High intent</span> }
                      </div>
                    </div>
                  </td>
                  <td class="t-muted">{{ a.course }}</td>
                  <td><va-status-badge [status]="a.stage"></va-status-badge></td>
                  <td><span class="fee" [attr.data-fee]="a.feeStatus"><va-icon name="dollar-sign" [size]="12"></va-icon>{{ a.feeStatus }}</span></td>
                  <td>
                    @if (a.missingDocs.length) {
                      <div class="doc-chips">
                        @for (doc of a.missingDocs; track doc) {
                          <span class="chip doc-block"><va-icon name="file-text" [size]="11"></va-icon>{{ doc }}</span>
                        }
                      </div>
                    } @else {
                      <span class="chip ok-chip"><va-icon name="check" [size]="11"></va-icon> Complete</span>
                    }
                  </td>
                  <td class="t-muted next-cell"><span class="truncate">{{ a.nextAction }}</span></td>
                  <td class="num"><va-icon name="chevron-right" [size]="16"></va-icon></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <va-empty icon="inbox" title="No applications match"
          message="No applications match the selected fee filter. Try a different filter."
          cta="Show all" ctaIcon="refresh" (action)="feeFilter.set('all')"></va-empty>
      }
    </va-section-card>
  </div>
  `,
  styles: [`
    :host { display: block; }

    .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: var(--s-4); }

    .ai-chip { background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); border-color: rgba(var(--color-accent-2-rgb), .22); }
    .warn-chip { background: var(--color-warning-soft); color: var(--color-warning); border-color: color-mix(in srgb, var(--color-warning) 30%, transparent); }
    .hi-chip { background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); border-color: rgba(var(--color-accent-2-rgb), .20); padding: 2px 7px; }
    .ok-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }

    .gd-banner { align-items: center; gap: 12px; }
    .gd-banner > span { flex: 1; }
    .gd-banner .btn { flex: none; }

    .cols { display: grid; grid-template-columns: 1.55fr 1fr; gap: var(--s-6); align-items: start; }
    @media (max-width: 1080px) { .cols { grid-template-columns: 1fr; } }

    /* Drop-off rail */
    .drops { display: flex; flex-direction: column; }
    .drop { display: flex; flex-direction: column; gap: 6px; padding: 14px 18px; border-bottom: 1px solid var(--color-border); }
    .drop:last-child { border-bottom: none; }
    .drop-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .drop-stages { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-sm); font-weight: 600; min-width: 0; }
    .drop-pct { font-weight: 700; color: var(--color-danger); font-size: var(--text-sm); flex: none; }
    .drop-track { height: 6px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; }
    .drop-fill { display: block; height: 100%; border-radius: 999px; background: var(--color-danger); opacity: .75; }

    /* Action lists */
    .act-list { display: flex; flex-direction: column; }
    .act-row { display: grid; grid-template-columns: minmax(150px, 1.3fr) auto minmax(140px, 1fr) auto;
      align-items: center; gap: 14px; padding: 12px 18px; border-bottom: 1px solid var(--color-border); transition: background .12s ease; }
    .act-row:last-child { border-bottom: none; }
    .act-row:hover { background: var(--color-surface-alt); }
    .act-main { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .act-id { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .act-name { font-weight: 600; font-size: var(--text-sm); }
    .act-meta { display: flex; align-items: center; gap: 8px; }
    .act-meta.blockers { flex-wrap: wrap; }
    .act-next { display: inline-flex; align-items: center; gap: 6px; min-width: 0; color: var(--color-accent-2); font-size: var(--text-cap); font-weight: 600; }
    .act-next va-icon { flex: none; }

    .block { background: var(--color-surface-alt); }
    .fee-block { color: var(--color-warning); background: var(--color-warning-soft); border-color: color-mix(in srgb, var(--color-warning) 26%, transparent); }
    .doc-block { color: var(--color-text-muted); }

    /* Fee status inline pill */
    .fee { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600;
      padding: 3px 9px; border-radius: var(--r-pill); white-space: nowrap; }
    .fee[data-fee='Paid'] { background: var(--color-success-soft); color: var(--color-success); }
    .fee[data-fee='Pending'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .fee[data-fee='Not started'] { background: var(--color-surface-alt); color: var(--color-text-muted); }

    /* Table */
    .scroll-x { overflow-x: auto; }
    .cell-cand { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .cand-id { display: flex; align-items: center; gap: 8px; font-weight: 600; min-width: 0; }
    .doc-chips { display: flex; flex-wrap: wrap; gap: 5px; max-width: 240px; }
    .next-cell { max-width: 220px; }
    .next-cell .truncate { display: block; }
    .va-table tbody td .chevron, .va-table .num va-icon { color: var(--color-text-muted); }

    .hide-xs { }
    @media (max-width: 640px) { .hide-xs { display: none; } }
  `],
})
export class ApplicationsComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);

  applications = this.store.applications;

  feeFilter = signal<FeeFilter>('all');
  feeFilters: { k: FeeFilter; l: string }[] = [
    { k: 'all', l: 'All' },
    { k: 'Not started', l: 'Not started' },
    { k: 'Pending', l: 'Fee pending' },
    { k: 'Paid', l: 'Fee paid' },
  ];

  /** Application-stage funnel (§32.28), realistic counts for the Fall 2026 cycle. */
  funnel: FunnelStage[] = [
    { key: 'link-sent',  label: 'Registration link sent', count: 1180, dropOffPct: 0,    trendPct: 12 },
    { key: 'registered', label: 'Registered',             count: 612,  dropOffPct: 48.1, trendPct: 10 },
    { key: 'started',    label: 'Application started',    count: 503,  dropOffPct: 17.8, trendPct: 9 },
    { key: 'docs-pend',  label: 'Documents pending',      count: 418,  dropOffPct: 16.9, trendPct: 4 },
    { key: 'fee-pend',   label: 'Fee pending',            count: 356,  dropOffPct: 14.8, trendPct: 7 },
    { key: 'fee-paid',   label: 'Fee paid',               count: 271,  dropOffPct: 23.9, trendPct: 11 },
    { key: 'submitted',  label: 'Submitted',              count: 244,  dropOffPct: 10.0, trendPct: 6 },
    { key: 'review',     label: 'Under review',           count: 221,  dropOffPct: 9.4,  trendPct: 5 },
    { key: 'offered',    label: 'Offered',                count: 187,  dropOffPct: 15.4, trendPct: 14 },
    { key: 'admitted',   label: 'Admitted',               count: 154,  dropOffPct: 17.6, trendPct: 19 },
  ];

  /** Largest stage-to-stage losses, derived from the funnel. */
  dropOffs = computed(() =>
    this.funnel.slice(1)
      .map((s, i) => {
        const prev = this.funnel[i];
        return { from: prev.label, to: s.label, pct: +s.dropOffPct.toFixed(1), lost: prev.count - s.count };
      })
      .filter(d => d.lost > 0)
      .sort((a, b) => b.lost - a.lost)
      .slice(0, 5));

  private spark = (base: number) => Array.from({ length: 12 }, (_, i) => Math.round(base * (0.62 + 0.42 * Math.sin(i / 1.8) + (i % 3) * 0.06)));

  /** KPI cards for the stage pipeline (§25). */
  metrics = computed<Metric[]>(() => {
    const apps = this.applications();
    const dropOffs = 1180 - 612; // link-sent → registered shortfall
    return [
      { key: 'registered',   label: 'Registered',          value: 612, deltaPct: 9.7,  trend: this.spark(520), format: 'int', tone: 'success', drillTo: '/app/applications' },
      { key: 'apps-started', label: 'Applications started', value: 503, deltaPct: 8.4,  trend: this.spark(440), format: 'int', tone: 'default' },
      { key: 'fee-paid',     label: 'Fee paid',             value: 271, deltaPct: 11.0, trend: this.spark(230), format: 'int', tone: 'success' },
      { key: 'submitted',    label: 'Submitted',            value: 244, deltaPct: 6.3,  trend: this.spark(210), format: 'int', tone: 'default' },
      { key: 'offers',       label: 'Offers',               value: 187, deltaPct: 13.1, trend: this.spark(150), format: 'int', tone: 'ai' },
      { key: 'admitted',     label: 'Admitted',             value: 154, deltaPct: 18.9, trend: this.spark(120), format: 'int', tone: 'success' },
      { key: 'drop-offs',    label: 'Registration drop-offs', value: dropOffs, deltaPct: -7.2, trend: this.spark(600), format: 'int', tone: 'warning' },
    ];
  });

  /** High-intent applicants whose fee is not yet paid (§25). */
  highIntent = computed(() =>
    this.applications().filter(a => a.highIntent && a.feeStatus !== 'Paid'));

  /** Anyone blocked on a fee or a missing document. */
  pending = computed(() =>
    this.applications().filter(a => a.feeStatus !== 'Paid' || a.missingDocs.length > 0));

  filtered = computed(() => {
    const f = this.feeFilter();
    const apps = this.applications();
    return f === 'all' ? apps : apps.filter(a => a.feeStatus === f);
  });

  hueFor(id: string) { return this.store.candidateById(id)?.avatarHue ?? 222; }

  drill(m: Metric) { if (m.drillTo) this.router.navigateByUrl(m.drillTo); }

  drillStage(s: FunnelStage) {
    this.toast.info(`Opening ${s.label} candidates (${s.count.toLocaleString('en-IN')})`);
    this.router.navigateByUrl('/app/crm');
  }

  followUp(a: Application) {
    this.toast.success(`Aisha queued an approved-knowledge follow-up for ${a.candidateName}: “${a.nextAction}”.`);
  }

  nudge(a: Application) {
    const blockers = [a.feeStatus !== 'Paid' ? 'fee' : null, ...a.missingDocs].filter(Boolean).join(', ');
    this.toast.success(`Reminder sent to ${a.candidateName} about: ${blockers || a.nextAction}.`);
  }

  exportPipeline() { this.toast.success('Applications pipeline export queued — you’ll be notified when ready.'); }

  go(url: string) { this.router.navigateByUrl(url); }
}
