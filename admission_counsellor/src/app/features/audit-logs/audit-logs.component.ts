import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { FilterBarComponent } from '../../shared/ui/filter-bar.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { fmtDate, fmtInt, relTime } from '../../shared/util/format';

/** Semantic grouping for action colouring + iconography. */
type ActionKind =
  | 'approve' | 'reject' | 'guardrail' | 'ai-message' | 'import'
  | 'escalation' | 'status' | 'config' | 'access' | 'delete' | 'export';

interface AuditEntry {
  id: string;
  ts: string;                 // ISO timestamp
  actorName: string;
  actorRole: string;
  isAi: boolean;
  actorHue: number;
  action: string;             // human-readable action
  kind: ActionKind;
  entityType: string;         // e.g. "KMS Document", "Guardrail"
  entityId: string;           // e.g. "doc-014"
  entityRoute?: string;       // deep link to the underlying record
  before: string;             // short diff text or '—'
  after: string;              // short diff text or '—'
  beforeJson?: string;        // full before payload (pretty JSON-ish)
  afterJson?: string;         // full after payload
  ip: string;
  correlationId: string;
  channel?: string;
  note?: string;
}

@Component({
  selector: 'va-audit-logs',
  standalone: true,
  imports: [
    RouterLink, IconComponent, PageHeaderComponent, EmptyStateComponent,
    FilterBarComponent, DrawerComponent, AvatarComponent, AiAvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page">
    <va-page-header
      title="Audit Logs & Compliance"
      [subtitle]="'An immutable, queryable record of every consequential action by ' + counselor.activeMeta().name + ' (' + counselor.activeMeta().title + ') and your team — who did what, to which record, with full before → after.'">
      <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
      <button class="btn btn-ghost" (click)="exportLog()">
        <va-icon name="download" [size]="16"></va-icon>Export
      </button>
      <button class="btn btn-primary" (click)="complianceReport()">
        <va-icon name="shield-check" [size]="16"></va-icon>Compliance report
      </button>
    </va-page-header>

    <!-- Trust / governance banner -->
    <div class="banner ai mb">
      <va-icon name="lock" [size]="18"></va-icon>
      <div>
        <strong>Tamper-evident trail.</strong>
        Every {{ career() ? 'pathway published, salary-band approval and AI pathway recommendation' : 'KMS approval, guardrail change and AI-sent message' }} is queryable with
        <em>before → after</em>, actor and timestamp. Records are append-only and retained for the full
        admission cycle — {{ auth.institution().name }} · {{ auth.admissionCycle() }}.
      </div>
    </div>

    <!-- Health tiles -->
    <div class="tiles mb">
      <div class="tile">
        <span class="tl"><va-icon name="scroll-text" [size]="13"></va-icon> Events (30 days)</span>
        <span class="tv t-num">{{ fmtInt(total()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="shield-check" [size]="13"></va-icon> Approvals logged</span>
        <span class="tv t-num good">{{ fmtInt(countKind('approve') + countKind('reject')) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="sparkles" [size]="13"></va-icon> AI actions</span>
        <span class="tv t-num ai">{{ fmtInt(aiCount()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="shield" [size]="13"></va-icon> Guardrail changes</span>
        <span class="tv t-num warn">{{ fmtInt(countKind('guardrail')) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="users" [size]="13"></va-icon> Distinct actors</span>
        <span class="tv t-num">{{ fmtInt(actorOptions().length) }}</span>
      </div>
    </div>

    <va-filter-bar
      [query]="query()"
      placeholder="Search action, entity, actor or correlation ID…"
      (queryChange)="query.set($event)">

      <ng-container filters>
        <select class="select sm" [value]="entityFilter()" (change)="entityFilter.set($any($event.target).value)">
          <option value="">All entity types</option>
          @for (e of entityOptions(); track e) { <option [value]="e">{{ e }}</option> }
        </select>
        <select class="select sm" [value]="actorFilter()" (change)="actorFilter.set($any($event.target).value)">
          <option value="">All actors</option>
          @for (a of actorOptions(); track a) { <option [value]="a">{{ a }}</option> }
        </select>
        <select class="select sm" [value]="kindFilter()" (change)="kindFilter.set($any($event.target).value)">
          <option value="">All actions</option>
          @for (k of kindOptions; track k.value) { <option [value]="k.value">{{ k.label }}</option> }
        </select>
        <select class="select sm" [value]="rangeFilter()" (change)="rangeFilter.set($any($event.target).value)">
          @for (r of rangeOptions; track r.value) { <option [value]="r.value">{{ r.label }}</option> }
        </select>
      </ng-container>

      <ng-container actions>
        <span class="seg" role="group" aria-label="Actor filter">
          <button [class.active]="aiOnly() === 'all'" (click)="aiOnly.set('all')">All</button>
          <button [class.active]="aiOnly() === 'human'" (click)="aiOnly.set('human')">Human</button>
          <button [class.active]="aiOnly() === 'ai'" (click)="aiOnly.set('ai')">{{ counselor.activeMeta().name }} (AI)</button>
        </span>
      </ng-container>
    </va-filter-bar>

    <div class="resultline t-sm t-muted">
      Showing <strong class="t-num">{{ fmtInt(rows().length) }}</strong> of {{ fmtInt(total()) }} events
      @if (activeFiltersOn()) {
        · <button class="linkbtn" (click)="clearFilters()">Clear filters</button>
      }
    </div>

    @if (rows().length === 0) {
      <div class="card">
        <va-empty
          icon="search"
          title="No events match your filters"
          message="Adjust the search term, entity type or date range — or clear the active filters to see the full trail."
          cta="Clear filters"
          ctaIcon="x"
          (action)="clearFilters()"></va-empty>
      </div>
    } @else {
      <div class="card flush tablewrap">
        <table class="va-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Before → after</th>
              <th>IP address</th>
              <th>Correlation</th>
              <th class="endcol"></th>
            </tr>
          </thead>
          <tbody>
            @for (e of rows(); track e.id) {
              <tr [class.selected]="selected()?.id === e.id" (click)="open(e)">
                <td class="nowrap">
                  <div class="when">
                    <span class="t-sm">{{ relTime(e.ts) }}</span>
                    <span class="t-cap t-muted" [title]="exactTs(e.ts)">{{ exactTs(e.ts) }}</span>
                  </div>
                </td>
                <td>
                  <div class="actor">
                    @if (e.isAi) {
                      <va-ai-avatar [size]="30"></va-ai-avatar>
                    } @else {
                      <va-avatar [name]="e.actorName" [hue]="e.actorHue" [size]="30"></va-avatar>
                    }
                    <div class="atext">
                      <span class="aname truncate">{{ e.actorName }}</span>
                      <span class="t-cap t-muted truncate">{{ e.actorRole }}</span>
                    </div>
                  </div>
                </td>
                <td>
                  <span class="act" [attr.data-kind]="e.kind">
                    <va-icon [name]="kindIcon(e.kind)" [size]="14"></va-icon>
                    <span class="truncate">{{ e.action }}</span>
                  </span>
                </td>
                <td>
                  <div class="entity">
                    <span class="t-sm">{{ e.entityType }}</span>
                    <span class="chip mono">{{ e.entityId }}</span>
                  </div>
                </td>
                <td>
                  <div class="diff">
                    @if (e.before === '—' && e.after === '—') {
                      <span class="t-muted">—</span>
                    } @else {
                      <span class="before truncate" [title]="e.before">{{ e.before }}</span>
                      <va-icon name="arrow-right" [size]="13"></va-icon>
                      <span class="after truncate" [title]="e.after">{{ e.after }}</span>
                    }
                  </div>
                </td>
                <td class="t-sm t-muted nowrap mono-sm">{{ e.ip }}</td>
                <td (click)="$event.stopPropagation()">
                  <button class="chip mono corr" (click)="copyCorrelation(e)" [title]="'Copy ' + e.correlationId">
                    <va-icon name="git-branch" [size]="11"></va-icon>{{ e.correlationId }}
                  </button>
                </td>
                <td class="endcol">
                  <va-icon name="chevron-right" [size]="16"></va-icon>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  </div>

  <!-- ============ DETAIL DRAWER ============ -->
  <va-drawer
    [open]="!!selected()"
    [title]="selected()?.action || 'Audit event'"
    [subtitle]="selected() ? (selected()!.entityType + ' · ' + selected()!.entityId) : ''"
    [width]="540"
    (close)="selected.set(null)">

    @if (selected(); as e) {
      <div class="detail">
        <!-- Action header -->
        <div class="d-head">
          <span class="act lg" [attr.data-kind]="e.kind">
            <va-icon [name]="kindIcon(e.kind)" [size]="16"></va-icon>{{ e.action }}
          </span>
          @if (e.channel) {
            <span class="chip">{{ e.channel }}</span>
          }
        </div>

        <!-- Actor + meta -->
        <div class="d-actor">
          @if (e.isAi) {
            <va-ai-avatar [size]="40" [glow]="true"></va-ai-avatar>
          } @else {
            <va-avatar [name]="e.actorName" [hue]="e.actorHue" [size]="40"></va-avatar>
          }
          <div class="stack">
            <span class="t-h4 row gap-1">
              {{ e.actorName }}
              @if (e.isAi) { <span class="ai-tag"><va-icon name="sparkles" [size]="11"></va-icon>AI</span> }
            </span>
            <span class="t-cap t-muted">{{ e.actorRole }}</span>
          </div>
        </div>

        <dl class="dl meta">
          <dt>Timestamp</dt>
          <dd class="t-num">{{ exactTs(e.ts) }}</dd>
          <dt>Relative</dt>
          <dd>{{ relTime(e.ts) }}</dd>
          <dt>Entity</dt>
          <dd>
            @if (e.entityRoute) {
              <a class="entlink" [routerLink]="e.entityRoute" (click)="selected.set(null)">
                {{ e.entityType }} · {{ e.entityId }} <va-icon name="external-link" [size]="12"></va-icon>
              </a>
            } @else {
              {{ e.entityType }} · {{ e.entityId }}
            }
          </dd>
          <dt>IP address</dt>
          <dd class="mono-sm">{{ e.ip }}</dd>
          <dt>Correlation ID</dt>
          <dd><span class="chip mono">{{ e.correlationId }}</span></dd>
        </dl>

        @if (e.note) {
          <div class="banner info note">
            <va-icon name="info" [size]="16"></va-icon>
            <span>{{ e.note }}</span>
          </div>
        }

        <!-- Before / after JSON -->
        @if (e.before === '—' && e.after === '—') {
          <div class="json-empty t-sm t-muted">
            <va-icon name="minus" [size]="15"></va-icon>
            No field-level change recorded — this action created or read a record rather than mutating one.
          </div>
        } @else {
          <div class="diff-grid">
            <div class="diff-pane before-pane">
              <div class="diff-label"><va-icon name="minus" [size]="13"></va-icon>Before</div>
              <pre class="json">{{ e.beforeJson || e.before }}</pre>
            </div>
            <div class="diff-pane after-pane">
              <div class="diff-label"><va-icon name="plus" [size]="13"></va-icon>After</div>
              <pre class="json">{{ e.afterJson || e.after }}</pre>
            </div>
          </div>
        }
      </div>
    }

    <div footer>
      <button class="btn btn-ghost grow" (click)="copyEvent()">
        <va-icon name="paperclip" [size]="15"></va-icon>Copy event JSON
      </button>
      @if (selected()?.entityRoute) {
        <button class="btn btn-primary grow" (click)="openEntity()">
          <va-icon name="external-link" [size]="15"></va-icon>Open record
        </button>
      }
    </div>
  </va-drawer>
  `,
  styles: [`
    :host { display: block; }
    .mb { margin-bottom: var(--s-4); }

    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    .banner { display: flex; align-items: flex-start; gap: 10px; }
    .banner em { font-style: normal; font-weight: 600; }
    .banner.ai va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }

    .tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--s-3); }
    @media (max-width: 1100px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
    .tile .tl { display: inline-flex; align-items: center; gap: 5px; }
    .tile .tv.good { color: var(--color-success); }
    .tile .tv.warn { color: var(--color-warning); }
    .tile .tv.ai   { color: var(--color-accent-2); }

    .select.sm { width: auto; padding: 7px 30px 7px 11px; min-width: 130px; cursor: pointer;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23889' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat; background-position: right 9px center; appearance: none; -webkit-appearance: none; }

    .resultline { margin: var(--s-3) 0 var(--s-2); }
    .linkbtn { border: none; background: none; padding: 0; font: inherit; font-weight: 600; color: var(--color-accent); cursor: pointer; }
    .linkbtn:hover { text-decoration: underline; }

    .tablewrap { overflow-x: auto; }
    .va-table { table-layout: auto; }
    .nowrap { white-space: nowrap; }
    .mono, .mono-sm { font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1;
      font-family: ui-monospace, 'SF Mono', 'Cascadia Mono', Menlo, monospace; }
    .mono-sm { font-size: var(--text-cap); }

    /* when cell */
    .when { display: flex; flex-direction: column; gap: 1px; }
    .when .t-cap { font-variant-numeric: tabular-nums; }

    /* actor cell */
    .actor { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .atext { display: flex; flex-direction: column; gap: 1px; min-width: 0; max-width: 150px; }
    .aname { font-weight: 600; }

    /* action chip — colour by kind */
    .act { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 600;
      padding: 4px 10px; border-radius: var(--r-pill); max-width: 220px; border: 1px solid transparent;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .act va-icon { flex: none; }
    .act.lg { font-size: var(--text-sm); padding: 6px 12px; }
    .act[data-kind='approve']    { background: var(--color-success-soft); color: var(--color-success); }
    .act[data-kind='reject']     { background: var(--color-danger-soft); color: var(--color-danger); }
    .act[data-kind='delete']     { background: var(--color-danger-soft); color: var(--color-danger); }
    .act[data-kind='guardrail']  { background: var(--color-warning-soft); color: var(--color-warning); }
    .act[data-kind='ai-message'] { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .act[data-kind='import']     { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .act[data-kind='escalation'] { background: rgba(var(--color-accent-rgb), .12); color: var(--color-accent); }
    .act[data-kind='status']     { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .act[data-kind='config']     { background: var(--color-surface-alt); color: var(--color-text); }
    .act[data-kind='access']     { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .act[data-kind='export']     { background: rgba(var(--color-accent-rgb), .10); color: var(--color-accent); }

    /* entity cell */
    .entity { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .chip.mono { background: var(--color-surface-2); color: var(--color-text-muted); padding: 3px 8px;
      font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 11px; }

    /* diff cell */
    .diff { display: flex; align-items: center; gap: 7px; max-width: 320px; }
    .diff va-icon { flex: none; color: var(--color-text-muted); }
    .diff .before { color: var(--color-text-muted); max-width: 130px; }
    .diff .after { color: var(--color-text); font-weight: 600; max-width: 150px; }

    /* correlation chip */
    .corr { cursor: pointer; border-color: var(--color-border); transition: border-color .15s, color .15s; }
    .corr:hover { border-color: var(--color-accent); color: var(--color-accent); }

    .endcol { width: 36px; text-align: right; color: var(--color-text-muted); }

    /* ===== drawer detail ===== */
    .detail { display: flex; flex-direction: column; gap: var(--s-4); }
    .d-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .d-actor { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: var(--r-md);
      background: var(--color-surface-2); border: 1px solid var(--color-border); }
    .ai-tag { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700;
      padding: 2px 7px; border-radius: var(--r-pill); background: var(--gradient-ai); color: #06121A; }

    .dl.meta { padding: 0 2px; }
    .dl.meta dd { font-weight: 500; }
    .entlink { display: inline-flex; align-items: center; gap: 4px; color: var(--color-accent); font-weight: 600; }
    .entlink:hover { text-decoration: underline; }

    .banner.note { align-items: flex-start; }
    .banner.note va-icon { color: var(--color-accent); flex: none; margin-top: 1px; }

    .json-empty { display: flex; align-items: center; gap: 8px; padding: 14px; border-radius: var(--r-md);
      background: var(--color-surface-2); border: 1px dashed var(--color-border-strong); }
    .json-empty va-icon { flex: none; }

    .diff-grid { display: grid; grid-template-columns: 1fr; gap: var(--s-3); }
    .diff-pane { border: 1px solid var(--color-border); border-radius: var(--r-md); overflow: hidden; }
    .diff-label { display: flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700;
      text-transform: uppercase; letter-spacing: .04em; padding: 8px 12px; border-bottom: 1px solid var(--color-border); }
    .before-pane .diff-label { background: var(--color-danger-soft); color: var(--color-danger); }
    .after-pane .diff-label { background: var(--color-success-soft); color: var(--color-success); }
    .json { margin: 0; padding: 12px 14px; font-family: ui-monospace, 'SF Mono', Menlo, monospace;
      font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; color: var(--color-text);
      background: var(--color-surface-2); }
  `],
})
export class AuditLogsComponent {
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');

  fmtInt = fmtInt;
  relTime = relTime;

  // ---- filter state ----
  query = signal('');
  entityFilter = signal('');
  actorFilter = signal('');
  kindFilter = signal('');
  rangeFilter = signal('30d');
  aiOnly = signal<'all' | 'human' | 'ai'>('all');
  selected = signal<AuditEntry | null>(null);

  kindOptions: { value: ActionKind; label: string }[] = [
    { value: 'approve', label: 'Approvals' },
    { value: 'reject', label: 'Rejections' },
    { value: 'guardrail', label: 'Guardrail changes' },
    { value: 'ai-message', label: 'AI-sent messages' },
    { value: 'import', label: 'Data imports' },
    { value: 'escalation', label: 'Escalations' },
    { value: 'status', label: 'Status changes' },
    { value: 'config', label: 'Configuration' },
    { value: 'access', label: 'Access & sign-in' },
    { value: 'delete', label: 'Deletions / unlearn' },
    { value: 'export', label: 'Exports' },
  ];

  rangeOptions = [
    { value: '24h', label: 'Last 24 hours' },
    { value: '7d', label: 'Last 7 days' },
    { value: '30d', label: 'Last 30 days' },
    { value: 'all', label: 'Full cycle' },
  ];

  private readonly NOW = new Date('2026-06-14T09:30:00').getTime();
  private readonly DAY = 86400000;

  // ---- seed: ~24 realistic entries, switched by active counsellor ----
  private entries = computed<AuditEntry[]>(() => this.career() ? this.careerSeed() : this.seed());

  total = computed(() => this.entries().length);
  aiCount = computed(() => this.entries().filter(e => e.isAi).length);

  entityOptions = computed(() =>
    Array.from(new Set(this.entries().map(e => e.entityType))).sort());
  actorOptions = computed(() =>
    Array.from(new Set(this.entries().map(e => e.actorName))).sort());

  countKind(k: ActionKind): number { return this.entries().filter(e => e.kind === k).length; }

  activeFiltersOn = computed(() =>
    !!this.query() || !!this.entityFilter() || !!this.actorFilter() ||
    !!this.kindFilter() || this.rangeFilter() !== '30d' || this.aiOnly() !== 'all');

  rows = computed<AuditEntry[]>(() => {
    const q = this.query().trim().toLowerCase();
    const ent = this.entityFilter();
    const act = this.actorFilter();
    const kind = this.kindFilter();
    const range = this.rangeFilter();
    const who = this.aiOnly();
    const cutoff = this.cutoffFor(range);
    return this.entries().filter(e => {
      if (q && !this.matchesQuery(e, q)) return false;
      if (ent && e.entityType !== ent) return false;
      if (act && e.actorName !== act) return false;
      if (kind && e.kind !== kind) return false;
      if (who === 'ai' && !e.isAi) return false;
      if (who === 'human' && e.isAi) return false;
      if (cutoff !== null && new Date(e.ts).getTime() < cutoff) return false;
      return true;
    });
  });

  private matchesQuery(e: AuditEntry, q: string): boolean {
    return e.action.toLowerCase().includes(q)
      || e.entityType.toLowerCase().includes(q)
      || e.entityId.toLowerCase().includes(q)
      || e.actorName.toLowerCase().includes(q)
      || e.correlationId.toLowerCase().includes(q)
      || e.before.toLowerCase().includes(q)
      || e.after.toLowerCase().includes(q);
  }

  private cutoffFor(range: string): number | null {
    switch (range) {
      case '24h': return this.NOW - this.DAY;
      case '7d': return this.NOW - 7 * this.DAY;
      case '30d': return this.NOW - 30 * this.DAY;
      default: return null;
    }
  }

  exactTs(iso: string): string {
    return new Date(iso).toLocaleString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  kindIcon(k: ActionKind): string {
    switch (k) {
      case 'approve': return 'check-circle';
      case 'reject': return 'x';
      case 'guardrail': return 'shield-check';
      case 'ai-message': return 'sparkles';
      case 'import': return 'upload';
      case 'escalation': return 'flag';
      case 'status': return 'git-branch';
      case 'config': return 'settings';
      case 'access': return 'lock';
      case 'delete': return 'trash';
      case 'export': return 'download';
      default: return 'circle';
    }
  }

  // ---- interactions ----
  open(e: AuditEntry) { this.selected.set(e); }

  clearFilters() {
    this.query.set('');
    this.entityFilter.set('');
    this.actorFilter.set('');
    this.kindFilter.set('');
    this.rangeFilter.set('30d');
    this.aiOnly.set('all');
  }

  copyCorrelation(e: AuditEntry) {
    navigator.clipboard?.writeText(e.correlationId).catch(() => {});
    this.toast.success(`Correlation ID ${e.correlationId} copied.`);
  }
  copyEvent() {
    const e = this.selected();
    if (!e) return;
    navigator.clipboard?.writeText(JSON.stringify(e, null, 2)).catch(() => {});
    this.toast.success('Event JSON copied to clipboard.');
  }
  openEntity() {
    const e = this.selected();
    if (e?.entityRoute) { this.selected.set(null); this.router.navigateByUrl(e.entityRoute); }
  }
  exportLog() {
    this.toast.success(`Exporting ${fmtInt(this.rows().length)} filtered audit events to signed CSV — you'll be notified when ready.`);
  }
  complianceReport() {
    this.toast.info('Generating the cycle compliance report (approvals, guardrail changes & AI message attestations).');
  }

  // ---------------------------------------------------------------------------
  private seed(): AuditEntry[] {
    const iso = (offsetMin: number) => new Date(this.NOW - offsetMin * 60000).toISOString();
    const e: AuditEntry[] = [
      {
        id: 'aud-001', ts: iso(18), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Approved KMS document', kind: 'approve',
        entityType: 'KMS Document', entityId: 'doc-014', entityRoute: '/app/kms/document/doc-014',
        before: 'Under Approval', after: 'Active · trained',
        beforeJson: '{\n  "status": "Under Approval",\n  "version": 3,\n  "aiTrainingStatus": "Queued",\n  "effectiveDate": null\n}',
        afterJson: '{\n  "status": "Active",\n  "version": 3,\n  "aiTrainingStatus": "Trained",\n  "effectiveDate": "2026-06-14",\n  "approvedBy": "Sneha Banerjee"\n}',
        ip: '10.4.22.18', correlationId: 'cor-9F2A1B', channel: 'KMS',
        note: 'Document "B.Tech AI & DS — Fee Structure 2026" is now the single approved source Aisha may quote fees from.',
      },
      {
        id: 'aud-002', ts: iso(41), actorName: 'Imran Sheikh', actorRole: 'AI Counselor Supervisor', isAi: false, actorHue: 200,
        action: 'Edited guardrail', kind: 'guardrail',
        entityType: 'Guardrail', entityId: 'grd-007', entityRoute: '/app/guardrails',
        before: 'Never quote unverified placement %', after: 'Never quote placement % or salary',
        beforeJson: '{\n  "rule": "never",\n  "text": "Never quote unverified placement percentages",\n  "claimBearing": true\n}',
        afterJson: '{\n  "rule": "never",\n  "text": "Never quote placement percentages or salary figures of any kind",\n  "claimBearing": true,\n  "requiresApproval": true\n}',
        ip: '10.4.22.61', correlationId: 'cor-77C3DD',
        note: 'Stricter never-rule submitted for Compliance approval before it can affect Aisha.',
      },
      {
        id: 'aud-003', ts: iso(58), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'AI sent WhatsApp message', kind: 'ai-message',
        entityType: 'Candidate', entityId: 'cand-1042', entityRoute: '/app/crm/candidate/cand-1042',
        before: '—', after: 'Sent · scholarship eligibility (approved KMS)',
        afterJson: '{\n  "channel": "whatsapp",\n  "intent": "scholarship_eligibility",\n  "knowledgeSource": "doc-031 (Active)",\n  "aiConfidence": 92,\n  "disclosure": "Identified as AI assistant",\n  "claimsMade": "none beyond approved knowledge"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E04', channel: 'WhatsApp',
        note: 'Reply drawn strictly from Active knowledge; no fees, scholarships or placements were invented.',
      },
      {
        id: 'aud-004', ts: iso(96), actorName: 'Rahul Desai', actorRole: 'CRM / Data Manager', isAi: false, actorHue: 30,
        action: 'Imported 118 leads', kind: 'import',
        entityType: 'Lead Batch', entityId: 'imp-2026-061', entityRoute: '/app/crm/import',
        before: '0 records', after: '118 imported · 6 duplicates merged',
        beforeJson: '{\n  "source": "EduFair Hyderabad 2026",\n  "rows": 124,\n  "consentCaptured": true\n}',
        afterJson: '{\n  "imported": 118,\n  "duplicatesMerged": 6,\n  "rejected": 0,\n  "consentVerified": 118,\n  "assignedTo": "Aisha (AI)"\n}',
        ip: '10.4.22.44', correlationId: 'cor-3B8821', channel: 'CRM',
      },
      {
        id: 'aud-005', ts: iso(140), actorName: 'Meera Nair', actorRole: 'Human Counselor', isAi: false, actorHue: 330,
        action: 'Resolved escalation', kind: 'escalation',
        entityType: 'Escalation', entityId: 'esc-3391', entityRoute: '/app/handoff',
        before: 'Claimed', after: 'Resolved · parent reassured',
        beforeJson: '{\n  "status": "Claimed",\n  "reason": "Parent fee-affordability concern",\n  "urgency": "High",\n  "assignedTo": "Meera Nair"\n}',
        afterJson: '{\n  "status": "Resolved",\n  "outcome": "Parent reassured; EMI options shared from approved KMS",\n  "handbackToAi": true\n}',
        ip: '10.4.22.77', correlationId: 'cor-5C0AA9', channel: 'Voice',
      },
      {
        id: 'aud-006', ts: iso(175), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'Changed candidate status', kind: 'status',
        entityType: 'Candidate', entityId: 'cand-0987', entityRoute: '/app/crm/candidate/cand-0987',
        before: 'Contacted', after: 'Interested',
        beforeJson: '{\n  "currentStage": "Contacted",\n  "conversionProbability": 48,\n  "sentiment": "neutral"\n}',
        afterJson: '{\n  "currentStage": "Interested",\n  "conversionProbability": 67,\n  "sentiment": "pos",\n  "trigger": "candidate asked about B.Des UX portfolio round"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E07', channel: 'WhatsApp',
      },
      {
        id: 'aud-007', ts: iso(210), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Submitted document for approval', kind: 'config',
        entityType: 'KMS Document', entityId: 'doc-031', entityRoute: '/app/kms/document/doc-031',
        before: 'Draft', after: 'Under Approval',
        beforeJson: '{\n  "status": "Draft",\n  "title": "MBA Scholarship Policy 2026",\n  "version": 1\n}',
        afterJson: '{\n  "status": "Under Approval",\n  "submittedTo": "Compliance",\n  "claimBearing": true\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D44F0', channel: 'KMS',
      },
      {
        id: 'aud-008', ts: iso(255), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Rejected approval request', kind: 'reject',
        entityType: 'Approval Request', entityId: 'apr-019', entityRoute: '/app/approvals',
        before: 'Under Review', after: 'Rejected · claim unverifiable',
        beforeJson: '{\n  "status": "Under Review",\n  "title": "Add 95% placement claim to B.Tech FAQ",\n  "riskLevel": "high"\n}',
        afterJson: '{\n  "status": "Rejected",\n  "reason": "Placement figure not backed by an Active source document",\n  "guidance": "Resubmit with audited placement report"\n}',
        ip: '10.4.22.18', correlationId: 'cor-6D44F1', channel: 'Approvals',
        note: 'Responsible-AI control in action: an unverifiable claim was blocked before it could reach candidates.',
      },
      {
        id: 'aud-009', ts: iso(320), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'Escalated to human counselor', kind: 'escalation',
        entityType: 'Escalation', entityId: 'esc-3402',
        before: '—', after: 'Open · low confidence on hostel policy',
        afterJson: '{\n  "reason": "No Active document covers hostel allotment policy",\n  "aiConfidence": 34,\n  "action": "Declined to answer; routed to human",\n  "urgency": "Medium"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E0B', channel: 'WhatsApp',
        note: 'Aisha correctly declined rather than inventing an answer outside approved knowledge.',
      },
      {
        id: 'aud-010', ts: iso(390), actorName: 'Priya Menon', actorRole: 'Admission Director', isAi: false, actorHue: 222,
        action: 'Exported analytics report', kind: 'export',
        entityType: 'Report', entityId: 'rpt-2026-Q2', entityRoute: '/app/analytics',
        before: '—', after: 'PDF · cycle funnel + conversion',
        afterJson: '{\n  "format": "PDF",\n  "scope": "Fall 2026 cycle",\n  "rows": 4821,\n  "containsPII": false\n}',
        ip: '10.4.22.10', correlationId: 'cor-8E2210', channel: 'Analytics',
      },
      {
        id: 'aud-011', ts: iso(470), actorName: 'Imran Sheikh', actorRole: 'AI Counselor Supervisor', isAi: false, actorHue: 200,
        action: 'Paused channel', kind: 'config',
        entityType: 'Channel', entityId: 'ch-voice', entityRoute: '/app/integrations',
        before: 'live', after: 'paused',
        beforeJson: '{\n  "channel": "voice",\n  "status": "live"\n}',
        afterJson: '{\n  "channel": "voice",\n  "status": "paused",\n  "reason": "Scheduled telephony provider maintenance"\n}',
        ip: '10.4.22.61', correlationId: 'cor-77C3E2', channel: 'Voice',
      },
      {
        id: 'aud-012', ts: iso(40 + 12 * 60), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'AI sent WhatsApp message', kind: 'ai-message',
        entityType: 'Candidate', entityId: 'cand-1190', entityRoute: '/app/crm/candidate/cand-1190',
        before: '—', after: 'Sent · application fee reminder',
        afterJson: '{\n  "channel": "whatsapp",\n  "intent": "fee_reminder",\n  "knowledgeSource": "doc-014 (Active)",\n  "aiConfidence": 88,\n  "disclosure": "Identified as AI assistant"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E12', channel: 'WhatsApp',
      },
      {
        id: 'aud-013', ts: iso(60 + 14 * 60), actorName: 'Rahul Desai', actorRole: 'CRM / Data Manager', isAi: false, actorHue: 30,
        action: 'Merged duplicate candidates', kind: 'status',
        entityType: 'Candidate', entityId: 'cand-0771', entityRoute: '/app/crm/candidate/cand-0771',
        before: '2 records', after: '1 record · history merged',
        beforeJson: '{\n  "primary": "cand-0771",\n  "duplicate": "cand-1205",\n  "matchedOn": "mobile + email"\n}',
        afterJson: '{\n  "retained": "cand-0771",\n  "archived": "cand-1205",\n  "eventsMerged": 14,\n  "consentReconciled": true\n}',
        ip: '10.4.22.44', correlationId: 'cor-3B8830', channel: 'CRM',
      },
      {
        id: 'aud-014', ts: iso(90 + 18 * 60), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Requested document unlearn', kind: 'delete',
        entityType: 'KMS Document', entityId: 'doc-009', entityRoute: '/app/kms/document/doc-009',
        before: 'Active', after: 'Unlearn Pending',
        beforeJson: '{\n  "status": "Active",\n  "title": "Fee Structure 2025 (outdated)",\n  "aiTrainingStatus": "Trained"\n}',
        afterJson: '{\n  "status": "Unlearn Pending",\n  "reason": "Superseded by doc-014 for 2026 cycle",\n  "awaiting": "Compliance approval"\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D4500', channel: 'KMS',
        note: 'Aisha will stop using this document for answers once the unlearn is approved.',
      },
      {
        id: 'aud-015', ts: iso(30 + 20 * 60), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Updated data-retention policy', kind: 'config',
        entityType: 'Policy', entityId: 'pol-retention', entityRoute: '/app/settings',
        before: 'Retain 24 months', after: 'Retain 18 months',
        beforeJson: '{\n  "candidateDataRetentionMonths": 24,\n  "recordingRetentionMonths": 12\n}',
        afterJson: '{\n  "candidateDataRetentionMonths": 18,\n  "recordingRetentionMonths": 12,\n  "approvedBy": "Sneha Banerjee"\n}',
        ip: '10.4.22.18', correlationId: 'cor-6D4502', channel: 'Settings',
      },
      {
        id: 'aud-016', ts: iso(15 + 26 * 60), actorName: 'Priya Menon', actorRole: 'Admission Director', isAi: false, actorHue: 222,
        action: 'Signed in', kind: 'access',
        entityType: 'User Session', entityId: 'sess-7741',
        before: '—', after: 'Authenticated · SSO',
        afterJson: '{\n  "method": "SSO (Okta)",\n  "mfa": true,\n  "device": "macOS · Chrome",\n  "location": "Bengaluru, IN"\n}',
        ip: '10.4.22.10', correlationId: 'cor-8E2230',
      },
      {
        id: 'aud-017', ts: iso(45 + 28 * 60), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'AI sent email', kind: 'ai-message',
        entityType: 'Candidate', entityId: 'cand-0654', entityRoute: '/app/crm/candidate/cand-0654',
        before: '—', after: 'Sent · V-Con confirmation',
        afterJson: '{\n  "channel": "email",\n  "intent": "vcon_confirmation",\n  "knowledgeSource": "calendar + doc-022 (Active)",\n  "disclosure": "Footer states message authored by AI counselor"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E1A', channel: 'Email',
      },
      {
        id: 'aud-018', ts: iso(20 + 32 * 60), actorName: 'Imran Sheikh', actorRole: 'AI Counselor Supervisor', isAi: false, actorHue: 200,
        action: 'Edited AI persona', kind: 'config',
        entityType: 'AI Counselor Config', entityId: 'aria-persona', entityRoute: '/app/ai-counselor',
        before: 'Tone: friendly', after: 'Tone: warm & formal',
        beforeJson: '{\n  "name": "Aisha",\n  "tone": "friendly",\n  "languages": ["English", "Hindi", "Telugu"]\n}',
        afterJson: '{\n  "name": "Aisha",\n  "tone": "warm & formal",\n  "languages": ["English", "Hindi", "Telugu", "Tamil"],\n  "requiresApproval": true\n}',
        ip: '10.4.22.61', correlationId: 'cor-77C3F0', channel: 'AI Counselor',
      },
      {
        id: 'aud-019', ts: iso(50 + 50 * 60), actorName: 'Meera Nair', actorRole: 'Human Counselor', isAi: false, actorHue: 330,
        action: 'Added counselor note', kind: 'config',
        entityType: 'Candidate', entityId: 'cand-1042', entityRoute: '/app/crm/candidate/cand-1042',
        before: '—', after: 'Note · prefers evening calls',
        afterJson: '{\n  "note": "Parent prefers calls after 6pm IST; candidate keen on AI & DS scholarship",\n  "visibleToAi": true\n}',
        ip: '10.4.22.77', correlationId: 'cor-5C0AB4', channel: 'CRM',
      },
      {
        id: 'aud-020', ts: iso(10 + 70 * 60), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Approved guardrail change', kind: 'approve',
        entityType: 'Guardrail', entityId: 'grd-004', entityRoute: '/app/guardrails',
        before: 'Under Review', after: 'Active',
        beforeJson: '{\n  "status": "Under Review",\n  "rule": "always",\n  "text": "Always disclose Aisha is an AI at conversation start"\n}',
        afterJson: '{\n  "status": "Active",\n  "rule": "always",\n  "text": "Always disclose Aisha is an AI at conversation start",\n  "appliesTo": ["voice", "whatsapp", "email", "vcon"]\n}',
        ip: '10.4.22.18', correlationId: 'cor-6D4520', channel: 'Approvals',
      },
      {
        id: 'aud-021', ts: iso(30 + 96 * 60), actorName: 'Rahul Desai', actorRole: 'CRM / Data Manager', isAi: false, actorHue: 30,
        action: 'Updated candidate consent', kind: 'config',
        entityType: 'Candidate', entityId: 'cand-1190', entityRoute: '/app/crm/candidate/cand-1190',
        before: 'WhatsApp: yes', after: 'WhatsApp: no',
        beforeJson: '{\n  "consent": { "call": true, "whatsapp": true, "email": true, "recording": true }\n}',
        afterJson: '{\n  "consent": { "call": true, "whatsapp": false, "email": true, "recording": true },\n  "source": "candidate opt-out reply"\n}',
        ip: '10.4.22.44', correlationId: 'cor-3B8855', channel: 'CRM',
        note: 'Aisha will no longer contact this candidate on WhatsApp until consent is restored.',
      },
      {
        id: 'aud-022', ts: iso(45 + 120 * 60), actorName: 'Aisha (AI)', actorRole: 'AI Virtual Counselor', isAi: true, actorHue: 190,
        action: 'Flagged knowledge gap', kind: 'escalation',
        entityType: 'Knowledge Gap', entityId: 'gap-0231', entityRoute: '/app/learning-review',
        before: '—', after: 'Open · 7 unanswered on transport',
        afterJson: '{\n  "topic": "Campus transport & bus routes",\n  "occurrences": 7,\n  "recommendedDoc": "Transport Policy 2026",\n  "aiAction": "Declined; routed to Knowledge Manager"\n}',
        ip: 'svc-aria-prod', correlationId: 'cor-A11E25', channel: 'Web',
      },
      {
        id: 'aud-023', ts: iso(20 + 150 * 60), actorName: 'Priya Menon', actorRole: 'Admission Director', isAi: false, actorHue: 222,
        action: 'Changed role permissions', kind: 'config',
        entityType: 'Role', entityId: 'role-crm-manager', entityRoute: '/app/settings',
        before: 'No export rights', after: 'Export rights (PII-masked)',
        beforeJson: '{\n  "role": "crm-manager",\n  "permissions": ["read", "import", "edit"]\n}',
        afterJson: '{\n  "role": "crm-manager",\n  "permissions": ["read", "import", "edit", "export"],\n  "exportPolicy": "PII masked"\n}',
        ip: '10.4.22.10', correlationId: 'cor-8E2240', channel: 'Settings',
      },
      {
        id: 'aud-024', ts: iso(15 + 200 * 60), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Resolved knowledge conflict', kind: 'status',
        entityType: 'KMS Document', entityId: 'doc-022', entityRoute: '/app/kms/document/doc-022',
        before: 'Conflict score 41', after: 'Conflict score 6',
        beforeJson: '{\n  "documentId": "doc-022",\n  "conflictScore": 41,\n  "conflictsWith": "doc-009"\n}',
        afterJson: '{\n  "documentId": "doc-022",\n  "conflictScore": 6,\n  "resolution": "Archived superseded doc-009"\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D4540', channel: 'KMS',
      },
    ];
    return e.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  }

  // ---------------------------------------------------------------------------
  /** Career-counsellor (Vera) audit trail — pathways, salary bands, recommendations, skill plans. */
  private careerSeed(): AuditEntry[] {
    const iso = (offsetMin: number) => new Date(this.NOW - offsetMin * 60000).toISOString();
    const e: AuditEntry[] = [
      {
        id: 'caud-001', ts: iso(16), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Published career pathway', kind: 'approve',
        entityType: 'Career Pathway', entityId: 'path-022', entityRoute: '/app/kms',
        before: 'Under Approval', after: 'Active · published',
        beforeJson: '{\n  "status": "Under Approval",\n  "pathway": "Cloud Engineering",\n  "version": 2\n}',
        afterJson: '{\n  "status": "Active",\n  "pathway": "Cloud Engineering",\n  "prerequisites": ["Linux", "Networking"],\n  "publishedBy": "Kavya Iyer"\n}',
        ip: '10.4.22.29', correlationId: 'cor-7C1A0B', channel: 'KMS',
        note: 'Cloud Engineering is now an approved pathway Vera may recommend, with its prerequisites.',
      },
      {
        id: 'caud-002', ts: iso(38), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Approved salary-band data', kind: 'approve',
        entityType: 'Salary Band', entityId: 'band-da-26', entityRoute: '/app/approvals',
        before: '₹5–9 LPA (FY25)', after: '₹6–11 LPA (FY26)',
        beforeJson: '{\n  "role": "Data Analyst",\n  "band": "5-9 LPA",\n  "source": "FY25 market dataset"\n}',
        afterJson: '{\n  "role": "Data Analyst",\n  "band": "6-11 LPA",\n  "source": "FY26 market dataset",\n  "disclaimer": "indicative market range"\n}',
        ip: '10.4.22.18', correlationId: 'cor-7C1A0C', channel: 'Approvals',
        note: 'Vera now quotes the FY26 indicative band; figures remain ranges, never individual promises.',
      },
      {
        id: 'caud-003', ts: iso(57), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'AI recommended pathway', kind: 'ai-message',
        entityType: 'Student', entityId: 'stu-1042', entityRoute: '/app/crm',
        before: '—', after: 'Sent · Data Analyst pathway (approved library)',
        afterJson: '{\n  "channel": "whatsapp",\n  "intent": "pathway_recommendation",\n  "pathwaySource": "path-014 (Active)",\n  "framedAs": "one of several options",\n  "disclosure": "Identified as AI counsellor",\n  "claimsMade": "no job or salary guarantee"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E04', channel: 'WhatsApp',
        note: 'Recommendation drawn from the approved pathway library; presented as indicative, with options.',
      },
      {
        id: 'caud-004', ts: iso(94), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'Created skill plan', kind: 'config',
        entityType: 'Skill Plan', entityId: 'plan-0771', entityRoute: '/app/crm',
        before: '—', after: '12-week plan · Data Analyst track',
        afterJson: '{\n  "track": "Data Analyst",\n  "weeks": 12,\n  "courseSource": "approved tracks",\n  "outcomeClaim": "improves readiness; no guarantee"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E07', channel: 'Web',
        note: 'Skill plan built from approved courses with honest effort expectations.',
      },
      {
        id: 'caud-005', ts: iso(138), actorName: 'Imran Sheikh', actorRole: 'AI Counselor Supervisor', isAi: false, actorHue: 200,
        action: 'Edited guardrail', kind: 'guardrail',
        entityType: 'Guardrail', entityId: 'grd-c01', entityRoute: '/app/guardrails',
        before: 'May state recommended field', after: 'Aptitude is indicative, not a verdict',
        beforeJson: '{\n  "rule": "always",\n  "text": "Vera may state a recommended field from the aptitude score"\n}',
        afterJson: '{\n  "rule": "always",\n  "text": "Vera must frame aptitude results as indicative guidance, never a verdict",\n  "requiresApproval": true\n}',
        ip: '10.4.22.61', correlationId: 'cor-77C3DE',
        note: 'Stricter rule submitted for Compliance so Vera never labels a student unfit.',
      },
      {
        id: 'caud-006', ts: iso(176), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Rejected approval request', kind: 'reject',
        entityType: 'Approval Request', entityId: 'capr-009', entityRoute: '/app/approvals',
        before: 'Under Review', after: 'Rejected · implies job guarantee',
        beforeJson: '{\n  "status": "Under Review",\n  "title": "Add \'guaranteed placement\' to Cloud track",\n  "riskLevel": "high"\n}',
        afterJson: '{\n  "status": "Rejected",\n  "reason": "Implies an assured job — Vera may never guarantee employment",\n  "guidance": "Reframe as readiness support"\n}',
        ip: '10.4.22.18', correlationId: 'cor-6D44F2', channel: 'Approvals',
        note: 'Responsible-AI control: a guaranteed-job claim was blocked before reaching students.',
      },
      {
        id: 'caud-007', ts: iso(212), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'Escalated to human mentor', kind: 'escalation',
        entityType: 'Escalation', entityId: 'esc-c402',
        before: '—', after: 'Open · low confidence on overseas study',
        afterJson: '{\n  "reason": "No Active pathway covers overseas study routes",\n  "aiConfidence": 31,\n  "action": "Declined; routed to human mentor",\n  "urgency": "Medium"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E0B', channel: 'WhatsApp',
        note: 'Vera correctly declined rather than inventing guidance outside approved knowledge.',
      },
      {
        id: 'caud-008', ts: iso(40 + 12 * 60), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'AI sent WhatsApp message', kind: 'ai-message',
        entityType: 'Student', entityId: 'stu-1190', entityRoute: '/app/crm',
        before: '—', after: 'Sent · mentor-match introduction',
        afterJson: '{\n  "channel": "whatsapp",\n  "intent": "mentor_match",\n  "scriptSource": "tpl-mentor-01 (Approved)",\n  "disclosure": "Identified as AI counsellor",\n  "claimsMade": "no referral or outcome promise"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E12', channel: 'WhatsApp',
      },
      {
        id: 'caud-009', ts: iso(60 + 14 * 60), actorName: 'Rahul Desai', actorRole: 'CRM / Data Manager', isAi: false, actorHue: 30,
        action: 'Imported 96 students', kind: 'import',
        entityType: 'Student Batch', entityId: 'imp-2026-072', entityRoute: '/app/crm',
        before: '0 records', after: '96 imported · 4 duplicates merged',
        beforeJson: '{\n  "source": "Campus career fair 2026",\n  "rows": 100,\n  "consentCaptured": true\n}',
        afterJson: '{\n  "imported": 96,\n  "duplicatesMerged": 4,\n  "consentVerified": 96,\n  "assignedTo": "Vera (AI)"\n}',
        ip: '10.4.22.44', correlationId: 'cor-3B8840', channel: 'CRM',
      },
      {
        id: 'caud-010', ts: iso(90 + 18 * 60), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Submitted pathway for approval', kind: 'config',
        entityType: 'Career Pathway', entityId: 'path-031', entityRoute: '/app/kms',
        before: 'Draft', after: 'Under Approval',
        beforeJson: '{\n  "status": "Draft",\n  "pathway": "Product Management",\n  "version": 1\n}',
        afterJson: '{\n  "status": "Under Approval",\n  "submittedTo": "Compliance",\n  "claimBearing": true\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D44F8', channel: 'KMS',
      },
      {
        id: 'caud-011', ts: iso(30 + 20 * 60), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'Flagged knowledge gap', kind: 'escalation',
        entityType: 'Knowledge Gap', entityId: 'gap-c031', entityRoute: '/app/learning-review',
        before: '—', after: 'Open · 9 unanswered on internships',
        afterJson: '{\n  "topic": "Summer internship pipelines",\n  "occurrences": 9,\n  "recommendedDoc": "Internships Guide 2026",\n  "aiAction": "Declined; routed to Knowledge Manager"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E25', channel: 'Web',
      },
      {
        id: 'caud-012', ts: iso(15 + 26 * 60), actorName: 'Priya Menon', actorRole: 'Career Services Director', isAi: false, actorHue: 222,
        action: 'Signed in', kind: 'access',
        entityType: 'User Session', entityId: 'sess-8841',
        before: '—', after: 'Authenticated · SSO',
        afterJson: '{\n  "method": "SSO (Okta)",\n  "mfa": true,\n  "device": "macOS · Chrome",\n  "location": "Bengaluru, IN"\n}',
        ip: '10.4.22.10', correlationId: 'cor-8E2250',
      },
      {
        id: 'caud-013', ts: iso(45 + 28 * 60), actorName: 'Sneha Banerjee', actorRole: 'Compliance & Approval Officer', isAi: false, actorHue: 280,
        action: 'Approved guardrail change', kind: 'approve',
        entityType: 'Guardrail', entityId: 'grd-c04', entityRoute: '/app/guardrails',
        before: 'Under Review', after: 'Active',
        beforeJson: '{\n  "status": "Under Review",\n  "rule": "never",\n  "text": "Never guarantee a job, salary or placement"\n}',
        afterJson: '{\n  "status": "Active",\n  "rule": "never",\n  "text": "Never guarantee a job, salary or placement",\n  "appliesTo": ["voice", "whatsapp", "email"]\n}',
        ip: '10.4.22.18', correlationId: 'cor-6D4530', channel: 'Approvals',
      },
      {
        id: 'caud-014', ts: iso(20 + 32 * 60), actorName: 'Imran Sheikh', actorRole: 'AI Counselor Supervisor', isAi: false, actorHue: 200,
        action: 'Edited AI persona', kind: 'config',
        entityType: 'AI Counselor Config', entityId: 'vera-persona', entityRoute: '/app/ai-counselor',
        before: 'Tone: friendly', after: 'Tone: warm & encouraging',
        beforeJson: '{\n  "name": "Vera",\n  "tone": "friendly",\n  "languages": ["English", "Hindi"]\n}',
        afterJson: '{\n  "name": "Vera",\n  "tone": "warm & encouraging",\n  "languages": ["English", "Hindi", "Telugu"],\n  "requiresApproval": true\n}',
        ip: '10.4.22.61', correlationId: 'cor-77C3F4', channel: 'AI Counselor',
      },
      {
        id: 'caud-015', ts: iso(50 + 50 * 60), actorName: 'Vera (AI)', actorRole: 'AI Virtual Career Counselor', isAi: true, actorHue: 168,
        action: 'AI sent email', kind: 'ai-message',
        entityType: 'Student', entityId: 'stu-0654', entityRoute: '/app/crm',
        before: '—', after: 'Sent · skill-plan summary',
        afterJson: '{\n  "channel": "email",\n  "intent": "skill_plan_summary",\n  "planSource": "plan-0654 (Active)",\n  "disclosure": "Footer states message authored by AI counsellor"\n}',
        ip: 'svc-vera-prod', correlationId: 'cor-V21E1A', channel: 'Email',
      },
      {
        id: 'caud-016', ts: iso(10 + 70 * 60), actorName: 'Rahul Desai', actorRole: 'CRM / Data Manager', isAi: false, actorHue: 30,
        action: 'Updated student consent', kind: 'config',
        entityType: 'Student', entityId: 'stu-1190', entityRoute: '/app/crm',
        before: 'WhatsApp: yes', after: 'WhatsApp: no',
        beforeJson: '{\n  "consent": { "call": true, "whatsapp": true, "email": true }\n}',
        afterJson: '{\n  "consent": { "call": true, "whatsapp": false, "email": true },\n  "source": "student opt-out reply"\n}',
        ip: '10.4.22.44', correlationId: 'cor-3B8865', channel: 'CRM',
        note: 'Vera will no longer contact this student on WhatsApp until consent is restored.',
      },
      {
        id: 'caud-017', ts: iso(30 + 96 * 60), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Requested pathway unlearn', kind: 'delete',
        entityType: 'Career Pathway', entityId: 'path-009', entityRoute: '/app/kms',
        before: 'Active', after: 'Unlearn Pending',
        beforeJson: '{\n  "status": "Active",\n  "pathway": "Legacy ITES support (outdated)"\n}',
        afterJson: '{\n  "status": "Unlearn Pending",\n  "reason": "Superseded for 2026 market",\n  "awaiting": "Compliance approval"\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D4510', channel: 'KMS',
        note: 'Vera will stop recommending this pathway once the unlearn is approved.',
      },
      {
        id: 'caud-018', ts: iso(45 + 120 * 60), actorName: 'Meera Nair', actorRole: 'Human Mentor', isAi: false, actorHue: 330,
        action: 'Resolved escalation', kind: 'escalation',
        entityType: 'Escalation', entityId: 'esc-c391', entityRoute: '/app/handoff',
        before: 'Claimed', after: 'Resolved · student guided',
        beforeJson: '{\n  "status": "Claimed",\n  "reason": "Unsure between Data and Design tracks",\n  "assignedTo": "Meera Nair"\n}',
        afterJson: '{\n  "status": "Resolved",\n  "outcome": "Explored both tracks; student chose to trial Data",\n  "handbackToAi": true\n}',
        ip: '10.4.22.77', correlationId: 'cor-5C0AB9', channel: 'Voice',
      },
      {
        id: 'caud-019', ts: iso(20 + 150 * 60), actorName: 'Priya Menon', actorRole: 'Career Services Director', isAi: false, actorHue: 222,
        action: 'Exported analytics report', kind: 'export',
        entityType: 'Report', entityId: 'rpt-2026-Q2c', entityRoute: '/app/analytics',
        before: '—', after: 'PDF · pathway engagement + readiness',
        afterJson: '{\n  "format": "PDF",\n  "scope": "2026 career cohort",\n  "rows": 3120,\n  "containsPII": false\n}',
        ip: '10.4.22.10', correlationId: 'cor-8E2218', channel: 'Analytics',
      },
      {
        id: 'caud-020', ts: iso(15 + 200 * 60), actorName: 'Kavya Iyer', actorRole: 'Knowledge Manager', isAi: false, actorHue: 150,
        action: 'Resolved knowledge conflict', kind: 'status',
        entityType: 'Career Pathway', entityId: 'path-022', entityRoute: '/app/kms',
        before: 'Conflict score 38', after: 'Conflict score 5',
        beforeJson: '{\n  "pathwayId": "path-022",\n  "conflictScore": 38,\n  "conflictsWith": "path-009"\n}',
        afterJson: '{\n  "pathwayId": "path-022",\n  "conflictScore": 5,\n  "resolution": "Archived superseded path-009"\n}',
        ip: '10.4.22.29', correlationId: 'cor-6D4548', channel: 'KMS',
      },
    ];
    return e.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  }
}
