import {
  ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal,
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import {
  SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent,
} from '../../shared/ui/badges.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Candidate, Sentiment } from '../../domain/models';
import { band, relFuture, relTime, SENTI_LABEL } from '../../shared/util/format';

interface TranscriptLine {
  id: number;
  speaker: 'ai' | 'candidate' | 'parent' | 'system';
  text: string;
  ts: number;          // seconds into the call
  intent?: string;
}

interface OutcomeTag {
  key: string;
  label: string;
  icon: string;
  tone: 'pos' | 'neutral' | 'neg' | 'action';
}

@Component({
  selector: 'va-voice-console',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink, IconComponent, AvatarComponent, AiAvatarComponent, SectionCardComponent,
    SentimentBadgeComponent, ProbabilityBadgeComponent, BandChipComponent,
  ],
  template: `
  <div class="page vc">
    <!-- ── Header ─────────────────────────────────────────── -->
    <header class="vc-head">
      <div class="vc-head-text">
        <div class="row gap-2 wrap">
          <h1 class="t-h2">Voice console</h1>
          <span class="chip live-chip"><span class="dot live pulse"></span> Live calling</span>
        </div>
        <p class="t-sm t-muted">
          Aisha places approved-knowledge-only outbound calls, transcribes in real time and tags every
          outcome. Supervisors can listen, take over, or end calls. — {{ auth.institution().name }} · {{ auth.admissionCycle() }}
        </p>
      </div>
      <div class="vc-head-actions">
        <a class="btn btn-ghost btn-sm" routerLink="/app/handoff"><va-icon name="headphones" [size]="16"></va-icon>Handoff queue</a>
        <a class="btn btn-ghost btn-sm" routerLink="/app/communications"><va-icon name="layers" [size]="16"></va-icon>All channels</a>
      </div>
    </header>

    <!-- ── Recording-consent banner ───────────────────────── -->
    <div class="banner info vc-consent">
      <va-icon name="shield-check" [size]="18"></va-icon>
      <span class="grow">
        <strong>Recording with consent</strong> — {{ active().name }} opted in on
        {{ active().consent.capturedAt ? relTime(active().consent.capturedAt!) : 'enrolment' }}.
        Aisha discloses it is an AI counselor at the start of every call.
      </span>
      <span class="chip"><va-icon name="lock" [size]="12"></va-icon> Approved-knowledge-only</span>
    </div>

    <!-- ── 3-column body ──────────────────────────────────── -->
    <div class="vc-body">

      <!-- LEFT · call queue ------------------------------------ -->
      <aside class="vc-col">
        <va-section-card title="Call queue" [hint]="queueHint()" [flush]="true">
          <div class="queue scroll-y">
            @for (c of queue(); track c.candidateId) {
              <div class="qrow" [class.calling]="c.candidateId === activeId()">
                <va-avatar [name]="c.name" [hue]="c.avatarHue" [size]="38"></va-avatar>
                <div class="qmeta">
                  <div class="row gap-2">
                    <span class="qname truncate">{{ c.name }}</span>
                    @if (c.candidateId === activeId()) {
                      <span class="chip on-call"><span class="dot live pulse"></span> On call</span>
                    }
                  </div>
                  <span class="t-cap t-muted truncate">{{ c.preferredCourse }} · {{ c.city }}</span>
                  <div class="row gap-2 qflags">
                    @if (c.doNotContact) {
                      <span class="flag dnc"><va-icon name="lock" [size]="11"></va-icon> Do-Not-Contact</span>
                    } @else if (c.consent.call) {
                      <span class="flag ok"><va-icon name="check" [size]="11"></va-icon> Consent</span>
                    } @else {
                      <span class="flag warn"><va-icon name="alert-triangle" [size]="11"></va-icon> No call consent</span>
                    }
                    <span class="t-cap t-muted">{{ band(c.conversionProbability) === 'high' ? 'Hot' : band(c.conversionProbability) === 'med' ? 'Warm' : 'Cool' }} · {{ c.conversionProbability }}%</span>
                  </div>
                </div>
                <button class="btn btn-sm" [class.btn-primary]="!c.doNotContact" [class.btn-subtle]="c.doNotContact"
                        [disabled]="c.doNotContact || !c.consent.call || c.candidateId === activeId()"
                        (click)="startCall(c)">
                  <va-icon name="phone" [size]="14"></va-icon>
                  {{ c.candidateId === activeId() ? 'Active' : 'Call' }}
                </button>
              </div>
            }
          </div>
        </va-section-card>

        <div class="surface queue-foot">
          <div class="between">
            <span class="t-cap t-muted">Outbound today</span>
            <span class="t-sm t-num" style="font-weight:700">{{ outboundToday }}</span>
          </div>
          <div class="between">
            <span class="t-cap t-muted">Avg. handle time</span>
            <span class="t-sm t-num" style="font-weight:700">3:42</span>
          </div>
          <div class="between">
            <span class="t-cap t-muted">AI containment</span>
            <span class="t-sm t-num" style="font-weight:700;color:var(--color-success)">86%</span>
          </div>
        </div>
      </aside>

      <!-- CENTER · active call --------------------------------- -->
      <section class="vc-center">
        <div class="surface callcard">
          <!-- candidate header -->
          <div class="cc-head">
            <va-avatar [name]="active().name" [hue]="active().avatarHue" [size]="52"></va-avatar>
            <div class="cc-id">
              <div class="row gap-2 wrap">
                <span class="t-h3">{{ active().name }}</span>
                <span class="status-pill" [attr.data-s]="callState()">
                  <span class="dot" [class.live]="callState() === 'connected'"
                        [class.paused]="callState() === 'hold'"
                        [class.blocked]="callState() === 'ended'"
                        [class.pulse]="callState() === 'connected'"></span>
                  {{ stateLabel() }}
                </span>
              </div>
              <span class="t-sm t-muted">{{ active().preferredCourse }} · {{ active().city }}, {{ active().region }}</span>
            </div>
            <div class="cc-timer">
              <div class="t-num timer">{{ timerText() }}</div>
              <div class="t-cap t-muted">call duration</div>
            </div>
            <div class="cc-aria">
              <va-ai-avatar [size]="40" [glow]="callState() === 'connected'"></va-ai-avatar>
              <span class="t-cap t-muted">Aisha</span>
            </div>
          </div>

          <!-- compliance alert -->
          @if (lowConfidence()) {
            <div class="banner warning cc-alert fade-up">
              <va-icon name="alert-triangle" [size]="18"></va-icon>
              <span class="grow"><strong>AI confidence dropped</strong> to {{ confidence() }}% — the question may fall outside approved knowledge. Consider human takeover.</span>
              <button class="btn btn-sm btn-primary" (click)="takeOver()"><va-icon name="headphones" [size]="14"></va-icon>Take over</button>
            </div>
          }

          <!-- live signal strip -->
          <div class="signal-strip">
            <div class="sig">
              <div class="between"><span class="t-cap t-muted">Live sentiment</span>
                <va-sentiment-badge [value]="liveSentiment()" [showLabel]="true"></va-sentiment-badge>
              </div>
              <div class="senti-bar"><span [class]="'sf s-' + liveSentiment()" [style.width.%]="sentiPct()"></span></div>
            </div>
            <div class="sig">
              <div class="between"><span class="t-cap t-muted">AI confidence</span>
                <span class="t-sm t-num" [style.color]="confColor()" style="font-weight:700">{{ confidence() }}%</span>
              </div>
              <div class="progress ai"><span [style.width.%]="confidence()"></span></div>
            </div>
            <div class="sig">
              <div class="between"><span class="t-cap t-muted">Detected intent</span></div>
              <span class="chip intent-chip"><va-icon name="sparkles" [size]="12"></va-icon>{{ intent() }}</span>
            </div>
            <div class="sig">
              <div class="between"><span class="t-cap t-muted">Parent on line</span></div>
              @if (parentDetected()) {
                <span class="chip parent-chip"><va-icon name="users" [size]="12"></va-icon>Parent detected</span>
              } @else {
                <span class="chip"><va-icon name="user" [size]="12"></va-icon>Candidate only</span>
              }
            </div>
          </div>

          <!-- live transcript -->
          <div class="transcript-head between">
            <span class="t-cap t-muted row gap-2"><span class="dot live pulse"></span> LIVE TRANSCRIPT</span>
            <span class="chip"><va-icon name="globe" [size]="12"></va-icon>English · auto-detect</span>
          </div>
          <div class="transcript scroll-y" #scrollEl id="vc-transcript">
            @for (l of transcript(); track l.id) {
              <div class="tline" [attr.data-sp]="l.speaker">
                @if (l.speaker === 'system') {
                  <div class="t-sys"><va-icon name="info" [size]="13"></va-icon>{{ l.text }}</div>
                } @else {
                  <div class="t-who">
                    @if (l.speaker === 'ai') {
                      <va-ai-avatar [size]="22"></va-ai-avatar>
                    } @else {
                      <va-avatar [name]="l.speaker === 'parent' ? parentName() : active().name"
                                 [hue]="l.speaker === 'parent' ? 28 : active().avatarHue" [size]="22"></va-avatar>
                    }
                    <span class="t-cap t-muted">{{ speakerLabel(l.speaker) }} · {{ fmtClock(l.ts) }}</span>
                    @if (l.intent) { <span class="chip mini-intent">{{ l.intent }}</span> }
                  </div>
                  <p class="t-bubble">{{ l.text }}</p>
                }
              </div>
            }
            @if (callState() === 'connected') {
              <div class="typing"><span></span><span></span><span></span> Aisha is speaking…</div>
            }
          </div>

          <!-- controls -->
          <div class="controls">
            <button class="ctrl" [class.active]="muted()" (click)="toggleMute()" [disabled]="callState() === 'ended'">
              <va-icon [name]="muted() ? 'mic' : 'mic'" [size]="18"></va-icon>
              <span>{{ muted() ? 'Unmute' : 'Mute' }}</span>
            </button>
            <button class="ctrl" [class.active]="callState() === 'hold'" (click)="toggleHold()" [disabled]="callState() === 'ended'">
              <va-icon [name]="callState() === 'hold' ? 'play' : 'pause'" [size]="18"></va-icon>
              <span>{{ callState() === 'hold' ? 'Resume' : 'Hold' }}</span>
            </button>
            <button class="ctrl takeover" (click)="takeOver()" [disabled]="callState() === 'ended'">
              <va-icon name="headphones" [size]="18"></va-icon>
              <span>Take over</span>
            </button>
            <div class="grow"></div>
            <button class="btn btn-danger end-btn" [disabled]="!selectedOutcome() || callState() === 'ended'" (click)="endCall()">
              <va-icon name="phone" [size]="16"></va-icon>End call
            </button>
          </div>
        </div>

        <!-- OUTCOME TAGGER -->
        <va-section-card title="Outcome tagger" [hint]="selectedOutcome() ? 'Tagged · ready to end' : 'Required before ending the call'">
          <div class="outcomes">
            @for (o of outcomes; track o.key) {
              <button class="otag" [attr.data-tone]="o.tone" [class.sel]="selectedOutcome() === o.key" (click)="selectOutcome(o.key)">
                <va-icon [name]="o.icon" [size]="14"></va-icon>{{ o.label }}
                @if (selectedOutcome() === o.key) { <va-icon name="check" [size]="13"></va-icon> }
              </button>
            }
          </div>
          @if (!selectedOutcome()) {
            <p class="t-cap t-muted tag-hint"><va-icon name="info" [size]="13"></va-icon> Select an outcome to enable "End call". Outcomes drive next-best-action and analytics.</p>
          }
        </va-section-card>
      </section>

      <!-- RIGHT · candidate context ---------------------------- -->
      <aside class="vc-col">
        <va-section-card title="Candidate context">
          <dl class="dl">
            <dt>Stage</dt><dd>{{ active().currentStage }}</dd>
            <dt>Conversion</dt><dd><va-probability-badge [value]="active().conversionProbability" [ai]="true"></va-probability-badge></dd>
            <dt>Drop-off risk</dt><dd><va-band-chip [band]="active().dropOffRisk"></va-band-chip></dd>
            <dt>Budget</dt><dd>{{ active().budgetRange }}</dd>
            <dt>Scholarship</dt><dd>{{ active().scholarshipInterest ? 'Interested' : 'Not flagged' }}</dd>
            <dt>Lead source</dt><dd>{{ active().leadSource }}</dd>
            <dt>Last contacted</dt><dd>{{ relTime(active().lastContacted) }}</dd>
            <dt>Parent</dt><dd>{{ active().parentEngagement }}</dd>
          </dl>
          <div class="ctx-tags">
            @for (t of active().careerInterests; track t) { <span class="chip">{{ t }}</span> }
            @for (t of active().tags; track t) { <span class="chip tag-hot">{{ t }}</span> }
          </div>
        </va-section-card>

        <va-section-card title="Last AI summary" hint="Aisha · approved knowledge">
          <div class="ai-summary">
            <va-icon name="sparkles" [size]="15"></va-icon>
            <p class="t-sm">{{ active().lastAiSummary }}</p>
          </div>
          @if (active().pendingQuestions.length) {
            <div class="pending">
              <span class="t-cap t-muted">Open questions</span>
              @for (q of active().pendingQuestions; track q) {
                <div class="pq"><va-icon name="help-circle" [size]="13"></va-icon><span class="t-sm">{{ q }}</span></div>
              }
            </div>
          }
        </va-section-card>

        <va-section-card title="Recommended next action">
          <div class="nba">
            <div class="nba-ic" [attr.data-ch]="active().recommendedNextAction.channel">
              <va-icon [name]="nbaIcon()" [size]="18"></va-icon>
            </div>
            <div class="nba-body">
              <span class="t-sm" style="font-weight:600">{{ active().recommendedNextAction.label }}</span>
              <span class="t-cap t-muted">{{ active().recommendedNextAction.reason }}</span>
            </div>
          </div>
          <div class="row gap-2 wrap nba-actions">
            <button class="btn btn-accent btn-sm btn-block" (click)="applyNba()"><va-icon name="zap" [size]="14"></va-icon>Queue next action</button>
          </div>
          <div class="guardrail">
            <va-icon name="shield-check" [size]="14"></va-icon>
            <span class="t-cap t-muted">Aisha never quotes fees, scholarships or placements not in approved knowledge — it escalates instead.</span>
          </div>
        </va-section-card>
      </aside>
    </div>
  </div>
  `,
  styles: [`
    :host { display: block; }
    .vc { display: flex; flex-direction: column; gap: var(--s-6); }

    .vc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .vc-head-text p { margin-top: 4px; max-width: 78ch; }
    .vc-head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .live-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }

    .vc-consent { align-items: center; }
    .vc-consent va-icon { color: var(--color-accent); flex: none; }
    .vc-consent .chip { flex: none; }

    .vc-body { display: grid; grid-template-columns: 320px minmax(0, 1fr) 320px; gap: 18px; align-items: start; }
    .vc-col { display: flex; flex-direction: column; gap: 18px; }

    /* queue */
    .queue { padding: 8px; display: flex; flex-direction: column; gap: 4px; max-height: 540px; }
    .qrow { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--r-md); border: 1px solid transparent; transition: background .12s, border-color .12s; }
    .qrow:hover { background: var(--color-surface-alt); }
    .qrow.calling { background: rgba(var(--color-primary-rgb), .06); border-color: rgba(var(--color-primary-rgb), .2); }
    .qmeta { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
    .qname { font-size: var(--text-sm); font-weight: 600; }
    .qflags { gap: 8px; flex-wrap: wrap; }
    .flag { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: var(--r-pill); }
    .flag.ok { background: var(--color-success-soft); color: var(--color-success); }
    .flag.dnc { background: var(--color-danger-soft); color: var(--color-danger); }
    .flag.warn { background: var(--color-warning-soft); color: var(--color-warning); }
    .on-call { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; padding: 2px 8px; }

    .queue-foot { padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }

    /* center call card */
    .vc-center { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
    .callcard { padding: 18px; display: flex; flex-direction: column; gap: 16px; }
    .cc-head { display: flex; align-items: center; gap: 14px; }
    .cc-id { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
    .status-pill { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700; padding: 4px 10px; border-radius: var(--r-pill); }
    .status-pill[data-s='connected'] { background: var(--color-success-soft); color: var(--color-success); }
    .status-pill[data-s='hold'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .status-pill[data-s='ended'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .cc-timer { text-align: right; }
    .cc-timer .timer { font-size: 1.5rem; font-weight: 800; line-height: 1; letter-spacing: .01em; }
    .cc-aria { display: flex; flex-direction: column; align-items: center; gap: 4px; padding-left: 8px; border-left: 1px solid var(--color-border); }

    .cc-alert { align-items: center; }
    .cc-alert va-icon { color: var(--color-warning); flex: none; }
    .cc-alert .btn { flex: none; }

    /* signal strip */
    .signal-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .sig { background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; min-height: 64px; justify-content: space-between; }
    .senti-bar { height: 6px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; }
    .senti-bar .sf { display: block; height: 100%; border-radius: 999px; transition: width .4s ease, background .4s ease; }
    .sf.s-very-neg { background: var(--senti-very-neg); } .sf.s-neg { background: var(--senti-neg); }
    .sf.s-neutral { background: var(--senti-neutral); } .sf.s-pos { background: var(--senti-pos); } .sf.s-very-pos { background: var(--senti-very-pos); }
    .intent-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; align-self: flex-start; }
    .parent-chip { background: rgba(var(--color-accent-rgb), .12); color: var(--color-primary); border-color: transparent; align-self: flex-start; }

    /* transcript */
    .transcript-head { padding-top: 2px; }
    .transcript { background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 12px; display: flex; flex-direction: column; gap: 12px; height: 300px; }
    .tline { display: flex; flex-direction: column; gap: 4px; }
    .tline[data-sp='ai'] { align-items: flex-start; }
    .tline[data-sp='candidate'], .tline[data-sp='parent'] { align-items: flex-end; }
    .t-who { display: flex; align-items: center; gap: 6px; }
    .t-bubble { font-size: var(--text-sm); line-height: 1.45; padding: 9px 12px; border-radius: var(--r-md); max-width: 78%; margin: 0; }
    .tline[data-sp='ai'] .t-bubble { background: var(--color-surface); border: 1px solid var(--color-border); border-top-left-radius: 4px; }
    .tline[data-sp='candidate'] .t-bubble { background: var(--color-primary); color: #fff; border-top-right-radius: 4px; }
    .tline[data-sp='parent'] .t-bubble { background: color-mix(in srgb, var(--color-warning) 16%, var(--color-surface)); border: 1px solid color-mix(in srgb, var(--color-warning) 30%, var(--color-border)); border-top-right-radius: 4px; }
    .t-sys { display: flex; align-items: center; gap: 6px; font-size: var(--text-cap); color: var(--color-text-muted); justify-content: center; }
    .mini-intent { font-size: 10px; padding: 1px 7px; background: rgba(var(--color-accent-2-rgb), .1); color: var(--color-accent-2); border-color: transparent; }
    .typing { display: flex; align-items: center; gap: 4px; font-size: var(--text-cap); color: var(--color-text-muted); padding-left: 4px; }
    .typing span { width: 5px; height: 5px; border-radius: 50%; background: var(--color-accent-2); animation: va-pulse 1s ease-in-out infinite; }
    .typing span:nth-child(2) { animation-delay: .2s; } .typing span:nth-child(3) { animation-delay: .4s; margin-right: 4px; }

    /* controls */
    .controls { display: flex; align-items: center; gap: 10px; padding-top: 4px; }
    .ctrl { display: inline-flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; width: 72px; height: 60px; border-radius: var(--r-md);
      background: var(--color-surface-alt); border: 1px solid var(--color-border); color: var(--color-text); font-size: 11px; font-weight: 600; transition: all .15s; }
    .ctrl:hover:not(:disabled) { background: color-mix(in srgb, var(--color-surface-alt) 80%, var(--color-border)); }
    .ctrl:disabled { opacity: .45; cursor: not-allowed; }
    .ctrl.active { background: var(--color-warning-soft); border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); color: var(--color-warning); }
    .ctrl.takeover { background: rgba(var(--color-accent-2-rgb), .1); border-color: rgba(var(--color-accent-2-rgb), .25); color: var(--color-accent-2); }
    .end-btn { height: 60px; padding: 0 22px; }

    /* outcomes */
    .outcomes { display: flex; flex-wrap: wrap; gap: 8px; }
    .otag { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 600; padding: 7px 12px; border-radius: var(--r-pill);
      background: var(--color-surface); border: 1px solid var(--color-border); color: var(--color-text); transition: all .15s; }
    .otag:hover { background: var(--color-surface-alt); }
    .otag.sel { border-color: var(--color-primary); background: rgba(var(--color-primary-rgb), .08); color: var(--color-primary); }
    .otag[data-tone='pos'].sel { border-color: var(--color-success); background: var(--color-success-soft); color: var(--color-success); }
    .otag[data-tone='neg'].sel { border-color: var(--color-danger); background: var(--color-danger-soft); color: var(--color-danger); }
    .otag[data-tone='action'].sel { border-color: var(--color-accent-2); background: rgba(var(--color-accent-2-rgb), .1); color: var(--color-accent-2); }
    .tag-hint { display: flex; align-items: center; gap: 6px; margin-top: 10px; }

    /* right column */
    .dl dd { display: flex; justify-content: flex-end; }
    .ctx-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
    .tag-hot { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .ai-summary { display: flex; gap: 10px; }
    .ai-summary va-icon { color: var(--color-accent-2); flex: none; margin-top: 2px; }
    .ai-summary p { margin: 0; line-height: 1.5; }
    .pending { display: flex; flex-direction: column; gap: 6px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--color-border); }
    .pq { display: flex; align-items: flex-start; gap: 6px; }
    .pq va-icon { color: var(--color-warning); flex: none; margin-top: 2px; }
    .nba { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
    .nba-ic { width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .nba-ic[data-ch='voice'] { color: var(--ch-voice); background: rgba(var(--color-primary-rgb), .1); }
    .nba-ic[data-ch='whatsapp'] { color: var(--ch-whatsapp); background: color-mix(in srgb, var(--ch-whatsapp) 14%, transparent); }
    .nba-ic[data-ch='email'] { color: var(--ch-email); background: color-mix(in srgb, var(--ch-email) 14%, transparent); }
    .nba-ic[data-ch='vcon'] { color: var(--ch-vcon); background: color-mix(in srgb, var(--ch-vcon) 14%, transparent); }
    .nba-body { display: flex; flex-direction: column; gap: 2px; }
    .nba-actions { margin-bottom: 12px; }
    .guardrail { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: var(--r-md); background: rgba(var(--color-accent-2-rgb), .06); border: 1px solid rgba(var(--color-accent-2-rgb), .18); }
    .guardrail va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }

    @media (max-width: 1280px) {
      .vc-body { grid-template-columns: 280px minmax(0, 1fr); }
      .vc-col:last-child { grid-column: 1 / -1; }
      .vc-col:last-child { flex-direction: row; flex-wrap: wrap; }
      .vc-col:last-child > * { flex: 1 1 300px; }
    }
    @media (max-width: 900px) {
      .vc-body { grid-template-columns: 1fr; }
      .vc-col:last-child { flex-direction: column; }
      .signal-strip { grid-template-columns: repeat(2, 1fr); }
    }
  `],
})
export class VoiceConsoleComponent implements OnInit, OnDestroy {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);

  band = band;
  relTime = relTime;
  relFuture = relFuture;
  outboundToday = 47;

  // ── Call queue: contactable candidates worth calling ──
  queue = computed<Candidate[]>(() =>
    this.store.candidates()
      .filter(c => !c.duplicate)
      .sort((a, b) => {
        // active first, then by conversion probability
        if (a.candidateId === this.activeId()) return -1;
        if (b.candidateId === this.activeId()) return 1;
        return b.conversionProbability - a.conversionProbability;
      })
      .slice(0, 12));

  queueHint = computed(() => `${this.queue().filter(c => c.consent.call && !c.doNotContact).length} ready · approved-knowledge AI`);

  // ── Active call state ──
  activeId = signal<string>('cand-001');
  active = computed<Candidate>(() => this.store.candidateById(this.activeId()) ?? this.store.candidates()[0]);

  callState = signal<'connected' | 'hold' | 'ended'>('connected');
  muted = signal(false);
  elapsed = signal(0);              // seconds — ticked by setInterval
  confidence = signal(91);
  liveSentiment = signal<Sentiment>('pos');
  intent = signal('Course details enquiry');
  parentDetected = signal(false);
  selectedOutcome = signal<string | null>(null);

  private transcriptLines = signal<TranscriptLine[]>([]);
  transcript = computed(() => this.transcriptLines());
  private lineId = 0;

  private tick?: ReturnType<typeof setInterval>;
  private scriptTimer?: ReturnType<typeof setInterval>;
  private scriptStep = 0;

  // a scripted conversation that streams in over time
  private readonly script: Omit<TranscriptLine, 'id' | 'ts'>[] = [
    { speaker: 'candidate', text: 'Yes, I wanted to know more about the AI & Data Science program.', intent: 'Course details enquiry' },
    { speaker: 'ai', text: 'Of course. The B.Tech AI & Data Science is a 4-year programme with specialisations in machine learning and data engineering. May I share the structure?' },
    { speaker: 'candidate', text: 'Sure. And what about placements? My father is worried about that.' },
    { speaker: 'system', text: 'Parent voice detected on the line — switching to parent-aware tone.' },
    { speaker: 'ai', text: 'I can share our published placement statistics from the approved 2025 report. I should mention I am Aisha, an AI counselor — for specific company commitments I will connect you with a human counselor.' },
    { speaker: 'parent', text: 'What is the total fee, and are scholarships available for merit students?' },
    { speaker: 'ai', text: 'That is a great question. Let me pull the approved fee and scholarship details — one moment.' },
    { speaker: 'candidate', text: 'Also, is there an EMI option? And can we visit the campus?' },
  ];

  // sentiment / confidence / intent evolution synced to the script
  private readonly signalScript: { conf: number; senti: Sentiment; intent: string; parent: boolean }[] = [
    { conf: 92, senti: 'pos', intent: 'Course details enquiry', parent: false },
    { conf: 94, senti: 'pos', intent: 'Course structure', parent: false },
    { conf: 88, senti: 'neutral', intent: 'Placement assurance', parent: false },
    { conf: 85, senti: 'neutral', intent: 'Placement assurance', parent: true },
    { conf: 90, senti: 'pos', intent: 'Placement records', parent: true },
    { conf: 64, senti: 'neutral', intent: 'Fees & scholarships', parent: true },
    { conf: 58, senti: 'neg', intent: 'Fees & scholarships', parent: true },
    { conf: 33, senti: 'neg', intent: 'Out-of-scope: EMI & visit', parent: true },
  ];

  outcomes: OutcomeTag[] = [
    { key: 'interested', label: 'Interested', icon: 'thumbs-up', tone: 'pos' },
    { key: 'parent-discussion', label: 'Needs parent discussion', icon: 'users', tone: 'neutral' },
    { key: 'asked-fees', label: 'Asked for fees', icon: 'dollar-sign', tone: 'neutral' },
    { key: 'asked-scholarship', label: 'Asked for scholarship', icon: 'star', tone: 'neutral' },
    { key: 'wants-callback', label: 'Wants callback', icon: 'phone', tone: 'action' },
    { key: 'wants-whatsapp', label: 'Wants WhatsApp info', icon: 'message-circle', tone: 'action' },
    { key: 'wants-vcon', label: 'Wants V-Con', icon: 'video', tone: 'action' },
    { key: 'registered', label: 'Registered', icon: 'check-circle', tone: 'pos' },
    { key: 'not-interested', label: 'Not interested', icon: 'x', tone: 'neg' },
    { key: 'wrong-number', label: 'Wrong number', icon: 'alert-circle', tone: 'neg' },
    { key: 'no-answer', label: 'No answer', icon: 'phone', tone: 'neg' },
    { key: 'call-dropped', label: 'Call dropped', icon: 'alert-triangle', tone: 'neg' },
  ];

  // ── Derived display helpers ──
  lowConfidence = computed(() => this.confidence() < 60 && this.callState() === 'connected');
  stateLabel = computed(() =>
    this.callState() === 'connected' ? 'Connected' : this.callState() === 'hold' ? 'On hold' : 'Call ended');
  timerText = computed(() => this.fmtClock(this.elapsed()));
  sentiPct = computed(() => {
    const map: Record<Sentiment, number> = { 'very-neg': 12, 'neg': 32, 'neutral': 55, 'pos': 78, 'very-pos': 95 };
    return map[this.liveSentiment()];
  });
  confColor = computed(() => {
    const b = band(this.confidence());
    return b === 'high' ? 'var(--color-success)' : b === 'med' ? 'var(--color-warning)' : 'var(--color-danger)';
  });
  parentName = computed(() => this.active().parents[0]?.name ?? 'Parent');
  nbaIcon = computed(() => {
    const ch = this.active().recommendedNextAction.channel;
    const map: Record<string, string> = { voice: 'phone', whatsapp: 'message-circle', email: 'mail', vcon: 'video', web: 'globe', note: 'edit' };
    return map[ch] ?? 'zap';
  });

  // ── lifecycle ──
  ngOnInit(): void {
    this.seedTranscript();
    this.tick = setInterval(() => {
      if (this.callState() === 'connected') this.elapsed.update(s => s + 1);
    }, 1000);
    // stream the scripted conversation
    this.scriptTimer = setInterval(() => {
      if (this.callState() !== 'connected') return;
      if (this.scriptStep >= this.script.length) return;
      const line = this.script[this.scriptStep];
      this.pushLine(line);
      const sig = this.signalScript[this.scriptStep];
      if (sig) {
        this.confidence.set(sig.conf);
        this.liveSentiment.set(sig.senti);
        this.intent.set(sig.intent);
        this.parentDetected.set(sig.parent);
        if (sig.parent && this.scriptStep === 3) this.toast.info('Parent voice detected on the call.', 'users');
        if (sig.conf < 60 && this.confidence() === sig.conf && this.scriptStep === 5) {
          this.toast.warning('AI confidence dropping — review for human takeover.');
        }
      }
      this.scriptStep++;
    }, 3500);
  }

  ngOnDestroy(): void {
    if (this.tick) clearInterval(this.tick);
    if (this.scriptTimer) clearInterval(this.scriptTimer);
  }

  // ── actions ──
  startCall(c: Candidate): void {
    if (c.doNotContact || !c.consent.call) return;
    this.activeId.set(c.candidateId);
    this.resetCall();
    this.toast.success(`Aisha is dialing ${c.name}…`, 'phone');
  }

  private resetCall(): void {
    this.callState.set('connected');
    this.muted.set(false);
    this.elapsed.set(0);
    this.confidence.set(91);
    this.liveSentiment.set('pos');
    this.intent.set('Course details enquiry');
    this.parentDetected.set(false);
    this.selectedOutcome.set(null);
    this.scriptStep = 0;
    this.seedTranscript();
  }

  private seedTranscript(): void {
    this.lineId = 0;
    this.transcriptLines.set([]);
    this.pushLine({ speaker: 'system', text: 'Call connected · Aisha disclosed AI identity · recording with consent.' });
    this.pushLine({ speaker: 'ai', text: `Hello ${this.active().name.split(' ')[0]}, this is Aisha, an AI admission counselor from ${this.auth.institution().name}. Is this a good time to talk about ${this.active().preferredCourse}?` });
  }

  private pushLine(l: Omit<TranscriptLine, 'id' | 'ts'>): void {
    this.transcriptLines.update(list => [...list, { ...l, id: ++this.lineId, ts: this.elapsed() }]);
    queueMicrotask(() => {
      const el = document.getElementById('vc-transcript');
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  toggleMute(): void {
    this.muted.update(m => !m);
    this.toast.info(this.muted() ? 'Microphone muted.' : 'Microphone live.', 'mic');
  }

  toggleHold(): void {
    if (this.callState() === 'ended') return;
    const next = this.callState() === 'hold' ? 'connected' : 'hold';
    this.callState.set(next);
    this.pushLine({ speaker: 'system', text: next === 'hold' ? 'Call placed on hold.' : 'Call resumed.' });
    this.toast.info(next === 'hold' ? 'Call on hold.' : 'Call resumed.', 'pause');
  }

  takeOver(): void {
    this.pushLine({ speaker: 'system', text: 'Human counselor took over the call — Aisha handed off context.' });
    this.toast.success(`Routed to a human counselor (${this.auth.user().name}). Aisha handed off the full context.`, 'headphones');
  }

  selectOutcome(key: string): void {
    this.selectedOutcome.set(this.selectedOutcome() === key ? null : key);
  }

  endCall(): void {
    if (!this.selectedOutcome()) {
      this.toast.warning('Tag an outcome before ending the call.');
      return;
    }
    this.callState.set('ended');
    const o = this.outcomes.find(x => x.key === this.selectedOutcome());
    this.pushLine({ speaker: 'system', text: `Call ended · outcome tagged: ${o?.label}.` });
    this.toast.success(`Call ended — tagged "${o?.label}". Outcome saved to ${this.active().name}'s journey.`);
  }

  applyNba(): void {
    this.toast.success(`Queued: ${this.active().recommendedNextAction.label}.`, 'zap');
  }

  speakerLabel(sp: TranscriptLine['speaker']): string {
    if (sp === 'ai') return 'Aisha (AI)';
    if (sp === 'parent') return this.parentName();
    if (sp === 'candidate') return this.active().name.split(' ')[0];
    return 'System';
  }

  fmtClock(totalSec: number): string {
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = Math.floor(totalSec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  // expose for template (used only to satisfy strict-template label maps if needed)
  protected readonly SENTI_LABEL = SENTI_LABEL;
}
