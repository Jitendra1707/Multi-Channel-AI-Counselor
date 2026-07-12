import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import {
  SentimentBadgeComponent, ProbabilityBadgeComponent, StatusBadgeComponent,
} from '../../shared/ui/badges.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { CounselorService } from '../../core/counselor.service';
import { Candidate, Escalation } from '../../domain/models';
import { CHANNEL_ICON, CHANNEL_LABEL, relFuture, relTime } from '../../shared/util/format';

type Urgency = Escalation['urgency'];
type StatusFilter = 'all' | 'open' | 'claimed' | 'distress';

const URGENCY_RANK: Record<Urgency, number> = { Critical: 0, High: 1, Medium: 2, Low: 3 };

/** Helpline resources surfaced for emotional-distress escalations (care-first, no admissions framing). */
interface CareResource { label: string; detail: string; icon: string; }

@Component({
  selector: 'va-handoff',
  standalone: true,
  imports: [
    IconComponent, AvatarComponent, AiAvatarComponent, SectionCardComponent, EmptyStateComponent,
    SentimentBadgeComponent, ProbabilityBadgeComponent, StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <!-- Header -->
    <header class="hh-head">
      <div>
        <div class="hh-title">
          <div class="t-h2">Human Handoff Center</div>
          <span class="cnsl-pill" [attr.data-v]="counselor.active()">
            <va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}
          </span>
        </div>
        <p class="t-sm t-muted">
          Live {{ career() ? 'career-guidance' : 'admission' }} escalations where <b>{{ counselor.activeMeta().name }}</b> handed off to a human · <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }}
        </p>
      </div>
      <div class="hh-actions">
        <span class="chip"><span class="dot live"></span> {{ openCount() }} awaiting claim</span>
        <button class="btn btn-ghost btn-icon" title="Refresh queue" (click)="refresh()">
          <va-icon name="refresh" [size]="16"></va-icon>
        </button>
      </div>
    </header>

    <!-- Metric tiles -->
    <section class="tiles">
      <div class="tile-card" data-tone="default">
        <span class="tc-ic"><va-icon name="inbox" [size]="18"></va-icon></span>
        <div><div class="tc-val t-num">{{ openCount() }}</div><div class="tc-lab">Open escalations</div></div>
      </div>
      <div class="tile-card" data-tone="active">
        <span class="tc-ic"><va-icon name="headphones" [size]="18"></va-icon></span>
        <div><div class="tc-val t-num">{{ claimedCount() }}</div><div class="tc-lab">Claimed by counselors</div></div>
      </div>
      <div class="tile-card" data-tone="warning">
        <span class="tc-ic"><va-icon name="clock" [size]="18"></va-icon></span>
        <div><div class="tc-val t-num">{{ avgSla() }}</div><div class="tc-lab">Avg SLA remaining</div></div>
      </div>
      <div class="tile-card" [attr.data-tone]="distressCount() ? 'danger' : 'default'">
        <span class="tc-ic"><va-icon name="alert-triangle" [size]="18"></va-icon></span>
        <div><div class="tc-val t-num">{{ distressCount() }}</div><div class="tc-lab">Distress — care response</div></div>
      </div>
    </section>

    <!-- Two-pane -->
    <div class="hh-body">
      <!-- LEFT: prioritized queue -->
      <aside class="queue">
        <va-section-card title="Prioritized queue" [hint]="queue().length + ' active'" [flush]="true">
          <div actions class="seg seg-sm">
            @for (f of filters; track f.k) {
              <button [class.active]="filter() === f.k" (click)="filter.set(f.k)">{{ f.l }}</button>
            }
          </div>
          <div class="q-list scroll-y">
            @for (e of queue(); track e.escalationId) {
              <button class="q-card" [class.distress]="e.distress" [class.selected]="e.escalationId === selectedId()"
                      (click)="select(e)">
                @if (e.distress) {
                  <span class="distress-flag"><va-icon name="alert-triangle" [size]="12"></va-icon> DISTRESS — care response</span>
                }
                <div class="q-top">
                  <va-avatar [name]="e.candidateName" [hue]="hueFor(e.candidateId)" [size]="34"></va-avatar>
                  <div class="q-id">
                    <span class="q-name truncate">{{ e.candidateName }}</span>
                    <span class="q-reason truncate t-cap t-muted">{{ reasonFor(e) }}</span>
                  </div>
                  <span class="urg" [attr.data-u]="e.urgency">{{ e.urgency }}</span>
                </div>
                <div class="q-meta">
                  <span class="q-ch" [attr.data-ch]="e.channel" [title]="chLabel(e.channel)">
                    <va-icon [name]="chIcon(e.channel)" [size]="13"></va-icon> {{ chLabel(e.channel) }}
                  </span>
                  <va-sentiment-badge [value]="e.sentiment"></va-sentiment-badge>
                  @if (!e.distress) {
                    <span class="q-prob t-cap"><span class="t-muted">Conv</span> <b class="t-num">{{ e.conversionProbability }}%</b></span>
                  }
                </div>
                <div class="q-foot">
                  <span class="sla" [class.overdue]="isOverdue(e.slaDueAt)">
                    <va-icon name="clock" [size]="12"></va-icon> SLA {{ relFuture(e.slaDueAt) }}
                  </span>
                  @if (e.status === 'Claimed' && e.assignedTo) {
                    <span class="claimed-by t-cap"><va-icon name="user" [size]="12"></va-icon> {{ e.assignedTo }}</span>
                  } @else if (e.status === 'Open') {
                    <span class="claim-link t-cap" (click)="claim(e, $event)"><va-icon name="check-circle" [size]="13"></va-icon> Claim</span>
                  } @else {
                    <span class="chip chip-done t-cap">{{ e.status }}</span>
                  }
                </div>
              </button>
            } @empty {
              <va-empty icon="check-circle" title="Queue is clear"
                        [message]="'No escalations match this filter. ' + counselor.activeMeta().name + ' is handling conversations within approved knowledge.'"></va-empty>
            }
          </div>
        </va-section-card>
      </aside>

      <!-- RIGHT: counselor workspace -->
      <section class="workspace">
        @if (selected(); as e) {
          <!-- Distress banner pinned at top of workspace -->
          @if (e.distress) {
            <div class="distress-banner">
              <va-icon name="alert-triangle" [size]="20"></va-icon>
              <div class="db-text">
                <div class="db-title">Emotional-distress signal detected — care response active</div>
                <p class="t-sm">
                  {{ career() ? 'Career-guidance and salary framing' : 'Sales and admissions framing' }} has been <b>stopped</b> for this conversation. Lead with empathy, surface human
                  support resources, and connect to a counselor now. Do not discuss {{ career() ? 'pathways, salaries, or placements' : 'fees, scholarships, or applications' }}.
                </p>
              </div>
            </div>
          }

          <!-- Candidate profile summary -->
          <va-section-card [flush]="true">
            <div class="ws-profile">
              <va-avatar [name]="e.candidateName" [hue]="hueFor(e.candidateId)" [size]="52"></va-avatar>
              <div class="wp-main">
                <div class="row between wrap gap-2">
                  <div>
                    <div class="t-h3">{{ e.candidateName }}</div>
                    <span class="t-cap t-muted">{{ candidateSub(e) }}</span>
                  </div>
                  @if (candidate(e); as c) { <va-status-badge [status]="c.currentStage"></va-status-badge> }
                </div>
                <div class="wp-stats">
                  <div class="wp-stat">
                    <span class="t-cap t-muted">Conversion probability</span>
                    <va-probability-badge [value]="e.conversionProbability" [ai]="true"></va-probability-badge>
                  </div>
                  <div class="wp-stat">
                    <span class="t-cap t-muted">Live sentiment</span>
                    <va-sentiment-badge [value]="e.sentiment" [showLabel]="true"></va-sentiment-badge>
                  </div>
                  <div class="wp-stat">
                    <span class="t-cap t-muted">Channel</span>
                    <span class="wp-ch" [attr.data-ch]="e.channel"><va-icon [name]="chIcon(e.channel)" [size]="14"></va-icon> {{ chLabel(e.channel) }}</span>
                  </div>
                </div>
              </div>
              <div class="wp-side">
                @if (candidate(e)) {
                  <button class="btn btn-sm btn-ghost" (click)="openProfile(e)">
                    <va-icon name="external-link" [size]="14"></va-icon> Full profile
                  </button>
                }
              </div>
            </div>
          </va-section-card>

          <!-- Reason + AI summary callout -->
          <va-section-card [title]="'Why ' + counselor.activeMeta().name + ' escalated'" hint="Approved-knowledge handoff">
            <span actions class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> AI summary</span>
            <div class="reason-row">
              <span class="chip reason-chip" [class.danger]="e.distress">
                <va-icon [name]="e.distress ? 'alert-triangle' : 'flag'" [size]="13"></va-icon> {{ reasonFor(e) }}
              </span>
              <span class="sla" [class.overdue]="isOverdue(e.slaDueAt)"><va-icon name="clock" [size]="12"></va-icon> SLA {{ relFuture(e.slaDueAt) }}</span>
            </div>
            <div class="banner ai summary-callout">
              <va-ai-avatar [size]="30" [variant]="counselor.active()"></va-ai-avatar>
              <div>
                <p class="t-sm">{{ aiSummaryFor(e) }}</p>
                <span class="t-cap t-muted">{{ counselor.activeMeta().name }} speaks only from institution-approved knowledge and never invents {{ career() ? 'salaries, pathways, or placements' : 'fees, scholarships, or placements' }}.</span>
              </div>
            </div>
          </va-section-card>

          <!-- Recommended response -->
          <va-section-card [title]="e.distress ? 'Care & support script' : 'Recommended response'"
                           [hint]="e.distress ? 'Wellbeing-first — no conversion flow' : 'AI-drafted, pending your review'">
            <span actions class="chip" [class.ai-chip]="!e.distress" [class.care-chip]="e.distress">
              <va-icon [name]="e.distress ? 'shield-check' : 'wand'" [size]="12"></va-icon>
              {{ e.distress ? 'Care mode' : 'AI suggested' }}
            </span>
            <div class="reco" [class.care]="e.distress">
              <va-icon [name]="e.distress ? 'shield-check' : 'message-square'" [size]="16"></va-icon>
              <p class="t-sm">{{ recommendedResponseFor(e) }}</p>
            </div>

            @if (e.distress) {
              <div class="resources">
                <span class="t-cap t-muted res-head">Human support resources to share</span>
                @for (r of careResources; track r.label) {
                  <div class="res">
                    <span class="res-ic"><va-icon [name]="r.icon" [size]="15"></va-icon></span>
                    <div><span class="res-label">{{ r.label }}</span><span class="t-cap t-muted">{{ r.detail }}</span></div>
                  </div>
                }
              </div>
            } @else {
              <div class="reco-actions">
                <button class="btn btn-sm btn-subtle" (click)="copyDraft()"><va-icon name="paperclip" [size]="14"></va-icon> Copy draft</button>
                <button class="btn btn-sm btn-ghost" (click)="regenerate()"><va-icon name="refresh" [size]="14"></va-icon> Regenerate</button>
              </div>
            }
          </va-section-card>

          <!-- Parent details -->
          @if (parents(e).length) {
            <va-section-card [title]="career() ? 'Parent / mentor' : 'Parent / guardian'" hint="Engage with consent only">
              @for (p of parents(e); track p.parentId) {
                <div class="parent">
                  <va-avatar [name]="p.name" [hue]="280" [size]="36"></va-avatar>
                  <div class="p-main">
                    <div class="row between wrap gap-2">
                      <span class="p-name">{{ p.name }} <span class="t-cap t-muted">· {{ p.relationship }}</span></span>
                      <va-sentiment-badge [value]="p.sentiment" [showLabel]="true"></va-sentiment-badge>
                    </div>
                    <dl class="dl p-dl">
                      <dt>Language</dt><dd>{{ p.preferredLanguage }}</dd>
                      @if (p.mobile) { <dt>Mobile</dt><dd class="t-num">{{ p.mobile }}</dd> }
                      <dt>Concerns</dt><dd>{{ p.concerns.length ? p.concerns.join(', ') : '—' }}</dd>
                      <dt>Consent to discuss</dt>
                      <dd><span class="consent" [class.no]="!p.consentToDiscuss">{{ p.consentToDiscuss ? 'Given' : 'Not given' }}</span></dd>
                    </dl>
                  </div>
                </div>
              }
            </va-section-card>
          }

          <!-- Notes + actions -->
          <va-section-card title="Counselor notes" hint="Logged to the candidate journey">
            <textarea class="textarea" rows="3" [value]="note()" (input)="onNote($event)"
                      [placeholder]="e.distress ? 'Record the support offered and follow-up wellbeing check…' : 'Add context, agreed next steps, or what you committed to the candidate…'"></textarea>
            <div class="ws-actions">
              <button class="btn btn-primary" (click)="resolve(e)"><va-icon name="check-circle" [size]="16"></va-icon> Resolve escalation</button>
              <button class="btn btn-ghost" (click)="returnToAi(e)">
                <va-icon name="bot" [size]="16"></va-icon> {{ e.distress ? 'Return when stable' : 'Return to AI follow-up' }}
              </button>
              @if (e.status === 'Open') {
                <button class="btn btn-accent grow-end" (click)="claim(e)"><va-icon name="headphones" [size]="16"></va-icon> Claim this escalation</button>
              } @else if (e.status === 'Claimed') {
                <span class="chip claimed-chip grow-end"><va-icon name="user" [size]="12"></va-icon> Claimed by {{ e.assignedTo }}</span>
              }
            </div>
          </va-section-card>
        } @else {
          <div class="card ws-empty">
            <va-empty icon="headphones" title="Select an escalation"
                      message="Pick a candidate from the prioritized queue to open their counselor workspace. Distress cases are pinned to the top and styled for an immediate care response."></va-empty>
          </div>
        }
      </section>
    </div>
  </div>
  `,
  styles: [`
    :host { display: block; }

    .hh-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .hh-head p { margin-top: 4px; }
    .hh-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .hh-actions { display: flex; align-items: center; gap: 8px; }
    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}
    .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
    .dot.live { background: var(--color-success); box-shadow: 0 0 0 3px var(--color-success-soft); }

    /* metric tiles */
    .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .tile-card { display: flex; align-items: center; gap: 12px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-left: 3px solid var(--color-border); border-radius: var(--r-lg);
      padding: 14px 16px; box-shadow: var(--e1); }
    .tile-card[data-tone='active'] { border-left-color: var(--color-primary); }
    .tile-card[data-tone='warning'] { border-left-color: var(--color-warning); }
    .tile-card[data-tone='danger'] { border-left-color: var(--color-danger); background: var(--color-danger-soft); }
    .tc-ic { width: 38px; height: 38px; border-radius: var(--r-md); display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .tile-card[data-tone='danger'] .tc-ic { background: var(--color-danger); color: #fff; }
    .tc-val { font-size: 1.6rem; font-weight: 700; line-height: 1.1; }
    .tc-lab { font-size: var(--text-cap); color: var(--color-text-muted); font-weight: 500; }

    /* two-pane */
    .hh-body { display: grid; grid-template-columns: 380px minmax(0, 1fr); gap: 18px; align-items: start; }
    .queue { position: sticky; top: 0; }
    .seg-sm button { font-size: var(--text-cap); padding: 5px 9px; }

    /* queue list */
    .q-list { padding: 8px; display: flex; flex-direction: column; gap: 8px; max-height: calc(100vh - 320px); min-height: 240px; }
    .q-card { text-align: left; background: var(--color-surface); border: 1px solid var(--color-border);
      border-radius: var(--r-md); padding: 11px 12px; display: flex; flex-direction: column; gap: 9px; cursor: pointer;
      transition: border-color .14s ease, box-shadow .14s ease, transform .12s ease; }
    .q-card:hover { border-color: var(--color-border-strong); box-shadow: var(--e1); transform: translateY(-1px); }
    .q-card.selected { border-color: var(--color-accent); box-shadow: 0 0 0 1px var(--color-accent); }
    .q-card.distress { border-color: color-mix(in srgb, var(--color-danger) 55%, var(--color-border)); background: var(--color-danger-soft); }
    .q-card.distress.selected { box-shadow: 0 0 0 1px var(--color-danger); }
    .distress-flag { display: inline-flex; align-items: center; gap: 4px; align-self: flex-start; font-size: 10px; font-weight: 800;
      letter-spacing: .03em; padding: 3px 8px; border-radius: var(--r-pill); background: var(--color-danger); color: #fff; }

    .q-top { display: flex; align-items: center; gap: 10px; }
    .q-id { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .q-name { font-size: var(--text-sm); font-weight: 600; }
    .q-reason { display: block; }
    .q-meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .q-ch { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); }
    .q-ch[data-ch='voice'] { color: var(--ch-voice); } .q-ch[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .q-ch[data-ch='email'] { color: var(--ch-email); } .q-ch[data-ch='vcon'] { color: var(--ch-vcon); }
    .q-prob { color: var(--color-text); }
    .q-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-top: 1px dashed var(--color-border); padding-top: 8px; }

    .urg { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .02em; padding: 3px 8px; border-radius: var(--r-pill); flex: none; }
    .urg[data-u='Critical'] { background: var(--color-danger); color: #fff; }
    .urg[data-u='High'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .urg[data-u='Medium'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .urg[data-u='Low'] { background: var(--color-surface-alt); color: var(--color-text-muted); }

    .sla { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); }
    .sla.overdue { color: var(--color-danger); }
    .claimed-by { display: inline-flex; align-items: center; gap: 4px; color: var(--color-primary); font-weight: 600; }
    .claim-link { display: inline-flex; align-items: center; gap: 4px; color: var(--color-accent); font-weight: 700; cursor: pointer; }
    .claim-link:hover { text-decoration: underline; }
    .chip-done { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }

    /* workspace */
    .workspace { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    .ws-empty { display: grid; place-items: center; min-height: 420px; }

    .distress-banner { display: flex; gap: 12px; padding: 14px 16px; border-radius: var(--r-lg);
      background: var(--color-danger-soft); border: 1px solid color-mix(in srgb, var(--color-danger) 45%, transparent); }
    .distress-banner > va-icon { color: var(--color-danger); flex: none; margin-top: 1px; }
    .db-title { font-weight: 700; color: var(--color-danger); font-size: var(--text-base); }
    .db-text p { margin-top: 3px; }

    .ws-profile { display: flex; gap: 14px; padding: 16px 18px; align-items: flex-start; }
    .wp-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
    .wp-stats { display: flex; gap: 26px; flex-wrap: wrap; }
    .wp-stat { display: flex; flex-direction: column; gap: 5px; min-width: 140px; }
    .wp-ch { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-sm); font-weight: 600; }
    .wp-ch[data-ch='voice'] { color: var(--ch-voice); } .wp-ch[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .wp-ch[data-ch='email'] { color: var(--ch-email); } .wp-ch[data-ch='vcon'] { color: var(--ch-vcon); }
    .wp-side { flex: none; }

    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .care-chip { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    .reason-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
    .reason-chip { font-weight: 700; }
    .reason-chip.danger { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    .summary-callout { align-items: flex-start; }
    .summary-callout va-ai-avatar { flex: none; margin-top: 1px; }
    .summary-callout p { margin: 0 0 4px; }

    .reco { display: flex; gap: 10px; padding: 14px; border-radius: var(--r-md); border: 1px solid var(--color-border);
      background: var(--color-surface-alt); }
    .reco va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }
    .reco p { margin: 0; }
    .reco.care { background: var(--color-danger-soft); border-color: color-mix(in srgb, var(--color-danger) 30%, var(--color-border)); }
    .reco.care va-icon { color: var(--color-danger); }
    .reco-actions { display: flex; gap: 8px; margin-top: 12px; }

    .resources { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
    .res-head { display: block; margin-bottom: 2px; }
    .res { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface); }
    .res-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-danger-soft); color: var(--color-danger); }
    .res div { display: flex; flex-direction: column; gap: 1px; }
    .res-label { font-size: var(--text-sm); font-weight: 600; }

    .parent { display: flex; gap: 12px; }
    .parent + .parent { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--color-border); }
    .p-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
    .p-name { font-size: var(--text-sm); font-weight: 600; }
    .p-dl dt { white-space: nowrap; }
    .consent { font-weight: 600; color: var(--color-success); }
    .consent.no { color: var(--color-danger); }

    .textarea { width: 100%; resize: vertical; }
    .ws-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
    .grow-end { margin-left: auto; }
    .claimed-chip { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); border-color: transparent; }

    @media (max-width: 1100px) {
      .tiles { grid-template-columns: repeat(2, 1fr); }
      .hh-body { grid-template-columns: 1fr; }
      .queue { position: static; }
      .q-list { max-height: 520px; }
    }
    @media (max-width: 620px) {
      .wp-stats { gap: 14px; }
    }
  `],
})
export class HandoffComponent {
  private store = inject(DataStore);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);

  // ---- counselor mode ----
  career = computed(() => this.counselor.active() === 'career');

  // Career-guidance escalation flavor (pathway / salary / placement). The emotional-distress
  // item is intentionally left untouched in both modes — care content is identical.
  private readonly careerReasons = [
    'Career pathway clarity', 'Salary & growth expectations', 'Placement-guidance request', 'Upskilling plan review',
  ];
  private hashIdx(seed: string, n: number): number {
    return Math.abs(seed.split('').reduce((s, ch) => s + ch.charCodeAt(0), 0)) % n;
  }
  /** Reason shown in queue/workspace: career-flavored for non-distress escalations in career mode. */
  reasonFor = (e: Escalation): string =>
    (this.career() && !e.distress) ? this.careerReasons[this.hashIdx(e.escalationId, this.careerReasons.length)] : e.reason;

  recommendedResponseFor = (e: Escalation): string => {
    if (e.distress || !this.career()) return e.recommendedResponse;
    return `Acknowledge ${e.candidateName}'s career question, share the approved pathway map and indicative salary bands, and book a 1:1 with a placement mentor. Speak only from approved guidance — never promise a specific salary or placement.`;
  };

  aiSummaryFor = (e: Escalation): string => {
    if (e.distress || !this.career()) return e.aiSummary;
    return `${this.counselor.activeMeta().name} reached the edge of approved career guidance for ${e.candidateName} (${this.reasonFor(e)}). A human career counselor should confirm pathway fit and next upskilling steps.`;
  };

  relFuture = relFuture;
  relTime = relTime;
  chIcon = (c: Escalation['channel']) => CHANNEL_ICON[c];
  chLabel = (c: Escalation['channel']) => CHANNEL_LABEL[c];

  filter = signal<StatusFilter>('all');
  filters = [
    { k: 'all', l: 'All' }, { k: 'open', l: 'Open' }, { k: 'claimed', l: 'Claimed' }, { k: 'distress', l: 'Distress' },
  ] as const;

  note = signal('');
  selectedId = signal<string | null>(null);

  /** Distress first, then urgency, then soonest SLA. */
  private sorted = computed(() => {
    const active = this.store.escalations().filter(e => e.status === 'Open' || e.status === 'Claimed');
    return [...active].sort((a, b) => {
      if (!!b.distress !== !!a.distress) return (b.distress ? 1 : 0) - (a.distress ? 1 : 0);
      const u = URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency];
      if (u !== 0) return u;
      return new Date(a.slaDueAt).getTime() - new Date(b.slaDueAt).getTime();
    });
  });

  queue = computed(() => {
    const f = this.filter();
    return this.sorted().filter(e => {
      if (f === 'open') return e.status === 'Open';
      if (f === 'claimed') return e.status === 'Claimed';
      if (f === 'distress') return !!e.distress;
      return true;
    });
  });

  selected = computed<Escalation | undefined>(() => {
    const id = this.selectedId();
    const all = this.store.escalations();
    return (id ? all.find(e => e.escalationId === id) : undefined) ?? this.sorted()[0];
  });

  openCount = computed(() => this.store.escalations().filter(e => e.status === 'Open').length);
  claimedCount = computed(() => this.store.escalations().filter(e => e.status === 'Claimed').length);
  distressCount = computed(() => this.store.escalations().filter(e => e.distress && e.status !== 'Resolved').length);
  avgSla = computed(() => {
    const active = this.store.escalations().filter(e => e.status === 'Open' || e.status === 'Claimed');
    if (!active.length) return '—';
    const now = new Date('2026-06-14T09:30:00').getTime();
    const mins = active.map(e => (new Date(e.slaDueAt).getTime() - now) / 60000);
    const avg = Math.round(mins.reduce((s, m) => s + m, 0) / mins.length);
    if (avg <= 0) return Math.abs(avg) + 'm late';
    if (avg < 60) return avg + 'm';
    return (avg / 60).toFixed(1) + 'h';
  });

  careResources: CareResource[] = [
    { label: 'Campus Wellbeing & Counselling Cell', detail: 'Confidential support · Mon–Sat, 9 AM–7 PM', icon: 'headphones' },
    { label: 'Tele-MANAS national mental health helpline', detail: '14416 · Toll-free, 24×7, multilingual', icon: 'phone' },
    { label: 'Senior human counselor on call', detail: 'Warm-transfer this conversation immediately', icon: 'user' },
  ];

  constructor() {
    const cid = this.route.snapshot.paramMap.get('candidateId');
    if (cid) {
      const match = this.store.escalations().find(e => e.candidateId === cid);
      if (match) this.selectedId.set(match.escalationId);
    }
  }

  candidate(e: Escalation): Candidate | undefined { return this.store.candidateById(e.candidateId); }
  parents(e: Escalation) { return this.candidate(e)?.parents ?? []; }
  hueFor(candidateId: string): number { return this.store.candidateById(candidateId)?.avatarHue ?? 222; }

  candidateSub(e: Escalation): string {
    const c = this.candidate(e);
    const who = this.career() ? 'Student' : 'Candidate';
    if (!c) return who + ' · ' + e.candidateId;
    const interest = this.career()
      ? (c.careerInterests.length ? c.careerInterests[0] : 'Career pathway')
      : c.preferredCourse;
    return [interest, c.city].filter(Boolean).join(' · ');
  }

  isOverdue(iso: string): boolean { return relFuture(iso).includes('overdue'); }

  select(e: Escalation) { this.selectedId.set(e.escalationId); this.note.set(''); }

  onNote(ev: Event) { this.note.set((ev.target as HTMLTextAreaElement).value); }

  claim(e: Escalation, ev?: Event) {
    ev?.stopPropagation();
    if (e.status !== 'Open') return;
    this.store.claimEscalation(e.escalationId, this.auth.user().name);
    this.selectedId.set(e.escalationId);
    this.toast.success(`Claimed ${e.candidateName}'s escalation — you’re now the owner.`, 'headphones');
  }

  resolve(e: Escalation) {
    this.store.resolveEscalation(e.escalationId);
    this.toast.success(`Escalation for ${e.candidateName} resolved and logged to the journey.`);
    this.note.set('');
    this.selectedId.set(null);
  }

  returnToAi(e: Escalation) {
    const name = this.counselor.activeMeta().name;
    if (e.distress) {
      this.toast.info(`${name} will resume ${e.candidateName} only once a counselor confirms wellbeing.`, 'shield-check');
    } else {
      this.toast.info(`Returned ${e.candidateName} to ${name} for approved-knowledge follow-up.`, 'bot');
    }
  }

  copyDraft() { this.toast.success('Recommended response copied — review before sending.', 'paperclip'); }
  regenerate() { this.toast.info(`${this.counselor.activeMeta().name} is re-drafting from approved knowledge only…`, 'sparkles'); }

  openProfile(e: Escalation) { this.router.navigateByUrl('/app/crm/candidate/' + e.candidateId); }
  refresh() { this.toast.info('Queue refreshed — sorted by distress, then urgency.', 'refresh'); }
}
