import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import {
  StatusBadgeComponent, SentimentBadgeComponent, ProbabilityBadgeComponent,
  BandChipComponent, ApprovalChipComponent,
} from '../../shared/ui/badges.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { TimelineComponent } from '../../shared/ui/timeline.component';
import { DataStore } from '../../data-access/data.store';
import { BusinessApiService, LeadSession, leadToCandidate } from '../../data-access/business-api.service';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Candidate, JourneyEvent, Parent, Channel, Sentiment } from '../../domain/models';
import {
  relTime, relFuture, relTimeLive, relFutureLive, fmtDate, fmtTime, CHANNEL_LABEL, CHANNEL_ICON,
} from '../../shared/util/format';

type TabKey =
  | 'overview' | 'journey' | 'conversations' | 'parents' | 'course'
  | 'application' | 'documents' | 'notes' | 'insights' | 'audit';

/** BusinessLayer session sentiment → app Sentiment scale. */
const SENTIMENT_MAP: Record<string, Sentiment> = {
  positive: 'pos', neutral: 'neutral', negative: 'neg', frustrated: 'very-neg',
};

interface ConvRow {
  id: string;
  channel: Channel | 'system';
  owner: 'ai' | 'human' | 'system';
  label: string;
  summary: string;
  ts: string;
}

interface AuditRow {
  id: string;
  actor: string;
  isAi: boolean;
  action: string;
  detail: string;
  ts: string;
}

@Component({
  selector: 'va-candidate-profile',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink, IconComponent, AvatarComponent, AiAvatarComponent,
    StatusBadgeComponent, SentimentBadgeComponent, ProbabilityBadgeComponent,
    BandChipComponent, ApprovalChipComponent,
    SectionCardComponent, EmptyStateComponent, TimelineComponent,
  ],
  template: `
@if (candidate(); as c) {
  <div class="page cp">
    <!-- ===================== STICKY HEADER ===================== -->
    <header class="cp-head surface">
      <a class="back" routerLink="/app/crm" title="Back to CRM">
        <va-icon name="arrow-left" [size]="18"></va-icon>
      </a>

      <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="56"></va-avatar>

      <div class="head-id">
        <div class="row gap-2 wrap">
          <span class="t-h2">{{ c.name }}</span>
          @if (c.tags.length) {
            @for (t of c.tags; track t) { <span class="chip tag"><va-icon name="star" [size]="11"></va-icon>{{ t }}</span> }
          }
          @if (c.doNotContact) { <span class="chip dnc"><va-icon name="lock" [size]="11"></va-icon>Do not contact</span> }
        </div>
        <div class="row gap-3 wrap meta">
          <va-status-badge [status]="c.currentStage"></va-status-badge>
          @if (c.leadPriority) {
            <span class="prio" [class.hot]="c.leadPriority === 'hot'"
                  [class.warm]="c.leadPriority === 'warm'"
                  [class.cold]="c.leadPriority === 'cold'">{{ priorityLabel(c.leadPriority) }}</span>
          }
          @if (profileSentiment(); as sv) { <va-sentiment-badge [value]="sv" [showLabel]="true"></va-sentiment-badge> }
          <span class="assign"><va-icon name="bot" [size]="13"></va-icon>{{ c.assignedAiCounselor }}<span class="t-muted"> · AI counselor</span></span>
          @if (c.assignedHumanCounselor) {
            <span class="assign human"><va-icon name="user" [size]="13"></va-icon>{{ c.assignedHumanCounselor }}<span class="t-muted"> · human</span></span>
          } @else {
            <span class="assign t-muted"><va-icon name="user" [size]="13"></va-icon>No human assigned</span>
          }
          <span class="t-cap t-muted">·  {{ c.city }}, {{ c.region }}</span>
        </div>
      </div>

      <div class="head-actions">
        <button class="btn btn-icon btn-ghost" title="Call" (click)="quickCall(c)"><va-icon name="phone" [size]="16"></va-icon></button>
        <a class="btn btn-icon btn-ghost wa" title="WhatsApp" [routerLink]="'/app/communications/whatsapp/' + c.candidateId"><va-icon name="message-circle" [size]="16"></va-icon></a>
        <button class="btn btn-icon btn-ghost" title="Email" (click)="quickEmail(c)"><va-icon name="mail" [size]="16"></va-icon></button>
        <button class="btn btn-icon btn-ghost" title="Schedule V-Con" (click)="quickVcon(c)"><va-icon name="video" [size]="16"></va-icon></button>
        <div class="more">
          <button class="btn btn-ghost btn-icon" title="More" (click)="toggleMenu()"><va-icon name="more-vertical" [size]="16"></va-icon></button>
          @if (menuOpen()) {
            <div class="menu" (mouseleave)="menuOpen.set(false)">
              <button (click)="changeStatus()"><va-icon name="git-branch" [size]="15"></va-icon>Change status</button>
              <button (click)="assign()"><va-icon name="users" [size]="15"></va-icon>Assign counselor</button>
              <button class="danger" (click)="escalate(c)"><va-icon name="alert-triangle" [size]="15"></va-icon>Escalate to human</button>
            </div>
          }
        </div>
      </div>

      <!-- probability strip -->
      <div class="prob-strip">
        <div class="ps-cell">
          <span class="ps-lab t-cap t-muted">Conversion probability</span>
          <div class="row gap-2">
            <va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge>
            <va-icon [name]="c.conversionProbability >= 70 ? 'trending-up' : c.conversionProbability >= 40 ? 'activity' : 'trending-down'" [size]="15" class="ps-trend"></va-icon>
          </div>
        </div>
        <div class="ps-div"></div>
        <div class="ps-cell">
          <span class="ps-lab t-cap t-muted">Parent engagement</span>
          <span class="ps-val">{{ c.parentEngagement }}</span>
        </div>
        <div class="ps-div"></div>
        <div class="ps-cell">
          <span class="ps-lab t-cap t-muted">Next follow-up</span>
          <span class="ps-val" [class.warn]="overdue(c)">{{ c.nextFollowUp ? relFutureLive(c.nextFollowUp) : 'Not scheduled' }}</span>
        </div>
      </div>
    </header>

    <!-- ===================== TABS ===================== -->
    <div class="tabs cp-tabs">
      @for (t of tabs; track t.key) {
        <button class="tab" [class.active]="tab() === t.key" (click)="tab.set(t.key)">
          {{ t.label }}
          @if (t.count !== undefined && t.count > 0) { <span class="count t-num">{{ t.count }}</span> }
        </button>
      }
    </div>

    <!-- ===================== BODY ===================== -->
    <div class="cp-body">
      <div class="cp-main">
        @switch (tab()) {

          @case ('overview') {
            <!-- AI summary callout -->
            <div class="banner ai ai-summary">
              <va-ai-avatar [size]="36" [glow]="true"></va-ai-avatar>
              <div class="grow">
                <div class="row between">
                  <span class="t-sm" style="font-weight:600">Last AI summary — {{ c.assignedAiCounselor }}</span>
                  <span class="chip approved"><va-icon name="shield-check" [size]="11"></va-icon>Approved knowledge only</span>
                </div>
                <p class="t-sm">{{ c.lastAiSummary }}</p>
              </div>
            </div>

            <div class="ov-grid">
              <va-section-card title="Summary" hint="Key candidate facts">
                <dl class="dl">
                  <dt>Mobile</dt><dd class="t-num">{{ c.mobile }}</dd>
                  <dt>WhatsApp</dt><dd class="t-num">{{ c.whatsapp }}</dd>
                  <dt>Email</dt><dd class="truncate" [title]="c.email">{{ c.email }}</dd>
                  <dt>City</dt><dd>{{ c.city }}, {{ c.region }}</dd>
                  <dt>Stage</dt><dd>{{ funnelLabel(c.funnelStage) }}</dd>
                  <dt>Priority</dt><dd>{{ priorityLabel(c.leadPriority) }}</dd>
                  <dt>Academic</dt><dd>{{ c.academicBackground }}</dd>
                  <dt>Budget</dt><dd>{{ c.budgetRange }}</dd>
                  <dt>Lead source</dt><dd>{{ c.leadSource }}</dd>
                  <dt>Reference</dt><dd>{{ c.referenceProvider || '—' }}</dd>
                  <dt>Created</dt><dd>{{ fmtDate(c.createdAt) }} · {{ c.createdBy }}</dd>
                  <dt>Last contact</dt><dd>{{ relTimeLive(c.lastContacted) }}</dd>
                </dl>
              </va-section-card>

              <va-section-card title="Conversion & signals" hint="AI-scored">
                <div class="stack gap-4">
                  <div class="meter">
                    <div class="row between"><span class="t-sm t-muted">Conversion probability</span><span class="t-h3 t-num">{{ c.conversionProbability }}%</span></div>
                    <div class="progress ai"><span [style.width.%]="c.conversionProbability"></span></div>
                  </div>
                  <div class="tilegrid">
                    <div class="tile"><span class="tl">Budget sensitivity</span><span class="tv"><va-band-chip [band]="c.budgetSensitivity"></va-band-chip></span></div>
                    <div class="tile"><span class="tl">Scholarship</span><span class="tv t-h4">{{ c.scholarshipInterest ? 'Interested' : 'No' }}</span></div>
                    <div class="tile"><span class="tl">Sentiment</span><span class="tv">
                      @if (profileSentiment(); as sv) { <va-sentiment-badge [value]="sv" [showLabel]="true"></va-sentiment-badge> }
                      @else { <span class="t-sm t-muted">—</span> }
                    </span></div>
                  </div>
                </div>
              </va-section-card>

              <va-section-card title="Course interest">
                <div class="stack gap-3">
                  <div class="course-pref">
                    <va-icon name="graduation-cap" [size]="18"></va-icon>
                    <div><div class="t-h4">{{ c.preferredCourse }}</div><span class="t-cap t-muted">Preferred course · {{ auth.admissionCycle() }}</span></div>
                  </div>
                  <div class="row wrap gap-2">
                    @for (i of c.careerInterests; track i) { <span class="chip">{{ i }}</span> }
                    @if (!c.careerInterests.length) { <span class="t-sm t-muted">No interests captured yet.</span> }
                  </div>
                </div>
              </va-section-card>

              <va-section-card title="Parent involvement">
                @if (c.parents.length) {
                  <div class="stack gap-3">
                    @for (p of c.parents; track p.parentId) {
                      <div class="parent-mini">
                        <va-avatar [name]="p.name" [hue]="(c.avatarHue + 120) % 360" [size]="32"></va-avatar>
                        <div class="grow"><div class="t-sm" style="font-weight:600">{{ p.name }}</div><span class="t-cap t-muted">{{ p.relationship }} · {{ p.preferredLanguage }}</span></div>
                      </div>
                    }
                    <span class="chip" [class.consent-ok]="hasConsent(c)" [class.consent-no]="!hasConsent(c)">
                      <va-icon [name]="hasConsent(c) ? 'shield-check' : 'lock'" [size]="11"></va-icon>
                      {{ hasConsent(c) ? 'Consent to discuss on file' : 'Discussion restricted — no consent' }}
                    </span>
                  </div>
                } @else {
                  <p class="t-sm t-muted">No parent or guardian linked to this candidate.</p>
                }
              </va-section-card>
            </div>

            <va-section-card title="Open concerns" hint="Awaiting approved answers">
              <span actions class="chip ai-chip"><va-icon name="brain" [size]="12"></va-icon>Knowledge check</span>
              @if (c.pendingQuestions.length) {
                <div class="stack gap-2">
                  @for (q of c.pendingQuestions; track q) {
                    <div class="pq">
                      <va-icon name="help-circle" [size]="16"></va-icon>
                      <span class="grow t-sm">{{ q }}</span>
                      <button class="btn btn-sm btn-ghost" (click)="routeToKms()">Find answer<va-icon name="arrow-up-right" [size]="13"></va-icon></button>
                    </div>
                  }
                </div>
              } @else {
                <p class="t-sm t-muted">No open concerns — everything raised has been addressed from approved knowledge.</p>
              }
            </va-section-card>
          }

          @case ('journey') {
            <va-section-card title="Candidate journey" hint="Every AI & human touchpoint">
              <span actions class="t-cap t-muted">{{ journey().length }} events</span>
              @if (journey().length) {
                <va-timeline [events]="journey()" [clickable]="true" (openEvent)="openEvent($event)"></va-timeline>
              } @else {
                <va-empty icon="git-branch" title="No journey yet" message="This candidate has no recorded touchpoints."></va-empty>
              }
            </va-section-card>
          }

          @case ('conversations') {
            <va-section-card title="Conversations" hint="Calls, WhatsApp & emails merged">
              <a actions class="btn btn-sm btn-ghost" [routerLink]="'/app/communications/whatsapp/' + c.candidateId">Open WhatsApp console<va-icon name="arrow-up-right" [size]="13"></va-icon></a>
              @if (conversations().length) {
                <div class="conv-list">
                  @for (e of conversations(); track e.id) {
                    <div class="conv" [attr.data-ch]="e.channel">
                      <span class="conv-ic"><va-icon [name]="convIcon(e.channel)" [size]="15"></va-icon></span>
                      <div class="grow">
                        <div class="row gap-2 wrap">
                          <span class="t-sm" style="font-weight:600">{{ e.label }}</span>
                          <span class="owner-tag" [attr.data-owner]="e.owner">{{ e.owner === 'ai' ? 'AI' : e.owner === 'human' ? 'Human' : 'System' }}</span>
                          <span class="t-cap t-muted ch-name">{{ chLabel(e.channel) }}</span>
                          <span class="t-cap t-muted time">{{ relTime(e.ts) }} · {{ fmtTime(e.ts) }}</span>
                        </div>
                        <p class="t-sm conv-sum">{{ e.summary }}</p>
                      </div>
                    </div>
                  }
                </div>
              } @else {
                <va-empty icon="message-square" title="No conversations" message="No calls, messages or emails recorded for this candidate."></va-empty>
              }
            </va-section-card>
          }

          @case ('parents') {
            @if (c.parents.length) {
              <div class="ov-grid">
                @for (p of c.parents; track p.parentId) {
                  <va-section-card [title]="p.relationship">
                    <div class="stack gap-4">
                      <div class="parent-mini">
                        <va-avatar [name]="p.name" [hue]="(c.avatarHue + 120) % 360" [size]="40"></va-avatar>
                        <div class="grow"><div class="t-h4">{{ p.name }}</div><span class="t-cap t-muted">Speaks {{ p.preferredLanguage }}</span></div>
                      </div>
                      <dl class="dl">
                        <dt>Mobile</dt><dd class="t-num">{{ p.mobile || '—' }}</dd>
                        <dt>WhatsApp</dt><dd class="t-num">{{ p.whatsapp || '—' }}</dd>
                        <dt>Email</dt><dd class="truncate" [title]="p.email || ''">{{ p.email || '—' }}</dd>
                        <dt>Last contacted</dt><dd>{{ p.lastContacted ? relTime(p.lastContacted) : 'Never' }}</dd>
                      </dl>
                      <div>
                        <span class="t-cap t-muted">Concerns</span>
                        <div class="row wrap gap-2" style="margin-top:6px">
                          @for (cn of p.concerns; track cn) { <span class="chip">{{ cn }}</span> }
                          @if (!p.concerns.length) { <span class="t-sm t-muted">None recorded.</span> }
                        </div>
                      </div>
                      <div class="consent-line" [class.ok]="p.consentToDiscuss" [class.no]="!p.consentToDiscuss">
                        <va-icon [name]="p.consentToDiscuss ? 'shield-check' : 'lock'" [size]="15"></va-icon>
                        <span class="t-sm">{{ p.consentToDiscuss ? 'Consent given — the counselor may discuss this candidate.' : 'No consent — the counselor will not discuss this candidate.' }}</span>
                      </div>
                    </div>
                  </va-section-card>
                }
              </div>
              <div class="banner info guard-note">
                <va-icon name="shield" [size]="16"></va-icon>
                <span class="t-sm">Aisha never discusses a candidate with a parent where consent rules forbid it. Consent is enforced on every channel.</span>
              </div>
            } @else {
              <va-section-card title="Parent / Guardian">
                <va-empty icon="users" title="No parent or guardian linked"
                  message="Add a parent contact so Aisha can involve the family — only after consent is captured."></va-empty>
              </va-section-card>
            }
          }

          @case ('course') {
            <va-section-card title="Course interest & fit">
              <div class="stack gap-6">
                <div class="course-pref big">
                  <va-icon name="graduation-cap" [size]="22"></va-icon>
                  <div><div class="t-h3">{{ c.preferredCourse }}</div><span class="t-sm t-muted">Preferred course · {{ auth.admissionCycle() }} · {{ auth.institution().name }}</span></div>
                  <va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge>
                </div>
                <div>
                  <span class="t-cap t-muted">Career interests</span>
                  <div class="row wrap gap-2" style="margin-top:8px">
                    @for (i of c.careerInterests; track i) { <span class="chip">{{ i }}</span> }
                    @if (!c.careerInterests.length) { <span class="t-sm t-muted">No interests captured.</span> }
                  </div>
                </div>
                <div class="tilegrid">
                  <div class="tile"><span class="tl">Budget range</span><span class="tv t-h4">{{ c.budgetRange }}</span></div>
                  <div class="tile"><span class="tl">Budget sensitivity</span><span class="tv"><va-band-chip [band]="c.budgetSensitivity"></va-band-chip></span></div>
                  <div class="tile"><span class="tl">Scholarship interest</span><span class="tv t-h4">{{ c.scholarshipInterest ? 'Yes' : 'No' }}</span></div>
                </div>
              </div>
            </va-section-card>
          }

          @case ('application') {
            @if (application(); as app) {
              <va-section-card title="Application" [hint]="app.applicationId">
                <span actions><va-status-badge [status]="app.stage"></va-status-badge></span>
                <div class="stack gap-6">
                  <div class="tilegrid">
                    <div class="tile"><span class="tl">Course</span><span class="tv t-h4">{{ app.course }}</span></div>
                    <div class="tile"><span class="tl">Stage</span><span class="tv t-h4">{{ app.stage }}</span></div>
                    <div class="tile"><span class="tl">Fee status</span><span class="tv"><span class="chip" [attr.data-fee]="app.feeStatus">{{ app.feeStatus }}</span></span></div>
                  </div>
                  <div class="docsplit">
                    <div>
                      <span class="t-cap t-muted">Submitted documents</span>
                      <div class="stack gap-2" style="margin-top:8px">
                        @for (d of app.submittedDocs; track d) { <span class="doc-ok"><va-icon name="check-circle" [size]="14"></va-icon>{{ d }}</span> }
                        @if (!app.submittedDocs.length) { <span class="t-sm t-muted">None yet.</span> }
                      </div>
                    </div>
                    <div>
                      <span class="t-cap t-muted">Missing documents</span>
                      <div class="stack gap-2" style="margin-top:8px">
                        @for (d of app.missingDocs; track d) { <span class="doc-miss"><va-icon name="alert-circle" [size]="14"></va-icon>{{ d }}</span> }
                        @if (!app.missingDocs.length) { <span class="t-sm t-muted">Nothing outstanding.</span> }
                      </div>
                    </div>
                  </div>
                  <div class="banner info">
                    <va-icon name="info" [size]="16"></va-icon>
                    <span class="t-sm">Next action: <b>{{ app.nextAction }}</b></span>
                  </div>
                </div>
              </va-section-card>
            } @else {
              <va-section-card title="Application">
                <va-empty icon="file-text" title="No application started yet"
                  message="Send a registration link so this candidate can begin their application for {{ c.preferredCourse }}."
                  cta="Send registration link" ctaIcon="send" (action)="sendRegistration(c)"></va-empty>
              </va-section-card>
            }
          }

          @case ('documents') {
            <va-section-card title="Documents shared" hint="Approved knowledge sent to this candidate">
              @if (sharedDocs().length) {
                <div class="stack gap-2">
                  @for (d of sharedDocs(); track d.name + d.ts) {
                    <div class="docrow">
                      <span class="doc-ic"><va-icon name="file-text" [size]="15"></va-icon></span>
                      <div class="grow"><div class="t-sm" style="font-weight:600">{{ d.name }}</div><span class="t-cap t-muted">Shared {{ relTimeLive(d.ts) }} · via {{ chLabel(d.channel) }}</span></div>
                      <va-approval-chip state="approved"></va-approval-chip>
                    </div>
                  }
                </div>
              } @else {
                <va-empty icon="file-text" title="No documents shared" message="No approved documents have been sent to this candidate yet."></va-empty>
              }
            </va-section-card>
          }

          @case ('notes') {
            <va-section-card title="Internal notes" hint="Visible to staff only">
              <button actions class="btn btn-sm btn-primary" (click)="addNote()"><va-icon name="plus" [size]="14"></va-icon>Add note</button>
              @if (notes().length) {
                <div class="stack gap-3">
                  @for (n of notes(); track n.id) {
                    <div class="note">
                      <va-avatar [name]="n.actor" [hue]="210" [size]="28"></va-avatar>
                      <div class="grow">
                        <div class="row gap-2"><span class="t-sm" style="font-weight:600">{{ n.actor }}</span><span class="t-cap t-muted">{{ relTime(n.ts) }}</span></div>
                        <p class="t-sm">{{ n.detail }}</p>
                      </div>
                    </div>
                  }
                </div>
              } @else {
                <va-empty icon="edit" title="No notes yet" message="Add an internal note to keep the team aligned on this candidate."
                  cta="Add note" ctaIcon="plus" (action)="addNote()"></va-empty>
              }
            </va-section-card>
          }

          @case ('insights') {
            <div class="stack gap-4">
              <div class="banner ai">
                <va-icon name="sparkles" [size]="16"></va-icon>
                <span class="t-sm">AI insights are generated from this candidate's interactions and approved institutional knowledge. They never invent fees, scholarships or placement figures.</span>
              </div>
              <va-section-card title="AI insights" hint="Tailored to this candidate">
                <div class="stack gap-3">
                  @for (i of aiInsights(); track i.title) {
                    <div class="insight" [attr.data-tone]="i.tone">
                      <va-icon [name]="i.tone === 'warning' ? 'alert-triangle' : 'sparkles'" [size]="16"></va-icon>
                      <div><div class="t-sm" style="font-weight:600">{{ i.title }}</div><p class="t-sm">{{ i.body }}</p></div>
                    </div>
                  }
                </div>
              </va-section-card>
            </div>
          }

          @case ('audit') {
            <va-section-card title="Audit trail" hint="Immutable record of every action">
              <table class="va-table">
                <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead>
                <tbody>
                  @for (a of audit(); track a.id) {
                    <tr>
                      <td class="t-num"><span class="t-sm">{{ relTime(a.ts) }}</span><br><span class="t-cap t-muted">{{ fmtTime(a.ts) }}</span></td>
                      <td><span class="audit-actor" [class.ai]="a.isAi">{{ a.actor }}</span></td>
                      <td class="t-sm" style="font-weight:600">{{ a.action }}</td>
                      <td class="t-sm t-muted">{{ a.detail }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </va-section-card>
          }
        }
      </div>

      <!-- ===================== ACTION RAIL ===================== -->
      <aside class="cp-rail">
        @if (recommendedAction(); as ra) {
          <va-section-card title="Recommended next action">
            <span actions class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon>AI</span>
            <div class="stack gap-2">
              <p class="t-sm"><va-icon name="zap" [size]="14"></va-icon> {{ ra }}</p>
              <span class="t-cap t-muted">From {{ c.assignedAiCounselor }}'s latest session analysis</span>
            </div>
          </va-section-card>
        }

        <va-section-card title="Next follow-up">
          <div class="row between">
            <div class="row gap-2"><va-icon name="calendar" [size]="16"></va-icon><span class="t-sm" style="font-weight:600">{{ c.nextFollowUp ? fmtDate(c.nextFollowUp) : 'Not scheduled' }}</span></div>
            @if (c.nextFollowUp) { <span class="chip" [class.dnc]="overdue(c)">{{ relFutureLive(c.nextFollowUp) }}</span> }
          </div>
          <button class="btn btn-ghost btn-block" style="margin-top:12px" (click)="scheduleFollowUp(c)"><va-icon name="clock" [size]="15"></va-icon>{{ c.nextFollowUp ? 'Reschedule' : 'Schedule follow-up' }}</button>
        </va-section-card>

        <va-section-card title="Quick stats">
          <div class="qstats">
            <div class="tile"><span class="tl">Journey events</span><span class="tv t-num">{{ journey().length }}</span></div>
            <div class="tile"><span class="tl">Conversations</span><span class="tv t-num">{{ conversations().length }}</span></div>
            <div class="tile"><span class="tl">Open concerns</span><span class="tv t-num">{{ c.pendingQuestions.length }}</span></div>
            <div class="tile"><span class="tl">Docs shared</span><span class="tv t-num">{{ sharedDocs().length }}</span></div>
          </div>
          <dl class="dl" style="margin-top:14px">
            <dt>Consent — call</dt><dd>{{ c.consent.call ? 'Yes' : 'No' }}</dd>
            <dt>Consent — WhatsApp</dt><dd>{{ c.consent.whatsapp ? 'Yes' : 'No' }}</dd>
            <dt>Consent — recording</dt><dd>{{ c.consent.recording ? 'Yes' : 'No' }}</dd>
          </dl>
        </va-section-card>
      </aside>
    </div>
  </div>
} @else {
  <div class="page">
    <va-empty icon="user" title="Candidate not found"
      message="We couldn't find a candidate with this ID. It may have been merged, removed, or the link is out of date."
      cta="Back to CRM" ctaIcon="arrow-left" (action)="backToCrm()"></va-empty>
  </div>
}
  `,
  styles: [`
    :host { display: block; }
    .cp { display: flex; flex-direction: column; gap: var(--s-4); }

    /* ---- header ---- */
    .cp-head {
      position: sticky; top: 0; z-index: 20;
      display: grid; grid-template-columns: auto auto 1fr auto; align-items: center;
      gap: var(--s-4); padding: var(--s-4) var(--s-6); backdrop-filter: blur(8px);
      background: color-mix(in srgb, var(--color-surface) 92%, transparent);
    }
    .back { width: 36px; height: 36px; border-radius: var(--r-md); display: grid; place-items: center;
      border: 1px solid var(--color-border); color: var(--color-text-muted); background: var(--color-surface); transition: background .15s, color .15s; }
    .back:hover { background: var(--color-surface-alt); color: var(--color-text); }
    .head-id { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
    .meta { row-gap: 6px; }
    .assign { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-sm); font-weight: 600; color: var(--color-accent-2); }
    .assign va-icon { color: var(--color-accent-2); }
    .assign.human { color: var(--color-primary); } .assign.human va-icon { color: var(--color-primary); }
    .assign.t-muted, .assign.t-muted va-icon { color: var(--color-text-muted); font-weight: 500; }
    .tag { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .dnc { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    .head-actions { display: flex; align-items: center; gap: 6px; }
    .head-actions .wa:hover { color: var(--ch-whatsapp); border-color: color-mix(in srgb, var(--ch-whatsapp) 40%, var(--color-border)); }
    .more { position: relative; }
    .menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 30; min-width: 200px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-md); box-shadow: var(--e3); padding: 6px; display: flex; flex-direction: column; gap: 2px; }
    .menu button { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; background: transparent; border: none;
      padding: 9px 10px; border-radius: var(--r-sm); font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
    .menu button:hover { background: var(--color-surface-alt); }
    .menu button.danger { color: var(--color-danger); } .menu button.danger va-icon { color: var(--color-danger); }

    /* probability strip spans full header width */
    .prob-strip { grid-column: 1 / -1; display: flex; align-items: stretch; gap: var(--s-4); margin-top: 2px;
      padding-top: var(--s-3); border-top: 1px solid var(--color-border); flex-wrap: wrap; }
    .ps-cell { display: flex; flex-direction: column; gap: 5px; min-width: 130px; }
    .ps-lab { letter-spacing: .04em; text-transform: uppercase; }
    .ps-val { font-size: var(--text-sm); font-weight: 700; }
    .ps-val.warn { color: var(--color-danger); }
    .ps-trend { color: var(--color-success); }
    .ps-div { width: 1px; background: var(--color-border); }

    /* ---- tabs ---- */
    .cp-tabs { padding: 0 var(--s-2); }

    /* ---- body layout ---- */
    .cp-body { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: var(--s-6); align-items: start; }
    .cp-main { display: flex; flex-direction: column; gap: var(--s-4); min-width: 0; }
    .cp-rail { display: flex; flex-direction: column; gap: var(--s-4); position: sticky; top: 150px; }

    .ov-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); }

    /* AI summary */
    .ai-summary { align-items: flex-start; gap: var(--s-3); }
    .ai-summary p { margin-top: 4px; }
    .chip.approved { background: rgba(var(--color-accent-rgb), .12); color: var(--color-primary); border-color: transparent; }
    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }

    .dl dd.truncate { max-width: 200px; }

    .meter { display: flex; flex-direction: column; gap: 8px; }
    .tilegrid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2); }
    .tile .tv { display: flex; align-items: center; }

    .course-pref { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3); border-radius: var(--r-md); background: var(--color-surface-2); border: 1px solid var(--color-border); }
    .course-pref va-icon { color: var(--color-primary); flex: none; }
    .course-pref.big { gap: var(--s-4); }
    .course-pref.big va-probability-badge { margin-left: auto; }

    .parent-mini { display: flex; align-items: center; gap: var(--s-3); }

    .chip.consent-ok { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .chip.consent-no { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    .pq { display: flex; align-items: center; gap: var(--s-3); padding: 11px 13px; border-radius: var(--r-md);
      background: var(--color-surface-2); border: 1px solid var(--color-border); }
    .pq va-icon { color: var(--color-warning); flex: none; }

    /* conversations */
    .conv-list { display: flex; flex-direction: column; }
    .conv { display: flex; gap: var(--s-3); padding: var(--s-3) 0; border-bottom: 1px solid var(--color-border); }
    .conv:last-child { border-bottom: none; }
    .conv-ic { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .conv[data-ch='voice'] .conv-ic { color: var(--ch-voice); } .conv[data-ch='whatsapp'] .conv-ic { color: var(--ch-whatsapp); }
    .conv[data-ch='email'] .conv-ic { color: var(--ch-email); } .conv[data-ch='vcon'] .conv-ic { color: var(--ch-vcon); }
    .conv-sum { margin-top: 3px; color: var(--color-text); }
    .owner-tag { font-size: 10px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; padding: 2px 6px; border-radius: 999px; }
    .owner-tag[data-owner='ai'] { background: rgba(var(--color-accent-2-rgb), .14); color: var(--color-accent-2); }
    .owner-tag[data-owner='human'] { background: rgba(var(--color-primary-rgb), .12); color: var(--color-primary); }
    .owner-tag[data-owner='system'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .conv .time { margin-left: auto; }

    /* parents tab */
    .consent-line { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--r-md); border: 1px solid transparent; }
    .consent-line.ok { background: var(--color-success-soft); color: var(--color-success); }
    .consent-line.no { background: var(--color-danger-soft); color: var(--color-danger); }
    .guard-note { align-items: center; }
    .guard-note va-icon { color: var(--color-primary); flex: none; }

    /* application */
    .docsplit { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-6); }
    .doc-ok, .doc-miss { display: inline-flex; align-items: center; gap: 7px; font-size: var(--text-sm); font-weight: 500; }
    .doc-ok { color: var(--color-success); } .doc-miss { color: var(--color-danger); }
    .chip[data-fee='Paid'] { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .chip[data-fee='Pending'] { background: var(--color-warning-soft); color: var(--color-warning); border-color: transparent; }

    /* documents */
    .docrow { display: flex; align-items: center; gap: var(--s-3); padding: 11px 13px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .doc-ic { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--ch-email); }

    /* notes */
    .note { display: flex; gap: var(--s-3); }
    .note p { margin-top: 3px; }

    /* insights */
    .insight { display: flex; gap: 10px; padding: 12px 14px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .insight p { margin-top: 2px; color: var(--color-text-muted); }
    .insight[data-tone='warning'] { border-color: color-mix(in srgb, var(--color-warning) 30%, var(--color-border)); }
    .insight[data-tone='warning'] va-icon { color: var(--color-warning); }
    .insight[data-tone='ai'] va-icon, .insight[data-tone='positive'] va-icon { color: var(--color-accent-2); }
    .insight va-icon { flex: none; margin-top: 1px; }

    /* audit */
    .audit-actor { font-size: var(--text-sm); font-weight: 600; }
    .audit-actor.ai { color: var(--color-accent-2); }

    /* rail */
    .rna-ch { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; font-size: var(--text-cap); font-weight: 700;
      padding: 4px 10px; border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); }
    .rna-ch[data-ch='whatsapp'] { color: var(--ch-whatsapp); } .rna-ch[data-ch='voice'] { color: var(--ch-voice); }
    .rna-ch[data-ch='email'] { color: var(--ch-email); } .rna-ch[data-ch='vcon'] { color: var(--ch-vcon); }
    .rna-btn { justify-content: flex-start; padding: 14px 16px; font-size: var(--text-sm); height: auto; }
    .reason { display: flex; gap: 6px; align-items: flex-start; }
    .reason va-icon { flex: none; margin-top: 2px; }
    .qstats { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2); }
    .chip.dnc { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    @media (max-width: 1200px) {
      .cp-body { grid-template-columns: 1fr; }
      .cp-rail { position: static; }
    }
    @media (max-width: 720px) {
      .ov-grid, .tilegrid, .docsplit, .qstats { grid-template-columns: 1fr; }
      .cp-head { grid-template-columns: auto 1fr; }
      .head-actions { grid-column: 1 / -1; }
    }
    /* lead-priority (temperature) badge */
    .prio { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
      font-weight: 600; text-transform: capitalize; border: 1px solid transparent; white-space: nowrap; }
    .prio.hot  { color: #b91c1c; background: #fef2f2; border-color: #fecaca; }
    .prio.warm { color: #b45309; background: #fffbeb; border-color: #fde68a; }
    .prio.cold { color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }
  `],
})
export class CandidateProfileComponent {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private store = inject(DataStore);
  private api = inject(BusinessApiService);
  private toast = inject(ToastService);
  auth = inject(AuthService);

  private id = this.route.snapshot.paramMap.get('id') ?? '';
  readonly candidate = signal<Candidate | undefined>(this.store.candidateById(this.id));

  // Real conversation sessions (BusinessLayer) — drive Journey + Conversations
  // for backed leads; seed candidates keep their generated journey.
  readonly sessions = signal<LeadSession[]>([]);
  private backed = computed(() => !!this.candidate()?.backed);

  constructor() {
    const existing = this.store.candidateById(this.id);
    if (existing?.backed) {
      this.loadSessions();
    } else if (!existing) {
      // Deep-link to a real backend lead not yet in the store → fetch it.
      this.api.getLead(this.id)
        .then(d => { this.candidate.set(leadToCandidate(d)); this.loadSessions(); })
        .catch(() => undefined);
    }
  }
  private loadSessions(): void {
    this.api.listLeadSessions(this.id).then(s => this.sessions.set(s)).catch(() => undefined);
  }
  private cap(s: string): string { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  private sessionsToJourney(): JourneyEvent[] {
    const chMap: Record<string, Channel | 'system'> = { voice: 'voice', whatsapp: 'whatsapp', chat: 'web' };
    return this.sessions().slice().reverse().map(s => ({
      id: s.session_id,
      type: 'conversation',
      label: `${this.cap(s.direction)} ${s.channel === 'voice' ? 'call' : s.channel} session`,
      channel: chMap[s.channel] ?? 'note',
      owner: 'ai',
      ts: s.started_at ?? '',
      summary: ((s.analysis?.['summary'] as string) ?? null)
        ?? `${s.turns} turn(s) · ${s.status}${s.end_reason ? ' · ' + s.end_reason : ''}`,
    }));
  }
  // Real delivered documents (lead.sent_items) as journey touchpoints.
  private sentItemsToJourney(): JourneyEvent[] {
    return (this.candidate()?.sentItems ?? []).map(s => ({
      id: `doc-${s.at}-${s.item}`,
      type: 'document',
      label: 'Document shared',
      channel: (s.channel as Channel) || 'note',
      owner: 'ai',
      ts: s.at,
      summary: s.item,
      docsShared: [s.item],
    }));
  }

  tab = signal<TabKey>('overview');
  menuOpen = signal(false);

  // formatters exposed to template (frozen for seed/mock; *Live for real lead fields)
  relTime = relTime;
  relFuture = relFuture;
  relTimeLive = relTimeLive;
  relFutureLive = relFutureLive;
  fmtDate = fmtDate;
  fmtTime = fmtTime;

  journey = computed<JourneyEvent[]>(() => {
    if (this.backed()) {
      return [...this.sessionsToJourney(), ...this.sentItemsToJourney()]
        .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));   // oldest → newest
    }
    return this.candidate() ? this.store.journeyFor(this.id) : [];
  });

  conversations = computed<ConvRow[]>(() =>
    this.journey()
      .filter(e => e.channel !== 'system' && e.type !== 'document')
      .map(e => ({
        id: e.id,
        channel: e.channel,
        owner: e.owner,
        label: e.label,
        summary: e.summary,
        ts: e.ts,
      }))
      .reverse(),
  );

  application = computed(() => this.store.applications().find(a => a.candidateId === this.id));

  // Recommended next action — real backend signal: the latest analyzed session's
  // `next_best_action` (sessions returned most-recent-first). Seed candidates fall
  // back to their generated recommendation; otherwise null so the card is hidden.
  readonly recommendedAction = computed<string | null>(() => {
    for (const s of this.sessions()) {
      const nba = s.analysis?.['next_best_action'] as string | undefined;
      if (nba) return nba;
    }
    const c = this.candidate();
    return c && !c.backed && c.recommendedNextAction.label ? c.recommendedNextAction.label : null;
  });

  // Sentiment — real backend signal: the latest analyzed session's `analysis.sentiment`
  // (sessions newest-first). Seed candidates keep their generated value; null → "—".
  readonly profileSentiment = computed<Sentiment | null>(() => {
    for (const s of this.sessions()) {
      const v = s.analysis?.['sentiment'] as string | undefined;
      if (v) return SENTIMENT_MAP[v] ?? 'neutral';
    }
    const c = this.candidate();
    return c && !c.backed ? c.sentiment : null;
  });

  sharedDocs = computed<{ name: string; channel: Channel | 'system'; ts: string }[]>(() => {
    const c = this.candidate();
    // Real leads: the backend's lead.sent_items (what's actually been delivered),
    // newest first (the backend appends newest last).
    if (c?.backed) {
      return (c.sentItems ?? [])
        .map(s => ({ name: s.item, channel: ((s.channel as Channel) || 'note'), ts: s.at }))
        .reverse();
    }
    // Seed/mock candidates keep their generated journey docs.
    const out: { name: string; channel: Channel | 'system'; ts: string }[] = [];
    for (const e of this.journey()) {
      for (const d of e.docsShared ?? []) out.push({ name: d, channel: e.channel, ts: e.ts });
    }
    return out;
  });

  get tabs() {
    const c = this.candidate();
    return [
      { key: 'overview' as TabKey, label: 'Overview', count: undefined },
      { key: 'journey' as TabKey, label: 'Journey', count: this.journey().length },
      { key: 'conversations' as TabKey, label: 'Conversations', count: this.conversations().length },
      { key: 'parents' as TabKey, label: 'Parent / Guardian', count: c?.parents.length ?? 0 },
      { key: 'course' as TabKey, label: 'Course Interest', count: undefined },
      { key: 'application' as TabKey, label: 'Application', count: undefined },
      { key: 'documents' as TabKey, label: 'Documents', count: this.sharedDocs().length },
      { key: 'notes' as TabKey, label: 'Notes', count: this.notes().length },
      { key: 'insights' as TabKey, label: 'AI Insights', count: undefined },
      { key: 'audit' as TabKey, label: 'Audit', count: undefined },
    ];
  }

  // ---- inline realistic mock content ----
  notes = signal<AuditRow[]>([
    { id: 'note-1', actor: 'Rahul Desai', isAi: false, action: 'Note', detail: 'Father wants placement assurance before committing. Loop in a senior counselor for the V-Con.', ts: this.daysAgo(2) },
    { id: 'note-2', actor: 'Priya Menon', isAi: false, action: 'Note', detail: 'High-intent — prioritise scholarship eligibility check this week.', ts: this.daysAgo(5) },
  ]);

  aiInsights = computed<{ title: string; body: string; tone: 'ai' | 'positive' | 'warning' }[]>(() => {
    const c = this.candidate();
    const out: { title: string; body: string; tone: 'ai' | 'positive' | 'warning' }[] = [
      { title: 'High-intent signal', body: `Conversion probability is ${c?.conversionProbability ?? 0}% — above cohort average for ${c?.preferredCourse ?? 'this course'}. A timely follow-up keeps momentum.`, tone: 'ai' as const },
      { title: 'Best channel', body: 'WhatsApp follow-ups convert 1.8× better than email for high-intent candidates in this segment.', tone: 'positive' as const },
    ];
    if (c?.scholarshipInterest) out.push({ title: 'Scholarship eligibility pending', body: 'Scholarship interest detected. Confirm eligibility from the approved policy before quoting any figures — never improvise scholarship amounts.', tone: 'warning' as const });
    if (c?.parentEngagement === 'Concerns Raised') out.push({ title: 'Parent concerns open', body: 'A parent has raised concerns. Sharing the approved placement report reduces escalation rate by 23%.', tone: 'warning' as const });
    return out;
  });

  audit = computed<AuditRow[]>(() => {
    const c = this.candidate();
    const ai = c?.assignedAiCounselor ?? 'Aisha';
    const rows: AuditRow[] = [
      { id: 'au-1', actor: ai, isAi: true, action: 'AI disclosure', detail: 'Identified itself as an AI counselor at the start of the first call.', ts: this.daysAgo(38) },
      { id: 'au-2', actor: ai, isAi: true, action: 'Document shared', detail: `Sent approved ${c?.preferredCourse ?? 'course'} brochure.`, ts: this.daysAgo(33) },
      { id: 'au-3', actor: 'System', isAi: false, action: 'Stage change', detail: `Stage updated to "${c?.currentStage ?? '—'}".`, ts: this.daysAgo(14) },
      { id: 'au-4', actor: ai, isAi: true, action: 'Probability update', detail: `Conversion probability recalculated to ${c?.conversionProbability ?? 0}%.`, ts: this.daysAgo(2) },
      { id: 'au-5', actor: 'Priya Menon', isAi: false, action: 'Assignment', detail: 'Reviewed candidate and confirmed AI assignment.', ts: this.daysAgo(1) },
    ];
    return rows;
  });

  // ---- helpers ----
  convIcon(ch: Channel | 'system'): string { return ch === 'system' ? 'flag' : (CHANNEL_ICON as Record<string, string>)[ch] ?? 'dot'; }
  chLabel(ch: Channel | 'system'): string { return ch === 'system' ? 'System' : (CHANNEL_LABEL as Record<string, string>)[ch] ?? ch; }
  /** Admissions lifecycle stage → display label; pre-application stages show '—'. */
  funnelLabel(stage?: string): string {
    const s = (stage ?? '').trim();
    if (!s || s === 'lead' || s === 'raw') return '—';
    return s.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }
  /** Lead temperature → display label (Hot / Warm / Cold); '—' until analyzed. */
  priorityLabel(p?: string | null): string {
    const s = (p ?? '').trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
  }
  hasConsent(c: Candidate): boolean { return c.parents.some(p => p.consentToDiscuss); }
  overdue(c: Candidate): boolean { return !!c.nextFollowUp && relFutureLive(c.nextFollowUp).includes('overdue'); }
  private daysAgo(d: number): string { const t = new Date('2026-06-14T09:30:00'); t.setDate(t.getDate() - d); return t.toISOString(); }

  // ---- actions ----
  toggleMenu() { this.menuOpen.update(v => !v); }
  backToCrm() { this.router.navigateByUrl('/app/crm'); }
  routeToKms() { this.router.navigateByUrl('/app/kms'); }

  quickCall(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }
  quickEmail(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }
  quickVcon(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }

  changeStatus() { this.menuOpen.set(false); this.toast.info('Coming soon', 'clock'); }
  assign() { this.menuOpen.set(false); this.toast.info('Coming soon', 'clock'); }
  escalate(_c: Candidate) { this.menuOpen.set(false); this.toast.info('Coming soon', 'clock'); }

  openEvent(e: JourneyEvent) { this.toast.info(`${e.label} · ${this.chLabel(e.channel)}`, this.convIcon(e.channel)); }
  sendRegistration(_c: Candidate) { this.toast.info('Coming soon', 'clock'); }
  addNote() { this.toast.info('Add an internal note (staff-only).', 'edit'); }
  async scheduleFollowUp(c: Candidate) {
    try {
      await this.api.scheduleFollowup(this.id);
      this.toast.success(`Follow-up scheduled for ${c.name}.`, 'calendar');
      const d = await this.api.getLead(this.id);
      this.candidate.set(leadToCandidate(d));
    } catch {
      this.toast.danger('Could not schedule follow-up — the leads service is unavailable.');
    }
  }
}
