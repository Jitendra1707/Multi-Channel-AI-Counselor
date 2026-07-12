import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { FunnelComponent } from '../../shared/ui/funnel.component';
import { BarListComponent, DonutComponent, SparklineComponent } from '../../shared/ui/charts.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { CounselorService } from '../../core/counselor.service';
import { FunnelStage, BarDatum, InsightCard } from '../../domain/models';
import { fmtInt } from '../../shared/util/format';

type CatKey =
  | 'funnel' | 'lead-source' | 'course' | 'region' | 'sentiment' | 'channel' | 'ai';

interface CourseRow {
  course: string;
  leads: number;
  interested: number;
  registered: number;
  conversionPct: number;
  scope: string;
  trend: number[];
}

@Component({
  selector: 'va-analytics',
  standalone: true,
  imports: [
    IconComponent, FunnelComponent, BarListComponent, DonutComponent,
    SparklineComponent, PageHeaderComponent, SectionCardComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page page-grid">
      <va-page-header
        title="Analytics & Insights"
        [subtitle]="subtitle()">
        <span class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> Approved-knowledge analytics</span>
        <div class="seg range-seg">
          @for (r of ranges; track r.k) {
            <button [class.active]="range() === r.k" (click)="range.set(r.k)">{{ r.l }}</button>
          }
        </div>
        <button class="btn btn-ghost" (click)="export()">
          <va-icon name="download" [size]="16"></va-icon><span class="hide-xs">Export</span>
        </button>
      </va-page-header>

      <!-- Drill-down breadcrumb -->
      <div class="surface drill" role="navigation" aria-label="Drill-down scope">
        <span class="t-cap t-muted drill-lead">Scope</span>
        @for (d of drillPath; track d.level; let last = $last; let i = $index) {
          <button class="crumb" [class.active]="last" (click)="drillTo(i)" [attr.title]="'Filter to ' + d.label">
            <va-icon [name]="d.icon" [size]="14"></va-icon>
            <span class="stack crumb-text">
              <span class="t-cap t-muted">{{ d.level }}</span>
              <span class="crumb-name">{{ d.label }}</span>
            </span>
          </button>
          @if (!last) { <va-icon class="sep" name="chevron-right" [size]="14"></va-icon> }
        }
        <span class="grow"></span>
        <span class="chip live-chip"><span class="dot live"></span> Live · {{ asOf }}</span>
      </div>

      <!-- Category selector -->
      <div class="cat-bar wrap row gap-2">
        @for (c of categories; track c.key) {
          <button class="cat" [class.active]="category() === c.key" (click)="setCategory(c.key)">
            <va-icon [name]="c.icon" [size]="15"></va-icon>{{ c.label }}
          </button>
        }
      </div>

      <!-- Summary tiles for the active category -->
      <section class="kpi-strip">
        @for (t of activeTiles(); track t.label) {
          <div class="surface stat" [attr.data-tone]="t.tone">
            <div class="stat-top">
              <span class="t-cap t-muted">{{ t.label }}</span>
              @if (t.tone === 'ai') { <span class="ai-dot" title="AI metric"></span> }
            </div>
            <div class="stat-val t-num">{{ t.value }}</div>
            <div class="stat-foot">
              <span class="delta" [class.neg]="t.delta < 0">
                <va-icon [name]="t.delta < 0 ? 'arrow-down' : 'arrow-up'" [size]="12"></va-icon>{{ absDelta(t.delta) }}%
              </span>
              <span class="stat-spark"><va-sparkline [data]="t.trend" [color]="t.spark" [height]="28"></va-sparkline></span>
            </div>
          </div>
        }
      </section>

      <!-- Body: chart area + AI insight rail -->
      <div class="an-body">
        <div class="an-main">
          <!-- ===== Chart area, switched by category ===== -->
          @switch (category()) {
            @case ('funnel') {
              <va-section-card [title]="career() ? 'Career pathway funnel' : 'Admissions funnel'" hint="Click any stage to drill into candidates">
                <button actions class="btn btn-sm btn-ghost" (click)="go('/app/crm')">
                  View candidates <va-icon name="arrow-up-right" [size]="14"></va-icon>
                </button>
                <va-funnel [stages]="funnel()" (stageClick)="drillStage($event)"></va-funnel>
              </va-section-card>
            }
            @case ('lead-source') {
              <va-section-card title="Lead-source performance" hint="Volume · conversion — click a bar to drill">
                <div class="clickable-bars" (click)="drillBar('lead source')">
                  <va-bar-list [data]="leadSources()"></va-bar-list>
                </div>
              </va-section-card>
            }
            @case ('course') {
              <va-section-card [title]="career() ? 'Top career interests' : 'Course-wise demand'" hint="Applicant interest by program">
                <div class="clickable-bars" (click)="drillBar('course demand')">
                  <va-bar-list [data]="courseDemand()"></va-bar-list>
                </div>
              </va-section-card>
            }
            @case ('region') {
              <va-section-card title="Region-wise demand" hint="Heatmap by applicant index">
                <div class="heatmap">
                  @for (r of regions; track r.name) {
                    <button class="hcell" [attr.data-h]="heat(r.v)" (click)="drillBar('region — ' + r.name)"
                            [attr.title]="r.name + ': ' + r.v + ' index'">
                      <span class="hname">{{ r.name }}</span>
                      <span class="hval t-num">{{ r.v }}</span>
                    </button>
                  }
                </div>
              </va-section-card>
            }
            @case ('sentiment') {
              <va-section-card title="Sentiment distribution" hint="Across all AI-handled conversations">
                <div class="clickable-bars" (click)="drillBar('sentiment')">
                  <va-bar-list [data]="sentimentDist"></va-bar-list>
                </div>
              </va-section-card>
            }
            @case ('channel') {
              <va-section-card title="Channel effectiveness" hint="Conversion contribution by channel">
                <div class="split">
                  <div class="clickable-bars grow" (click)="drillBar('channel effectiveness')">
                    <va-bar-list [data]="channelEffect"></va-bar-list>
                  </div>
                  <va-donut [data]="probabilityDist()" centerLabel="candidates"></va-donut>
                </div>
              </va-section-card>
            }
            @case ('ai') {
              <va-section-card title="AI counselor performance" hint="Aisha — approved-knowledge-only responses">
                <div class="split">
                  <div class="clickable-bars grow" (click)="drillBar('AI performance')">
                    <va-bar-list [data]="aiPerf"></va-bar-list>
                  </div>
                  <va-donut [data]="probabilityDist()" centerLabel="candidates"></va-donut>
                </div>
                <div class="banner ai guard">
                  <va-icon name="shield-check" [size]="16"></va-icon>
                  <span>Aisha answers only from institution-approved knowledge, always discloses it is an AI, and escalates to a human when confidence is low. <b>7.1%</b> of conversations escalated this cycle.</span>
                </div>
              </va-section-card>
            }
          }

          <!-- ===== Drillable detail table (course-wise) ===== -->
          <va-section-card title="Course-wise breakdown" hint="Leads → interested → conversion · click a row to drill" [flush]="true">
            <span actions class="chip">{{ courseRows().length }} programs</span>
            @if (courseRows().length) {
              <div class="scroll-y tbl-wrap">
                <table class="va-table">
                  <thead>
                    <tr>
                      <th>Course</th>
                      <th class="num">Leads</th>
                      <th class="num">Interested</th>
                      <th class="num">Registered</th>
                      <th class="num">Conversion</th>
                      <th>Trend</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (row of courseRows(); track row.course) {
                      <tr (click)="drillRow(row)">
                        <td>
                          <div class="cell-course">
                            <span class="dot-course"></span>
                            <span class="truncate">{{ row.course }}</span>
                          </div>
                        </td>
                        <td class="num t-num">{{ fmt(row.leads) }}</td>
                        <td class="num t-num">{{ fmt(row.interested) }}</td>
                        <td class="num t-num">{{ fmt(row.registered) }}</td>
                        <td class="num">
                          <span class="conv-pill" [attr.data-band]="convBand(row.conversionPct)">{{ row.conversionPct.toFixed(1) }}%</span>
                        </td>
                        <td class="trend-cell"><va-sparkline [data]="row.trend" color="var(--color-primary)" [height]="24"></va-sparkline></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <p class="t-sm t-muted center empty-row">No course data for this scope.</p>
            }
          </va-section-card>
        </div>

        <!-- ===== AI insight cards rail (the highlight) ===== -->
        <aside class="an-rail">
          <va-section-card title="AI-narrated insights" hint="Generated from live, approved data">
            <span actions class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> Aisha</span>
            <div class="insights">
              @for (i of insights(); track i.id) {
                <button class="insight" [attr.data-tone]="i.tone" (click)="openInsight(i)">
                  <span class="ins-ic"><va-icon [name]="insightIcon(i)" [size]="16"></va-icon></span>
                  <span class="ins-body">
                    <span class="ins-text">{{ i.narrative }}</span>
                    <span class="ins-scope t-cap">
                      <va-icon name="map-pin" [size]="11"></va-icon>{{ i.scope }}
                    </span>
                  </span>
                  <va-icon class="ins-go" name="arrow-up-right" [size]="14"></va-icon>
                </button>
              } @empty {
                <p class="t-sm t-muted center">No insights for this scope yet.</p>
              }
            </div>
          </va-section-card>

          <va-section-card title="Data confidence" hint="Coverage of approved knowledge">
            <div class="conf-list">
              @for (c of confidence; track c.label) {
                <div class="conf">
                  <div class="between conf-top">
                    <span class="t-sm">{{ c.label }}</span>
                    <span class="t-sm t-num conf-val">{{ c.value }}%</span>
                  </div>
                  <div class="progress" [class.ai]="c.ai">
                    <span [style.width.%]="c.value"></span>
                  </div>
                </div>
              }
            </div>
          </va-section-card>
        </aside>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .hide-xs { }

    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .range-seg button { white-space: nowrap; }
    .live-chip { background: var(--color-surface); }
    .live-chip .dot { width: 8px; height: 8px; }

    /* Drill-down breadcrumb */
    .drill { display: flex; align-items: center; gap: 4px; padding: 10px 14px; flex-wrap: wrap; }
    .drill-lead { text-transform: uppercase; letter-spacing: .05em; margin-right: 6px; }
    .crumb { display: inline-flex; align-items: center; gap: 8px; background: transparent; border: 1px solid transparent;
      border-radius: var(--r-md); padding: 5px 10px; color: var(--color-text); transition: background .15s, border-color .15s; }
    .crumb:hover { background: var(--color-surface-alt); }
    .crumb.active { background: rgba(var(--color-primary-rgb), .08); border-color: rgba(var(--color-primary-rgb), .2); }
    .crumb va-icon { color: var(--color-text-muted); flex: none; }
    .crumb.active va-icon { color: var(--color-primary); }
    .crumb-text { gap: 0; line-height: 1.1; text-align: left; }
    .crumb-name { font-size: var(--text-sm); font-weight: 600; }
    .sep { color: var(--color-border-strong); flex: none; }

    /* Category selector */
    .cat-bar { gap: 8px; }
    .cat { display: inline-flex; align-items: center; gap: 7px; padding: 8px 14px; border-radius: var(--r-pill);
      border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text-muted);
      font-size: var(--text-sm); font-weight: 600; transition: all .15s ease; white-space: nowrap; }
    .cat:hover { border-color: var(--color-border-strong); color: var(--color-text); }
    .cat va-icon { flex: none; }
    .cat.active { background: var(--color-primary); border-color: var(--color-primary); color: #fff; box-shadow: var(--e1); }

    /* KPI strip */
    .kpi-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .stat { position: relative; padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; overflow: hidden; }
    .stat::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--color-border); }
    .stat[data-tone='success']::before { background: var(--color-success); }
    .stat[data-tone='warning']::before { background: var(--color-warning); }
    .stat[data-tone='ai']::before { background: var(--gradient-ai); }
    .stat-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .ai-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gradient-ai); flex: none; }
    .stat-val { font-size: 1.65rem; font-weight: 700; line-height: 1.05; letter-spacing: -.01em; }
    .stat-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .delta { display: inline-flex; align-items: center; gap: 2px; font-size: var(--text-cap); font-weight: 700;
      color: var(--color-success); background: var(--color-success-soft); padding: 3px 7px; border-radius: var(--r-pill); }
    .delta.neg { color: var(--color-danger); background: var(--color-danger-soft); }
    .stat-spark { width: 84px; height: 28px; flex: none; opacity: .9; }

    /* Body layout */
    .an-body { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 18px; align-items: start; }
    .an-main { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
    .an-rail { display: flex; flex-direction: column; gap: 18px; position: sticky; top: 0; }

    .clickable-bars { cursor: pointer; }
    .split { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
    .split .grow { min-width: 240px; }
    .guard { margin-top: 16px; align-items: center; }
    .guard va-icon { color: var(--color-accent-2); flex: none; }

    /* Region heatmap */
    .heatmap { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .hcell { text-align: left; border-radius: var(--r-md); padding: 12px; display: flex; flex-direction: column; gap: 4px;
      border: 1px solid var(--color-border); background: var(--color-surface-alt); transition: transform .12s ease, box-shadow .15s ease; cursor: pointer; }
    .hcell:hover { transform: translateY(-2px); box-shadow: var(--e2); }
    .hcell .hname { font-size: var(--text-cap); font-weight: 600; }
    .hcell .hval { font-size: var(--text-h4); font-weight: 700; }
    .hcell[data-h='h1'] { background: color-mix(in srgb, var(--color-accent) 12%, var(--color-surface)); border-color: color-mix(in srgb, var(--color-accent) 25%, var(--color-border)); }
    .hcell[data-h='h2'] { background: color-mix(in srgb, var(--color-accent) 26%, var(--color-surface)); border-color: color-mix(in srgb, var(--color-accent) 40%, var(--color-border)); }
    .hcell[data-h='h3'] { background: var(--gradient-ai); color: #06121A; border-color: transparent; }
    .hcell[data-h='h3'] .hname { color: #06121A; }

    /* Detail table */
    .tbl-wrap { max-height: 360px; }
    .cell-course { display: flex; align-items: center; gap: 10px; min-width: 0; font-weight: 600; }
    .dot-course { width: 8px; height: 8px; border-radius: 50%; background: var(--gradient-ai); flex: none; }
    .conv-pill { display: inline-flex; font-size: var(--text-cap); font-weight: 700; padding: 3px 8px; border-radius: var(--r-pill); font-variant-numeric: tabular-nums; }
    .conv-pill[data-band='low'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .conv-pill[data-band='med'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .conv-pill[data-band='high'] { background: var(--color-success-soft); color: var(--color-success); }
    .trend-cell { width: 90px; }
    .empty-row { padding: 24px; }

    /* Insight cards */
    .insights { display: flex; flex-direction: column; gap: 10px; }
    .insight { display: flex; gap: 11px; padding: 13px 14px; border-radius: var(--r-md); border: 1px solid var(--color-border);
      background: var(--color-surface-2); text-align: left; align-items: flex-start; transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
    .insight:hover { transform: translateY(-2px); box-shadow: var(--e2); border-color: var(--color-border-strong); }
    .ins-ic { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; flex: none;
      background: rgba(var(--color-accent-2-rgb), .1); color: var(--color-accent-2); }
    .insight[data-tone='warning'] { border-color: color-mix(in srgb, var(--color-warning) 30%, var(--color-border)); }
    .insight[data-tone='warning'] .ins-ic { background: var(--color-warning-soft); color: var(--color-warning); }
    .insight[data-tone='positive'] .ins-ic { background: var(--color-success-soft); color: var(--color-success); }
    .ins-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
    .ins-text { font-size: var(--text-sm); line-height: 1.4; }
    .ins-scope { display: inline-flex; align-items: center; gap: 4px; color: var(--color-text-muted); font-weight: 600; }
    .ins-scope va-icon { flex: none; }
    .ins-go { color: var(--color-text-muted); flex: none; margin-top: 2px; }
    .insight:hover .ins-go { color: var(--color-primary); }

    /* Confidence */
    .conf-list { display: flex; flex-direction: column; gap: 14px; }
    .conf-top { margin-bottom: 6px; }
    .conf-val { font-weight: 700; }

    @media (max-width: 1200px) {
      .kpi-strip { grid-template-columns: repeat(2, 1fr); }
      .an-body { grid-template-columns: 1fr; }
      .an-rail { position: static; }
    }
    @media (max-width: 720px) {
      .kpi-strip { grid-template-columns: 1fr; }
      .heatmap { grid-template-columns: repeat(2, 1fr); }
      .hide-xs { display: none; }
    }
  `],
})
export class AnalyticsComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');

  range = signal<'7d' | '30d' | 'cycle'>('30d');
  ranges = [{ k: '7d', l: '7 days' }, { k: '30d', l: '30 days' }, { k: 'cycle', l: 'Full cycle' }] as const;

  category = signal<CatKey>('funnel');
  get categories(): { key: CatKey; label: string; icon: string }[] {
    const c = this.career();
    return [
      { key: 'funnel', label: c ? 'Career pathway funnel' : 'Admissions funnel', icon: 'git-branch' },
      { key: 'lead-source', label: 'Lead source', icon: 'users' },
      { key: 'course', label: c ? 'Career interests' : 'Course demand', icon: c ? 'compass' : 'book-open' },
      { key: 'region', label: 'Region demand', icon: 'map-pin' },
      { key: 'sentiment', label: 'Sentiment', icon: 'smile' },
      { key: 'channel', label: 'Channel effectiveness', icon: 'headphones' },
      { key: 'ai', label: c ? 'Career AI performance' : 'AI performance', icon: c ? 'compass' : 'bot' },
    ];
  }

  asOf = '14 Jun, 9:30 AM';

  drillPath = [
    { level: 'Institution', label: 'Northgate University', icon: 'building' },
    { level: 'Campus', label: 'Main Campus', icon: 'map-pin' },
    { level: 'Department', label: 'Engineering', icon: 'layers' },
    { level: 'Course', label: 'B.Tech AI & Data Science', icon: 'graduation-cap' },
  ];

  // ---- store-backed chart data (counselor-aware) ----
  funnel = computed(() => this.career() ? this.store.careerFunnel() : this.store.funnel());
  leadSources = this.store.leadSources;
  courseDemand = computed(() => this.career() ? this.store.careerInterests() : this.store.courseDemand());
  probabilityDist = computed(() => this.career() ? this.store.careerReadiness() : this.store.probabilityDist());
  insights = computed(() => this.career() ? this.store.careerInsights() : this.store.insights());

  // ---- inline realistic datasets ----
  sentimentDist: BarDatum[] = [
    { label: 'Very positive', value: 1240, sub: '28% of convos', tone: 'high' },
    { label: 'Positive', value: 1690, sub: '38% of convos', tone: 'high' },
    { label: 'Neutral', value: 980, sub: '22% of convos', tone: 'med' },
    { label: 'Negative', value: 380, sub: '9% of convos', tone: 'low' },
    { label: 'Very negative', value: 130, sub: '3% · routed to humans', tone: 'low' },
  ];

  channelEffect: BarDatum[] = [
    { label: 'WhatsApp', value: 1420, sub: '18.2% conv', tone: 'high' },
    { label: 'Voice (Aisha)', value: 1180, sub: '14.6% conv', tone: 'ai' },
    { label: 'V-Con', value: 540, sub: '31.4% conv', tone: 'high' },
    { label: 'Email', value: 760, sub: '7.9% conv', tone: 'med' },
    { label: 'Web chat', value: 410, sub: '9.3% conv', tone: 'med' },
  ];

  aiPerf: BarDatum[] = [
    { label: 'Answered from approved KB', value: 9210, sub: '92.9% of questions', tone: 'ai' },
    { label: 'Escalated to human', value: 704, sub: '7.1% · low confidence', tone: 'med' },
    { label: 'AI disclosure shown', value: 9914, sub: '100% of conversations', tone: 'high' },
    { label: 'Blocked unapproved claim', value: 168, sub: 'fees / placement guardrail', tone: 'low' },
  ];

  regions = [
    { name: 'Telangana', v: 92 }, { name: 'Karnataka', v: 78 }, { name: 'Maharashtra', v: 71 },
    { name: 'Tamil Nadu', v: 64 }, { name: 'Delhi', v: 52 }, { name: 'Kerala', v: 48 },
    { name: 'Gujarat', v: 39 }, { name: 'West Bengal', v: 33 }, { name: 'Rajasthan', v: 28 },
  ];

  confidence = [
    { label: 'Approved knowledge coverage', value: 88, ai: true },
    { label: 'AI confidence average', value: 91, ai: true },
    { label: 'Data freshness', value: 96, ai: false },
    { label: 'Conflict-free documents', value: 82, ai: false },
  ];

  // ---- per-category summary tiles ----
  private tilesByCat: Record<CatKey, { label: string; value: string; delta: number; tone: string; spark: string; trend: number[] }[]> = {
    'funnel': [
      { label: 'Total leads', value: '4,820', delta: 12.4, tone: 'default', spark: 'var(--color-primary)', trend: [38, 41, 44, 43, 47, 49, 48] },
      { label: 'Interested', value: '2,188', delta: 15.2, tone: 'ai', spark: 'var(--color-accent)', trend: [16, 18, 17, 20, 21, 22, 21] },
      { label: 'Registered', value: '612', delta: 9.7, tone: 'success', spark: 'var(--color-success)', trend: [5.0, 5.4, 5.6, 5.9, 6.0, 6.1, 6.1] },
      { label: 'Admitted', value: '154', delta: 18.9, tone: 'success', spark: 'var(--color-success)', trend: [1.1, 1.2, 1.3, 1.3, 1.4, 1.5, 1.5] },
    ],
    'lead-source': [
      { label: 'Active sources', value: '9', delta: 0, tone: 'default', spark: 'var(--color-primary)', trend: [7, 8, 8, 9, 9, 9, 9] },
      { label: 'Best converting', value: 'School Partner', delta: 16.4, tone: 'success', spark: 'var(--color-success)', trend: [12, 13, 14, 15, 15, 16, 16] },
      { label: 'Highest volume', value: 'Website', delta: 8.2, tone: 'default', spark: 'var(--color-primary)', trend: [11, 12, 13, 13, 14, 14, 14] },
      { label: 'Cost per lead', value: '₹ 214', delta: -6.3, tone: 'success', spark: 'var(--color-success)', trend: [26, 24, 23, 22, 22, 21, 21] },
    ],
    'course': [
      { label: 'Programs tracked', value: '12', delta: 0, tone: 'default', spark: 'var(--color-primary)', trend: [10, 11, 11, 12, 12, 12, 12] },
      { label: 'Top demand', value: 'B.Tech AI & DS', delta: 22.0, tone: 'ai', spark: 'var(--color-accent)', trend: [9, 10, 10, 11, 11, 12, 12] },
      { label: 'Fastest growing', value: 'B.Sc Data Sci', delta: 31.5, tone: 'ai', spark: 'var(--color-accent)', trend: [3, 4, 4, 5, 5, 5, 6] },
      { label: 'Highest drop-off', value: 'MBA', delta: -4.4, tone: 'warning', spark: 'var(--color-warning)', trend: [42, 44, 45, 47, 46, 48, 47] },
    ],
    'region': [
      { label: 'States with leads', value: '12', delta: 4.0, tone: 'default', spark: 'var(--color-primary)', trend: [9, 10, 11, 11, 12, 12, 12] },
      { label: 'Top region', value: 'Telangana', delta: 14.0, tone: 'ai', spark: 'var(--color-accent)', trend: [78, 82, 85, 88, 90, 91, 92] },
      { label: 'Emerging region', value: 'Gujarat', delta: 27.0, tone: 'success', spark: 'var(--color-success)', trend: [22, 24, 28, 31, 34, 37, 39] },
      { label: 'Out-of-state %', value: '34.2%', delta: 3.1, tone: 'default', spark: 'var(--color-primary)', trend: [29, 30, 31, 32, 33, 34, 34] },
    ],
    'sentiment': [
      { label: 'Positive share', value: '66%', delta: 5.4, tone: 'success', spark: 'var(--color-success)', trend: [58, 60, 61, 63, 64, 65, 66] },
      { label: 'Negative share', value: '12%', delta: -3.2, tone: 'success', spark: 'var(--color-success)', trend: [18, 17, 16, 15, 14, 13, 12] },
      { label: 'Avg sentiment', value: '+0.42', delta: 6.1, tone: 'ai', spark: 'var(--color-accent)', trend: [30, 33, 35, 37, 39, 41, 42] },
      { label: 'Distress flagged', value: '11', delta: -18.0, tone: 'warning', spark: 'var(--color-warning)', trend: [18, 16, 15, 14, 13, 12, 11] },
    ],
    'channel': [
      { label: 'Channels live', value: '5', delta: 0, tone: 'default', spark: 'var(--color-primary)', trend: [4, 4, 5, 5, 5, 5, 5] },
      { label: 'Best converting', value: 'V-Con', delta: 9.0, tone: 'success', spark: 'var(--color-success)', trend: [26, 27, 28, 29, 30, 31, 31] },
      { label: 'Highest volume', value: 'WhatsApp', delta: 12.4, tone: 'default', spark: 'var(--color-primary)', trend: [11, 12, 12, 13, 14, 14, 14] },
      { label: 'WhatsApp vs email', value: '1.8×', delta: 4.2, tone: 'ai', spark: 'var(--color-accent)', trend: [15, 16, 16, 17, 17, 18, 18] },
    ],
    'ai': [
      { label: 'AI confidence avg', value: '91%', delta: 1.4, tone: 'ai', spark: 'var(--color-accent)', trend: [86, 87, 88, 89, 90, 90, 91] },
      { label: 'Answered from KB', value: '92.9%', delta: 2.8, tone: 'ai', spark: 'var(--color-accent)', trend: [88, 89, 90, 91, 92, 92, 93] },
      { label: 'Escalation rate', value: '7.1%', delta: -14.0, tone: 'success', spark: 'var(--color-success)', trend: [12, 11, 10, 9, 8, 8, 7] },
      { label: 'Knowledge gaps', value: '7', delta: -30.0, tone: 'warning', spark: 'var(--color-warning)', trend: [12, 11, 10, 9, 8, 8, 7] },
    ],
  };
  activeTiles = computed(() => this.tilesByCat[this.category()]);

  // ---- detail table ----
  courseRows = computed<CourseRow[]>(() => {
    const trends = [
      [9, 10, 10, 11, 11, 12, 12], [8, 9, 9, 9, 10, 9, 10], [7, 8, 8, 9, 9, 9, 9],
      [4, 4, 5, 5, 5, 5, 6], [3, 4, 4, 4, 4, 4, 4], [3, 3, 4, 4, 4, 4, 4],
    ];
    return this.courseDemand().map((d, i) => {
      const leads = d.value;
      const interested = Math.round(leads * (0.42 + (i % 3) * 0.04));
      const registered = Math.round(interested * (0.30 + (i % 4) * 0.03));
      const conversionPct = Math.round((registered / leads) * 1000) / 10;
      return {
        course: d.label, leads, interested, registered, conversionPct,
        scope: d.label, trend: trends[i % trends.length],
      };
    });
  });

  subtitle = computed(() =>
    `${this.counselor.activeMeta().name} · ${this.counselor.activeMeta().title} — ${this.auth.institution().name} · ${this.auth.admissionCycle()} · ${this.rangeLabel()}`);

  rangeLabel = computed(() => this.ranges.find(r => r.k === this.range())?.l ?? '');

  fmt = fmtInt;
  absDelta(d: number) { return Math.abs(d).toFixed(1); }
  heat(v: number) { return v >= 75 ? 'h3' : v >= 55 ? 'h2' : v >= 35 ? 'h1' : 'h0'; }
  convBand(p: number) { return p >= 8 ? 'high' : p >= 4 ? 'med' : 'low'; }
  insightIcon(i: InsightCard) { return i.tone === 'warning' ? 'alert-triangle' : 'sparkles'; }

  setCategory(c: CatKey) {
    this.category.set(c);
    const label = this.categories.find(x => x.key === c)?.label ?? '';
    this.toast.info(`Drilling into ${label} analytics`, 'bar-chart');
  }

  drillTo(i: number) {
    this.toast.info(`Scoping analytics to ${this.drillPath[i].level}: ${this.drillPath[i].label}`, 'filter');
  }
  /** Drill destinations resolve to actual records, scoped to the active counselor. */
  private peopleRoute() { return '/app/crm'; }
  drillStage(s: FunnelStage) {
    this.toast.info(`${s.label} — ${s.count.toLocaleString('en-IN')} ${this.career() ? 'students' : 'candidates'}`, 'git-branch');
    this.router.navigateByUrl(this.peopleRoute());
  }
  drillBar(what: string) {
    this.toast.info(`Opening ${what}`, 'bar-chart');
    this.router.navigateByUrl(this.career() ? '/app/career/pathways' : this.peopleRoute());
  }
  drillRow(r: CourseRow) {
    this.toast.info(`${r.course} — ${r.conversionPct.toFixed(1)}% ${this.career() ? 'readiness' : 'conversion'}`, 'book-open');
    this.router.navigateByUrl(this.career() ? '/app/career/pathways' : this.peopleRoute());
  }
  openInsight(i: InsightCard) {
    this.toast.info(`Insight: ${i.scope}`, 'sparkles');
    this.router.navigateByUrl('/app/learning-review');
  }

  go(url: string) { this.router.navigateByUrl(url); }
  export() { this.toast.success('Analytics export queued — you’ll be notified when the report is ready.', 'download'); }
}
