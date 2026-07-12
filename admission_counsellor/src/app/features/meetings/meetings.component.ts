import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { SentimentBadgeComponent } from '../../shared/ui/badges.component';
import { CounselorService } from '../../core/counselor.service';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Sentiment } from '../../domain/models';
import { MeetingService } from './meeting.service';

interface Participant { name: string; role: string; }
interface Live { id: string; title: string; topic: string; min: number; sentiment: Sentiment; confidence: number; participants: Participant[]; }
interface Slot { id: string; start: number; dur: number; title: string; who: string; type: string; participants: number; status: 'live' | 'upcoming' | 'done'; lane?: number; lanes?: number; }
interface Done { id: string; title: string; who: string; ago: string; dur: number; sentiment: Sentiment; summary: string; actions: string[]; sent: { candidate: boolean; parent: boolean; counselor: boolean }; }

@Component({
  selector: 'va-meetings',
  standalone: true,
  imports: [IconComponent, AiAvatarComponent, SectionCardComponent, DrawerComponent, SentimentBadgeComponent],
  // MeetingService here is used only for the control plane (create/schedule/token);
  // the live room runs on the full-page /meeting/:room route, not in this screen.
  providers: [MeetingService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <header class="mt-head">
      <div>
        <div class="row gap-2">
          <div class="t-h2">Meetings & Calendar</div>
          <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ meta().name }} · {{ meta().short }}</span>
        </div>
        <p class="t-sm t-muted">Collaborative {{ career() ? 'career sessions' : 'counseling meetings' }} — {{ auth.institution().name }} · today, {{ today }}</p>
      </div>
      <div class="row gap-2">
        <div class="seg">
          @for (v of views; track v) { <button [class.active]="view() === v" (click)="view.set(v)">{{ v }}</button> }
        </div>
        <button class="btn btn-subtle" (click)="join.set(true)"><va-icon name="video" [size]="16"></va-icon> Join meeting</button>
        <button class="btn btn-subtle" (click)="startInstant()" [disabled]="starting()"><va-icon name="phone" [size]="16"></va-icon> Instant meeting</button>
        <button class="btn btn-primary" (click)="schedule.set(true)"><va-icon name="plus" [size]="16"></va-icon> Schedule meeting</button>
      </div>
    </header>

    <!-- Concurrency strip: one AI counselor, many simultaneous sessions -->
    <div class="live-strip card" [attr.data-v]="counselor.active()">
      <div class="ls-id">
        <va-ai-avatar [size]="46" [glow]="true" [variant]="counselor.active()"></va-ai-avatar>
        <div>
          <div class="t-h4">{{ meta().name }} is in <span class="num">{{ live().length }}</span> concurrent sessions</div>
          <div class="t-cap t-muted">One AI counselor attends many meetings at once — each recorded, analysed & communicated to stakeholders.</div>
        </div>
        <span class="rec"><span class="dot"></span> Recording all</span>
      </div>
      <div class="ls-cards">
        @for (s of live(); track s.id) {
          <div class="ls-card">
            <div class="between">
              <span class="ls-title truncate">{{ s.title }}</span>
              <span class="ls-rec"><span class="dot pulse"></span>{{ s.min }}m</span>
            </div>
            <div class="ls-meta t-cap t-muted truncate">{{ s.topic }}</div>
            <div class="ls-people">
              @for (p of s.participants; track p.name) { <span class="pp" [title]="p.name + ' · ' + p.role">{{ initials(p.name) }}</span> }
            </div>
            <div class="ls-foot">
              <va-sentiment-badge [value]="s.sentiment"></va-sentiment-badge>
              <span class="conf"><va-icon name="gauge" [size]="12"></va-icon>{{ s.confidence }}%</span>
            </div>
            <div class="ls-actions">
              <button class="btn btn-sm btn-subtle" (click)="toast.info('Listening in to ' + s.title)"><va-icon name="headphones" [size]="13"></va-icon> Listen</button>
              <button class="btn btn-sm btn-subtle" (click)="toast.info('Live analysis for ' + s.title)"><va-icon name="sparkles" [size]="13"></va-icon> Analyse</button>
              <button class="btn btn-sm btn-subtle" (click)="toast.success('Update sent to stakeholders')"><va-icon name="send" [size]="13"></va-icon></button>
            </div>
          </div>
        }
      </div>
    </div>

    <div class="mt-body">
      <!-- Day calendar with concurrency lanes -->
      <va-section-card [title]="view() === 'Today' ? 'Today’s schedule' : 'This week'" hint="Overlapping blocks show concurrent AI sessions" [flush]="true">
        <span actions class="chip"><va-icon name="calendar" [size]="12"></va-icon> {{ slots.length }} meetings</span>
        <div class="cal">
          <div class="cal-gutter">
            @for (h of hours; track h) { <div class="hr"><span>{{ fmtHour(h) }}</span></div> }
          </div>
          <div class="cal-track" [style.height.px]="trackHeight">
            @for (h of hours; track h) { <div class="cal-line" [style.top.px]="(h - startH) * hourPx"></div> }
            <div class="now-line" [style.top.px]="nowTop"><span>now</span></div>
            @for (s of laidOut(); track s.id) {
              <div class="ev" [attr.data-status]="s.status" [attr.data-v]="counselor.active()"
                   [style.top.px]="top(s)" [style.height.px]="height(s)"
                   [style.left.%]="laneLeft(s)" [style.width.%]="laneWidth(s)"
                   (click)="toast.info(s.title)">
                <div class="ev-t truncate">{{ s.title }}</div>
                <div class="ev-m truncate">{{ fmtMin(s.start) }} · {{ s.who }}</div>
                @if (s.status === 'live') { <span class="ev-rec"><span class="dot pulse"></span></span> }
              </div>
            }
          </div>
        </div>
      </va-section-card>

      <!-- Recent sessions: record → analyse → communicate -->
      <aside class="mt-rail">
        <va-section-card title="Recent sessions" hint="Recorded · analysed · communicated">
          <div class="done-list">
            @for (d of done; track d.id) {
              <div class="done">
                <div class="between">
                  <span class="done-t truncate">{{ d.title }}</span>
                  <span class="t-cap t-muted">{{ d.ago }}</span>
                </div>
                <div class="t-cap t-muted">{{ d.who }} · {{ d.dur }}m</div>
                <p class="done-sum t-sm">{{ d.summary }}</p>
                <div class="done-actions-row">
                  @for (a of d.actions; track a) { <span class="chip sm"><va-icon name="check-square" [size]="11"></va-icon>{{ a }}</span> }
                </div>
                <div class="done-foot">
                  <va-sentiment-badge [value]="d.sentiment"></va-sentiment-badge>
                  <span class="sent" [title]="'Summary communicated to stakeholders'">
                    <va-icon name="send" [size]="12"></va-icon>
                    <span [class.on]="d.sent.candidate">{{ career() ? 'Student' : 'Candidate' }}</span>
                    @if (d.sent.parent) { <span class="on">Parent</span> }
                    <span [class.on]="d.sent.counselor">Counsellor</span>
                  </span>
                  <button class="link-btn" (click)="toast.info('Opening recording & transcript')">Recording</button>
                </div>
              </div>
            }
          </div>
        </va-section-card>
        <div class="banner ai" [attr.data-v]="counselor.active()">
          <va-icon name="sparkles" [size]="16"></va-icon>
          <span>{{ meta().name }} auto-generates a summary, sentiment read & action items for every session, then shares them with the right stakeholders — from approved knowledge only.</span>
        </div>
      </aside>
    </div>
  </div>

  <!-- Schedule drawer — one public meeting link, no names (Google-Meet style) -->
  <va-drawer [open]="schedule()" title="Schedule a meeting" [subtitle]="'Creates one link you can share with anyone'" [width]="460" (close)="closeSchedule()">
    <div class="grid2">
      <div class="field"><span class="label">Date</span><input class="input" type="date" value="2026-06-15" /></div>
      <div class="field"><span class="label">Time</span><input class="input" type="time" value="11:00" /></div>
    </div>
    <div class="field"><span class="label">Type</span>
      <select class="select">
        @for (t of meetingTypes(); track t) { <option>{{ t }}</option> }
      </select>
    </div>
    <div class="field"><span class="label">Agenda</span><textarea class="textarea" rows="3" [value]="career() ? 'Discuss aptitude results, recommended pathways and a skill plan.' : 'Walk through course, fees, scholarships and next steps.'"></textarea></div>
    <label class="ai-opt"><input type="checkbox" [checked]="includeAi()" (change)="includeAi.set($any($event.target).checked)" /> Include the {{ meta().name }} AI assistant from the start</label>
    <div class="banner info"><va-icon name="info" [size]="15"></va-icon><span>One shareable link is created — send it to anyone. Each person enters their own name to join. You can also add the AI from inside the room.</span></div>
    @if (shareLink()) {
      <div class="banner success">
        <va-icon name="check-circle" [size]="15"></va-icon>
        <div class="links">
          <span>Meeting ready — share this link:</span>
          <div class="link-row">
            <input class="input link-input" readonly [value]="shareLink()" (focus)="$any($event.target).select()" />
            <button class="btn btn-sm btn-subtle" (click)="copy(shareLink())"><va-icon name="clipboard-check" [size]="13"></va-icon> Copy</button>
          </div>
        </div>
      </div>
    }
    <div footer>
      <button class="btn btn-ghost grow" (click)="closeSchedule()">Close</button>
      <button class="btn btn-primary grow" (click)="book()" [disabled]="booking()"><va-icon name="calendar" [size]="16"></va-icon> {{ booking() ? 'Creating…' : (shareLink() ? 'Create another' : 'Create link') }}</button>
    </div>
  </va-drawer>

  <!-- Join meeting drawer: enter a room name/link → opens the full-page room -->
  <va-drawer [open]="join()" title="Join a meeting" subtitle="Paste the room name or link" [width]="420" (close)="join.set(false)">
    <div class="field"><span class="label">Room or link</span><input class="input" [value]="joinRoom()" (input)="joinRoom.set($any($event.target).value)" placeholder="e.g. meet-abc123" /></div>
    <div class="banner info"><va-icon name="info" [size]="15"></va-icon><span>Opens the meeting page where you enter your name and join over camera & mic.</span></div>
    <div footer>
      <button class="btn btn-ghost grow" (click)="join.set(false)">Cancel</button>
      <button class="btn btn-primary grow" (click)="doJoin()" [disabled]="!joinRoom().trim()"><va-icon name="video" [size]="16"></va-icon> Join</button>
    </div>
  </va-drawer>`,
  styles: [`
    :host { display: block; }
    .mt-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .cnsl-pill { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700; padding: 4px 10px 4px 5px; border-radius: var(--r-pill); background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .cnsl-pill[data-v='career'] { background: rgba(var(--color-career-rgb), .14); color: var(--color-career); }

    .live-strip { display: flex; flex-direction: column; gap: 14px; border-left: 3px solid var(--color-accent-2); }
    .live-strip[data-v='career'] { border-left-color: var(--color-career); }
    .ls-id { display: flex; align-items: center; gap: 14px; }
    .ls-id .num { background: var(--gradient-ai); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
    .live-strip[data-v='career'] .ls-id .num { background: var(--gradient-career); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
    .rec { margin-left: auto; display: inline-flex; align-items: center; gap: 7px; font-size: var(--text-cap); font-weight: 700; color: var(--color-danger); }
    .rec .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-danger); }
    .ls-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .ls-card { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 12px; display: flex; flex-direction: column; gap: 7px; background: var(--color-surface-2); }
    .ls-title { font-size: var(--text-sm); font-weight: 600; }
    .ls-rec { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; color: var(--color-danger); }
    .ls-rec .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-danger); }
    .ls-people { display: flex; gap: 4px; }
    .pp { width: 24px; height: 24px; border-radius: 50%; background: var(--color-surface-alt); display: grid; place-items: center; font-size: 10px; font-weight: 700; color: var(--color-text-muted); border: 1px solid var(--color-border); }
    .ls-foot { display: flex; align-items: center; justify-content: space-between; }
    .conf { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 700; color: var(--color-accent); }
    .ls-actions { display: flex; gap: 5px; }
    .ls-actions .btn { flex: 1; padding: 5px; }

    .mt-body { display: grid; grid-template-columns: minmax(0,1fr) 360px; gap: 18px; align-items: start; }
    .mt-rail { display: flex; flex-direction: column; gap: 18px; }
    .cal { display: grid; grid-template-columns: 56px 1fr; padding: 8px 14px 14px; }
    .cal-gutter .hr { height: 64px; position: relative; }
    .cal-gutter .hr span { position: absolute; top: -7px; font-size: 11px; color: var(--color-text-muted); }
    .cal-track { position: relative; border-left: 1px solid var(--color-border); }
    .cal-line { position: absolute; left: 0; right: 0; border-top: 1px dashed var(--color-border); }
    .now-line { position: absolute; left: 0; right: 0; border-top: 2px solid var(--color-danger); z-index: 3; }
    .now-line span { position: absolute; left: 4px; top: -8px; font-size: 9px; font-weight: 700; color: #fff; background: var(--color-danger); padding: 1px 5px; border-radius: 999px; }
    .ev { position: absolute; border-radius: 8px; padding: 6px 8px; overflow: hidden; cursor: pointer; border: 1px solid transparent; box-shadow: var(--e1); transition: transform .1s; }
    .ev:hover { transform: scale(1.01); z-index: 4; }
    .ev[data-v='admission'] { background: rgba(var(--color-accent-2-rgb), .12); border-color: color-mix(in srgb, var(--color-accent-2) 30%, transparent); }
    .ev[data-v='career'] { background: rgba(var(--color-career-rgb), .14); border-color: color-mix(in srgb, var(--color-career) 30%, transparent); }
    .ev[data-status='done'] { opacity: .55; }
    .ev[data-status='live'] { box-shadow: 0 0 0 2px var(--color-danger); }
    .ev-t { font-size: var(--text-cap); font-weight: 700; }
    .ev-m { font-size: 10px; color: var(--color-text-muted); }
    .ev-rec { position: absolute; top: 6px; right: 6px; }
    .ev-rec .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-danger); }

    .done-list { display: flex; flex-direction: column; gap: 12px; }
    .done { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 12px; }
    .done-t { font-size: var(--text-sm); font-weight: 600; }
    .done-sum { color: var(--color-text-muted); margin: 6px 0; }
    .done-actions-row { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
    .chip.sm { font-size: 10px; padding: 2px 7px; }
    .done-foot { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding-top: 8px; border-top: 1px solid var(--color-border); }
    .sent { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--color-text-muted); }
    .sent va-icon { color: var(--color-success); }
    .sent .on { color: var(--color-success); font-weight: 700; }
    .link-btn { margin-left: auto; background: none; border: none; color: var(--color-primary); font-size: var(--text-cap); font-weight: 600; }
    .banner.ai[data-v='career'] va-icon { color: var(--color-career); }
    .banner.ai va-icon { color: var(--color-accent-2); }

    .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .pick { display: flex; flex-wrap: wrap; gap: 7px; }
    .pick-chip { cursor: pointer; }
    .pick-chip.on { background: rgba(var(--color-primary-rgb), .1); color: var(--color-primary); border-color: rgba(var(--color-primary-rgb), .3); }
    .banner.success { background: rgba(var(--color-success-rgb), .1); border: 1px solid color-mix(in srgb, var(--color-success) 30%, transparent); border-radius: var(--r-md); padding: 10px 12px; display: flex; gap: 10px; align-items: flex-start; margin-bottom: 14px; }
    .banner.success va-icon { color: var(--color-success); }
    .banner.success .links { display: flex; flex-direction: column; gap: 6px; font-size: var(--text-sm); width: 100%; }
    .banner.success .link-btn { align-self: flex-start; background: none; border: none; color: var(--color-primary); font-size: var(--text-cap); font-weight: 600; cursor: pointer; padding: 0; }
    .link-row { display: flex; gap: 6px; align-items: center; }
    .link-input { flex: 1; font-size: 12px; }
    .ai-opt { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); color: var(--color-text); margin-bottom: 14px; cursor: pointer; }

    @media (max-width: 1100px) { .mt-body { grid-template-columns: 1fr; } .ls-cards { grid-template-columns: repeat(2, 1fr); } }
  `],
})
export class MeetingsComponent {
  counselor = inject(CounselorService);
  auth = inject(AuthService);
  toast = inject(ToastService);
  meeting = inject(MeetingService);
  private router = inject(Router);

  meta = this.counselor.activeMeta;
  career = computed(() => this.counselor.active() === 'career');
  today = '15 Jun 2026';
  views = ['Today', 'Week'] as const;
  view = signal<'Today' | 'Week'>('Today');
  schedule = signal(false);
  picked = signal<string[]>([]);

  // ── real meeting state (one-link create / schedule / join) ─────────────────
  // The live room runs on the full-page /meeting/:room route, so this screen
  // only creates/mints + navigates there (no in-screen room state).
  booking = signal(false);
  includeAi = signal(false);
  shareLink = signal('');          // the one public link shown after create
  starting = signal(false);
  join = signal(false);
  joinRoom = signal('');

  startH = 8; endH = 18; hourPx = 64;
  get hours() { return Array.from({ length: this.endH - this.startH + 1 }, (_, i) => this.startH + i); }
  get trackHeight() { return (this.endH - this.startH) * this.hourPx; }
  nowTop = (11 * 60 + 18 - this.startH * 60) * (this.hourPx / 60); // 11:18

  fmtHour(h: number) { const p = h >= 12 ? 'pm' : 'am'; const hh = h % 12 || 12; return `${hh} ${p}`; }
  fmtMin(m: number) { const h = Math.floor(m / 60), mm = m % 60; const p = h >= 12 ? 'pm' : 'am'; const hh = h % 12 || 12; return `${hh}:${mm.toString().padStart(2, '0')} ${p}`; }
  top(s: Slot) { return (s.start - this.startH * 60) * (this.hourPx / 60); }
  height(s: Slot) { return Math.max(26, s.dur * (this.hourPx / 60) - 4); }
  laneLeft(s: Slot) { return 1 + (s.lane! * (98 / (s.lanes || 1))); }
  laneWidth(s: Slot) { return (98 / (s.lanes || 1)) - 1.5; }

  live = computed<Live[]>(() => this.career() ? [
    { id: 'l1', title: 'Pathway review — Aditya K.', topic: 'Data Science vs SDE pathway', min: 12, sentiment: 'pos', confidence: 92, participants: [{ name: 'Aditya Kapoor', role: 'Student' }, { name: 'Lata Kapoor', role: 'Parent' }] },
    { id: 'l2', title: 'Aptitude debrief — Riya S.', topic: 'Interest profile + skill plan', min: 7, sentiment: 'pos', confidence: 88, participants: [{ name: 'Riya Sharma', role: 'Student' }] },
    { id: 'l3', title: 'Mentor intro — Yash P.', topic: 'Cloud career mentor match', min: 19, sentiment: 'neutral', confidence: 84, participants: [{ name: 'Yash Patel', role: 'Student' }, { name: 'Vikram Rao', role: 'Mentor' }] },
    { id: 'l4', title: 'Upskilling plan — Meera N.', topic: 'Python & SQL gap closure', min: 4, sentiment: 'very-pos', confidence: 90, participants: [{ name: 'Meera Nair', role: 'Student' }] },
  ] : [
    { id: 'l1', title: 'Course & fees — Ananya R.', topic: 'B.Tech AI fees + scholarship', min: 12, sentiment: 'pos', confidence: 94, participants: [{ name: 'Ananya Reddy', role: 'Candidate' }, { name: 'Suresh Reddy', role: 'Parent' }] },
    { id: 'l2', title: 'Parent counseling — Kabir J.', topic: 'Placement & hostel safety', min: 8, sentiment: 'neutral', confidence: 86, participants: [{ name: 'Kabir Joshi', role: 'Candidate' }, { name: 'Anita Joshi', role: 'Parent' }, { name: 'R. Desai', role: 'Human counselor' }] },
    { id: 'l3', title: 'Application help — Diya M.', topic: 'Document upload + fee payment', min: 21, sentiment: 'pos', confidence: 91, participants: [{ name: 'Diya Menon', role: 'Candidate' }] },
    { id: 'l4', title: 'Discovery call — Arjun S.', topic: 'Course discovery', min: 3, sentiment: 'very-pos', confidence: 89, participants: [{ name: 'Arjun Singh', role: 'Candidate' }] },
  ]);

  // scheduled meetings (minutes from midnight) — overlaps demonstrate concurrency
  slots: Slot[] = [
    { id: 's1', start: 9 * 60, dur: 30, title: 'Discovery call', who: 'Arjun S.', type: 'Discovery', participants: 1, status: 'done' },
    { id: 's2', start: 9 * 60 + 15, dur: 40, title: 'Parent counseling', who: 'Kabir J. + parent', type: 'Parent', participants: 3, status: 'done' },
    { id: 's3', start: 10 * 60, dur: 45, title: 'Course & fees', who: 'Ananya R. + parent', type: 'Counseling', participants: 2, status: 'done' },
    { id: 's4', start: 10 * 60 + 30, dur: 30, title: 'Mentor intro', who: 'Yash P. + mentor', type: 'Mentor', participants: 2, status: 'done' },
    { id: 's5', start: 11 * 60, dur: 40, title: 'Course & fees — Ananya R.', who: 'Ananya R. + parent', type: 'Counseling', participants: 2, status: 'live' },
    { id: 's6', start: 11 * 60, dur: 30, title: 'Parent counseling — Kabir J.', who: 'Kabir J. + counselor', type: 'Parent', participants: 3, status: 'live' },
    { id: 's7', start: 11 * 60 + 10, dur: 35, title: 'Application help — Diya M.', who: 'Diya M.', type: 'Application', participants: 1, status: 'live' },
    { id: 's8', start: 11 * 60 + 15, dur: 25, title: 'Discovery call — Arjun S.', who: 'Arjun S.', type: 'Discovery', participants: 1, status: 'live' },
    { id: 's9', start: 12 * 60 + 30, dur: 45, title: 'V-Con with parents', who: 'Saanvi I. + family', type: 'V-Con', participants: 4, status: 'upcoming' },
    { id: 's10', start: 14 * 60, dur: 30, title: 'Scholarship review', who: 'Vihaan G.', type: 'Counseling', participants: 1, status: 'upcoming' },
    { id: 's11', start: 14 * 60 + 15, dur: 40, title: 'Aptitude debrief', who: 'Riya S.', type: 'Career', participants: 1, status: 'upcoming' },
    { id: 's12', start: 15 * 60 + 30, dur: 30, title: 'Follow-up call', who: 'Imran S.', type: 'Follow-up', participants: 1, status: 'upcoming' },
  ];

  // lane assignment so overlapping (concurrent) meetings render side by side
  laidOut = computed<Slot[]>(() => {
    const items = [...this.slots].sort((a, b) => a.start - b.start);
    // cluster overlapping groups
    const out: Slot[] = [];
    let cluster: Slot[] = [];
    let clusterEnd = -1;
    const flush = () => {
      const lanes: number[] = []; // lane -> end
      for (const it of cluster) {
        let lane = lanes.findIndex(end => end <= it.start);
        if (lane === -1) { lane = lanes.length; lanes.push(0); }
        lanes[lane] = it.start + it.dur;
        it.lane = lane;
      }
      const n = lanes.length;
      cluster.forEach(it => (it.lanes = n));
      out.push(...cluster);
      cluster = [];
    };
    for (const it of items) {
      if (cluster.length && it.start >= clusterEnd) flush();
      cluster.push(it);
      clusterEnd = Math.max(clusterEnd, it.start + it.dur);
    }
    if (cluster.length) flush();
    return out;
  });

  done: Done[] = [
    { id: 'd1', title: 'Course & fees — Ananya R.', who: 'Ananya R. + parent', ago: '1h ago', dur: 42, sentiment: 'pos', summary: 'Walked through B.Tech AI fees and the 40% merit scholarship. Parent reassured on placements; V-Con booked.', actions: ['Send scholarship doc', 'Book V-Con'], sent: { candidate: true, parent: true, counselor: false } },
    { id: 'd2', title: 'Parent counseling — Kabir J.', who: 'Kabir J. + parent', ago: '2h ago', dur: 28, sentiment: 'neutral', summary: 'Addressed hostel safety and placement record from approved report. Flagged for human counselor follow-up.', actions: ['Share placement report', 'Human follow-up'], sent: { candidate: true, parent: true, counselor: true } },
    { id: 'd3', title: 'Discovery call — Arjun S.', who: 'Arjun S.', ago: '3h ago', dur: 18, sentiment: 'very-pos', summary: 'Strong interest in CSE; captured interests and budget. Sent course brochure and next steps.', actions: ['Send brochure'], sent: { candidate: true, parent: false, counselor: false } },
  ];

  meetingTypes = computed(() => this.career()
    ? ['Career discovery', 'Aptitude debrief', 'Pathway review', 'Mentor introduction', 'Parent career talk']
    : ['Discovery call', 'Course & fee counseling', 'Parent counseling', 'Application help', 'V-Con (video)']);
  partOptions = computed(() => this.career()
    ? ['Student', 'Parent', 'Mentor', 'Human counselor']
    : ['Candidate', 'Parent', 'Human counselor', 'Faculty']);

  initials(n: string) { return n.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase(); }
  togglePart(p: string) { this.picked.update(l => l.includes(p) ? l.filter(x => x !== p) : [...l, p]); }

  // ── real actions ───────────────────────────────────────────────────────────

  /** Schedule: create ONE public room + shareable link (no names). Optionally
   *  drops the AI agent in from the start. The link is shown for copying. */
  async book(): Promise<void> {
    this.booking.set(true);
    this.shareLink.set('');
    try {
      const res = await this.meeting.createMeeting();
      this.shareLink.set(res.share_url);
      if (this.includeAi()) {
        try { await this.meeting.addAgent(res.room, 'panel'); } catch { /* non-fatal */ }
      }
      this.toast.success('Meeting link created — share it with anyone.');
    } catch (e) {
      this.toast.warning(this.errMsg(e, 'Could not create the meeting.'));
    } finally {
      this.booking.set(false);
    }
  }

  closeSchedule(): void { this.schedule.set(false); this.shareLink.set(''); }

  /** Instant meeting: create ONE room, then open the full-page room in a NEW TAB
   *  (Google-Meet style) where the host auto-joins. The dashboard tab stays put.
   *  Adds the AI first if 'include' is on. */
  async startInstant(): Promise<void> {
    this.starting.set(true);
    try {
      const res = await this.meeting.createMeeting();
      this.copy(res.share_url, false);  // host can paste the invite immediately
      if (this.includeAi()) {
        try { await this.meeting.addAgent(res.room, 'panel'); } catch { /* non-fatal */ }
      }
      this.openRoomTab(res.room, 'Host', 'counsellor');
      this.toast.success('Meeting opened in a new tab — invite link copied.');
    } catch (e) {
      this.toast.warning(this.errMsg(e, 'Could not start the instant meeting.'));
    } finally {
      this.starting.set(false);
    }
  }

  /** Join an existing room → open the full-page room in a NEW TAB (name gate is
   *  there). Accepts a room name OR a pasted /meeting/<room> link. */
  doJoin(): void {
    const raw = this.joinRoom().trim();
    if (!raw) return;
    const room = raw.includes('/meeting/') ? raw.split('/meeting/')[1].split(/[?#]/)[0] : raw;
    this.join.set(false);
    this.openRoomTab(decodeURIComponent(room));
  }

  /** Open the full-page meeting room in a new browser tab. Optional name auto-
   *  joins (host); without it the new tab shows the name gate. */
  private openRoomTab(room: string, name?: string, role?: string): void {
    const tree = this.router.createUrlTree(['/meeting', room], {
      queryParams: name ? { name, role } : undefined,
    });
    const url = this.router.serializeUrl(tree);
    window.open(url, '_blank');
  }

  copy(url: string, notify = true): void {
    void navigator.clipboard?.writeText(url);
    if (notify) this.toast.success('Link copied.');
  }

  private errMsg(e: unknown, fallback: string): string {
    const m = e instanceof Error ? e.message : '';
    if (m === 'SERVICE_UNAVAILABLE') return 'Meeting service is unreachable. Is it running?';
    return m || fallback;
  }
}
