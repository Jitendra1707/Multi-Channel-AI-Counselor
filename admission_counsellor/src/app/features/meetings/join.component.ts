import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { MeetingService } from './meeting.service';
import { MeetingRoomComponent } from './meeting-room.component';

/**
 * JoinComponent — the PUBLIC page a shared meeting link opens
 * (/meeting/:room, Google-Meet style). Unauthenticated: anyone with the link
 * lands here, enters their own name, and joins. No app shell.
 *
 * Reads the room from the route + (optional) ?token / ?role from the URL. If a
 * token is present (a personal pre-minted link) it joins directly; otherwise it
 * shows the name gate and mints a token via the meeting service.
 */
@Component({
  selector: 'va-meeting-join',
  standalone: true,
  imports: [IconComponent, MeetingRoomComponent],
  providers: [MeetingService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  @if (!joined()) {
    <!-- Name gate (centered card) -->
    <div class="join-wrap">
      <div class="join-card">
        <div class="brand"><va-icon name="video" [size]="22"></va-icon> <span>Join the meeting</span></div>
        <p class="t-sm t-muted">Room <span class="room-chip">{{ room() }}</span></p>
        <form (submit)="join($event)">
          <label class="label">Your name</label>
          <input class="input" [value]="name()" (input)="name.set($any($event.target).value)" placeholder="e.g. Ramesh" autofocus />
          @if (err()) { <p class="err">{{ err() }}</p> }
          <button class="btn btn-primary grow" type="submit" [disabled]="joining() || !name().trim()">
            <va-icon name="video" [size]="16"></va-icon> {{ joining() ? 'Joining…' : 'Join meeting' }}
          </button>
        </form>
        <p class="t-cap t-muted hint">You’ll join with your camera & mic — allow access when prompted.</p>
      </div>
    </div>
  } @else {
    <!-- In the call → FULL SCREEN room (like the web-app). -->
    <div class="room-full">
      <va-meeting-room (left)="leave()"></va-meeting-room>
    </div>
  }`,
  styles: [`
    :host { display: block; }
    .join-wrap { min-height: 100vh; display: grid; place-items: center; background: var(--color-surface); padding: 20px; }
    .join-card { width: 100%; max-width: 420px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--r-lg, 16px); padding: 28px; box-shadow: var(--e2, 0 8px 30px rgba(0,0,0,.12)); }
    .brand { display: flex; align-items: center; gap: 10px; font-size: var(--text-h4, 18px); font-weight: 700; color: var(--color-text); }
    .brand va-icon { color: var(--color-primary); }
    .room-chip { background: var(--color-surface-alt); padding: 2px 8px; border-radius: var(--r-pill, 999px); font-size: var(--text-cap); }
    .label { display: block; font-size: var(--text-cap); font-weight: 600; margin: 16px 0 6px; color: var(--color-text-muted); }
    form { display: flex; flex-direction: column; gap: 6px; }
    form .btn { margin-top: 14px; justify-content: center; }
    .err { color: var(--color-danger); font-size: var(--text-cap); margin: 4px 0 0; }
    .hint { margin-top: 14px; }
    /* Full-viewport room — fills the page, no card/max-width. */
    .room-full { position: fixed; inset: 0; z-index: 50; background: #0b0b0f; }
    .room-full va-meeting-room { display: block; width: 100%; height: 100%; }
  `],
})
export class JoinComponent {
  private route = inject(ActivatedRoute);
  meeting = inject(MeetingService);

  room = signal<string>(this.route.snapshot.paramMap.get('room') ?? '');
  name = signal(this.route.snapshot.queryParamMap.get('name') ?? '');
  joining = signal(false);
  err = signal<string | null>(null);
  joined = computed(() => this.meeting.status() === 'connecting' || this.meeting.status() === 'connected');

  constructor() {
    // If the link carried a name (e.g. the host from "Instant meeting"), join
    // straight away — no need to retype it on the gate.
    if (this.name().trim() && this.room()) {
      void this.join();
    }
  }

  async join(e?: Event): Promise<void> {
    e?.preventDefault();
    const nm = this.name().trim();
    if (!nm || !this.room()) return;
    this.joining.set(true);
    this.err.set(null);
    try {
      const qp = this.route.snapshot.queryParamMap;
      // Always mint a fresh token by name via the service (it returns the SFU url
      // too). A ?token= on a personal link is honoured but still needs the url,
      // so the name-mint path is the simplest single source for both.
      const t = await this.meeting.getToken(this.room(), nm, qp.get('role') ?? 'guest');
      this.meeting.enterRoom(t.room, t.token, t.url);
    } catch (e2) {
      this.err.set(e2 instanceof Error ? e2.message : 'Could not join the meeting.');
    } finally {
      this.joining.set(false);
    }
  }

  async leave(): Promise<void> {
    await this.meeting.leave();
  }
}
