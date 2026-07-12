import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { FunnelComponent } from '../../shared/ui/funnel.component';
import { ProbabilityBadgeComponent } from '../../shared/ui/badges.component';
import { PageHeaderComponent, SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { ReferenceProvider, FunnelStage } from '../../domain/models';
import { fmtInt, fmtCurrency } from '../../shared/util/format';

interface RefTile { key: string; label: string; value: string; sub: string; tone: 'default' | 'success' | 'ai'; icon: string; }

@Component({
  selector: 'va-references',
  standalone: true,
  imports: [
    IconComponent, FunnelComponent, ProbabilityBadgeComponent,
    PageHeaderComponent, SectionCardComponent, EmptyStateComponent, DrawerComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header
      title="Reference & Conversion Tracking"
      subtitle="Provider-attributed referrals across the {{ cycle() }} cycle for {{ inst() }}. Conversion is measured on AI-counseled, approved-knowledge-only journeys.">
      <span class="chip"><va-icon name="git-branch" [size]="14"></va-icon>{{ providers().length }} active providers</span>
      <button class="btn btn-ghost" (click)="export()"><va-icon name="download" [size]="16"></va-icon>Export</button>
      <button class="btn btn-primary" (click)="goCrm()"><va-icon name="users" [size]="16"></va-icon>View candidates</button>
    </va-page-header>

    <!-- KPI tiles -->
    <div class="tiles">
      @for (t of tiles(); track t.key) {
        <div class="ktile" [attr.data-tone]="t.tone">
          <div class="kt-head">
            <span class="kt-ic"><va-icon [name]="t.icon" [size]="16"></va-icon></span>
            <span class="kt-label">{{ t.label }}</span>
          </div>
          <div class="kt-value t-num">{{ t.value }}</div>
          <div class="kt-sub t-cap t-muted">{{ t.sub }}</div>
        </div>
      }
    </div>

    @if (lowQualityCount() > 0) {
      <div class="banner warning">
        <va-icon name="alert-triangle" [size]="18"></va-icon>
        <span>
          <strong>{{ lowQualityCount() }}</strong> provider{{ lowQualityCount() === 1 ? '' : 's' }} flagged for low
          reference quality (score &lt; 55). Aisha will keep counseling these leads on approved knowledge only —
          review provider intake quality before scaling spend.
        </span>
      </div>
    }

    <!-- Leaderboard -->
    <va-section-card title="Provider leaderboard" hint="Ranked by end-to-end conversion %" [flush]="true">
      <div slot="actions" class="row gap-2">
        <span class="chip"><span class="dot live"></span>Live attribution</span>
      </div>

      @if (providers().length) {
        <div class="tbl-wrap scroll-y">
          <table class="va-table">
            <thead>
              <tr>
                <th style="width:46px">#</th>
                <th>Provider</th>
                <th>Type</th>
                <th class="num">Referred</th>
                <th class="num">Contacted</th>
                <th class="num">Interested</th>
                <th class="num">Registered</th>
                <th class="num">Applied</th>
                <th class="num">Admitted</th>
                <th class="num">Conversion</th>
                <th style="width:130px">Quality</th>
                <th class="num">Revenue potential</th>
              </tr>
            </thead>
            <tbody>
              @for (p of providers(); track p.providerId; let i = $index) {
                <tr [class.selected]="p.providerId === selectedId()" (click)="select(p)">
                  <td>
                    <span class="rank" [class.top]="i === 0">
                      @if (i === 0) { <va-icon name="star" [size]="13"></va-icon> } @else { {{ i + 1 }} }
                    </span>
                  </td>
                  <td>
                    <div class="prov">
                      <span class="prov-name truncate">{{ p.name }}</span>
                      @if (i === 0) { <span class="top-chip"><va-icon name="trending-up" [size]="11"></va-icon>Top performer</span> }
                      @if (p.qualityScore < 55) { <span class="low-chip"><va-icon name="alert-triangle" [size]="11"></va-icon>Low quality</span> }
                    </div>
                  </td>
                  <td><span class="type-chip" [attr.data-t]="p.type">{{ p.type }}</span></td>
                  <td class="num t-num">{{ fmtInt(p.referred) }}</td>
                  <td class="num t-num">{{ fmtInt(p.contacted) }}</td>
                  <td class="num t-num">{{ fmtInt(p.interested) }}</td>
                  <td class="num t-num">{{ fmtInt(p.registered) }}</td>
                  <td class="num t-num">{{ fmtInt(p.applied) }}</td>
                  <td class="num t-num strong">{{ fmtInt(p.admitted) }}</td>
                  <td class="num"><span class="conv t-num" [attr.data-band]="convBand(p.conversionPct)">{{ p.conversionPct.toFixed(1) }}%</span></td>
                  <td><va-probability-badge [value]="p.qualityScore" [ai]="true"></va-probability-badge></td>
                  <td class="num t-num">{{ fmtCurrency(p.revenuePotential) }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <va-empty icon="git-branch" title="No reference providers yet"
          message="Once agents, school partners, alumni and campaigns start referring candidates, their attributed conversion will appear here."></va-empty>
      }
    </va-section-card>
  </div>

  <!-- Provider detail drawer -->
  <va-drawer [open]="!!selected()" [title]="selected()?.name || ''"
    [subtitle]="selected() ? selected()!.type + ' · ' + selected()!.referred + ' referred' : ''"
    [width]="480" (close)="close()">

    @if (selected(); as p) {
      <div class="stack gap-6">
        @if (p.qualityScore < 55) {
          <div class="banner warning">
            <va-icon name="alert-triangle" [size]="18"></va-icon>
            <span>Reference quality is below threshold ({{ p.qualityScore }}/100). Verify lead intent and consent at intake before increasing volume.</span>
          </div>
        } @else if (isTop(p)) {
          <div class="banner ai">
            <va-icon name="sparkles" [size]="18"></va-icon>
            <span>Top-converting provider this cycle at {{ p.conversionPct.toFixed(1) }}%. Strong candidate to scale.</span>
          </div>
        }

        <div class="kgrid">
          <div class="tile"><span class="tv t-num" [attr.data-band]="convBand(p.conversionPct)">{{ p.conversionPct.toFixed(1) }}%</span><span class="tl">Conversion</span></div>
          <div class="tile"><span class="tv t-num">{{ p.admitted }}</span><span class="tl">Admitted</span></div>
          <div class="tile"><span class="tv t-num">{{ fmtCurrency(p.revenuePotential) }}</span><span class="tl">Revenue potential</span></div>
        </div>

        <div class="stack gap-2">
          <div class="between">
            <span class="t-sm" style="font-weight:600">Reference quality</span>
            <va-probability-badge [value]="p.qualityScore" [ai]="true"></va-probability-badge>
          </div>
        </div>

        <div class="stack gap-3">
          <span class="t-h4">Conversion funnel</span>
          <va-funnel [stages]="funnelFor(p)" (stageClick)="stageClick($event, p)"></va-funnel>
        </div>

        <div class="stack gap-2">
          <span class="t-h4">Attribution path</span>
          <div class="drill">
            @for (step of drillPath; track step.label; let last = $last) {
              <span class="drill-node">
                <span class="drill-ic"><va-icon [name]="step.icon" [size]="14"></va-icon></span>
                <span class="t-sm">{{ step.label }}</span>
              </span>
              @if (!last) { <va-icon class="drill-arrow" name="chevron-right" [size]="14"></va-icon> }
            }
          </div>
          <p class="t-cap t-muted" style="margin:0">
            Each referral is tracked from provider attribution through Aisha's approved-knowledge counseling to a verified admission outcome.
          </p>
        </div>
      </div>
    }

    <div footer>
      <button class="btn btn-ghost btn-block" (click)="close()">Close</button>
      <button class="btn btn-primary btn-block" (click)="viewCandidates()">
        <va-icon name="users" [size]="16"></va-icon>View candidates
      </button>
    </div>
  </va-drawer>
  `,
  styles: [`
    :host { display: block; }
    .tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
    .ktile { position: relative; background: var(--color-surface); border: 1px solid var(--color-border);
      border-radius: var(--r-lg); padding: 16px; box-shadow: var(--e1); display: flex; flex-direction: column; gap: 6px; overflow: hidden; }
    .ktile::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--color-border); }
    .ktile[data-tone='success']::before { background: var(--color-success); }
    .ktile[data-tone='ai']::before { background: var(--gradient-ai); }
    .kt-head { display: flex; align-items: center; gap: 8px; }
    .kt-ic { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .ktile[data-tone='success'] .kt-ic { background: var(--color-success-soft); color: var(--color-success); }
    .ktile[data-tone='ai'] .kt-ic { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .kt-label { font-size: var(--text-sm); color: var(--color-text-muted); font-weight: 500; }
    .kt-value { font-size: 1.8rem; font-weight: 700; line-height: 1.05; letter-spacing: -.01em; }
    .kt-sub { line-height: 1.3; }

    .banner.warning va-icon { color: var(--color-warning); flex: none; margin-top: 1px; }
    .banner.ai va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }

    .tbl-wrap { max-height: 620px; }
    .va-table tbody td.strong { font-weight: 700; }

    .rank { width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); font-size: var(--text-cap); font-weight: 700; color: var(--color-text-muted); }
    .rank.top { background: var(--gradient-ai); color: #06121A; }

    .prov { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .prov-name { font-weight: 600; max-width: 180px; }
    .top-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; padding: 2px 7px;
      border-radius: var(--r-pill); background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); white-space: nowrap; }
    .low-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; padding: 2px 7px;
      border-radius: var(--r-pill); background: var(--color-warning-soft); color: var(--color-warning); white-space: nowrap; }

    .type-chip { display: inline-flex; align-items: center; font-size: var(--text-cap); font-weight: 600; padding: 3px 9px;
      border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); border: 1px solid var(--color-border); white-space: nowrap; }
    .type-chip[data-t='Agent'] { color: var(--color-primary); }
    .type-chip[data-t='Alumni'] { color: var(--color-accent-2); }
    .type-chip[data-t='Campaign'] { color: var(--ch-email); }

    .conv { font-weight: 700; }
    .conv[data-band='low'] { color: var(--color-text-muted); }
    .conv[data-band='med'] { color: var(--color-warning); }
    .conv[data-band='high'] { color: var(--color-success); }

    .kgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .tile .tv[data-band='high'] { color: var(--color-success); }
    .tile .tv[data-band='med'] { color: var(--color-warning); }
    .tile .tv[data-band='low'] { color: var(--color-text-muted); }

    .drill { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .drill-node { display: inline-flex; align-items: center; gap: 6px; padding: 7px 11px; border-radius: var(--r-md);
      background: var(--color-surface-2); border: 1px solid var(--color-border); font-weight: 600; }
    .drill-ic { color: var(--color-accent-2); display: inline-flex; }
    .drill-arrow { color: var(--color-text-muted); }

    @media (max-width: 1200px) { .tiles { grid-template-columns: repeat(3, 1fr); } }
    @media (max-width: 720px) { .tiles { grid-template-columns: repeat(2, 1fr); } .kgrid { grid-template-columns: 1fr; } }
  `],
})
export class ReferencesComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  private auth = inject(AuthService);

  fmtInt = fmtInt;
  fmtCurrency = fmtCurrency;

  providers = this.store.references;
  selectedId = signal<string | null>(null);
  selected = computed<ReferenceProvider | null>(
    () => this.providers().find(p => p.providerId === this.selectedId()) ?? null);

  cycle = () => this.auth.admissionCycle();
  inst = () => this.auth.institution().name;

  lowQualityCount = computed(() => this.providers().filter(p => p.qualityScore < 55).length);

  private totals = computed(() => {
    const list = this.providers();
    const sum = (k: keyof ReferenceProvider) => list.reduce((a, p) => a + (p[k] as number), 0);
    const referred = sum('referred'), contacted = sum('contacted');
    const registered = sum('registered'), admitted = sum('admitted');
    const avgConv = referred ? (admitted / referred) * 100 : 0;
    return { referred, contacted, registered, admitted, avgConv };
  });

  tiles = computed<RefTile[]>(() => {
    const t = this.totals();
    return [
      { key: 'referred', label: 'Total referred', value: fmtInt(t.referred), sub: 'across all providers', tone: 'default', icon: 'git-branch' },
      { key: 'contacted', label: 'Contacted', value: fmtInt(t.contacted), sub: this.pctOf(t.contacted, t.referred) + ' of referred', tone: 'default', icon: 'phone' },
      { key: 'registered', label: 'Registered', value: fmtInt(t.registered), sub: this.pctOf(t.registered, t.referred) + ' of referred', tone: 'default', icon: 'file-check' },
      { key: 'admitted', label: 'Admitted', value: fmtInt(t.admitted), sub: this.pctOf(t.admitted, t.referred) + ' of referred', tone: 'success', icon: 'graduation-cap' },
      { key: 'conv', label: 'Avg conversion', value: t.avgConv.toFixed(1) + '%', sub: 'admitted ÷ referred', tone: 'ai', icon: 'trending-up' },
    ];
  });

  drillPath = [
    { label: 'Provider', icon: 'git-branch' },
    { label: 'Campaign', icon: 'megaphone' },
    { label: 'Candidate', icon: 'user' },
    { label: 'Outcome', icon: 'graduation-cap' },
  ];

  private pctOf(n: number, d: number) { return d ? Math.round((n / d) * 100) + '%' : '0%'; }
  convBand(v: number) { return v >= 12 ? 'high' : v >= 6 ? 'med' : 'low'; }
  isTop(p: ReferenceProvider) { return this.providers()[0]?.providerId === p.providerId; }

  funnelFor(p: ReferenceProvider): FunnelStage[] {
    const rows = [
      { key: 'referred', label: 'Referred', count: p.referred },
      { key: 'contacted', label: 'Contacted', count: p.contacted },
      { key: 'interested', label: 'Interested', count: p.interested },
      { key: 'registered', label: 'Registered', count: p.registered },
      { key: 'applied', label: 'Applied', count: p.applied },
      { key: 'admitted', label: 'Admitted', count: p.admitted },
    ];
    return rows.map((r, i) => {
      const prev = i > 0 ? rows[i - 1].count : r.count;
      const dropOffPct = i > 0 && prev > 0 ? ((prev - r.count) / prev) * 100 : 0;
      return { key: r.key, label: r.label, count: r.count, dropOffPct: Math.round(dropOffPct * 10) / 10, trendPct: 0 };
    });
  }

  select(p: ReferenceProvider) { this.selectedId.set(p.providerId); }
  close() { this.selectedId.set(null); }

  stageClick(s: FunnelStage, p: ReferenceProvider) {
    this.toast.info(`${p.name}: ${fmtInt(s.count)} candidates at "${s.label}"`);
  }

  viewCandidates() {
    const p = this.selected();
    if (p) this.toast.success(`Opening candidates referred by ${p.name}`);
    this.router.navigateByUrl('/app/crm');
  }
  goCrm() { this.router.navigateByUrl('/app/crm'); }
  export() { this.toast.success('Reference report export queued — you’ll be notified when ready.'); }
}
