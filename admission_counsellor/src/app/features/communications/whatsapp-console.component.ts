import {
  ChangeDetectionStrategy, Component, ElementRef, ViewChild, computed, inject, signal,
} from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import {
  SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent, ApprovalChipComponent,
} from '../../shared/ui/badges.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Candidate, ChatMessage } from '../../domain/models';
import { fmtTime, relTime } from '../../shared/util/format';

interface WaTemplate { key: string; label: string; icon: string; body: string; }
interface QuickReply { label: string; body: string; }
type CardKind = 'course-card' | 'fee-card' | 'scholarship-card';

@Component({
  selector: 'va-whatsapp-console',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink, IconComponent, AvatarComponent, AiAvatarComponent,
    SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent, ApprovalChipComponent,
  ],
  template: `
  <div class="page wa">
    <!-- ── Header ─────────────────────────────────────────── -->
    <header class="wa-head">
      <div class="wa-head-text">
        <div class="row gap-2 wrap">
          <span class="wa-mark"><va-icon name="message-circle" [size]="18"></va-icon></span>
          <h1 class="t-h2">WhatsApp console</h1>
          <span class="chip live-chip"><span class="dot live pulse"></span> Connected · Northgate Business API</span>
        </div>
        <p class="t-sm t-muted">
          Aisha replies from approved knowledge only, always discloses it is an AI, and hands over to a human
          when unsure. — {{ auth.institution().name }} · {{ auth.admissionCycle() }}
        </p>
      </div>
      <div class="wa-head-actions">
        <a class="btn btn-ghost btn-sm" routerLink="/app/handoff"><va-icon name="headphones" [size]="16"></va-icon>Handoff queue</a>
        <a class="btn btn-ghost btn-sm" routerLink="/app/communications"><va-icon name="layers" [size]="16"></va-icon>All channels</a>
      </div>
    </header>

    <!-- ── 3-pane body ────────────────────────────────────── -->
    <div class="wa-body">

      <!-- LEFT · thread list ----------------------------------- -->
      <aside class="wa-threads surface">
        <div class="tl-head">
          <div class="row between">
            <span class="t-h4">Conversations</span>
            <span class="chip">{{ threads().length }}</span>
          </div>
          <div class="tl-search">
            <va-icon name="search" [size]="15"></va-icon>
            <input class="tl-input" type="search" placeholder="Search candidates…"
                   [value]="query()" (input)="query.set($any($event.target).value)" />
          </div>
          <div class="seg tl-seg">
            @for (f of filters; track f.k) {
              <button [class.active]="filter() === f.k" (click)="filter.set(f.k)">{{ f.l }}</button>
            }
          </div>
        </div>
        <div class="tl-list scroll-y">
          @for (t of threads(); track t.cand.candidateId) {
            <button class="thread" [class.active]="t.cand.candidateId === activeId()"
                    (click)="select(t.cand.candidateId)">
              <div class="th-av">
                <va-avatar [name]="t.cand.name" [hue]="t.cand.avatarHue" [size]="42"></va-avatar>
                @if (t.online) { <span class="th-dot"></span> }
              </div>
              <div class="th-mid">
                <div class="row between gap-2">
                  <span class="th-name truncate">{{ t.cand.name }}</span>
                  <span class="t-cap t-muted th-time">{{ relTime(t.lastTs) }}</span>
                </div>
                <div class="row between gap-2">
                  <span class="th-prev truncate" [class.unread]="t.unread > 0">
                    @if (t.lastFromUs) { <va-icon name="check" [size]="13" class="th-prev-tick"></va-icon> }
                    {{ t.preview }}
                  </span>
                  @if (t.unread > 0) { <span class="th-badge">{{ t.unread }}</span> }
                </div>
              </div>
            </button>
          } @empty {
            <div class="tl-empty center">
              <va-icon name="inbox" [size]="22"></va-icon>
              <span class="t-sm t-muted">No conversations match.</span>
            </div>
          }
        </div>
        <div class="tl-foot">
          <div class="row gap-2">
            <va-icon name="shield-check" [size]="14"></va-icon>
            <span class="t-cap t-muted">Opt-outs are honoured automatically — STOP keyword removes consent.</span>
          </div>
        </div>
      </aside>

      <!-- CENTER · chat thread --------------------------------- -->
      <section class="wa-chat surface">
        @if (active(); as c) {
          <!-- chat header -->
          <header class="ch-head">
            <button class="ch-back btn btn-icon btn-ghost" (click)="select('')" title="Back to list">
              <va-icon name="arrow-left" [size]="16"></va-icon>
            </button>
            <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="40"></va-avatar>
            <div class="ch-id">
              <a class="ch-name" [routerLink]="'/app/crm/candidate/' + c.candidateId">{{ c.name }}</a>
              <span class="t-cap t-muted truncate">{{ c.whatsapp }} · {{ c.preferredCourse }}</span>
            </div>
            <div class="ch-actor">
              <span class="actor-tag" [class.ai]="!humanMode()" [class.human]="humanMode()">
                @if (!humanMode()) {
                  <va-icon name="sparkles" [size]="12"></va-icon> Aisha (AI) is answering
                } @else {
                  <va-icon name="user" [size]="12"></va-icon> You are answering
                }
              </span>
              <button class="switch" [class.on]="humanMode()" (click)="toggleTakeover()"
                      [attr.aria-label]="humanMode() ? 'Hand back to AI' : 'Take over from AI'"></button>
            </div>
            <div class="ch-tools">
              <a class="btn btn-icon btn-ghost" title="Voice call" [routerLink]="'/app/communications/voice'"><va-icon name="phone" [size]="16"></va-icon></a>
              <a class="btn btn-icon btn-ghost" title="Schedule V-Con" routerLink="/app/communications"><va-icon name="video" [size]="16"></va-icon></a>
              <button class="btn btn-icon btn-ghost" title="More"><va-icon name="more-vertical" [size]="16"></va-icon></button>
            </div>
          </header>

          <!-- 24h window banner -->
          @if (outsideWindow()) {
            <div class="banner warning ch-window">
              <va-icon name="clock" [size]="16"></va-icon>
              <span class="grow">Outside the 24-hour window — only approved templates can be sent.</span>
              <button class="btn btn-sm btn-ghost" (click)="reopenWindow()">Last reply {{ relTime(lastInboundTs()) }}</button>
            </div>
          }
          @if (c.doNotContact) {
            <div class="banner danger ch-window">
              <va-icon name="alert-circle" [size]="16"></va-icon>
              <span class="grow"><strong>Opted out.</strong> This candidate revoked WhatsApp consent — outbound messaging is blocked.</span>
            </div>
          }

          <!-- message stream -->
          <div class="ch-stream scroll-y" #stream>
            <div class="ch-daysep"><span>{{ dayLabel() }}</span></div>
            <div class="ch-disclaimer">
              <va-ai-avatar [size]="22"></va-ai-avatar>
              <span class="t-cap t-muted">Aisha introduced itself as an AI admission counselor at the start of this chat.</span>
            </div>

            @for (m of messages(); track m.id) {
              <div class="bubble-row" [class.right]="isOutgoing(m)" [class.left]="!isOutgoing(m)">
                @if (m.kind === 'text' || !m.kind) {
                  <div class="bubble" [attr.data-author]="m.author">
                    @if (m.author === 'ai') { <span class="ai-flag"><va-icon name="sparkles" [size]="10"></va-icon> Aisha · AI</span> }
                    @if (m.author === 'parent') { <span class="role-flag">Parent</span> }
                    <p class="b-text">{{ m.text }}</p>
                    <span class="b-meta">
                      <span class="b-time">{{ fmtTime(m.ts) }}</span>
                      @if (isOutgoing(m)) { <va-icon [name]="tickIcon(m)" [size]="14" class="tick" [attr.data-st]="m.status"></va-icon> }
                    </span>
                  </div>
                } @else if (m.kind === 'link') {
                  <div class="bubble link-bubble" [attr.data-author]="m.author">
                    @if (m.author === 'ai') { <span class="ai-flag"><va-icon name="sparkles" [size]="10"></va-icon> Aisha · AI</span> }
                    <a class="link-chip" href="#" (click)="$event.preventDefault()">
                      <span class="lc-ic"><va-icon name="external-link" [size]="16"></va-icon></span>
                      <span class="lc-body"><span class="lc-title">{{ m.cardTitle || 'Open link' }}</span><span class="lc-meta t-cap t-muted">{{ m.cardMeta || m.text }}</span></span>
                    </a>
                    <span class="b-meta"><span class="b-time">{{ fmtTime(m.ts) }}</span>
                      @if (isOutgoing(m)) { <va-icon [name]="tickIcon(m)" [size]="14" class="tick" [attr.data-st]="m.status"></va-icon> }</span>
                  </div>
                } @else {
                  <div class="bubble rich-bubble" [attr.data-author]="m.author">
                    @if (m.author === 'ai') { <span class="ai-flag"><va-icon name="sparkles" [size]="10"></va-icon> Aisha · AI</span> }
                    <div class="rich-card" [attr.data-kind]="m.kind">
                      <span class="rc-ic"><va-icon [name]="cardIcon(m.kind)" [size]="18"></va-icon></span>
                      <div class="rc-body">
                        <span class="rc-tag">{{ cardTag(m.kind) }}</span>
                        <span class="rc-title">{{ m.cardTitle }}</span>
                        <span class="rc-meta t-cap t-muted">{{ m.cardMeta }}</span>
                      </div>
                      <span class="rc-doc"><va-icon name="file-text" [size]="14"></va-icon></span>
                    </div>
                    <span class="b-meta"><span class="b-time">{{ fmtTime(m.ts) }}</span>
                      @if (isOutgoing(m)) { <va-icon [name]="tickIcon(m)" [size]="14" class="tick" [attr.data-st]="m.status"></va-icon> }</span>
                  </div>
                }
              </div>
            }

            @if (aiTyping()) {
              <div class="bubble-row right">
                <div class="bubble typing" data-author="ai">
                  <span class="ai-flag"><va-icon name="sparkles" [size]="10"></va-icon> Aisha · AI</span>
                  <span class="dots"><i></i><i></i><i></i></span>
                </div>
              </div>
            }
          </div>

          <!-- template menu -->
          @if (showTemplates()) {
            <div class="tpl-menu">
              <div class="row between tpl-head">
                <span class="t-sm" style="font-weight:600">Approved templates</span>
                <span class="chip"><va-icon name="lock" [size]="11"></va-icon> Compliance-approved</span>
              </div>
              <div class="tpl-grid">
                @for (t of templates; track t.key) {
                  <button class="tpl" (click)="insertTemplate(t)">
                    <span class="tpl-ic"><va-icon [name]="t.icon" [size]="16"></va-icon></span>
                    <span class="tpl-l">{{ t.label }}</span>
                  </button>
                }
              </div>
            </div>
          }

          <!-- quick replies -->
          @if (!showTemplates()) {
            <div class="qr-row scroll-x">
              @for (q of quickReplies; track q.label) {
                <button class="qr" (click)="sendText(q.body)" [disabled]="composerBlocked()">{{ q.label }}</button>
              }
            </div>
          }

          <!-- composer -->
          <footer class="composer">
            <div class="cmp-inserters">
              <button class="cmp-ic" [class.on]="showTemplates()" (click)="showTemplates.set(!showTemplates())" title="Approved templates">
                <va-icon name="scroll-text" [size]="18"></va-icon>
              </button>
              <button class="cmp-ic" (click)="insertCard('course-card')" title="Insert course card"><va-icon name="graduation-cap" [size]="18"></va-icon></button>
              <button class="cmp-ic" (click)="insertCard('fee-card')" title="Insert fee card"><va-icon name="dollar-sign" [size]="18"></va-icon></button>
              <button class="cmp-ic" (click)="insertCard('scholarship-card')" title="Insert scholarship card"><va-icon name="star" [size]="18"></va-icon></button>
              <button class="cmp-ic" title="Attach"><va-icon name="paperclip" [size]="18"></va-icon></button>
            </div>
            <div class="cmp-field">
              <input class="cmp-input" type="text" [value]="draft()"
                     [placeholder]="composerBlocked() ? 'Outbound blocked — opted out' : (outsideWindow() ? 'Outside 24h window — pick an approved template' : 'Type a message…')"
                     [disabled]="composerBlocked()"
                     (input)="draft.set($any($event.target).value)"
                     (keydown.enter)="sendDraft()" />
            </div>
            @if (draft().trim()) {
              <button class="cmp-send" (click)="sendDraft()" [disabled]="composerBlocked()" title="Send"><va-icon name="send" [size]="18"></va-icon></button>
            } @else {
              <button class="cmp-send mic" (click)="recordVoice()" [disabled]="composerBlocked()" title="Voice note"><va-icon name="mic" [size]="18"></va-icon></button>
            }
          </footer>
        } @else {
          <div class="ch-empty center">
            <va-icon name="message-circle" [size]="30"></va-icon>
            <div class="t-h4">Select a conversation</div>
            <p class="t-sm t-muted">Pick a candidate on the left to view the WhatsApp thread.</p>
          </div>
        }
      </section>

      <!-- RIGHT · context panel -------------------------------- -->
      @if (active(); as c) {
        <aside class="wa-rail scroll-y">
          <!-- sentiment & intent -->
          <div class="surface rail-card">
            <div class="rail-head"><span class="t-h4">Live sentiment & intent</span><span class="chip ai-chip"><va-icon name="sparkles" [size]="11"></va-icon> AI</span></div>
            <div class="rail-body">
              <div class="si-row">
                <span class="t-cap t-muted">Sentiment</span>
                <va-sentiment-badge [value]="c.sentiment" [showLabel]="true"></va-sentiment-badge>
              </div>
              <div class="si-row">
                <span class="t-cap t-muted">Detected intent</span>
                <span class="intent-chip">{{ detectedIntent() }}</span>
              </div>
              <div class="si-row">
                <span class="t-cap t-muted">Conversion</span>
                <va-probability-badge [value]="c.conversionProbability" [ai]="true"></va-probability-badge>
              </div>
              <div class="si-row">
                <span class="t-cap t-muted">Drop-off risk</span>
                <va-band-chip [band]="c.dropOffRisk"></va-band-chip>
              </div>
              <div class="intent-tags">
                @for (t of intentTags(); track t) { <span class="chip i-tag">{{ t }}</span> }
              </div>
            </div>
          </div>

          <!-- AI conversation summary -->
          <div class="surface rail-card">
            <div class="rail-head"><span class="t-h4">Conversation summary</span><va-ai-avatar [size]="22"></va-ai-avatar></div>
            <div class="rail-body">
              <p class="summary">{{ c.lastAiSummary }}</p>
              @if (c.pendingQuestions.length) {
                <div class="pending">
                  <span class="t-cap t-muted">Open questions</span>
                  @for (q of c.pendingQuestions; track q) {
                    <div class="pq"><va-icon name="help-circle" [size]="14"></va-icon><span class="t-sm">{{ q }}</span></div>
                  }
                </div>
              }
              <div class="grd">
                <va-icon name="shield-check" [size]="14"></va-icon>
                <span class="t-cap t-muted">Answers drawn from approved knowledge only. No fees, scholarships or placements are invented.</span>
              </div>
            </div>
          </div>

          <!-- recommended next action -->
          <div class="surface rail-card">
            <div class="rail-head"><span class="t-h4">Recommended next step</span></div>
            <div class="rail-body">
              <div class="rna">
                <span class="rna-ic" [attr.data-ch]="c.recommendedNextAction.channel"><va-icon [name]="actionIcon(c.recommendedNextAction.channel)" [size]="16"></va-icon></span>
                <div class="rna-body">
                  <span class="rna-l">{{ c.recommendedNextAction.label }}</span>
                  <span class="t-cap t-muted">{{ c.recommendedNextAction.reason }}</span>
                </div>
              </div>
              <button class="btn btn-accent btn-block btn-sm" (click)="applyNextAction()"><va-icon name="zap" [size]="14"></va-icon>Apply suggestion</button>
            </div>
          </div>

          <!-- candidate mini-profile -->
          <div class="surface rail-card">
            <div class="rail-head"><span class="t-h4">Candidate profile</span>
              <a class="btn btn-icon btn-ghost" [routerLink]="'/app/crm/candidate/' + c.candidateId" title="Open full profile"><va-icon name="arrow-up-right" [size]="14"></va-icon></a>
            </div>
            <div class="rail-body">
              <dl class="dl">
                <dt>Stage</dt><dd>{{ c.currentStage }}</dd>
                <dt>Course</dt><dd>{{ c.preferredCourse }}</dd>
                <dt>City</dt><dd>{{ c.city }}, {{ c.region }}</dd>
                <dt>Budget</dt><dd class="t-num">{{ c.budgetRange }}</dd>
                <dt>Scholarship</dt><dd>{{ c.scholarshipInterest ? 'Interested' : '—' }}</dd>
                <dt>AI counselor</dt><dd>{{ c.assignedAiCounselor }}</dd>
              </dl>
              @if (c.parents.length) {
                <div class="parent">
                  <div class="row between">
                    <span class="t-cap t-muted">Parent · {{ c.parents[0].relationship }}</span>
                    <va-approval-chip [state]="c.parents[0].consentToDiscuss ? 'approved' : 'pending'"></va-approval-chip>
                  </div>
                  <div class="row gap-2 parent-row">
                    <va-avatar [name]="c.parents[0].name" [hue]="(c.avatarHue + 60) % 360" [size]="28"></va-avatar>
                    <div class="stack">
                      <span class="t-sm" style="font-weight:600">{{ c.parents[0].name }}</span>
                      <span class="t-cap t-muted">Prefers {{ c.parents[0].preferredLanguage }} · {{ c.parents[0].concerns[0] }}</span>
                    </div>
                  </div>
                  <button class="btn btn-ghost btn-sm btn-block" (click)="inviteParent()"><va-icon name="users" [size]="14"></va-icon>Invite parent to chat</button>
                </div>
              }
              <div class="consent-row">
                <span class="cns" [class.ok]="c.consent.whatsapp"><va-icon [name]="c.consent.whatsapp ? 'check-circle' : 'x'" [size]="13"></va-icon> WhatsApp consent</span>
              </div>
            </div>
          </div>
        </aside>
      }
    </div>
  </div>
  `,
  styles: [`
    :host { display: block; }

    .wa { display: flex; flex-direction: column; gap: var(--s-4); height: calc(100vh - var(--topbar-h) - var(--s-6) * 2); max-width: 1560px; }

    /* header */
    .wa-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; flex: none; }
    .wa-head-text p { margin-top: 4px; max-width: 78ch; }
    .wa-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .wa-mark { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; color: #fff; background: var(--ch-whatsapp); box-shadow: var(--e1); }
    .live-chip { background: color-mix(in srgb, var(--ch-whatsapp) 12%, var(--color-surface)); color: color-mix(in srgb, var(--ch-whatsapp) 70%, var(--color-text)); border-color: color-mix(in srgb, var(--ch-whatsapp) 30%, var(--color-border)); }
    .live-chip .dot.live { background: var(--ch-whatsapp); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ch-whatsapp) 22%, transparent); }

    /* body grid */
    .wa-body { display: grid; grid-template-columns: 320px minmax(0, 1fr) 340px; gap: var(--s-4); flex: 1; min-height: 0; }

    /* ── LEFT thread list ─────────────────────────────── */
    .wa-threads { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
    .tl-head { padding: 14px 14px 10px; border-bottom: 1px solid var(--color-border); display: flex; flex-direction: column; gap: 10px; flex: none; }
    .tl-search { display: flex; align-items: center; gap: 8px; background: var(--color-surface-alt); border: 1px solid transparent; border-radius: var(--r-md); padding: 0 10px; color: var(--color-text-muted); transition: border-color .15s, box-shadow .15s; }
    .tl-search:focus-within { border-color: var(--color-accent); box-shadow: var(--ring); background: var(--color-surface); }
    .tl-input { border: none; background: transparent; padding: 8px 0; font: inherit; font-size: var(--text-sm); color: var(--color-text); width: 100%; outline: none; }
    .tl-seg { width: 100%; }
    .tl-seg button { flex: 1; padding: 5px 0; }
    .tl-list { flex: 1; padding: 6px; display: flex; flex-direction: column; gap: 2px; }
    .thread { display: flex; align-items: center; gap: 11px; padding: 9px 10px; border-radius: var(--r-md); border: 1px solid transparent; background: transparent; text-align: left; width: 100%; transition: background .12s; }
    .thread:hover { background: var(--color-surface-alt); }
    .thread.active { background: rgba(var(--color-primary-rgb), .07); border-color: rgba(var(--color-primary-rgb), .14); }
    .th-av { position: relative; flex: none; }
    .th-dot { position: absolute; right: -1px; bottom: -1px; width: 11px; height: 11px; border-radius: 50%; background: var(--ch-whatsapp); border: 2px solid var(--color-surface); }
    .th-mid { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
    .th-name { font-size: var(--text-sm); font-weight: 600; }
    .th-time { flex: none; }
    .th-prev { font-size: var(--text-cap); color: var(--color-text-muted); display: inline-flex; align-items: center; gap: 3px; }
    .th-prev.unread { color: var(--color-text); font-weight: 600; }
    .th-prev-tick { color: var(--ch-whatsapp); flex: none; }
    .th-badge { flex: none; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px; background: var(--ch-whatsapp); color: #053; font-size: 11px; font-weight: 800; display: grid; place-items: center; }
    .tl-empty { flex-direction: column; gap: 8px; padding: 40px 16px; color: var(--color-text-muted); }
    .tl-foot { padding: 10px 14px; border-top: 1px solid var(--color-border); flex: none; }
    .tl-foot va-icon { color: var(--color-success); flex: none; }

    /* ── CENTER chat ──────────────────────────────────── */
    .wa-chat { display: flex; flex-direction: column; min-height: 0; overflow: hidden; position: relative; }
    .ch-head { display: flex; align-items: center; gap: 11px; padding: 11px 14px; border-bottom: 1px solid var(--color-border); flex: none; }
    .ch-back { display: none; }
    .ch-id { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .ch-name { font-size: var(--text-h4); font-weight: 600; }
    .ch-name:hover { color: var(--color-primary); }
    .ch-actor { display: flex; align-items: center; gap: 8px; margin-left: auto; }
    .actor-tag { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; padding: 5px 10px; border-radius: var(--r-pill); }
    .actor-tag.ai { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .actor-tag.human { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .ch-tools { display: flex; align-items: center; gap: 4px; }

    .ch-window { margin: 12px 14px 0; align-items: center; }
    .ch-window va-icon { flex: none; }
    .ch-window .grow { flex: 1; }

    /* message stream */
    .ch-stream { flex: 1; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px;
      background:
        radial-gradient(circle at 18% 12%, rgba(var(--color-accent-rgb), .05), transparent 30%),
        radial-gradient(circle at 82% 78%, rgba(var(--color-accent-2-rgb), .05), transparent 32%),
        var(--color-surface-2);
    }
    .ch-daysep { display: flex; justify-content: center; margin: 2px 0 6px; }
    .ch-daysep span { font-size: 11px; font-weight: 600; color: var(--color-text-muted); background: var(--color-surface-alt); padding: 3px 12px; border-radius: var(--r-pill); }
    .ch-disclaimer { display: flex; align-items: center; gap: 8px; justify-content: center; text-align: center; padding: 8px 14px; margin-bottom: 4px;
      background: rgba(var(--color-accent-2-rgb), .06); border: 1px solid rgba(var(--color-accent-2-rgb), .18); border-radius: var(--r-md); }

    .bubble-row { display: flex; }
    .bubble-row.left { justify-content: flex-start; }
    .bubble-row.right { justify-content: flex-end; }
    .bubble { position: relative; max-width: 78%; padding: 9px 12px; border-radius: 14px; box-shadow: var(--e1); display: flex; flex-direction: column; gap: 3px; }
    /* incoming (candidate / parent) — left, neutral */
    .bubble-row.left .bubble { background: var(--color-surface); border: 1px solid var(--color-border); border-top-left-radius: 5px; }
    /* outgoing human — right, whatsapp green */
    .bubble-row.right .bubble[data-author='human'] { background: color-mix(in srgb, var(--ch-whatsapp) 16%, var(--color-surface)); border: 1px solid color-mix(in srgb, var(--ch-whatsapp) 30%, var(--color-border)); border-top-right-radius: 5px; }
    /* outgoing AI — right, gradient-accent edge */
    .bubble-row.right .bubble[data-author='ai'] { background: var(--color-surface); border: 1px solid rgba(var(--color-accent-2-rgb), .28); border-top-right-radius: 5px;
      box-shadow: var(--e1), inset 3px 0 0 0 transparent; background-image: linear-gradient(var(--color-surface), var(--color-surface)), var(--gradient-ai);
      background-origin: border-box; background-clip: padding-box, border-box; }
    .ai-flag { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; font-weight: 800; letter-spacing: .02em;
      background: var(--gradient-ai); -webkit-background-clip: text; background-clip: text; color: transparent; }
    .role-flag { font-size: 10px; font-weight: 700; color: var(--color-accent-2); }
    .b-text { font-size: var(--text-sm); line-height: 1.45; white-space: pre-wrap; }
    .b-meta { display: flex; align-items: center; gap: 4px; align-self: flex-end; }
    .b-time { font-size: 10px; color: var(--color-text-muted); }
    .tick { color: var(--color-text-muted); }
    .tick[data-st='read'] { color: var(--ch-email); }
    .tick[data-st='failed'] { color: var(--color-danger); }
    .tick[data-st='sending'] { color: var(--color-border-strong); }

    /* link chip */
    .link-chip { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 10px; background: var(--color-surface-alt); border: 1px solid var(--color-border); min-width: 220px; }
    .link-chip:hover { border-color: var(--color-accent); }
    .lc-ic { width: 34px; height: 34px; border-radius: 8px; display: grid; place-items: center; background: var(--color-surface); color: var(--ch-email); flex: none; }
    .lc-body { display: flex; flex-direction: column; min-width: 0; }
    .lc-title { font-size: var(--text-sm); font-weight: 600; }

    /* rich card */
    .rich-card { display: flex; align-items: center; gap: 11px; padding: 11px; border-radius: 11px; background: var(--color-surface-alt); border: 1px solid var(--color-border); min-width: 250px; }
    .rc-ic { width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; color: #fff; flex: none; }
    .rich-card[data-kind='course-card'] .rc-ic { background: var(--color-primary); }
    .rich-card[data-kind='fee-card'] .rc-ic { background: var(--color-accent-2); }
    .rich-card[data-kind='scholarship-card'] .rc-ic { background: var(--color-success); }
    .rc-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .rc-tag { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--color-text-muted); }
    .rc-title { font-size: var(--text-sm); font-weight: 700; }
    .rc-doc { width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; background: var(--color-surface); color: var(--color-text-muted); flex: none; }

    /* typing */
    .bubble.typing .dots { display: inline-flex; gap: 4px; padding: 2px 0; }
    .bubble.typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--color-accent-2); animation: va-pulse 1.1s ease-in-out infinite; }
    .bubble.typing i:nth-child(2) { animation-delay: .2s; }
    .bubble.typing i:nth-child(3) { animation-delay: .4s; }

    /* template menu */
    .tpl-menu { border-top: 1px solid var(--color-border); padding: 12px 14px; background: var(--color-surface); flex: none; }
    .tpl-head { margin-bottom: 10px; }
    .tpl-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
    .tpl { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface-2); text-align: center; transition: all .12s; }
    .tpl:hover { border-color: var(--ch-whatsapp); background: color-mix(in srgb, var(--ch-whatsapp) 7%, var(--color-surface)); transform: translateY(-1px); }
    .tpl-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--ch-whatsapp); }
    .tpl-l { font-size: var(--text-cap); font-weight: 600; line-height: 1.2; }

    /* quick replies */
    .qr-row { display: flex; gap: 8px; padding: 10px 14px 0; overflow-x: auto; flex: none; scrollbar-width: none; }
    .qr-row::-webkit-scrollbar { display: none; }
    .qr { flex: none; padding: 6px 12px; border-radius: var(--r-pill); border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); font-size: var(--text-cap); font-weight: 600; white-space: nowrap; transition: all .12s; }
    .qr:hover { border-color: var(--ch-whatsapp); color: color-mix(in srgb, var(--ch-whatsapp) 70%, var(--color-text)); }
    .qr:disabled { opacity: .5; cursor: not-allowed; }

    /* composer */
    .composer { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-top: 1px solid var(--color-border); flex: none; background: var(--color-surface); }
    .cmp-inserters { display: flex; align-items: center; gap: 2px; }
    .cmp-ic { width: 36px; height: 36px; border-radius: var(--r-md); display: grid; place-items: center; border: none; background: transparent; color: var(--color-text-muted); transition: all .12s; }
    .cmp-ic:hover { background: var(--color-surface-alt); color: var(--color-text); }
    .cmp-ic.on { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .cmp-field { flex: 1; background: var(--color-surface-alt); border: 1px solid transparent; border-radius: var(--r-pill); padding: 0 16px; transition: border-color .15s, box-shadow .15s; }
    .cmp-field:focus-within { border-color: var(--color-accent); box-shadow: var(--ring); background: var(--color-surface); }
    .cmp-input { width: 100%; border: none; background: transparent; padding: 11px 0; font: inherit; font-size: var(--text-sm); color: var(--color-text); outline: none; }
    .cmp-input:disabled { cursor: not-allowed; }
    .cmp-send { width: 44px; height: 44px; border-radius: 50%; border: none; display: grid; place-items: center; flex: none; background: var(--ch-whatsapp); color: #fff; box-shadow: var(--e1); transition: transform .1s, filter .15s; }
    .cmp-send:hover { filter: brightness(1.05); }
    .cmp-send:active { transform: scale(.94); }
    .cmp-send:disabled { opacity: .45; cursor: not-allowed; }
    .cmp-send.mic { background: var(--color-surface-alt); color: var(--color-text-muted); box-shadow: none; }
    .cmp-send.mic:hover { color: var(--color-text); }

    .ch-empty, .ch-empty.center { flex: 1; flex-direction: column; gap: 8px; color: var(--color-text-muted); padding: 40px; }
    .ch-empty va-icon { color: var(--color-text-muted); }

    /* ── RIGHT rail ───────────────────────────────────── */
    .wa-rail { display: flex; flex-direction: column; gap: var(--s-4); min-height: 0; padding-right: 2px; }
    .rail-card { padding: 0; overflow: hidden; }
    .rail-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 13px 15px; border-bottom: 1px solid var(--color-border); }
    .rail-body { padding: 14px 15px; display: flex; flex-direction: column; gap: 12px; }
    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }

    .si-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .intent-chip { font-size: var(--text-cap); font-weight: 600; padding: 4px 10px; border-radius: var(--r-pill); background: rgba(var(--color-accent-rgb), .10); color: color-mix(in srgb, var(--color-accent) 60%, var(--color-text)); border: 1px solid rgba(var(--color-accent-rgb), .25); }
    .intent-tags { display: flex; flex-wrap: wrap; gap: 6px; }
    .i-tag { background: var(--color-surface-alt); }

    .summary { font-size: var(--text-sm); line-height: 1.5; }
    .pending { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-radius: var(--r-md); background: var(--color-warning-soft); border: 1px solid color-mix(in srgb, var(--color-warning) 25%, transparent); }
    .pq { display: flex; align-items: flex-start; gap: 7px; }
    .pq va-icon { color: var(--color-warning); flex: none; margin-top: 2px; }
    .grd { display: flex; align-items: flex-start; gap: 8px; padding-top: 4px; }
    .grd va-icon { color: var(--color-success); flex: none; margin-top: 1px; }

    .rna { display: flex; gap: 11px; align-items: flex-start; }
    .rna-ic { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--color-text-muted); flex: none; }
    .rna-ic[data-ch='whatsapp'] { color: var(--ch-whatsapp); } .rna-ic[data-ch='voice'] { color: var(--ch-voice); }
    .rna-ic[data-ch='email'] { color: var(--ch-email); } .rna-ic[data-ch='vcon'] { color: var(--ch-vcon); }
    .rna-body { display: flex; flex-direction: column; gap: 1px; }
    .rna-l { font-size: var(--text-sm); font-weight: 600; }

    .parent { display: flex; flex-direction: column; gap: 9px; padding: 12px; border-radius: var(--r-md); background: var(--color-surface-2); border: 1px solid var(--color-border); }
    .parent-row { align-items: center; }
    .consent-row { display: flex; gap: 8px; }
    .cns { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; color: var(--color-danger); }
    .cns.ok { color: var(--color-success); }

    /* responsive */
    @media (max-width: 1240px) {
      .wa-body { grid-template-columns: 280px minmax(0, 1fr); }
      .wa-rail { display: none; }
    }
    @media (max-width: 860px) {
      .wa { height: auto; }
      .wa-body { grid-template-columns: 1fr; }
      .wa-threads { max-height: 340px; }
      .ch-back { display: grid; }
      .tpl-grid { grid-template-columns: repeat(2, 1fr); }
    }
  `],
})
export class WhatsappConsoleComponent {
  private store = inject(DataStore);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);

  @ViewChild('stream') stream?: ElementRef<HTMLDivElement>;

  fmtTime = fmtTime;
  relTime = relTime;

  query = signal('');
  filter = signal<'all' | 'unread' | 'high'>('all');
  filters = [{ k: 'all', l: 'All' }, { k: 'unread', l: 'Unread' }, { k: 'high', l: 'High intent' }] as const;

  humanMode = signal(false);
  showTemplates = signal(false);
  draft = signal('');
  aiTyping = signal(false);

  // override flags keyed per candidate so window/state edits survive thread switches
  private reopened = signal<Set<string>>(new Set());
  private extraMsgs = signal<Record<string, ChatMessage[]>>({});

  activeId = signal<string>(this.route.snapshot.paramMap.get('candidateId') ?? this.store.candidates()[0]?.candidateId ?? '');

  active = computed<Candidate | undefined>(() => this.store.candidateById(this.activeId()));

  templates: WaTemplate[] = [
    { key: 'brochure', label: 'Course brochure', icon: 'book-open', body: 'Sharing the approved course brochure with curriculum and outcomes for your reference.' },
    { key: 'fee', label: 'Fee structure', icon: 'dollar-sign', body: 'Here is the official fee structure for the 2026 cycle from our approved document.' },
    { key: 'scholarship', label: 'Scholarship guide', icon: 'star', body: 'Sharing the merit & need-based scholarship guide. Eligibility and cut-offs are inside.' },
    { key: 'applink', label: 'Application link', icon: 'external-link', body: 'You can start your application here. The link is personalised for you.' },
    { key: 'vcon', label: 'V-Con invitation', icon: 'video', body: 'Would a short video call with a human counselor and your parents help? I can set it up.' },
    { key: 'reminder', label: 'Reminder', icon: 'clock', body: 'A gentle reminder about your pending application step. Reply here if you need any help.' },
    { key: 'parent', label: 'Parent invite', icon: 'users', body: 'May I invite your parent to this chat so they can ask questions about placements and safety directly?' },
  ];

  quickReplies: QuickReply[] = [
    { label: 'Yes, sounds good', body: 'Yes, that sounds good — please go ahead.' },
    { label: 'Tell me about fees', body: 'Could you share the fee structure for this course?' },
    { label: 'Share scholarship info', body: 'I would like to know about scholarship eligibility.' },
    { label: 'Schedule a V-Con', body: 'I would like to schedule a video call with a counselor.' },
    { label: 'Talk to a human', body: 'Can I speak with a human counselor, please?' },
  ];

  // ── thread list ───────────────────────────────────────
  threads = computed(() => {
    const q = this.query().trim().toLowerCase();
    const f = this.filter();
    return this.store.candidates()
      .filter(c => c.consent.whatsapp || c.duplicate === false)
      .map((c, i) => {
        const msgs = this.messagesFor(c.candidateId);
        const last = msgs[msgs.length - 1];
        const lastFromUs = last ? (last.author === 'ai' || last.author === 'human') : false;
        const preview = last ? (last.kind && last.kind !== 'text' ? this.cardTag(last.kind as any) : last.text) || 'Attachment' : 'No messages yet';
        // deterministic unread / online flavour from probability + index
        const unread = (!lastFromUs && c.conversionProbability % 3 === 0) ? (c.conversionProbability % 3) + 1 : 0;
        return {
          cand: c,
          lastTs: last?.ts ?? c.lastContacted,
          preview,
          lastFromUs,
          unread,
          online: i % 4 === 0,
          high: c.conversionProbability >= 70,
        };
      })
      .filter(t => !q || t.cand.name.toLowerCase().includes(q) || t.cand.preferredCourse.toLowerCase().includes(q) || t.cand.city.toLowerCase().includes(q))
      .filter(t => f === 'all' || (f === 'unread' && t.unread > 0) || (f === 'high' && t.high))
      .sort((a, b) => new Date(b.lastTs).getTime() - new Date(a.lastTs).getTime())
      .slice(0, 18);
  });

  // ── messages ──────────────────────────────────────────
  private messagesFor(id: string): ChatMessage[] {
    const base = this.store.chatFor(id);
    const extra = this.extraMsgs()[id] ?? [];
    return [...base, ...extra];
  }
  messages = computed(() => this.messagesFor(this.activeId()));

  lastInboundTs = computed(() => {
    const inbound = this.messages().filter(m => m.author === 'candidate' || m.author === 'parent');
    return inbound.length ? inbound[inbound.length - 1].ts : (this.active()?.lastContacted ?? new Date().toISOString());
  });

  outsideWindow = computed(() => {
    if (this.reopened().has(this.activeId())) return false;
    const ts = new Date(this.lastInboundTs()).getTime();
    const now = new Date('2026-06-14T09:30:00').getTime();
    return (now - ts) > 24 * 3600 * 1000;
  });

  composerBlocked = computed(() => !!this.active()?.doNotContact);

  dayLabel = () => 'Conversation history';

  // ── intent / context ──────────────────────────────────
  detectedIntent = computed(() => {
    const c = this.active();
    if (!c) return '—';
    if (c.scholarshipInterest) return 'Scholarship enquiry';
    if (c.currentStage.includes('Fee')) return 'Fee clarification';
    if (c.currentStage.includes('Parent')) return 'Parent reassurance';
    if (c.currentStage.includes('Application')) return 'Application support';
    return 'Course exploration';
  });
  intentTags = computed(() => {
    const c = this.active();
    if (!c) return [];
    const tags = [c.preferredCourse, ...c.careerInterests.slice(0, 1)];
    if (c.scholarshipInterest) tags.push('Scholarship');
    if (c.parents.length) tags.push('Parent involved');
    return tags.slice(0, 4);
  });

  // ── selection ─────────────────────────────────────────
  select(id: string) {
    if (!id) { this.activeId.set(''); return; }
    this.activeId.set(id);
    this.showTemplates.set(false);
    this.draft.set('');
    this.router.navigate(['/app/communications/whatsapp', id]);
    this.scrollSoon();
  }

  // ── helpers for template ──────────────────────────────
  isOutgoing(m: ChatMessage) { return m.author === 'ai' || m.author === 'human'; }
  tickIcon(m: ChatMessage) {
    if (m.status === 'read' || m.status === 'delivered') return 'check-circle';
    if (m.status === 'failed') return 'alert-circle';
    if (m.status === 'sending') return 'clock';
    return 'check';
  }
  cardIcon(kind?: string) {
    return kind === 'course-card' ? 'graduation-cap' : kind === 'fee-card' ? 'dollar-sign' : 'star';
  }
  cardTag(kind?: string) {
    return kind === 'course-card' ? 'Course' : kind === 'fee-card' ? 'Fee structure' : kind === 'scholarship-card' ? 'Scholarship' : 'Voice note';
  }
  actionIcon(ch: string) {
    return ch === 'whatsapp' ? 'message-circle' : ch === 'voice' ? 'phone' : ch === 'email' ? 'mail' : ch === 'vcon' ? 'video' : 'send';
  }

  // ── actions ───────────────────────────────────────────
  toggleTakeover() {
    this.humanMode.update(v => !v);
    this.toast.info(this.humanMode()
      ? 'You have taken over — Aisha has paused. Messages now send as you.'
      : 'Handed back to Aisha — AI counselor resumed (approved-knowledge-only).',
      this.humanMode() ? 'user' : 'sparkles');
  }

  reopenWindow() {
    this.reopened.update(s => { const n = new Set(s); n.add(this.activeId()); return n; });
    this.toast.success('24-hour window treated as open for this demo thread.');
  }

  private append(msg: Partial<ChatMessage>) {
    const id = this.activeId();
    const full: ChatMessage = {
      id: 'out-' + id + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      author: this.humanMode() ? 'human' : 'ai',
      text: '',
      ts: new Date('2026-06-14T09:30:00').toISOString(),
      status: 'sent',
      kind: 'text',
      ...msg,
    };
    this.extraMsgs.update(m => ({ ...m, [id]: [...(m[id] ?? []), full] }));
    this.scrollSoon();
    // simulate delivery → read progression
    setTimeout(() => this.bumpStatus(full.id, 'delivered'), 700);
    setTimeout(() => this.bumpStatus(full.id, 'read'), 1700);
  }

  private bumpStatus(msgId: string, status: ChatMessage['status']) {
    const id = this.activeId();
    this.extraMsgs.update(m => ({
      ...m,
      [id]: (m[id] ?? []).map(x => x.id === msgId ? { ...x, status } : x),
    }));
  }

  sendDraft() {
    const text = this.draft().trim();
    if (!text || this.composerBlocked()) return;
    this.sendText(text);
    this.draft.set('');
  }

  sendText(text: string) {
    if (this.composerBlocked()) return;
    if (this.outsideWindow()) { this.toast.warning('Outside the 24-hour window — send an approved template instead.'); return; }
    this.append({ text });
  }

  insertTemplate(t: WaTemplate) {
    if (this.composerBlocked()) return;
    this.showTemplates.set(false);
    if (t.key === 'applink') {
      this.append({ kind: 'link', cardTitle: 'Start your application', cardMeta: 'apply.northgate.edu · personalised link', text: t.body });
    } else if (t.key === 'brochure') {
      this.insertCard('course-card');
    } else if (t.key === 'fee') {
      this.insertCard('fee-card');
    } else if (t.key === 'scholarship') {
      this.insertCard('scholarship-card');
    } else {
      this.append({ text: t.body });
    }
    this.toast.success('Approved template "' + t.label + '" sent.');
  }

  insertCard(kind: CardKind) {
    if (this.composerBlocked()) return;
    const c = this.active();
    if (!c) return;
    const meta: Record<CardKind, { title: string; meta: string }> = {
      'course-card': { title: c.preferredCourse, meta: '4 years · Approved curriculum · Placement report attached' },
      'fee-card': { title: c.preferredCourse + ' — Fee structure', meta: 'Total ' + c.budgetRange + ' · EMI options available · Approved 2026' },
      'scholarship-card': { title: 'Merit Scholarship 2026', meta: 'Up to 40% tuition · Eligibility: 85%+ in 12th' },
    };
    this.append({ kind, cardTitle: meta[kind].title, cardMeta: meta[kind].meta });
  }

  recordVoice() {
    if (this.composerBlocked()) return;
    this.toast.info('Voice note recording — release to send (demo).', 'mic');
  }

  applyNextAction() {
    const c = this.active();
    if (!c) return;
    this.toast.success('Suggestion applied: ' + c.recommendedNextAction.label);
    if (c.recommendedNextAction.channel === 'whatsapp') this.append({ text: c.recommendedNextAction.label + ' — let me help you with that right now.' });
  }

  inviteParent() {
    const c = this.active();
    if (!c?.parents.length) return;
    this.append({ text: 'I have invited ' + c.parents[0].name + ' (' + c.parents[0].relationship + ') to join this chat so they can ask questions directly.' });
    this.toast.success('Parent invite sent to ' + c.parents[0].name + '.');
  }

  private scrollSoon() {
    setTimeout(() => {
      const el = this.stream?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 50);
  }
}
