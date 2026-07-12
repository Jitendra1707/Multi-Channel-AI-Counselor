import {
  ChangeDetectionStrategy, Component, ElementRef, OnDestroy, computed, effect, inject, signal, viewChild,
} from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { WebrtcAvatarService } from './webrtc-avatar.service';
import { ChatPanelComponent } from './chat-panel.component';
import { GenerativeUiPanelComponent } from './generative-ui-panel.component';
import { KnowledgeCardComponent } from './knowledge-card.component';

/**
 * VconsComponent — the director-briefing console. The director has an on-camera
 * video conversation with **Aisha** (the avatar_video / director-briefing
 * persona on AegisBackend): she answers university questions and briefs on
 * outreach analytics, putting charts on screen via her present_analytics tool
 * (the generative-UI report panel). Browser ↔ AegisBackend WebRTC.
 *
 * Lobby → in-call gate: the briefing starts on a user click (mic + optional
 * camera). In-call layout has three modes (normal / report / fullscreen) driven
 * by WebrtcAvatarService signals; the in-call <video> is never remounted across
 * those, so the avatar stream is preserved.
 */
@Component({
  selector: 'va-vcons',
  standalone: true,
  imports: [
    IconComponent, AiAvatarComponent,
    ChatPanelComponent, GenerativeUiPanelComponent, KnowledgeCardComponent,
  ],
  providers: [WebrtcAvatarService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="vcons" [class.fs]="fullscreen()" [class.report]="reportMode()" [class.live]="phase() === 'in-call'" [class.side-collapsed]="sideCollapsed()">

    @if (!fullscreen()) {
      <header class="vc-head">
        <div class="vc-head-text">
          @if (phase() === 'in-call') {
            <!-- Merged header: Aisha identity + scope live in one bar (the
                 stage-card no longer carries its own header). -->
            <div class="row gap-2 wrap vc-id-row">
              <va-ai-avatar [size]="30"></va-ai-avatar>
              <span class="t-h3 vc-name">Aisha</span>
              <span class="badge-vcon"><va-icon name="sparkles" [size]="12"></va-icon> AI presenter</span>
              <span class="chip scope-chip"><va-icon name="calendar" [size]="12"></va-icon> {{ cycle() }}</span>
              <span class="chip scope-chip"><va-icon name="clock" [size]="12"></va-icon> {{ period() }}</span>
              <span class="chip rec-chip"><span class="rdot"></span> REC · consent on file</span>
            </div>
          } @else {
            <div class="row gap-2 wrap">
              <h1 class="t-h2">V-Cons</h1>
              <span class="chip phase2-chip"><va-icon name="video" [size]="13"></va-icon> Director briefing</span>
            </div>
            <p class="t-sm t-muted">
              On-camera outreach briefings with Aisha, your AI analytics presenter.
              — {{ auth.institution().name }} · {{ auth.admissionCycle() }}
            </p>
          }
        </div>
        <div class="vc-head-actions">
          @if (phase() === 'in-call') {
            @if (svc.status() === 'connected') {
              <span class="pill-live"><span class="dot live pulse"></span> Live · {{ clock() }}</span>
            }
            @if (!reportMode() && !fullscreen()) {
              <button class="btn btn-ghost btn-sm" (click)="toggleSide()" [attr.aria-pressed]="!sideCollapsed()">
                <va-icon name="message-square" [size]="15"></va-icon> {{ sideCollapsed() ? 'Show chat' : 'Hide chat' }}
              </button>
            }
            <button class="btn btn-ghost btn-sm" (click)="escalate()"><va-icon name="users" [size]="15"></va-icon> Escalate</button>
          }
          <span class="status-pill" [attr.data-s]="svc.status()">
            <span class="dot"
              [class.live]="svc.status() === 'connected'"
              [class.limited]="svc.status() === 'connecting'"
              [class.blocked]="svc.status() === 'error' || svc.status() === 'ended'"
              [class.pulse]="svc.status() === 'connected' || svc.status() === 'connecting'"></span>
            {{ statusLabel() }}
          </span>
        </div>
      </header>
    }

    <!-- ───────────────── LOBBY ───────────────── -->
    @if (phase() === 'lobby') {
      <div class="lobby-grid">
        <div class="start-card card">
          <div class="sc-head">
            <div class="sc-id"><va-icon name="video" [size]="18"></va-icon> Start a briefing with Aisha</div>
            <span class="badge-vcon">V-Con</span>
          </div>
          <div class="sc-body lobby-body">
            <div class="aisha-hero">
              <va-ai-avatar [size]="76" [glow]="true"></va-ai-avatar>
              <div class="t-h4">Aisha <span class="badge-vcon"><va-icon name="sparkles" [size]="12"></va-icon> AI presenter</span></div>
              <div class="t-cap t-muted">AI analytics presenter · on camera</div>
            </div>

            <div class="banner ai">
              <va-icon name="shield-check" [size]="18"></va-icon>
              <span>Aisha is an <strong>AI</strong>. Outreach figures come only from approved analytics; university facts from approved knowledge. She escalates to a human when unsure.</span>
            </div>

            <button class="btn btn-accent btn-block start-btn" (click)="startBriefing()">
              <va-icon name="video" [size]="16"></va-icon> Start briefing
            </button>
            <span class="t-cap t-muted lobby-note"><va-icon name="mic" [size]="12"></va-icon> Mic &amp; camera permission needed to start</span>
          </div>
        </div>
      </div>
    }

    <!-- ───────────────── IN-CALL ───────────────── -->
    @if (phase() === 'in-call') {
      <div class="stage-grid">

        <!-- MAIN: stage card (+ transcript docked beneath in normal mode) -->
        <div class="main">
          <div class="stage-card card">
            <div class="sc-body">
              <!-- Blurred full-bleed copy of the scene fills the side gutters so the
                   centered 16:9 stage has no hard black bars. -->
              <div class="stage-bg" aria-hidden="true"></div>
              <!-- Stage = the full GIF scene (neon frame + waveforms + halo + floor);
                   the avatar is seated INSIDE the GIF's central frame. -->
              <div class="stage">
                <video #videoEl class="avatar-video" autoplay playsinline></video>

                @if (svc.status() === 'connecting') {
                  <div class="overlay"><div class="spinner"></div><p class="t-sm">Connecting to Aisha…</p></div>
                } @else if (svc.status() === 'error') {
                  <div class="overlay">
                    <va-icon name="alert-triangle" [size]="34"></va-icon>
                    <p class="t-sm">{{ svc.error() ?? 'Aisha\\'s video stream dropped.' }} Your chat and scope are saved.</p>
                    <div class="row gap-2">
                      <button class="btn btn-primary btn-sm" (click)="retry()"><va-icon name="refresh" [size]="14"></va-icon> Retry</button>
                      <button class="btn btn-ghost btn-sm" (click)="escalate()"><va-icon name="users" [size]="14"></va-icon> Escalate</button>
                    </div>
                  </div>
                } @else if (svc.status() === 'ended') {
                  <div class="overlay">
                    <va-icon name="check-circle" [size]="34"></va-icon>
                    <p class="t-sm">Briefing ended. Aisha saved the chat and an outreach recap to your library.</p>
                    <button class="btn btn-accent btn-sm" (click)="backToLobby()"><va-icon name="video" [size]="14"></va-icon> New briefing</button>
                  </div>
                }

                @if (svc.status() === 'connected') {
                  <div class="stage-tag"><va-icon name="shield-check" [size]="13"></va-icon> Aisha · presenting from approved analytics</div>
                }

                <!-- director self-view PiP -->
                @if (svc.status() !== 'ended') {
                  <div class="pip">
                    <video #pipEl class="pip-cam" autoplay playsinline muted [class.hidden]="!svc.cameraOn()"></video>
                    @if (!svc.cameraOn()) { <div class="pip-cam pip-off">{{ auth.user().initials }}</div> }
                    <span class="pip-tag"><va-icon name="video" [size]="11"></va-icon> You · Director</span>
                  </div>
                }

                <!-- controls overlaid on the video (frees vertical space for a bigger stage) -->
                <div class="controls" [class.dim]="svc.status() !== 'connected'">
                  <button class="ctrl" [class.muted]="svc.muted()" (click)="svc.toggleMute()" [disabled]="svc.status() !== 'connected'" aria-label="Mute"><va-icon name="mic" [size]="20"></va-icon></button>
                  <button class="ctrl" [class.muted]="!svc.cameraOn()" (click)="svc.toggleCamera()" [disabled]="svc.status() !== 'connected'" aria-label="Camera"><va-icon name="video" [size]="20"></va-icon></button>
                  <button class="ctrl" [class.armed]="svc.knowledgeCaptureState() === 'armed'" (click)="svc.toggleKnowledgeCapture()" [disabled]="svc.status() !== 'connected' || svc.knowledgeCaptureState() === 'processing'" aria-label="Capture knowledge" [title]="svc.knowledgeCaptureState() === 'armed' ? 'Cancel capture' : 'Capture: click, then say the fact'"><va-icon name="bookmark" [size]="20"></va-icon></button>
                  <button class="ctrl end" (click)="endCall()" aria-label="End briefing"><va-icon name="phone" [size]="20"></va-icon></button>
                  <button class="ctrl fs-btn" (click)="fullscreen.set(!fullscreen())" aria-label="Fullscreen"><va-icon [name]="fullscreen() ? 'minus' : 'maximize'" [size]="20"></va-icon></button>
                </div>

                <!-- In-call knowledge deck: READ-ONLY result cards + capture-flow
                     chips. Actions live on the Knowledge Review screen. -->
                <va-knowledge-card></va-knowledge-card>
              </div>
            </div>
          </div>

        </div>

        <!-- RIGHT column: report (report mode) or live transcript (normal) -->
        @if (!fullscreen()) {
          @if (reportMode()) {
            @if (svc.uiDirective(); as dir) {
              <section class="report-area">
                <va-generative-ui-panel [directive]="dir" (dismiss)="svc.clearUiDirective()"></va-generative-ui-panel>
              </section>
            }
          } @else if (!sideCollapsed()) {
            <va-chat-panel class="side-transcript"
              [messages]="svc.transcript()"
              (send)="svc.sendChatMessage($event)"
              (attach)="svc.sendAttachment($event)"></va-chat-panel>
          }
        }
      </div>
    }
  </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .vcons { display: flex; flex-direction: column; height: 100%; gap: var(--s-6); padding: var(--s-6); }
    .vcons.fs { padding: 0; gap: 0; }
    /* In-call: reclaim vertical chrome so the avatar stage can be larger. */
    .vcons.live:not(.fs) { padding: 16px; gap: 12px; }
    .vcons.live:not(.fs) .sc-body { padding: 10px; }
    .vc-id-row { align-items: center; }
    .vc-name { font-weight: 700; }

    .vc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .vc-head-text p { margin-top: 6px; max-width: 78ch; }
    .vc-head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .phase2-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .scope-chip { background: var(--color-surface); border-color: var(--color-border); }
    .rec-chip { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }
    .rec-chip .rdot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-danger); }
    .status-pill { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700; padding: 5px 12px; border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); }
    .status-pill[data-s='connected'] { background: var(--color-success-soft); color: var(--color-success); }
    .status-pill[data-s='connecting'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .status-pill[data-s='error'] { background: var(--color-danger-soft); color: var(--color-danger); }

    .badge-vcon { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 700; padding: 3px 10px; border-radius: var(--r-pill);
      background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }

    /* shared card head */
    .sc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--color-border); }
    .sc-id { display: flex; align-items: center; gap: 10px; font-size: var(--text-h4); font-weight: 600; }
    .sc-id va-icon { color: var(--color-accent-2); }
    .sc-body { padding: 18px; }

    /* ── Lobby ── */
    .lobby-grid { display: grid; grid-template-columns: minmax(0, 560px); justify-content: center; gap: 18px; align-items: start; }
    .start-card { max-width: 660px; margin: 0 auto; width: 100%; }
    .lobby-body { display: flex; flex-direction: column; gap: 18px; align-items: center; text-align: center; }
    .aisha-hero { display: flex; flex-direction: column; align-items: center; gap: 8px; padding-top: 4px; }
    .start-btn { margin-top: 2px; }
    .lobby-note { display: inline-flex; align-items: center; gap: 6px; }

    /* ── In-call grid ── */
    .stage-grid { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 344px; gap: 18px; align-items: start; }
    /* Report mode: the report panel fills the whole area; Aisha floats as a bare
       avatar square bottom-right (no card ribbon). The report's scroll body gets
       a right safe-area so its full-width content never sits under the tile. */
    .vcons.report .stage-grid { grid-template-columns: 1fr; position: relative; align-items: stretch; }
    .vcons.report .report-area { grid-column: 1 / -1; --gp-pad-right: 236px; }
    .vcons.report .main { position: absolute; right: 48px; bottom: 8px; width: auto; margin: 0; z-index: 6; }
    .vcons.report .stage-card { background: transparent; border: none; box-shadow: none; }
    .vcons.report:not(.fs) .sc-body { padding: 0; }
    .vcons.report .stage { width: 168px; height: 168px; aspect-ratio: 1 / 1; box-shadow: var(--e3); }
    .vcons.report .pip, .vcons.report .stage-tag { display: none; }
    .vcons.report .controls { bottom: 8px; gap: 7px; padding: 5px 8px; }
    .vcons.report .controls .ctrl { width: 34px; height: 34px; }
    .vcons.report .controls .fs-btn { display: none; }
    .vcons.fs .stage-grid { grid-template-columns: 1fr; gap: 0; }
    /* Collapsed transcript (default on entry): stage fills the main area. */
    .vcons.side-collapsed:not(.report):not(.fs) .stage-grid { grid-template-columns: 1fr; }
    .main { display: flex; flex-direction: column; gap: 18px; min-width: 0; }

    .stage-card { display: flex; flex-direction: column; }
    .sc-status { display: flex; align-items: center; gap: 8px; }
    .pill-live, .pill-amber, .pill-danger, .pill-muted { display: inline-flex; align-items: center; gap: 7px; font-size: var(--text-cap); font-weight: 700; padding: 5px 11px; border-radius: var(--r-pill); }
    .pill-live { background: var(--color-success-soft); color: var(--color-success); }
    .pill-amber { background: var(--color-warning-soft); color: var(--color-warning); }
    .pill-danger { background: var(--color-danger-soft); color: var(--color-danger); }
    .pill-muted { background: var(--color-surface-alt); color: var(--color-text-muted); }

    /* Stage = the full GIF scene at 16:9 (frame + waveforms + halo + floor). The
       avatar is seated inside the GIF's central neon frame (rect = tunable vars). */
    .stage { position: relative; z-index: 1; aspect-ratio: 960 / 540; width: 100%; max-width: calc((100vh - 150px) * 16 / 9);
      margin-inline: auto; background: url('/avatar-ambient.webp') center / cover no-repeat; overflow: visible; display: block;
      --fr-l: 33%; --fr-t: 20%; --fr-w: 34.5%; --fr-h: 57%; }
    .avatar-video { position: absolute; left: var(--fr-l); top: var(--fr-t); width: var(--fr-w); height: var(--fr-h);
      object-fit: cover; display: block; background: transparent; }

    /* Immersive stage: transparent card, dark gutters blend with the GIF edges. */
    .stage-card .sc-body { position: relative; }
    .vcons.live:not(.fs) .stage-card { background: transparent; border: none; box-shadow: none; }
    .vcons.live:not(.fs) .sc-body { padding: 0; background: #070b14; border-radius: var(--r-lg); overflow: hidden;
      display: flex; align-items: center; justify-content: center; }

    /* Blurred full-bleed scene fills the side gutters (no hard black bars). */
    .stage-bg { position: absolute; inset: 0; z-index: 0; pointer-events: none;
      background: url('/avatar-ambient.webp') center / cover no-repeat;
      filter: blur(40px) brightness(.55) saturate(1.1); transform: scale(1.12); }
    .vcons.report .stage-bg, .vcons.fs .stage-bg { display: none; }

    /* Position the status tag, controls and self-view against the GIF's frame. */
    .vcons.live:not(.fs):not(.report) .stage-tag { top: 9%; left: 50%; transform: translateX(-50%); }
    .vcons.live:not(.fs):not(.report) .controls { bottom: 21%; }
    .vcons.live:not(.fs):not(.report) .pip { right: 17%; bottom: 24%; width: 92px; }
    .vcons.live:not(.fs):not(.report) .pip-cam { width: 92px; height: 62px;
      background: color-mix(in srgb, #0a1030 82%, transparent); border-color: rgba(120,140,200,.4); }
    .vcons.live:not(.fs):not(.report) .pip-off { color: #cdd7f0; }
    .vcons.live:not(.fs):not(.report) .pip-tag { background: color-mix(in srgb, #0a1030 72%, transparent);
      color: #cdd7f0; border-color: rgba(120,140,200,.35); }

    /* Report tile + fullscreen: no GIF bg, avatar fills its box (not the framed rect). */
    .vcons.report .stage { background: none; }
    .vcons.report .avatar-video, .vcons.fs .avatar-video {
      position: static; left: auto; top: auto; width: 100%; height: 100%; }
    .vcons.report .avatar-video { object-fit: cover; }
    .vcons.fs .avatar-video { object-fit: contain; }

    .overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
      gap: 12px; padding: 24px; background: color-mix(in srgb, var(--color-surface) 86%, transparent); backdrop-filter: blur(2px); }
    .overlay va-icon { color: var(--color-text-muted); }
    .overlay p { margin: 0; max-width: 42ch; }
    .spinner { width: 38px; height: 38px; border-radius: 50%; border: 3px solid var(--color-surface-alt); border-top-color: var(--color-accent); animation: va-spin .8s linear infinite; }

    .stage-tag { position: absolute; left: 14px; top: 14px; display: inline-flex; align-items: center; gap: 7px; font-size: var(--text-cap); font-weight: 600;
      color: var(--color-text); background: color-mix(in srgb, var(--color-surface) 82%, transparent); backdrop-filter: blur(4px); padding: 5px 11px; border-radius: var(--r-pill); border: 1px solid var(--color-border); }
    .stage-tag va-icon { color: var(--color-accent-2); }

    .pip { position: absolute; right: 14px; bottom: 14px; width: 116px; display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
    .pip-cam { width: 116px; height: 78px; border-radius: var(--r-md); object-fit: cover; border: 1.5px solid var(--color-border-strong);
      background: var(--color-surface-alt); box-shadow: var(--e2); }
    .pip-cam.hidden { display: none; }
    .pip-off { display: grid; place-items: center; font-weight: 700; color: var(--color-text-muted); }
    .pip-tag { display: inline-flex; align-items: center; gap: 5px; font-size: 10px; font-weight: 600; padding: 3px 8px; border-radius: var(--r-pill);
      background: color-mix(in srgb, var(--color-surface) 82%, transparent); backdrop-filter: blur(4px); border: 1px solid var(--color-border); }

    .controls { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 4;
      display: flex; align-items: center; justify-content: center; gap: 12px; padding: 8px 12px; border-radius: var(--r-pill);
      background: color-mix(in srgb, var(--color-surface) 66%, transparent); backdrop-filter: blur(8px);
      border: 1px solid var(--color-border); box-shadow: var(--e2); }
    .controls.dim .ctrl:not(.end) { opacity: .5; }
    .ctrl { width: 48px; height: 48px; display: grid; place-items: center; border-radius: 50%; background: var(--color-surface); border: 1px solid var(--color-border); color: var(--color-text); box-shadow: var(--e1); }
    .ctrl:hover:not(:disabled) { background: var(--color-surface-alt); }
    .ctrl:disabled { opacity: .5; cursor: not-allowed; }
    .ctrl.muted { background: var(--color-surface-alt); color: var(--color-text-muted); }
    /* Armed knowledge capture: the NEXT statement will be recorded — make the
       listening state unmissable. */
    .ctrl.armed { background: var(--accent, #6b4eff); color: #fff; border-color: transparent;
                  animation: ctrl-armed-pulse 1.4s ease-in-out infinite; }
    @keyframes ctrl-armed-pulse {
      0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent, #6b4eff) 55%, transparent); }
      50% { box-shadow: 0 0 0 7px color-mix(in srgb, var(--accent, #6b4eff) 0%, transparent); }
    }
    .ctrl.end { background: var(--color-danger); color: #fff; border-color: transparent; }

    /* ── Side transcript (normal mode) ── */
    .side-transcript { align-self: stretch; min-width: 0; min-height: 0; max-height: calc(100vh - 220px); }

    .report-area { min-width: 0; min-height: 0; }

    /* ── Fullscreen ── */
    .vcons.fs .stage-card { border: none; border-radius: 0; height: 100vh; box-shadow: none; }
    .vcons.fs .sc-head { background: rgba(2,6,23,.4); }
    .vcons.fs .sc-body { flex: 1; display: flex; flex-direction: column; padding: 0; }
    .vcons.fs .stage { flex: 1; aspect-ratio: auto; width: auto; height: auto; border: none; border-radius: 0; background: #0b0f1a; }
    .vcons.fs .controls { bottom: 28px; }

    @media (max-width: 1100px) {
      .lobby-grid { grid-template-columns: 1fr; }
      .stage-grid { grid-template-columns: 1fr; }
      .vcons.report .stage-grid { grid-template-columns: 1fr; }
      .side-transcript { order: 2; max-height: none; }
    }
  `],
})
export class VconsComponent implements OnDestroy {
  auth = inject(AuthService);
  toast = inject(ToastService);
  svc = inject(WebrtcAvatarService);

  private videoEl = viewChild<ElementRef<HTMLVideoElement>>('videoEl');
  private pipEl = viewChild<ElementRef<HTMLVideoElement>>('pipEl');

  phase = signal<'lobby' | 'in-call'>('lobby');
  fullscreen = signal(false);
  /** Avatar-matched gutter glow — an "r, g, b" triplet sampled once on connect. */
  glowColor = signal<string | null>(null);
  /** Chat side panel — shown by default on entry (showcases the chat + document
   *  upload capability alongside the audio); toggled in the header. */
  sideCollapsed = signal(false);
  reportMode = computed(() => this.svc.uiDirective() !== null);

  toggleSide(): void { this.sideCollapsed.update(v => !v); }

  // ── Scope ──
  cycle = computed(() => this.auth.admissionCycle());
  periods = [{ key: '7d', label: 'Last 7 days' }, { key: '30d', label: 'Last 30 days' }, { key: '90d', label: 'Last 90 days' }];
  periodKey = signal('30d');
  period = computed(() => this.periods.find(p => p.key === this.periodKey())?.label ?? 'Last 30 days');

  // ── Live call timer ──
  private elapsed = signal(0);
  clock = computed(() => {
    const s = this.elapsed();
    return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
  });
  private tick?: ReturnType<typeof setInterval>;

  private pendingStart = false;

  constructor() {
    // Start the briefing once the in-call <video> is in the DOM (set on click).
    effect(() => {
      const el = this.videoEl();
      if (this.pendingStart && this.phase() === 'in-call' && el) {
        this.pendingStart = false;
        void this.svc.start({ video: el.nativeElement });
      }
    });
    // Attach the director self-view stream to the PiP element when available.
    effect(() => {
      const stream = this.svc.selfView();
      const el = this.pipEl()?.nativeElement;
      if (el && el.srcObject !== stream) { el.srcObject = stream; el.play().catch(() => undefined); }
    });
    // Live call timer.
    effect(() => {
      if (this.svc.status() === 'connected' && !this.tick) {
        this.tick = setInterval(() => this.elapsed.update(s => s + 1), 1000);
      } else if (this.svc.status() !== 'connected' && this.tick) {
        clearInterval(this.tick); this.tick = undefined;
      }
    });
  }

  ngOnDestroy(): void {
    if (this.tick) clearInterval(this.tick);
    void this.svc.stop();
  }


  startBriefing(): void {
    this.elapsed.set(0);
    this.pendingStart = true;
    this.phase.set('in-call');
  }

  retry(): void {
    const el = this.videoEl()?.nativeElement;
    if (el) { this.elapsed.set(0); void this.svc.start({ video: el }); }
  }

  endCall(): void {
    void this.svc.stop();
    this.fullscreen.set(false);
  }

  backToLobby(): void {
    this.fullscreen.set(false);
    this.phase.set('lobby');
  }

  escalate(): void {
    this.toast.success('Escalated to the admissions operations lead — they’ll follow up.', 'users');
  }

  statusLabel(): string {
    switch (this.svc.status()) {
      case 'connecting': return 'Connecting';
      case 'connected': return 'Live';
      case 'error': return 'Connection error';
      case 'ended': return 'Ended';
      default: return 'Ready';
    }
  }
}
