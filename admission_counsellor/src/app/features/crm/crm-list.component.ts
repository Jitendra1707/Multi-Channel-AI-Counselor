import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import {
  StatusBadgeComponent, ProbabilityBadgeComponent,
} from '../../shared/ui/badges.component';
import { FilterBarComponent } from '../../shared/ui/filter-bar.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { EmptyStateComponent } from '../../shared/ui/layout.component';
import { DataStore } from '../../data-access/data.store';
import { BusinessApiService } from '../../data-access/business-api.service';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Candidate } from '../../domain/models';
import { relTimeLive, relFutureLive, fmtInt, CHANNEL_ICON, CHANNEL_LABEL } from '../../shared/util/format';

@Component({
  selector: 'va-crm-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink, IconComponent, AvatarComponent, AiAvatarComponent,
    StatusBadgeComponent, ProbabilityBadgeComponent,
    FilterBarComponent, DrawerComponent, EmptyStateComponent,
  ],
  template: `
<div class="page page-grid">
  <!-- Header -->
  <header class="ph">
    <div class="ph-text">
      <div class="t-h2">CRM Leads</div>
      <p class="t-sm t-muted">
        <b class="t-num">{{ fmtInt(allCandidates().length) }}</b> candidates ·
        <b class="t-num">{{ fmtInt(highIntentCount()) }}</b> high-intent ·
        managing for <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }}
      </p>
    </div>
    <div class="ph-actions">
      <button class="btn btn-ghost" routerLink="/app/crm/import">
        <va-icon name="upload" [size]="16"></va-icon>Upload Excel
      </button>
      <!-- Reset DB — two-step confirm so a destructive wipe is never one click;
           clears leads + sessions + tasks via BusinessLayer POST /leads/reset. -->
      @if (confirmReset()) {
        <span class="t-sm t-muted">Clear all data?</span>
        <button class="btn btn-danger btn-sm" [disabled]="resetting()" (click)="doReset()">
          <va-icon [name]="resetting() ? 'refresh' : 'trash'" [size]="14" [class.spin]="resetting()"></va-icon>Confirm
        </button>
        <button class="btn btn-ghost btn-sm" [disabled]="resetting()" (click)="confirmReset.set(false)">Cancel</button>
      } @else {
        <button class="btn btn-ghost reset-btn" title="Clear all leads, sessions and tasks" (click)="confirmReset.set(true)">
          <va-icon name="trash" [size]="16"></va-icon>Reset
        </button>
      }
      <button class="btn btn-primary" (click)="addCandidate()">
        <va-icon name="plus" [size]="16"></va-icon>Add candidate
      </button>
    </div>
  </header>

  <!-- Approved-knowledge guardrail strip -->
  <div class="banner ai guard">
    <va-icon name="shield-check" [size]="18"></va-icon>
    <span>All outreach is handled by AI counselors that speak only from <b>institution-approved knowledge</b>, always disclose they are AI, and escalate to humans when unsure.</span>
    <span class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> Aisha · Nova · Vega</span>
  </div>

  <!-- Filter bar -->
  <va-filter-bar
    [query]="query()"
    placeholder="Search by name, course or city…"
    [savedViews]="savedViews"
    [activeView]="activeView()"
    (queryChange)="query.set($event)"
    (selectView)="setView($event)">
    <div filters class="row gap-2 wrap">
      <select class="select fb-select" [value]="stageFilter()" (change)="stageFilter.set($any($event.target).value)">
        <option value="">All stages</option>
        @for (s of stageOptions(); track s) { <option [value]="s">{{ s }}</option> }
      </select>
      <select class="select fb-select" [value]="sourceFilter()" (change)="sourceFilter.set($any($event.target).value)">
        <option value="">All sources</option>
        @for (s of sourceOptions(); track s) { <option [value]="s">{{ s }}</option> }
      </select>
    </div>
    <button actions class="btn btn-ghost btn-sm" (click)="toast.info('Column chooser — drag to reorder, toggle to hide.')">
      <va-icon name="columns" [size]="16"></va-icon>Columns
    </button>
  </va-filter-bar>

  <!-- Bulk-action toolbar (sticky) -->
  @if (selectedCount() > 0) {
    <div class="bulkbar fade-up">
      <div class="row gap-3">
        <span class="bulk-count t-num">{{ selectedCount() }}</span>
        <span class="t-sm">selected</span>
        <button class="link-btn t-cap" (click)="clearSelection()">Clear</button>
      </div>
      <div class="row gap-2 wrap">
        <button class="btn btn-sm btn-subtle" (click)="bulkAssign()"><va-icon name="user" [size]="14"></va-icon>Assign</button>
        <button class="btn btn-sm btn-subtle" (click)="bulkCampaign()"><va-icon name="megaphone" [size]="14"></va-icon>Add to campaign</button>
        <button class="btn btn-sm btn-subtle" (click)="bulkSchedule()"><va-icon name="calendar" [size]="14"></va-icon>Schedule follow-up</button>
        <button class="btn btn-sm btn-ghost" (click)="bulkExport()"><va-icon name="download" [size]="14"></va-icon>Export</button>
      </div>
    </div>
  }

  <!-- Table -->
  <div class="surface table-wrap">
    @if (leadsLoading() && !leadsLoaded()) {
      <div class="state"><div class="spinner"></div><p class="t-sm t-muted">Loading leads…</p></div>
    } @else if (leadsError()) {
      <div class="state">
        <va-icon name="alert-triangle" [size]="32"></va-icon>
        <p class="t-sm">Couldn't reach the leads service. Make sure BusinessLayer is running on :8002.</p>
        <button class="btn btn-primary btn-sm" (click)="retry()"><va-icon name="refresh" [size]="14"></va-icon> Retry</button>
      </div>
    } @else if (paged().length > 0) {
      <div class="scroll-x">
        <table class="va-table">
          <thead>
            <tr>
              <th class="cb-col">
                <button class="cbox" [class.on]="allPageSelected()" [class.some]="somePageSelected()"
                        (click)="toggleAllPage()" [attr.aria-label]="'Select all on page'">
                  <va-icon [name]="allPageSelected() ? 'check' : 'minus'" [size]="13"></va-icon>
                </button>
              </th>
              <th>Candidate</th>
              <th>Course interest</th>
              <th>Location</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Priority</th>
              <th>Probability</th>
              <th>Last contacted</th>
              <th>Next follow-up</th>
              <th>Assigned</th>
              <th class="qa-col">Quick actions</th>
            </tr>
          </thead>
          <tbody>
            @for (c of paged(); track c.candidateId) {
              <tr [class.selected]="isSelected(c.candidateId)" (click)="openPeek(c)">
                <td class="cb-col" (click)="$event.stopPropagation()">
                  <button class="cbox" [class.on]="isSelected(c.candidateId)"
                          (click)="toggleRow(c.candidateId)" [attr.aria-label]="'Select ' + c.name">
                    @if (isSelected(c.candidateId)) { <va-icon name="check" [size]="13"></va-icon> }
                  </button>
                </td>
                <td>
                  <div class="cand">
                    <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="34"></va-avatar>
                    <div class="cand-id">
                      <div class="row gap-1">
                        <span class="cand-name truncate">{{ c.name }}</span>
                        @if (c.doNotContact) { <span class="flag dnc" title="Do not contact"><va-icon name="lock" [size]="11"></va-icon>DNC</span> }
                        @if (c.duplicate) { <span class="flag dup" title="Possible duplicate"><va-icon name="layers" [size]="11"></va-icon>Dup</span> }
                        @if (c.tags.length) { <span class="flag tag">{{ c.tags[0] }}</span> }
                      </div>
                      <span class="cand-email truncate t-cap t-muted">{{ c.email }}</span>
                    </div>
                  </div>
                </td>
                <td><span class="truncate course">{{ c.preferredCourse || '—' }}</span></td>
                <td><span class="loc">{{ c.city || '—' }}@if (c.region) { <span class="t-muted">, {{ c.region }}</span> }</span></td>
                <td><va-status-badge [status]="c.currentStage"></va-status-badge></td>
                <td><span class="t-sm funnel">{{ funnelLabel(c.funnelStage) }}</span></td>
                <td>
                  @if (c.leadPriority) {
                    <span class="prio" [class.hot]="c.leadPriority === 'hot'"
                          [class.warm]="c.leadPriority === 'warm'"
                          [class.cold]="c.leadPriority === 'cold'">{{ priorityLabel(c.leadPriority) }}</span>
                  } @else { <span class="t-sm t-muted">—</span> }
                </td>
                <td><va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge></td>
                <td><span class="t-sm t-muted">{{ relTime(c.lastContacted) }}</span></td>
                <td>
                  @if (c.nextFollowUp) {
                    <span class="t-sm" [class.overdue]="isOverdue(c.nextFollowUp)">{{ relFuture(c.nextFollowUp) }}</span>
                  } @else {
                    <span class="t-sm t-muted">—</span>
                  }
                </td>
                <td>
                  @if (c.backed) { <span class="t-sm t-muted">—</span> }
                  @else {
                    <div class="assigned">
                      <span class="ai-pill"><va-icon name="bot" [size]="12"></va-icon>{{ c.assignedAiCounselor }}</span>
                      @if (c.assignedHumanCounselor) { <span class="t-cap t-muted truncate">{{ c.assignedHumanCounselor }}</span> }
                    </div>
                  }
                </td>
                <td class="qa-col" (click)="$event.stopPropagation()">
                  <div class="qa">
                    <button class="btn btn-icon btn-ghost qa-btn" title="Call" (click)="quickCall(c)"><va-icon name="phone" [size]="15"></va-icon></button>
                    <button class="btn btn-icon btn-ghost qa-btn" title="WhatsApp" (click)="quickWhatsApp(c)"><va-icon name="message-circle" [size]="15"></va-icon></button>
                    <button class="btn btn-icon btn-ghost qa-btn" title="Schedule follow-up" (click)="quickSchedule(c)"><va-icon name="calendar" [size]="15"></va-icon></button>
                  </div>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div class="pager">
        <span class="t-sm t-muted">
          Showing <b class="t-num">{{ rangeStart() }}–{{ rangeEnd() }}</b> of <b class="t-num">{{ fmtInt(filtered().length) }}</b> candidates
        </span>
        <div class="pager-ctrls">
          <button class="btn btn-icon btn-ghost btn-sm" [disabled]="page() === 1" (click)="prevPage()" aria-label="Previous page">
            <va-icon name="chevron-left" [size]="16"></va-icon>
          </button>
          <span class="t-sm t-num">Page {{ page() }} / {{ totalPages() }}</span>
          <button class="btn btn-icon btn-ghost btn-sm" [disabled]="page() === totalPages()" (click)="nextPage()" aria-label="Next page">
            <va-icon name="chevron-right" [size]="16"></va-icon>
          </button>
        </div>
      </div>
    } @else if (allCandidates().length === 0) {
      <va-empty icon="users" title="No leads yet"
        message="Import an Excel of candidates to get started — leads appear here as soon as they're added."
        cta="Upload Excel" ctaIcon="upload" (action)="go('/app/crm/import')"></va-empty>
    } @else {
      <va-empty icon="search" title="No candidates match your filters"
        message="Try clearing the search or switching saved views."
        cta="Clear filters" ctaIcon="x" (action)="clearFilters()"></va-empty>
    }
  </div>
</div>

<!-- Candidate peek drawer -->
<va-drawer
  [open]="peek() !== null"
  [title]="peek()?.name || ''"
  [subtitle]="peek()?.preferredCourse || ''"
  [width]="460"
  (close)="closePeek()">
  @if (peek(); as c) {
    <div class="peek">
      <div class="peek-top">
        <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="56"></va-avatar>
        <div class="grow stack gap-1">
          <div class="row gap-2 wrap">
            <va-status-badge [status]="c.currentStage"></va-status-badge>
            @if (c.leadPriority) {
              <span class="prio" [class.hot]="c.leadPriority === 'hot'"
                    [class.warm]="c.leadPriority === 'warm'"
                    [class.cold]="c.leadPriority === 'cold'">{{ priorityLabel(c.leadPriority) }}</span>
            }
            @if (c.doNotContact) { <span class="flag dnc"><va-icon name="lock" [size]="11"></va-icon>Do not contact</span> }
            @if (c.duplicate) { <span class="flag dup"><va-icon name="layers" [size]="11"></va-icon>Possible duplicate</span> }
          </div>
          <span class="t-cap t-muted">{{ c.email }} · {{ c.mobile }}</span>
        </div>
      </div>

      <div class="peek-tiles">
        <div class="tile">
          <span class="tl">Conversion probability</span>
          <va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge>
        </div>
      </div>

      <dl class="dl peek-dl">
        <dt>Stage</dt><dd>{{ funnelLabel(c.funnelStage) }}</dd>
        <dt>Location</dt><dd>{{ c.city || '—' }}{{ c.region ? ', ' + c.region : '' }}</dd>
        <dt>Academic background</dt><dd>{{ c.academicBackground || '—' }}</dd>
        <dt>Budget</dt><dd>{{ c.budgetRange || '—' }}</dd>
        <dt>Lead source</dt><dd>{{ c.leadSource || '—' }}</dd>
        <dt>Parent engagement</dt><dd>{{ c.backed ? '—' : c.parentEngagement }}</dd>
        <dt>Last contacted</dt><dd>{{ c.lastContacted ? relTime(c.lastContacted) : '—' }}</dd>
        <dt>Next follow-up</dt><dd>{{ c.nextFollowUp ? relFuture(c.nextFollowUp) : '—' }}</dd>
        <dt>Assigned AI</dt><dd>{{ c.backed ? '—' : c.assignedAiCounselor }}</dd>
        @if (c.assignedHumanCounselor) { <dt>Human counselor</dt><dd>{{ c.assignedHumanCounselor }}</dd> }
      </dl>

      <div class="ai-summary">
        <div class="row between">
          <span class="row gap-2 t-h4"><va-ai-avatar [size]="22"></va-ai-avatar> Last AI summary</span>
          <span class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> {{ c.assignedAiCounselor }}</span>
        </div>
        <p class="t-sm">{{ c.lastAiSummary || 'No summary yet — fills in after Aisha speaks with this lead.' }}</p>
      </div>

      @if (c.pendingQuestions.length) {
        <div class="pending">
          <span class="t-cap t-muted block">Open concerns raised</span>
          @for (q of c.pendingQuestions; track q) {
            <div class="pending-q"><va-icon name="help-circle" [size]="14"></va-icon><span class="t-sm">{{ q }}</span></div>
          }
        </div>
      }

      <div class="next-action">
        <div class="na-head">
          <va-icon name="zap" [size]="16"></va-icon>
          <span class="t-cap t-muted">Recommended next action</span>
        </div>
        @if (c.recommendedNextAction.label) {
          <button class="btn btn-accent btn-block" (click)="runNextAction(c)">
            <va-icon [name]="chIcon(c.recommendedNextAction.channel)" [size]="16"></va-icon>
            {{ c.recommendedNextAction.label }}
          </button>
          <p class="t-cap t-muted na-reason">{{ c.recommendedNextAction.reason }}</p>
        } @else {
          <p class="t-cap t-muted na-reason">No recommendation yet — appears once Aisha has engaged this lead.</p>
        }
      </div>
    </div>
  }

  <div footer class="row gap-2 grow">
    @if (peek(); as c) {
      <button class="btn btn-ghost" (click)="quickWhatsApp(c)" title="Message on WhatsApp">
        <va-icon name="message-circle" [size]="16"></va-icon>
      </button>
      <button class="btn btn-primary grow" (click)="closePeek(); go('/app/crm/candidate/' + c.candidateId)">
        Open full profile <va-icon name="arrow-right" [size]="16"></va-icon>
      </button>
    }
  </div>
</va-drawer>
  `,
  styles: [`
:host { display: block; }

/* header (mirrors va-page-header rhythm) */
.ph { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.ph-text p { margin-top: 4px; max-width: 70ch; }
.ph-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.reset-btn { color: var(--color-danger); border-color: color-mix(in srgb, var(--color-danger) 30%, transparent); }
.reset-btn:hover { background: var(--color-danger-soft); }

/* guard banner */
.guard { align-items: center; }
.guard > span:first-of-type { flex: 1; }
.guard va-icon { color: var(--color-accent-2); flex: none; }
.ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; white-space: nowrap; }

/* filter selects */
.fb-select { width: auto; min-width: 150px; padding: 8px 10px; }

/* bulk bar */
.bulkbar { position: sticky; top: 64px; z-index: 6; display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; padding: 10px 14px; border-radius: var(--r-lg);
  background: rgba(var(--color-primary-rgb), .06); border: 1px solid rgba(var(--color-primary-rgb), .22); box-shadow: var(--e1); }
.bulk-count { display: inline-flex; align-items: center; justify-content: center; min-width: 28px; height: 24px; padding: 0 8px;
  border-radius: var(--r-pill); background: var(--color-primary); color: #fff; font-weight: 700; font-size: var(--text-cap); }
.link-btn { background: none; border: none; color: var(--color-primary); font-weight: 600; padding: 0; }
.link-btn:hover { text-decoration: underline; }

/* table */
.table-wrap { overflow: hidden; }
.state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 48px 24px; text-align: center; }
.state va-icon { color: var(--color-text-muted); }
.state p { margin: 0; max-width: 46ch; }
.spinner { width: 34px; height: 34px; border-radius: 50%; border: 3px solid var(--color-surface-alt); border-top-color: var(--color-accent); animation: va-spin .8s linear infinite; }
.scroll-x { overflow-x: auto; }
.va-table { min-width: 1120px; }
.cb-col { width: 44px; padding-left: 16px; padding-right: 0; }
.center-col { text-align: center; }
.qa-col { width: 132px; }

/* checkbox */
.cbox { width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid var(--color-border-strong);
  background: var(--color-surface); display: inline-flex; align-items: center; justify-content: center;
  color: #fff; padding: 0; transition: background .12s, border-color .12s; }
.cbox:hover { border-color: var(--color-primary); }
.cbox.on { background: var(--color-primary); border-color: var(--color-primary); }
.cbox.some:not(.on) { background: var(--color-primary); border-color: var(--color-primary); }

/* candidate cell */
.cand { display: flex; align-items: center; gap: 10px; min-width: 0; }
.cand-id { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.cand-name { font-weight: 600; max-width: 200px; }
.cand-email { max-width: 200px; }
.course { display: inline-block; max-width: 180px; }
.loc { font-size: var(--text-sm); }

/* flags */
.flag { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; line-height: 1;
  padding: 3px 6px; border-radius: var(--r-pill); white-space: nowrap; }
.flag.dnc { background: var(--color-danger-soft); color: var(--color-danger); }
.flag.dup { background: var(--color-warning-soft); color: var(--color-warning); }
.flag.tag { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }

.overdue { color: var(--color-danger); font-weight: 600; }

/* assigned */
.assigned { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ai-pill { display: inline-flex; align-items: center; gap: 4px; width: fit-content; font-size: var(--text-cap); font-weight: 700;
  padding: 2px 8px; border-radius: var(--r-pill); background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); }
.assigned .t-cap { max-width: 130px; }

/* quick actions */
.qa { display: flex; gap: 4px; opacity: .55; transition: opacity .12s; }
tr:hover .qa { opacity: 1; }
.qa-btn { width: 30px; height: 30px; padding: 6px; }
.qa-btn:nth-child(1):hover { color: var(--ch-voice); }
.qa-btn:nth-child(2):hover { color: var(--ch-whatsapp); }
.qa-btn:nth-child(3):hover { color: var(--ch-vcon); }

/* pager */
.pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 12px 16px; border-top: 1px solid var(--color-border); }
.pager-ctrls { display: flex; align-items: center; gap: 10px; }

/* drawer peek */
.peek { display: flex; flex-direction: column; gap: 18px; }
.peek-top { display: flex; align-items: center; gap: 14px; }
.peek-tiles { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.peek-tiles .tile { gap: 8px; }
.peek-dl dd { font-weight: 500; }
.block { display: block; }

.ai-summary { padding: 14px; border-radius: var(--r-md); border: 1px solid rgba(var(--color-accent-2-rgb), .22);
  background: rgba(var(--color-accent-2-rgb), .06); display: flex; flex-direction: column; gap: 8px; }
.ai-summary p { margin: 0; }

.pending { display: flex; flex-direction: column; gap: 6px; }
.pending-q { display: flex; align-items: flex-start; gap: 8px; padding: 9px 12px; border-radius: var(--r-md);
  background: var(--color-warning-soft); }
.pending-q va-icon { color: var(--color-warning); flex: none; margin-top: 1px; }

.next-action { display: flex; flex-direction: column; gap: 8px; padding: 14px; border-radius: var(--r-md);
  border: 1px solid var(--color-border); background: var(--color-surface-2); }
.na-head { display: flex; align-items: center; gap: 6px; }
.na-head va-icon { color: var(--color-accent-2); }
.na-reason { margin: 0; }

@media (max-width: 720px) {
  .peek-tiles { grid-template-columns: 1fr; }
}
/* funnel-stage cell + lead-priority badge */
.funnel { color: var(--color-text); white-space: nowrap; }
.prio { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px;
  font-weight: 600; text-transform: capitalize; border: 1px solid transparent; white-space: nowrap; }
.prio.hot  { color: #b91c1c; background: #fef2f2; border-color: #fecaca; }
.prio.warm { color: #b45309; background: #fffbeb; border-color: #fde68a; }
.prio.cold { color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }
  `],
})
export class CrmListComponent implements OnInit, OnDestroy {
  private store = inject(DataStore);
  private api = inject(BusinessApiService);
  private router = inject(Router);
  toast = inject(ToastService);
  auth = inject(AuthService);

  /** Auto-refresh poll handle (CRM stays live without a manual reload). */
  private pollId: ReturnType<typeof setInterval> | null = null;
  private static readonly POLL_MS = 5_000;

  ngOnInit(): void {
    // Initial load (with spinner) from BusinessLayer.
    void this.store.loadLeads();
    // Auto-refresh every 5s so new/updated leads (dialer, analyzer, inbound
    // calls) appear without a manual refresh. Silent → no spinner flicker, and a
    // transient failure keeps the current rows on screen.
    this.pollId = setInterval(
      () => void this.store.loadLeads({ silent: true }),
      CrmListComponent.POLL_MS,
    );
  }

  ngOnDestroy(): void {
    if (this.pollId !== null) {
      clearInterval(this.pollId);
      this.pollId = null;
    }
  }

  // ---- source data (real leads only; no mock seed on this page) ----
  allCandidates = this.store.leads;
  highIntentCount = computed(() => this.allCandidates().filter(c => c.conversionProbability > 70).length);
  leadsLoading = this.store.leadsLoading;
  leadsLoaded = this.store.leadsLoaded;
  leadsError = this.store.leadsError;

  // ---- filter state ----
  query = signal('');
  stageFilter = signal('');
  sourceFilter = signal('');
  activeView = signal('All leads');
  savedViews = ['All leads', 'New', 'Follow-up', 'Converted', 'High intent', 'Needs follow-up', 'Parent discussion', 'Unassigned'];

  stageOptions = computed(() => Array.from(new Set(this.allCandidates().map(c => c.currentStage))));
  sourceOptions = computed(() => Array.from(new Set(this.allCandidates().map(c => c.leadSource))));

  // ---- selection ----
  selection = signal<Set<string>>(new Set());
  selectedCount = computed(() => this.selection().size);
  isSelected = (id: string) => this.selection().has(id);

  // ---- peek + pagination ----
  peek = signal<Candidate | null>(null);
  page = signal(1);
  readonly pageSize = 12;

  // ---- format helpers (exposed to template) ----
  relTime = relTimeLive;
  relFuture = relFutureLive;
  fmtInt = fmtInt;
  chIcon = (c: string) => (CHANNEL_ICON as Record<string, string>)[c] ?? 'message-square';
  chLabel = (c: string) => (CHANNEL_LABEL as Record<string, string>)[c] ?? c;

  // ---- computed list ----
  filtered = computed<Candidate[]>(() => {
    const q = this.query().trim().toLowerCase();
    const stage = this.stageFilter();
    const source = this.sourceFilter();
    const view = this.activeView();
    return this.allCandidates().filter(c => {
      if (q && !(`${c.name} ${c.preferredCourse} ${c.city} ${c.region} ${c.email}`.toLowerCase().includes(q))) return false;
      if (stage && c.currentStage !== stage) return false;
      if (source && c.leadSource !== source) return false;
      switch (view) {
        case 'New': return c.currentStage === 'New';
        case 'Follow-up': return c.currentStage === 'Follow-up';
        case 'Converted': return c.currentStage === 'Converted';
        case 'High intent': return c.conversionProbability > 70;
        case 'Needs follow-up': return !!c.nextFollowUp && this.isOverdue(c.nextFollowUp);
        case 'Parent discussion': return c.currentStage === 'Escalated' || c.parentEngagement === 'Concerns Raised';
        case 'Unassigned': return !c.assignedHumanCounselor;
        default: return true;
      }
    });
  });

  totalPages = computed(() => Math.max(1, Math.ceil(this.filtered().length / this.pageSize)));
  paged = computed<Candidate[]>(() => {
    const start = (this.page() - 1) * this.pageSize;
    return this.filtered().slice(start, start + this.pageSize);
  });
  rangeStart = computed(() => this.filtered().length === 0 ? 0 : (this.page() - 1) * this.pageSize + 1);
  rangeEnd = computed(() => Math.min(this.page() * this.pageSize, this.filtered().length));

  // page-level select-all state
  allPageSelected = computed(() => {
    const p = this.paged();
    return p.length > 0 && p.every(c => this.selection().has(c.candidateId));
  });
  somePageSelected = computed(() => {
    const sel = this.selection();
    return this.paged().some(c => sel.has(c.candidateId)) && !this.allPageSelected();
  });

  // ---- view / filter handlers ----
  setView(v: string) { this.activeView.set(v); this.page.set(1); }

  // ---- pagination handlers ----
  nextPage() { if (this.page() < this.totalPages()) this.page.update(p => p + 1); }
  prevPage() { if (this.page() > 1) this.page.update(p => p - 1); }

  // ---- selection handlers ----
  toggleRow(id: string) {
    this.selection.update(set => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  toggleAllPage() {
    const p = this.paged();
    const all = this.allPageSelected();
    this.selection.update(set => {
      const next = new Set(set);
      if (all) p.forEach(c => next.delete(c.candidateId));
      else p.forEach(c => next.add(c.candidateId));
      return next;
    });
  }
  clearSelection() { this.selection.set(new Set()); }

  // ---- bulk actions ----
  bulkAssign() { this.toast.info('Coming soon', 'clock'); }
  bulkCampaign() { this.toast.info('Coming soon', 'clock'); }
  async bulkSchedule() {
    const ids = [...this.selection()];
    if (!ids.length) return;
    try {
      await Promise.all(ids.map(id => this.api.scheduleFollowup(id)));
      this.toast.success(`Follow-up scheduled for ${ids.length} lead(s).`, 'calendar');
      await this.store.loadLeads();
    } catch {
      this.toast.danger('Could not schedule follow-up — the leads service is unavailable.');
    }
  }
  bulkExport() {
    const sel = this.selection();
    const rows = sel.size ? this.allCandidates().filter(c => sel.has(c.candidateId)) : this.filtered();
    this.exportCsv(rows);
  }

  /** Build a CSV from the given leads and trigger a client-side download. */
  private exportCsv(rows: Candidate[]) {
    if (!rows.length) { this.toast.info('No leads to export.'); return; }
    const cols: [string, (c: Candidate) => string | number | undefined][] = [
      ['Name', c => c.name], ['Email', c => c.email], ['Mobile', c => c.mobile],
      ['City', c => c.city], ['Course', c => c.preferredCourse],
      ['Status', c => c.currentStage], ['Stage', c => this.funnelLabel(c.funnelStage)],
      ['Priority', c => this.priorityLabel(c.leadPriority)],
      ['Conversion %', c => c.conversionProbability], ['Lead source', c => c.leadSource],
      ['Last contacted', c => c.lastContacted], ['Next follow-up', c => c.nextFollowUp ?? ''],
    ];
    const esc = (v: string | number | undefined) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const csv = [cols.map(([h]) => esc(h)).join(','),
      ...rows.map(c => cols.map(([, f]) => esc(f(c))).join(','))].join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const a = document.createElement('a');
    a.href = url; a.download = `leads-${rows.length}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    this.toast.success(`Exported ${rows.length} lead(s) to CSV.`, 'download');
  }

  // ---- peek ----
  openPeek(c: Candidate) { this.peek.set(c); }
  closePeek() { this.peek.set(null); }

  // ---- row quick actions ----
  quickCall(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }
  quickWhatsApp(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }
  async quickSchedule(c: Candidate) {
    try {
      await this.api.scheduleFollowup(c.candidateId);
      this.toast.success(`Follow-up scheduled for ${c.name}.`, 'calendar');
      await this.store.loadLeads();
    } catch {
      this.toast.danger('Could not schedule follow-up — the leads service is unavailable.');
    }
  }

  runNextAction(c: Candidate) {
    this.toast.success(`${c.recommendedNextAction.label} — queued for ${c.name} via ${this.chLabel(c.recommendedNextAction.channel)}.`,
      this.chIcon(c.recommendedNextAction.channel));
    this.closePeek();
  }

  // ---- reset DB (two-step confirm; clears leads + sessions + tasks) ----
  confirmReset = signal(false);
  resetting = signal(false);

  async doReset() {
    this.resetting.set(true);
    try {
      const r = await this.api.resetLeads();
      const n = Object.values(r.cleared ?? {}).reduce((a, b) => a + b, 0);
      this.toast.success(`Cleared all CRM data (${n} rows across leads, sessions, tasks).`, 'trash');
      await this.store.loadLeads();
    } catch (e) {
      const unavailable = e instanceof Error && e.message === 'LEADS_SERVICE_UNAVAILABLE';
      this.toast.danger(unavailable
        ? 'Reset failed — is BusinessLayer running on :8002?'
        : 'Reset failed — please try again.');
    } finally {
      this.resetting.set(false);
      this.confirmReset.set(false);
    }
  }

  // ---- misc ----
  addCandidate() { this.toast.info('New candidate form — capture consent before any outreach.'); }
  go(url: string) { this.router.navigateByUrl(url); }
  retry() { void this.store.loadLeads(); }
  clearFilters() { this.query.set(''); this.stageFilter.set(''); this.sourceFilter.set(''); this.setView('All leads'); }
  isOverdue(iso: string) { return new Date(iso).getTime() < Date.now(); }

  /** Admissions lifecycle stage → display label (snake_case → Title Case). */
  funnelLabel(stage?: string): string {
    const s = (stage ?? '').trim();
    if (!s || s === 'lead' || s === 'raw') return '—';   // pre-application stages: nothing to show
    return s.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }

  /** Lead temperature → display label (Hot / Warm / Cold). */
  priorityLabel(p?: string | null): string {
    const s = (p ?? '').trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
  }
}
