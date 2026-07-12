import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import {
  StatusBadgeComponent, SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent,
} from '../../shared/ui/badges.component';
import { TimelineComponent } from '../../shared/ui/timeline.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { CounselorService } from '../../core/counselor.service';
import { Candidate, Channel, JourneyEvent } from '../../domain/models';
import { CHANNEL_ICON, CHANNEL_LABEL, relTime, fmtDate, relFuture } from '../../shared/util/format';

type ChannelFilter = 'all' | Channel;

@Component({
  selector: 'va-communications',
  standalone: true,
  imports: [
    RouterLink, IconComponent, AvatarComponent, AiAvatarComponent,
    StatusBadgeComponent, SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent,
    TimelineComponent, SectionCardComponent, EmptyStateComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page comms">
      <!-- ===== Header ===== -->
      <header class="comms-head">
        <div class="comms-head-text">
          <div class="row gap-3 wrap">
            <span class="t-h2">Communication Center</span>
            <span class="cnsl-pill" [attr.data-v]="counselor.active()">
              <va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}
            </span>
            <span class="chip live-chip"><span class="dot live pulse"></span>Live unified inbox</span>
          </div>
          <p class="t-sm t-muted">
            Every voice call, WhatsApp, email and V-Con for {{ auth.institution().name }} — one {{ convoKind() }} timeline per {{ entityLabelTitle().toLowerCase() }}.
            {{ counselor.activeMeta().name }}, the {{ counselor.activeMeta().title }}, speaks only from approved knowledge and discloses it is an AI.
          </p>
        </div>
        <div class="comms-head-actions">
          <a class="btn btn-ghost btn-sm" routerLink="/app/communications/voice">
            <va-icon name="phone" [size]="16"></va-icon>Voice console
          </a>
          <a class="btn btn-ghost btn-sm" routerLink="/app/handoff">
            <va-icon name="headphones" [size]="16"></va-icon>Handoff queue
          </a>
        </div>
      </header>

      <!-- ===== Three-pane workspace ===== -->
      <div class="workspace surface">
        <!-- LEFT: candidate list -->
        <aside class="pane left">
          <div class="pane-head stack gap-3">
            <div class="search-wrap">
              <va-icon name="search" [size]="16"></va-icon>
              <input class="input search" type="text" [placeholder]="'Search ' + entityLabel() + '…'"
                     [value]="query()" (input)="onQuery($event)" />
            </div>
            <div class="seg list-seg">
              @for (s of listScopes; track s.k) {
                <button [class.active]="scope() === s.k" (click)="scope.set(s.k)">
                  {{ s.l }}<span class="seg-count t-num">{{ scopeCount(s.k) }}</span>
                </button>
              }
            </div>
          </div>
          <div class="list scroll-y">
            @for (c of filteredCandidates(); track c.candidateId) {
              <button class="lead" [class.selected]="c.candidateId === selectedId()"
                      (click)="select(c.candidateId)">
                <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="40"></va-avatar>
                <div class="lead-main">
                  <div class="row between gap-2">
                    <span class="lead-name truncate">{{ c.name }}</span>
                    <span class="t-cap t-muted lead-time">{{ relTime(c.lastContacted) }}</span>
                  </div>
                  <div class="row gap-2 lead-sub">
                    <span class="ch-dot" [attr.data-ch]="lastChannel(c)">
                      <va-icon [name]="chIcon(lastChannel(c))" [size]="12"></va-icon>
                    </span>
                    <span class="t-cap t-muted truncate grow">{{ interestValue(c) }}</span>
                    <va-sentiment-badge [value]="c.sentiment"></va-sentiment-badge>
                  </div>
                </div>
                @if (unread(c) > 0) { <span class="badge-unread t-num">{{ unread(c) }}</span> }
              </button>
            } @empty {
              <div class="list-empty">
                <va-empty icon="search" [title]="'No ' + entityLabel()"
                  [message]="'No ' + entityLabel() + ' match your search or filter.'"></va-empty>
              </div>
            }
          </div>
        </aside>

        <!-- CENTER: unified timeline -->
        <section class="pane center">
          @if (selected(); as c) {
            <div class="center-head">
              <div class="row gap-3 grow" style="min-width:0">
                <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="44"></va-avatar>
                <div class="stack" style="min-width:0">
                  <div class="row gap-2 wrap">
                    <a class="center-name truncate" [routerLink]="['/app/crm/candidate', c.candidateId]">{{ c.name }}</a>
                    <va-status-badge [status]="c.currentStage"></va-status-badge>
                  </div>
                  <div class="row gap-2 t-cap t-muted wrap center-meta">
                    <span class="row gap-1"><va-icon name="map-pin" [size]="12"></va-icon>{{ c.city }}</span>
                    <span class="sep">·</span>
                    <span class="row gap-1"><va-ai-avatar [size]="16" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ c.assignedAiCounselor }}</span>
                    @if (c.assignedHumanCounselor) {
                      <span class="sep">·</span>
                      <span class="row gap-1"><va-icon name="user" [size]="12"></va-icon>{{ c.assignedHumanCounselor }}</span>
                    }
                  </div>
                </div>
              </div>
              <div class="center-actions">
                <button class="btn btn-accent btn-sm" (click)="summarize(c)">
                  <va-icon name="sparkles" [size]="16"></va-icon>Summarize
                </button>
                <button class="btn btn-primary btn-sm" (click)="takeOver(c)">
                  <va-icon name="headphones" [size]="16"></va-icon>Take over
                </button>
              </div>
            </div>

            <!-- channel quick links -->
            <div class="quick-links">
              <span class="t-cap t-muted ql-label">Open in console</span>
              <a class="ql ql-voice" routerLink="/app/communications/voice">
                <va-icon name="phone" [size]="14"></va-icon>Voice
              </a>
              <a class="ql ql-whatsapp" [routerLink]="['/app/communications/whatsapp', c.candidateId]">
                <va-icon name="message-circle" [size]="14"></va-icon>WhatsApp
              </a>
              <a class="ql ql-email" routerLink="/app/communications/email">
                <va-icon name="mail" [size]="14"></va-icon>Email
              </a>
              <a class="ql ql-vcon" routerLink="/app/communications/voice">
                <va-icon name="video" [size]="14"></va-icon>V-Con
              </a>
            </div>

            <!-- filter chips + conversation search -->
            <div class="filter-row">
              <div class="ch-chips wrap">
                @for (f of channelFilters; track f.k) {
                  <button class="ch-chip" [class.active]="channel() === f.k" [attr.data-ch]="f.k"
                          (click)="channel.set(f.k)">
                    @if (f.k !== 'all') { <va-icon [name]="chIcon(f.k)" [size]="13"></va-icon> }
                    {{ f.l }}
                    <span class="cc-count t-num">{{ channelCount(f.k) }}</span>
                  </button>
                }
              </div>
              <div class="search-wrap convo-search">
                <va-icon name="search" [size]="15"></va-icon>
                <input class="input search" type="text" placeholder="Search this conversation…"
                       [value]="convoQuery()" (input)="onConvoQuery($event)" />
              </div>
            </div>

            <!-- timeline -->
            <div class="timeline-scroll scroll-y">
              @if (visibleEvents().length > 0) {
                <va-timeline [events]="visibleEvents()" [clickable]="true"
                             (openEvent)="openEvent($event)"></va-timeline>
              } @else {
                <va-empty icon="message-square" title="No matching messages"
                  message="No interactions match the selected channel or search. Adjust the filters above."></va-empty>
              }
            </div>
          } @else {
            <div class="center-placeholder">
              <va-empty icon="inbox" [title]="'Select a ' + entityLabelTitle().toLowerCase()"
                [message]="'Choose a ' + entityLabelTitle().toLowerCase() + ' from the list to view their unified ' + convoKind() + ' history across every channel.'"></va-empty>
            </div>
          }
        </section>

        <!-- RIGHT: mini profile -->
        <aside class="pane right scroll-y">
          @if (selected(); as c) {
            <div class="profile-top center stack gap-2">
              <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="64"></va-avatar>
              <div class="t-h4">{{ c.name }}</div>
              <va-status-badge [status]="c.currentStage"></va-status-badge>
              @for (t of c.tags; track t) { <span class="chip tag-chip">{{ t }}</span> }
            </div>

            <div class="prob-block">
              <div class="row between">
                <span class="t-cap t-muted">{{ career() ? 'Career-readiness' : 'Conversion probability' }}</span>
                <va-band-chip [band]="c.dropOffRisk" [label]="riskLabel(c)"></va-band-chip>
              </div>
              <va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge>
            </div>

            <va-section-card title="Key facts">
              <dl class="dl">
                <dt>{{ interestTermTitle() }}</dt><dd>{{ interestValue(c) }}</dd>
                <dt>Sentiment</dt>
                <dd><va-sentiment-badge [value]="c.sentiment" [showLabel]="true"></va-sentiment-badge></dd>
                <dt>Location</dt><dd>{{ c.city }}, {{ c.region }}</dd>
                @if (career()) {
                  <dt>Focus skills</dt><dd>Python · SQL · Cloud</dd>
                } @else {
                  <dt>Budget</dt><dd>{{ c.budgetRange }}</dd>
                }
                <dt>{{ career() ? 'Source' : 'Lead source' }}</dt><dd>{{ c.leadSource }}</dd>
                <dt>{{ career() ? 'Mentor' : 'Parents' }}</dt><dd>{{ c.parentEngagement }}</dd>
                @if (c.nextFollowUp) { <dt>Follow-up</dt><dd>{{ relFuture(c.nextFollowUp) }}</dd> }
                <dt>Last contact</dt><dd>{{ fmtDate(c.lastContacted) }}</dd>
              </dl>
            </va-section-card>

            <div class="banner ai ai-summary">
              <va-icon name="sparkles" [size]="16"></va-icon>
              <div class="stack gap-1">
                <span class="t-cap ai-summary-label">{{ counselor.activeMeta().name }} summary · approved-knowledge-only</span>
                <p class="t-sm">{{ c.lastAiSummary }}</p>
              </div>
            </div>

            @if (c.recommendedNextAction; as na) {
              <div class="next-action">
                <div class="row gap-2 na-head">
                  <span class="na-ic" [attr.data-ch]="na.channel"><va-icon [name]="chIcon(na.channel)" [size]="14"></va-icon></span>
                  <div class="stack gap-1">
                    <span class="t-cap t-muted">Recommended next action</span>
                    <span class="t-sm na-label">{{ na.label }}</span>
                  </div>
                </div>
                <p class="t-cap t-muted na-reason">{{ na.reason }}</p>
              </div>
            }

            @if (c.consent; as cn) {
              <div class="consent-row">
                <span class="t-cap t-muted">Consent</span>
                <div class="row gap-1 wrap">
                  <span class="consent-pill" [class.on]="cn.call"><va-icon name="phone" [size]="11"></va-icon></span>
                  <span class="consent-pill" [class.on]="cn.whatsapp"><va-icon name="message-circle" [size]="11"></va-icon></span>
                  <span class="consent-pill" [class.on]="cn.email"><va-icon name="mail" [size]="11"></va-icon></span>
                  <span class="consent-pill" [class.on]="cn.recording"><va-icon name="mic" [size]="11"></va-icon></span>
                </div>
              </div>
            }

            <a class="btn btn-ghost btn-block" [routerLink]="['/app/crm/candidate', c.candidateId]">
              <va-icon name="external-link" [size]="16"></va-icon>Open full profile
            </a>
          }
        </aside>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .comms { display: flex; flex-direction: column; gap: var(--s-4); height: calc(100vh - var(--topbar-h)); max-width: var(--content-max); }

    /* header */
    .comms-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-4); flex-wrap: wrap; }
    .comms-head-text p { margin-top: 4px; max-width: 78ch; }
    .comms-head-actions { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
    .live-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    /* workspace grid */
    .workspace { display: grid; grid-template-columns: 308px minmax(0, 1fr) 300px; flex: 1; min-height: 0; overflow: hidden; padding: 0; }
    .pane { min-height: 0; display: flex; flex-direction: column; }
    .pane.left { border-right: 1px solid var(--color-border); }
    .pane.right { border-left: 1px solid var(--color-border); padding: var(--s-4); gap: var(--s-4); }

    /* left pane */
    .pane-head { padding: var(--s-4); border-bottom: 1px solid var(--color-border); }
    .search-wrap { position: relative; display: flex; align-items: center; }
    .search-wrap va-icon { position: absolute; left: 11px; color: var(--color-text-muted); pointer-events: none; }
    .search-wrap .search { padding-left: 34px; }
    .list-seg { width: 100%; }
    .list-seg button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
    .seg-count { font-size: 11px; opacity: .75; }
    .list { flex: 1; padding: var(--s-2); display: flex; flex-direction: column; gap: 2px; }
    .lead { position: relative; display: flex; align-items: center; gap: var(--s-3); width: 100%; text-align: left;
      padding: 9px 10px; border-radius: var(--r-md); border: 1px solid transparent; background: transparent; transition: background .12s, border-color .12s; }
    .lead:hover { background: var(--color-surface-alt); }
    .lead.selected { background: rgba(var(--color-primary-rgb), .08); border-color: rgba(var(--color-primary-rgb), .22); }
    .lead-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .lead-name { font-size: var(--text-sm); font-weight: 600; }
    .lead-time { flex: none; }
    .lead-sub { min-width: 0; }
    .ch-dot { width: 18px; height: 18px; border-radius: 6px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .ch-dot[data-ch='voice'] { color: var(--ch-voice); } .ch-dot[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .ch-dot[data-ch='email'] { color: var(--ch-email); } .ch-dot[data-ch='vcon'] { color: var(--ch-vcon); }
    .badge-unread { position: absolute; top: 8px; right: 9px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px;
      background: var(--color-primary); color: #fff; font-size: 10px; font-weight: 700; display: grid; place-items: center; }
    .list-empty { padding: var(--s-4) 0; }

    /* center pane */
    .center { background: var(--color-surface-2); }
    .center-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-3);
      padding: var(--s-4); border-bottom: 1px solid var(--color-border); background: var(--color-surface); }
    .center-name { font-size: var(--text-h4); font-weight: 700; color: var(--color-text); }
    .center-name:hover { color: var(--color-primary); }
    .center-meta { margin-top: 3px; } .center-meta .sep { opacity: .5; }
    .center-actions { display: flex; align-items: center; gap: var(--s-2); flex: none; }

    .quick-links { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap;
      padding: 10px var(--s-4); border-bottom: 1px solid var(--color-border); background: var(--color-surface); }
    .ql-label { margin-right: 2px; }
    .ql { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600;
      padding: 5px 11px; border-radius: var(--r-pill); border: 1px solid var(--color-border); background: var(--color-surface);
      color: var(--color-text-muted); transition: all .12s; }
    .ql:hover { background: var(--color-surface-alt); color: var(--color-text); }
    .ql-voice:hover { color: var(--ch-voice); border-color: color-mix(in srgb, var(--ch-voice) 40%, var(--color-border)); }
    .ql-whatsapp:hover { color: var(--ch-whatsapp); border-color: color-mix(in srgb, var(--ch-whatsapp) 40%, var(--color-border)); }
    .ql-email:hover { color: var(--ch-email); border-color: color-mix(in srgb, var(--ch-email) 40%, var(--color-border)); }
    .ql-vcon:hover { color: var(--ch-vcon); border-color: color-mix(in srgb, var(--ch-vcon) 40%, var(--color-border)); }

    .filter-row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap;
      padding: 10px var(--s-4); border-bottom: 1px solid var(--color-border); }
    .ch-chips { display: flex; align-items: center; gap: 6px; }
    .ch-chip { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600;
      padding: 5px 10px; border-radius: var(--r-pill); border: 1px solid var(--color-border); background: var(--color-surface);
      color: var(--color-text-muted); transition: all .12s; }
    .ch-chip:hover { background: var(--color-surface-alt); }
    .ch-chip .cc-count { font-size: 10px; background: var(--color-surface-alt); padding: 1px 6px; border-radius: 999px; }
    .ch-chip.active { background: rgba(var(--color-primary-rgb), .1); color: var(--color-primary); border-color: rgba(var(--color-primary-rgb), .25); }
    .ch-chip.active[data-ch='voice'] { background: color-mix(in srgb, var(--ch-voice) 12%, var(--color-surface)); color: var(--ch-voice); border-color: color-mix(in srgb, var(--ch-voice) 40%, var(--color-border)); }
    .ch-chip.active[data-ch='whatsapp'] { background: color-mix(in srgb, var(--ch-whatsapp) 14%, var(--color-surface)); color: var(--ch-whatsapp); border-color: color-mix(in srgb, var(--ch-whatsapp) 45%, var(--color-border)); }
    .ch-chip.active[data-ch='email'] { background: color-mix(in srgb, var(--ch-email) 14%, var(--color-surface)); color: var(--ch-email); border-color: color-mix(in srgb, var(--ch-email) 45%, var(--color-border)); }
    .ch-chip.active[data-ch='vcon'] { background: color-mix(in srgb, var(--ch-vcon) 14%, var(--color-surface)); color: var(--ch-vcon); border-color: color-mix(in srgb, var(--ch-vcon) 45%, var(--color-border)); }
    .ch-chip.active .cc-count { background: rgba(255,255,255,.4); }
    .convo-search { flex: 1; max-width: 280px; min-width: 180px; }

    .timeline-scroll { flex: 1; padding: var(--s-4) var(--s-6); }

    .center-placeholder { flex: 1; display: grid; place-items: center; }

    /* right pane */
    .profile-top { text-align: center; }
    .tag-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .prob-block { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border-radius: var(--r-md);
      background: var(--color-surface-alt); }
    .prob-block va-probability-badge { width: 100%; }
    .ai-summary { align-items: flex-start; }
    .ai-summary va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }
    .ai-summary-label { font-weight: 700; letter-spacing: .02em; text-transform: uppercase; color: var(--color-accent-2); }
    .ai-summary p { margin: 0; }
    .next-action { padding: 12px 14px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface); }
    .na-head { align-items: flex-start; }
    .na-ic { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .na-ic[data-ch='voice'] { color: var(--ch-voice); } .na-ic[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .na-ic[data-ch='email'] { color: var(--ch-email); } .na-ic[data-ch='vcon'] { color: var(--ch-vcon); }
    .na-label { font-weight: 600; }
    .na-reason { margin: 8px 0 0; }
    .consent-row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); }
    .consent-pill { width: 24px; height: 24px; border-radius: 7px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); opacity: .55; }
    .consent-pill.on { background: var(--color-success-soft); color: var(--color-success); opacity: 1; }

    /* responsive */
    @media (max-width: 1180px) {
      .workspace { grid-template-columns: 280px minmax(0, 1fr); }
      .pane.right { display: none; }
    }
    @media (max-width: 880px) {
      .comms { height: auto; }
      .workspace { grid-template-columns: 1fr; }
      .pane.left { border-right: none; border-bottom: 1px solid var(--color-border); }
      .list { max-height: 320px; }
      .pane.center { min-height: 460px; }
    }
  `],
})
export class CommunicationsComponent {
  private store = inject(DataStore);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);

  // ---- counselor mode ----
  career = computed(() => this.counselor.active() === 'career');
  // domain term relabels that flip with the active counselor
  entityLabel = computed(() => this.career() ? 'students' : 'candidates');
  entityLabelTitle = computed(() => this.career() ? 'Student' : 'Candidate');
  interestTermTitle = computed(() => this.career() ? 'Career interest' : 'Course');
  convoKind = computed(() => this.career() ? 'career-guidance' : 'admissions');
  // career-flavored pathway for the chosen student
  private readonly careerPathways = [
    'Data Scientist', 'Software Engineer', 'UX Designer', 'Product Manager', 'Cloud Architect',
  ];
  pathwayFor = (c: Candidate): string => {
    if (c.careerInterests.length) return c.careerInterests[0];
    const i = Math.abs(c.candidateId.split('').reduce((s, ch) => s + ch.charCodeAt(0), 0)) % this.careerPathways.length;
    return this.careerPathways[i];
  };
  interestValue = (c: Candidate): string => this.career() ? this.pathwayFor(c) : c.preferredCourse;

  // exposed format helpers
  relTime = relTime;
  fmtDate = fmtDate;
  relFuture = relFuture;
  chIcon = (c: Channel | 'system') => (CHANNEL_ICON as Record<string, string>)[c] ?? 'dot';

  query = signal('');
  convoQuery = signal('');
  scope = signal<'all' | 'high' | 'mine'>('all');
  channel = signal<ChannelFilter>('all');

  listScopes = [
    { k: 'all' as const, l: 'All' },
    { k: 'high' as const, l: 'High intent' },
    { k: 'mine' as const, l: 'Assigned' },
  ];

  channelFilters: { k: ChannelFilter; l: string }[] = [
    { k: 'all', l: 'All' },
    { k: 'voice', l: CHANNEL_LABEL.voice },
    { k: 'whatsapp', l: CHANNEL_LABEL.whatsapp },
    { k: 'email', l: CHANNEL_LABEL.email },
    { k: 'vcon', l: CHANNEL_LABEL.vcon },
  ];

  private allCandidates = computed(() =>
    [...this.store.candidates()].sort((a, b) => +new Date(b.lastContacted) - +new Date(a.lastContacted)));

  private scoped = computed(() => {
    const me = this.auth.user().name;
    switch (this.scope()) {
      case 'high': return this.allCandidates().filter(c => c.conversionProbability >= 70);
      case 'mine': return this.allCandidates().filter(c => c.assignedHumanCounselor === me);
      default: return this.allCandidates();
    }
  });

  filteredCandidates = computed(() => {
    const q = this.query().trim().toLowerCase();
    const base = this.scoped();
    if (!q) return base;
    return base.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.preferredCourse.toLowerCase().includes(q) ||
      c.city.toLowerCase().includes(q));
  });

  selectedId = signal<string>(this.store.candidates()[0]?.candidateId ?? '');
  selected = computed<Candidate | undefined>(() => this.store.candidateById(this.selectedId()));

  private journey = computed<JourneyEvent[]>(() => this.store.journeyFor(this.selectedId()));

  visibleEvents = computed<JourneyEvent[]>(() => {
    const ch = this.channel();
    const q = this.convoQuery().trim().toLowerCase();
    return this.journey().filter(e => {
      if (ch !== 'all' && e.channel !== ch) return false;
      if (q && !(e.label.toLowerCase().includes(q) || e.summary.toLowerCase().includes(q))) return false;
      return true;
    });
  });

  channelCount(f: ChannelFilter): number {
    const j = this.journey();
    return f === 'all' ? j.length : j.filter(e => e.channel === f).length;
  }

  scopeCount(s: 'all' | 'high' | 'mine'): number {
    const me = this.auth.user().name;
    const all = this.allCandidates();
    if (s === 'high') return all.filter(c => c.conversionProbability >= 70).length;
    if (s === 'mine') return all.filter(c => c.assignedHumanCounselor === me).length;
    return all.length;
  }

  lastChannel(c: Candidate): Channel {
    const j = this.store.journeyFor(c.candidateId);
    for (let i = j.length - 1; i >= 0; i--) {
      const ch = j[i].channel;
      if (ch !== 'system') return ch;
    }
    return c.recommendedNextAction.channel;
  }

  unread(c: Candidate): number {
    // deterministic pseudo-unread for high-intent, recently engaged candidates
    if (c.conversionProbability > 70 && c.sentiment !== 'very-neg') {
      return (c.pendingQuestions.length || 0) + (c.parentEngagement === 'Concerns Raised' ? 1 : 0);
    }
    return 0;
  }

  riskLabel(c: Candidate): string {
    const term = this.career() ? 'disengagement' : 'drop-off';
    return c.dropOffRisk === 'high' ? `High ${term} risk`
      : c.dropOffRisk === 'med' ? 'Medium risk' : 'Low risk';
  }

  onQuery(e: Event) { this.query.set((e.target as HTMLInputElement).value); }
  onConvoQuery(e: Event) { this.convoQuery.set((e.target as HTMLInputElement).value); }

  select(id: string) {
    this.selectedId.set(id);
    this.channel.set('all');
    this.convoQuery.set('');
  }

  takeOver(c: Candidate) {
    this.toast.info(`You are taking over ${c.name}'s ${this.convoKind()} conversation from ${this.counselor.activeMeta().name}. The AI will pause until you hand back.`, 'headphones');
  }

  summarize(c: Candidate) {
    this.toast.success(`${this.counselor.activeMeta().name} generated a fresh summary for ${c.name} from approved knowledge only.`, 'sparkles');
  }

  openEvent(e: JourneyEvent) {
    const label = e.channel === 'system' ? 'event' : CHANNEL_LABEL[e.channel];
    this.toast.info(`Opening ${label}: ${e.label}`);
  }
}
