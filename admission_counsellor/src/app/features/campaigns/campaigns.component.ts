import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { MetricCardComponent } from '../../shared/ui/metric-card.component';
import { SparklineComponent } from '../../shared/ui/charts.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { FilterBarComponent } from '../../shared/ui/filter-bar.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { Channel, Metric } from '../../domain/models';
import { CHANNEL_ICON, CHANNEL_LABEL, fmtInt, relTime, relFuture, fmtDate } from '../../shared/util/format';

type CampaignStatus = 'Active' | 'Scheduled' | 'Completed' | 'Draft';
type CampaignChannel = Extract<Channel, 'voice' | 'whatsapp' | 'email'>;

interface CampaignTemplate {
  id: string;
  name: string;
  channel: CampaignChannel;
  approval: 'approved' | 'pending' | 'draft';
}

interface Campaign {
  id: string;
  name: string;
  goal: string;
  channels: CampaignChannel[];
  segment: string;
  audience: number;
  status: CampaignStatus;
  scheduleAt: string;          // ISO — launch / next send
  templateId: string;
  templateApproval: 'approved' | 'pending' | 'draft';
  owner: string;
  /* performance */
  sent: number;
  delivered: number;
  read: number;                // reads / opens
  replied: number;
  converted: number;
  trend: number[];
}

@Component({
  selector: 'va-campaigns',
  standalone: true,
  imports: [
    IconComponent, MetricCardComponent, SparklineComponent, SectionCardComponent,
    EmptyStateComponent, DrawerComponent, FilterBarComponent, ApprovalChipComponent, AiAvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="page page-grid">
  <!-- Header -->
  <header class="cm-head">
    <div>
      <div class="t-h2">Campaigns &amp; Follow-Ups</div>
      <p class="t-sm t-muted">
        Plan, schedule and measure omnichannel outreach for <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }} —
        every message is sent by <b>{{ counselor.activeMeta().name }}</b> ({{ counselor.activeMeta().title }}) using approved-knowledge-only templates.
      </p>
    </div>
    <div class="cm-actions">
      <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
      <button class="btn btn-ghost" (click)="go('/app/learning-review')" title="Approval-governed templates">
        <va-icon name="shield-check" [size]="16"></va-icon><span class="hide-xs">Template governance</span>
      </button>
      <button class="btn btn-primary" (click)="openWizard()">
        <va-icon name="plus" [size]="16"></va-icon> New campaign
      </button>
    </div>
  </header>

  <!-- Governance banner -->
  <div class="banner ai gov-banner">
    <va-icon name="shield-check" [size]="18"></va-icon>
    <span>
      Outreach templates are <b>approval-governed</b>. {{ counselor.activeMeta().name }} only sends messages from compliance-approved content,
      always discloses it is an AI, and never {{ career() ? 'guarantees a job, salary or placement' : 'invents fees, scholarships or placement claims' }}.
    </span>
    <button class="btn btn-sm btn-ghost" (click)="go('/app/approvals')">
      Template approvals <va-icon name="arrow-right" [size]="14"></va-icon>
    </button>
  </div>

  <!-- Summary KPI row -->
  <section class="kpis">
    @for (m of summary(); track m.key) { <va-metric-card [metric]="m"></va-metric-card> }
  </section>

  <!-- Filter bar -->
  <va-filter-bar
    [query]="query()"
    placeholder="Search campaigns…"
    [savedViews]="views"
    [activeView]="statusFilter()"
    (queryChange)="query.set($event)"
    (selectView)="statusFilter.set($event)">
    <div filters class="row gap-2 wrap">
      <span class="t-cap t-muted ch-lab">Channels</span>
      @for (c of channelKeys; track c) {
        <button class="ch-toggle" [attr.data-ch]="c" [class.on]="channelFilter().includes(c)" (click)="toggleChannelFilter(c)">
          <va-icon [name]="chIcon(c)" [size]="13"></va-icon>{{ chLabel(c) }}
        </button>
      }
    </div>
    <button actions class="btn btn-ghost btn-sm" (click)="resetFilters()" [disabled]="!filtersActive()">
      <va-icon name="refresh" [size]="14"></va-icon> Reset
    </button>
  </va-filter-bar>

  <!-- Campaign grid -->
  @if (filtered().length) {
    <section class="grid">
      @for (c of filtered(); track c.id) {
        <article class="camp" [attr.data-status]="c.status">
          <div class="camp-top">
            <div class="camp-id">
              <div class="t-h4 camp-name truncate">{{ c.name }}</div>
              <span class="t-cap t-muted">{{ c.goal }}</span>
            </div>
            <span class="stat" [attr.data-s]="c.status">
              @if (c.status === 'Active') { <span class="dot live pulse"></span> }
              {{ c.status }}
            </span>
          </div>

          <!-- channel chips -->
          <div class="row gap-1 wrap">
            @for (ch of c.channels; track ch) {
              <span class="ch-chip" [attr.data-ch]="ch"><va-icon [name]="chIcon(ch)" [size]="12"></va-icon>{{ chLabel(ch) }}</span>
            }
          </div>

          <!-- segment + schedule -->
          <div class="meta">
            <div class="meta-row">
              <va-icon name="users" [size]="14"></va-icon>
              <span class="truncate">{{ c.segment }}</span>
              <span class="t-num t-muted">· {{ fmtInt(c.audience) }} in audience</span>
            </div>
            <div class="meta-row">
              <va-icon name="calendar" [size]="14"></va-icon>
              <span>{{ scheduleLabel(c) }}</span>
              <span class="t-cap t-muted">· {{ fmtDate(c.scheduleAt) }}</span>
            </div>
            <div class="meta-row">
              <va-icon name="file-check" [size]="14"></va-icon>
              <span class="truncate">{{ templateName(c.templateId) }}</span>
              <va-approval-chip [state]="c.templateApproval"></va-approval-chip>
            </div>
          </div>

          <!-- performance -->
          @if (c.status === 'Draft') {
            <div class="draft-note">
              <va-icon name="edit" [size]="14"></va-icon>
              <span class="t-sm t-muted">Not launched yet — awaiting template approval &amp; schedule.</span>
            </div>
          } @else {
            <div class="perf">
              <div class="perf-tiles">
                <div class="ptile"><span class="pv t-num">{{ fmtInt(c.sent) }}</span><span class="pl">Sent</span></div>
                <div class="ptile"><span class="pv t-num">{{ pct(c.delivered, c.sent) }}%</span><span class="pl">Delivered</span></div>
                <div class="ptile accent"><span class="pv t-num">{{ pct(c.read, c.delivered) }}%</span><span class="pl">{{ readLabel(c) }}</span></div>
                <div class="ptile"><span class="pv t-num">{{ fmtInt(c.replied) }}</span><span class="pl">Replied</span></div>
                <div class="ptile good"><span class="pv t-num">{{ fmtInt(c.converted) }}</span><span class="pl">Converted</span></div>
              </div>
              <div class="perf-chart">
                <div class="row between">
                  <span class="t-cap t-muted">{{ readLabel(c) }} rate trend</span>
                  <span class="t-cap rate" [attr.data-band]="rateBand(c)">{{ pct(c.read, c.delivered) }}%</span>
                </div>
                <div class="spark"><va-sparkline [data]="c.trend" [color]="chColor(c.channels[0])" [height]="34"></va-sparkline></div>
                <div class="progress" [class.success]="rateBand(c) === 'high'">
                  <span [style.width.%]="pct(c.read, c.delivered)"></span>
                </div>
              </div>
            </div>
          }

          <!-- footer -->
          <div class="camp-foot">
            <span class="t-cap t-muted">Owner · {{ c.owner }}</span>
            <div class="row gap-1">
              <button class="btn btn-sm btn-ghost" (click)="viewReport(c)"><va-icon name="bar-chart" [size]="14"></va-icon>Report</button>
              @if (c.status === 'Active') {
                <button class="btn btn-sm btn-subtle" (click)="pause(c)"><va-icon name="pause" [size]="14"></va-icon>Pause</button>
              } @else if (c.status === 'Scheduled' || c.status === 'Draft') {
                <button class="btn btn-sm btn-accent" (click)="launchExisting(c)"><va-icon name="rocket" [size]="14"></va-icon>Launch</button>
              } @else {
                <button class="btn btn-sm btn-ghost" (click)="duplicate(c)"><va-icon name="refresh" [size]="14"></va-icon>Re-run</button>
              }
            </div>
          </div>
        </article>
      }
    </section>
  } @else {
    <va-section-card [flush]="true">
      <va-empty
        icon="megaphone"
        title="No campaigns match your filters"
        message="Try clearing the channel or status filters, or launch a new approval-governed outreach campaign."
        cta="New campaign"
        ctaIcon="plus"
        (action)="openWizard()"></va-empty>
    </va-section-card>
  }
</div>

<!-- ===========  New campaign wizard  =========== -->
<va-drawer
  [open]="wizardOpen()"
  title="New campaign"
  [subtitle]="'Approval-governed outreach · ' + counselor.activeMeta().name"
  [width]="500"
  (close)="closeWizard()">

  <div class="wiz">
    <div class="banner ai wiz-banner">
      <va-icon name="sparkles" [size]="16"></va-icon>
      <span class="t-sm">{{ counselor.activeMeta().name }} will only send from <b>approved templates</b> and discloses it is an AI in every message.</span>
    </div>

    <div class="field">
      <label class="label">Campaign name</label>
      <input class="input" [value]="draftName()" (input)="draftName.set($any($event.target).value)"
             [attr.placeholder]="career() ? 'e.g. 2026 — Career-fair invites (WhatsApp)' : 'e.g. Fall 2026 — MBA WhatsApp nudge'" />
    </div>

    <div class="field">
      <label class="label">Channels</label>
      <div class="row gap-2 wrap">
        @for (c of channelKeys; track c) {
          <button class="ch-toggle big" [attr.data-ch]="c" [class.on]="draftChannels().includes(c)" (click)="toggleDraftChannel(c)">
            <va-icon [name]="chIcon(c)" [size]="15"></va-icon>{{ chLabel(c) }}
            @if (draftChannels().includes(c)) { <va-icon name="check" [size]="13"></va-icon> }
          </button>
        }
      </div>
      <span class="t-cap t-muted">{{ counselor.activeMeta().name }} sequences messages across the selected channels with consent-aware fallbacks.</span>
    </div>

    <div class="field">
      <label class="label">Audience segment</label>
      <select class="select" [value]="draftSegment()" (change)="draftSegment.set($any($event.target).value)">
        @for (s of segments; track s) { <option [value]="s">{{ s }}</option> }
      </select>
    </div>

    <div class="grid-2">
      <div class="field">
        <label class="label">Send / schedule</label>
        <select class="select" [value]="draftSchedule()" (change)="draftSchedule.set($any($event.target).value)">
          @for (s of scheduleOptions; track s) { <option [value]="s">{{ s }}</option> }
        </select>
      </div>
      <div class="field">
        <label class="label">Approved template</label>
        <select class="select" [value]="draftTemplate()" (change)="draftTemplate.set($any($event.target).value)">
          @for (t of templatesForDraft(); track t.id) { <option [value]="t.id">{{ t.name }}</option> }
        </select>
      </div>
    </div>

    @if (selectedTemplate(); as t) {
      <div class="tpl-state" [attr.data-s]="t.approval">
        <va-approval-chip [state]="t.approval"></va-approval-chip>
        <span class="t-sm">
          @if (t.approval === 'approved') { Template is approved for {{ chLabel(t.channel) }} — ready to launch. }
          @else if (t.approval === 'pending') { Awaiting compliance sign-off — campaign saves as a draft until approved. }
          @else { Draft template — submit for approval before this campaign can send. }
        </span>
      </div>
    }

    <div class="banner warning wiz-note">
      <va-icon name="info" [size]="16"></va-icon>
      <span class="t-sm">Reaches an estimated <b>{{ fmtInt(estimatedAudience()) }}</b> consented contacts. Opt-outs and do-not-contact flags are honoured automatically.</span>
    </div>
  </div>

  <div footer class="row gap-2 grow">
    <button class="btn btn-ghost grow" (click)="closeWizard()">Cancel</button>
    <button class="btn btn-accent grow" [disabled]="!canLaunch()" (click)="launch()">
      <va-icon name="rocket" [size]="16"></va-icon> {{ launchLabel() }}
    </button>
  </div>
</va-drawer>
  `,
  styles: [`
    :host { display: block; }
    .hide-xs { }

    .cm-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .cm-head p { margin-top: 4px; max-width: 78ch; }
    .cm-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    .gov-banner { align-items: center; }
    .gov-banner span { flex: 1; }
    .gov-banner va-icon { color: var(--color-accent-2); flex: none; }

    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }

    /* channel filter toggles */
    .ch-lab { margin-right: 2px; }
    .ch-toggle { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600;
      padding: 6px 10px; border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted);
      border: 1px solid var(--color-border); transition: all .15s; }
    .ch-toggle:hover { color: var(--color-text); }
    .ch-toggle.on { color: #fff; border-color: transparent; }
    .ch-toggle.on[data-ch='voice']    { background: var(--ch-voice); }
    .ch-toggle.on[data-ch='whatsapp'] { background: var(--ch-whatsapp); }
    .ch-toggle.on[data-ch='email']    { background: var(--ch-email); }
    .ch-toggle.big { padding: 9px 13px; font-size: var(--text-sm); }

    /* grid */
    .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
    .camp { display: flex; flex-direction: column; gap: 12px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e1); padding: 18px;
      position: relative; overflow: hidden; transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
    .camp::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--color-border); }
    .camp[data-status='Active']::before    { background: var(--gradient-ai); }
    .camp[data-status='Scheduled']::before { background: var(--color-primary); }
    .camp[data-status='Completed']::before { background: var(--color-success); }
    .camp[data-status='Draft']::before     { background: var(--color-border-strong); }
    .camp:hover { transform: translateY(-2px); box-shadow: var(--e2); border-color: var(--color-border-strong); }

    .camp-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .camp-id { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .camp-name { line-height: 1.25; }

    .stat { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700;
      padding: 4px 10px; border-radius: var(--r-pill); white-space: nowrap; flex: none; }
    .stat[data-s='Active']    { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .stat[data-s='Scheduled'] { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .stat[data-s='Completed'] { background: var(--color-success-soft); color: var(--color-success); }
    .stat[data-s='Draft']     { background: var(--color-surface-alt); color: var(--color-text-muted); }

    .ch-chip { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700;
      padding: 4px 9px; border-radius: var(--r-pill); color: #fff; }
    .ch-chip[data-ch='voice']    { background: var(--ch-voice); }
    .ch-chip[data-ch='whatsapp'] { background: var(--ch-whatsapp); }
    .ch-chip[data-ch='email']    { background: var(--ch-email); }

    .meta { display: flex; flex-direction: column; gap: 7px; padding: 12px 0; border-top: 1px solid var(--color-border);
      border-bottom: 1px solid var(--color-border); }
    .meta-row { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); min-width: 0; }
    .meta-row va-icon { color: var(--color-text-muted); flex: none; }
    .meta-row .truncate { font-weight: 500; }

    .draft-note { display: flex; align-items: center; gap: 8px; padding: 16px 14px; border-radius: var(--r-md);
      background: var(--color-surface-2); border: 1px dashed var(--color-border-strong); }
    .draft-note va-icon { color: var(--color-text-muted); flex: none; }

    .perf { display: flex; flex-direction: column; gap: 12px; }
    .perf-tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
    .ptile { display: flex; flex-direction: column; gap: 2px; background: var(--color-surface-alt); border-radius: var(--r-md); padding: 9px 10px; }
    .ptile .pv { font-size: var(--text-h4); font-weight: 700; line-height: 1.1; }
    .ptile .pl { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--color-text-muted); }
    .ptile.accent .pv { color: var(--color-accent-2); }
    .ptile.good .pv { color: var(--color-success); }

    .perf-chart { display: flex; flex-direction: column; gap: 6px; }
    .perf-chart .spark { height: 34px; }
    .rate { font-weight: 700; }
    .rate[data-band='high'] { color: var(--color-success); }
    .rate[data-band='med']  { color: var(--color-warning); }
    .rate[data-band='low']  { color: var(--color-text-muted); }

    .camp-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: auto; padding-top: 4px; }

    /* wizard */
    .wiz { display: flex; flex-direction: column; gap: 16px; }
    .wiz-banner { align-items: center; }
    .wiz-banner va-icon { color: var(--color-accent-2); flex: none; }
    .wiz-note { align-items: center; }
    .wiz-note va-icon { color: var(--color-warning); flex: none; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .tpl-state { display: flex; align-items: center; gap: 10px; padding: 11px 13px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .tpl-state[data-s='approved'] { border-color: color-mix(in srgb, var(--color-success) 35%, var(--color-border)); }
    .tpl-state[data-s='pending']  { border-color: color-mix(in srgb, var(--color-warning) 35%, var(--color-border)); }
    .tpl-state .t-sm { color: var(--color-text-muted); }

    @media (max-width: 1200px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 980px)  { .grid { grid-template-columns: 1fr; } }
    @media (max-width: 720px)  {
      .kpis { grid-template-columns: 1fr 1fr; }
      .perf-tiles { grid-template-columns: repeat(3, 1fr); }
      .grid-2 { grid-template-columns: 1fr; }
      .hide-xs { display: none; }
    }
  `],
})
export class CampaignsComponent {
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');

  /* ----------  reference data  ---------- */
  channelKeys: CampaignChannel[] = ['whatsapp', 'voice', 'email'];
  views = ['Active', 'Scheduled', 'Completed', 'Draft'];

  private admissionSegments = [
    'High-intent — undecided (487)',
    'MBA — fee pending (164)',
    'B.Tech AI & Data Science — interested (612)',
    'Scholarship-interested leads (398)',
    'Parents — concerns raised (143)',
    'Application started — incomplete (211)',
    'Re-engage — dormant 14d+ (905)',
  ];
  private careerSegments = [
    'Final-year — pathway undecided (512)',
    'Skill-gap flagged — upskilling (286)',
    'Career-fair registrants (640)',
    'Mentor-match waitlist (174)',
    'Placement-readiness — low (231)',
    'Assessment completed — awaiting plan (398)',
    'Re-engage — dormant 14d+ (905)',
  ];
  get segments() { return this.career() ? this.careerSegments : this.admissionSegments; }
  scheduleOptions = ['Send now', 'Tomorrow 10:00 AM', 'In 2 days', 'Next Monday 9:00 AM', 'Custom date…'];

  private admissionTemplates: CampaignTemplate[] = [
    { id: 'tpl-wa-mba', name: 'MBA nudge — seats filling (WhatsApp)', channel: 'whatsapp', approval: 'approved' },
    { id: 'tpl-wa-fee', name: 'Fee reminder — gentle (WhatsApp)', channel: 'whatsapp', approval: 'approved' },
    { id: 'tpl-wa-schol', name: 'Scholarship awareness (WhatsApp)', channel: 'whatsapp', approval: 'pending' },
    { id: 'tpl-vo-counsel', name: 'Counselling call-back offer (Voice)', channel: 'voice', approval: 'approved' },
    { id: 'tpl-vo-parent', name: 'Parent reassurance script (Voice)', channel: 'voice', approval: 'approved' },
    { id: 'tpl-em-info', name: 'Programme information pack (Email)', channel: 'email', approval: 'approved' },
    { id: 'tpl-em-deadline', name: 'Application deadline reminder (Email)', channel: 'email', approval: 'pending' },
    { id: 'tpl-em-draft', name: 'Campus tour invite (Email) — draft', channel: 'email', approval: 'draft' },
  ];
  private careerTemplates: CampaignTemplate[] = [
    { id: 'tpl-wa-fair', name: 'Career-fair invite (WhatsApp)', channel: 'whatsapp', approval: 'approved' },
    { id: 'tpl-wa-upskill', name: 'Upskilling-track nudge (WhatsApp)', channel: 'whatsapp', approval: 'approved' },
    { id: 'tpl-wa-mentor', name: 'Mentor-program outreach (WhatsApp)', channel: 'whatsapp', approval: 'pending' },
    { id: 'tpl-vo-pathway', name: 'Pathway counselling call-back (Voice)', channel: 'voice', approval: 'approved' },
    { id: 'tpl-vo-readiness', name: 'Placement-readiness check-in (Voice)', channel: 'voice', approval: 'approved' },
    { id: 'tpl-em-plan', name: 'Personalised skill plan (Email)', channel: 'email', approval: 'approved' },
    { id: 'tpl-em-cert', name: 'Certification track reminder (Email)', channel: 'email', approval: 'pending' },
    { id: 'tpl-em-draft', name: 'Alumni mentor meetup invite (Email) — draft', channel: 'email', approval: 'draft' },
  ];
  get templates() { return this.career() ? this.careerTemplates : this.admissionTemplates; }

  private admissionCampaigns = signal<Campaign[]>([
    {
      id: 'cmp-001', name: 'Fall 2026 — MBA WhatsApp nudge', goal: 'Move fee-pending MBA leads to payment',
      channels: ['whatsapp'], segment: 'MBA — fee pending', audience: 164, status: 'Active',
      scheduleAt: '2026-06-12T10:00:00', templateId: 'tpl-wa-mba', templateApproval: 'approved', owner: 'Aisha · Rahul Desai',
      sent: 1620, delivered: 1583, read: 1192, replied: 437, converted: 86, trend: [54, 58, 61, 63, 67, 70, 72, 75],
    },
    {
      id: 'cmp-002', name: 'Scholarship awareness — Data Science', goal: 'Surface merit & need-based aid to eligible leads',
      channels: ['whatsapp', 'email'], segment: 'Scholarship-interested leads', audience: 398, status: 'Active',
      scheduleAt: '2026-06-13T09:30:00', templateId: 'tpl-em-info', templateApproval: 'approved', owner: 'Aisha · Kavya Iyer',
      sent: 2980, delivered: 2901, read: 1624, replied: 512, converted: 71, trend: [40, 44, 46, 49, 51, 53, 55, 56],
    },
    {
      id: 'cmp-003', name: 'Application fee reminder', goal: 'Recover stalled applications before deadline',
      channels: ['whatsapp', 'voice'], segment: 'Application started — incomplete', audience: 211, status: 'Active',
      scheduleAt: '2026-06-14T08:00:00', templateId: 'tpl-wa-fee', templateApproval: 'approved', owner: 'Aisha · Rahul Desai',
      sent: 844, delivered: 829, read: 690, replied: 298, converted: 119, trend: [70, 72, 75, 78, 80, 82, 81, 83],
    },
    {
      id: 'cmp-004', name: 'Parent information — Engineering', goal: 'Reassure parents on outcomes & safety',
      channels: ['voice', 'email'], segment: 'Parents — concerns raised', audience: 143, status: 'Scheduled',
      scheduleAt: '2026-06-16T11:00:00', templateId: 'tpl-vo-parent', templateApproval: 'approved', owner: 'Aisha · Meera Nair',
      sent: 0, delivered: 0, read: 0, replied: 0, converted: 0, trend: [0, 0, 0, 0, 0, 0, 0, 0],
    },
    {
      id: 'cmp-005', name: 'B.Des UX — portfolio webinar invite', goal: 'Drive registrations for the design open house',
      channels: ['email', 'whatsapp'], segment: 'B.Tech AI & Data Science — interested', audience: 612, status: 'Completed',
      scheduleAt: '2026-06-02T10:00:00', templateId: 'tpl-em-info', templateApproval: 'approved', owner: 'Aisha · Imran Sheikh',
      sent: 3120, delivered: 3047, read: 1980, replied: 604, converted: 142, trend: [48, 52, 55, 58, 60, 63, 64, 65],
    },
    {
      id: 'cmp-006', name: 'Dormant re-engagement — all programmes', goal: 'Revive leads inactive for 14+ days',
      channels: ['whatsapp', 'voice', 'email'], segment: 'Re-engage — dormant 14d+', audience: 905, status: 'Draft',
      scheduleAt: '2026-06-20T09:00:00', templateId: 'tpl-em-deadline', templateApproval: 'pending', owner: 'Aisha · Rahul Desai',
      sent: 0, delivered: 0, read: 0, replied: 0, converted: 0, trend: [0, 0, 0, 0, 0, 0, 0, 0],
    },
  ]);

  private careerCampaigns = signal<Campaign[]>([
    {
      id: 'ccmp-001', name: '2026 — Career-fair invites', goal: 'Drive registrations for the annual career fair',
      channels: ['whatsapp'], segment: 'Career-fair registrants', audience: 640, status: 'Active',
      scheduleAt: '2026-06-12T10:00:00', templateId: 'tpl-wa-fair', templateApproval: 'approved', owner: 'Vera · Rahul Desai',
      sent: 1980, delivered: 1934, read: 1487, replied: 521, converted: 142, trend: [56, 59, 62, 65, 68, 71, 73, 76],
    },
    {
      id: 'ccmp-002', name: 'Upskilling-track nudge — Data & Cloud', goal: 'Move skill-gap students into approved tracks',
      channels: ['whatsapp', 'email'], segment: 'Skill-gap flagged — upskilling', audience: 286, status: 'Active',
      scheduleAt: '2026-06-13T09:30:00', templateId: 'tpl-em-plan', templateApproval: 'approved', owner: 'Vera · Kavya Iyer',
      sent: 2240, delivered: 2188, read: 1310, replied: 446, converted: 98, trend: [42, 45, 48, 50, 52, 54, 55, 57],
    },
    {
      id: 'ccmp-003', name: 'Mentor-program outreach', goal: 'Match waitlisted students to consenting mentors',
      channels: ['whatsapp', 'voice'], segment: 'Mentor-match waitlist', audience: 174, status: 'Active',
      scheduleAt: '2026-06-14T08:00:00', templateId: 'tpl-wa-mentor', templateApproval: 'pending', owner: 'Vera · Meera Nair',
      sent: 612, delivered: 601, read: 498, replied: 211, converted: 73, trend: [66, 69, 72, 74, 77, 79, 78, 80],
    },
    {
      id: 'ccmp-004', name: 'Placement-readiness drive — final year', goal: 'Lift readiness scores before placement season',
      channels: ['voice', 'email'], segment: 'Placement-readiness — low', audience: 231, status: 'Scheduled',
      scheduleAt: '2026-06-16T11:00:00', templateId: 'tpl-vo-readiness', templateApproval: 'approved', owner: 'Vera · Imran Sheikh',
      sent: 0, delivered: 0, read: 0, replied: 0, converted: 0, trend: [0, 0, 0, 0, 0, 0, 0, 0],
    },
    {
      id: 'ccmp-005', name: 'Pathway webinar — explore your options', goal: 'Help undecided students compare pathways',
      channels: ['email', 'whatsapp'], segment: 'Final-year — pathway undecided', audience: 512, status: 'Completed',
      scheduleAt: '2026-06-02T10:00:00', templateId: 'tpl-em-plan', templateApproval: 'approved', owner: 'Vera · Imran Sheikh',
      sent: 2780, delivered: 2716, read: 1820, replied: 588, converted: 131, trend: [49, 53, 56, 59, 61, 63, 64, 66],
    },
    {
      id: 'ccmp-006', name: 'Dormant re-engagement — career cohort', goal: 'Revive students inactive for 14+ days',
      channels: ['whatsapp', 'voice', 'email'], segment: 'Re-engage — dormant 14d+', audience: 905, status: 'Draft',
      scheduleAt: '2026-06-20T09:00:00', templateId: 'tpl-em-cert', templateApproval: 'pending', owner: 'Vera · Rahul Desai',
      sent: 0, delivered: 0, read: 0, replied: 0, converted: 0, trend: [0, 0, 0, 0, 0, 0, 0, 0],
    },
  ]);

  private campaignSignal() { return this.career() ? this.careerCampaigns : this.admissionCampaigns; }
  campaigns = computed<Campaign[]>(() => this.campaignSignal()());

  /* ----------  filters  ---------- */
  query = signal('');
  statusFilter = signal('');                       // '' = all
  channelFilter = signal<CampaignChannel[]>([]);

  filtersActive = computed(() => !!this.query() || !!this.statusFilter() || this.channelFilter().length > 0);

  filtered = computed(() => {
    const q = this.query().trim().toLowerCase();
    const st = this.statusFilter();
    const chs = this.channelFilter();
    return this.campaigns().filter(c => {
      if (st && c.status !== st) return false;
      if (chs.length && !chs.some(ch => c.channels.includes(ch))) return false;
      if (q && !(c.name.toLowerCase().includes(q) || c.goal.toLowerCase().includes(q) || c.segment.toLowerCase().includes(q))) return false;
      return true;
    });
  });

  /* ----------  summary KPIs  ---------- */
  summary = computed<Metric[]>(() => {
    const list = this.campaigns();
    const active = list.filter(c => c.status === 'Active').length;
    const sent = list.reduce((a, c) => a + c.sent, 0);
    const delivered = list.reduce((a, c) => a + c.delivered, 0);
    const read = list.reduce((a, c) => a + c.read, 0);
    const conv = list.reduce((a, c) => a + c.converted, 0);
    const avgRead = delivered ? (read / delivered) * 100 : 0;
    return [
      { key: 'active', label: 'Active campaigns', value: active, display: String(active) + ' / ' + list.length, deltaPct: 25, trend: [2, 2, 3, 3, 3, 4, 3, 3], format: 'int', tone: 'ai' },
      { key: 'sent', label: 'Messages sent', value: sent, deltaPct: 14.2, trend: [4200, 5100, 6000, 7300, 8200, 9000, 9300, sent], format: 'int' },
      { key: 'read', label: 'Avg read / open rate', value: +avgRead.toFixed(1), deltaPct: 5.6, trend: [52, 55, 57, 59, 61, 62, 63, +avgRead.toFixed(1)], format: 'pct', tone: 'success' },
      { key: 'conv', label: 'Conversions attributed', value: conv, deltaPct: 19.8, trend: [120, 180, 260, 340, 410, 470, 510, conv], format: 'int', tone: 'success', drillTo: '/app/applications' },
    ];
  });

  /* ----------  wizard state  ---------- */
  wizardOpen = signal(false);
  draftName = signal('');
  draftChannels = signal<CampaignChannel[]>(['whatsapp']);
  draftSegment = signal(this.segments[0]);
  draftSchedule = signal(this.scheduleOptions[0]);
  draftTemplate = signal(this.templates[0]?.id ?? '');

  templatesForDraft = computed(() => {
    const chs = this.draftChannels();
    if (!chs.length) return this.templates;
    return this.templates.filter(t => chs.includes(t.channel));
  });
  selectedTemplate = computed<CampaignTemplate | undefined>(() => this.templates.find(t => t.id === this.draftTemplate()));
  estimatedAudience = computed(() => {
    const m = /\((\d[\d,]*)\)/.exec(this.draftSegment());
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : 0;
  });
  canLaunch = computed(() => this.draftName().trim().length > 1 && this.draftChannels().length > 0);
  launchLabel = computed(() => this.selectedTemplate()?.approval === 'approved' ? 'Launch campaign' : 'Save as draft');

  /* ----------  helpers exposed to template  ---------- */
  fmtInt = fmtInt;
  fmtDate = fmtDate;
  chIcon = (c: Channel) => CHANNEL_ICON[c];
  chLabel = (c: Channel) => CHANNEL_LABEL[c];
  chColor(c: CampaignChannel) { return c === 'whatsapp' ? 'var(--ch-whatsapp)' : c === 'voice' ? 'var(--ch-voice)' : 'var(--ch-email)'; }
  pct(part: number, whole: number) { return whole ? Math.round((part / whole) * 100) : 0; }
  rateBand(c: Campaign): 'low' | 'med' | 'high' {
    const r = this.pct(c.read, c.delivered);
    return r >= 70 ? 'high' : r >= 50 ? 'med' : 'low';
  }
  readLabel(c: Campaign) { return c.channels.includes('email') && c.channels.length === 1 ? 'Open' : 'Read'; }
  templateName(id: string) { return this.templates.find(t => t.id === id)?.name ?? 'Custom template'; }
  scheduleLabel(c: Campaign) {
    if (c.status === 'Active') return 'Sending · started ' + relTime(c.scheduleAt);
    if (c.status === 'Scheduled') return 'Scheduled · ' + relFuture(c.scheduleAt);
    if (c.status === 'Completed') return 'Completed · ' + relTime(c.scheduleAt);
    return 'Draft · target ' + relFuture(c.scheduleAt);
  }

  /* ----------  actions  ---------- */
  toggleChannelFilter(c: CampaignChannel) {
    this.channelFilter.update(list => list.includes(c) ? list.filter(x => x !== c) : [...list, c]);
  }
  resetFilters() { this.query.set(''); this.statusFilter.set(''); this.channelFilter.set([]); }

  openWizard() {
    // Reset draft to the active counsellor's first segment/template each time.
    this.draftChannels.set(['whatsapp']);
    this.draftSegment.set(this.segments[0]);
    const valid = this.templatesForDraft();
    this.draftTemplate.set((valid[0] ?? this.templates[0])?.id ?? '');
    this.wizardOpen.set(true);
  }
  closeWizard() { this.wizardOpen.set(false); }

  toggleDraftChannel(c: CampaignChannel) {
    this.draftChannels.update(list => list.includes(c) ? list.filter(x => x !== c) : [...list, c]);
    // keep template valid for the channel selection
    const valid = this.templatesForDraft();
    if (!valid.some(t => t.id === this.draftTemplate()) && valid.length) this.draftTemplate.set(valid[0].id);
  }

  launch() {
    const t = this.selectedTemplate();
    const name = this.draftName().trim() || 'Untitled campaign';
    if (t?.approval === 'approved') {
      this.toast.success(`Campaign "${name}" launched — ${this.counselor.activeMeta().name} is reaching out across ${this.draftChannels().length} channel(s).`, 'rocket');
    } else {
      this.toast.warning(`Campaign "${name}" saved as draft — template needs compliance approval before it can send.`);
    }
    this.closeWizard();
  }

  launchExisting(c: Campaign) {
    if (c.templateApproval !== 'approved') {
      this.toast.warning(`"${c.name}" can't launch yet — its template is awaiting approval.`);
      return;
    }
    this.campaignSignal().update(list => list.map(x => x.id === c.id ? { ...x, status: 'Active' } : x));
    this.toast.success(`"${c.name}" launched — ${this.counselor.activeMeta().name} has started sending.`, 'rocket');
  }
  pause(c: Campaign) {
    this.campaignSignal().update(list => list.map(x => x.id === c.id ? { ...x, status: 'Scheduled' } : x));
    this.toast.info(`"${c.name}" paused. Sending can be resumed any time.`);
  }
  duplicate(c: Campaign) { this.toast.info(`Re-run of "${c.name}" prepared as a new draft.`); }
  viewReport(c: Campaign) { this.toast.info(`Opening performance report for "${c.name}".`); }

  go(url: string) { this.router.navigateByUrl(url); }
}
