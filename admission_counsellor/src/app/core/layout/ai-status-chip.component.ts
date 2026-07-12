import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { ChannelStatus } from '../../domain/models';
import { CHANNEL_ICON, CHANNEL_LABEL } from '../../shared/util/format';
import { CounselorService } from '../counselor.service';
import { AuthService } from '../auth.service';

/** Persistent AI counselor status chip with per-channel popover (§11.2). */
@Component({
  selector: 'va-ai-status-chip',
  standalone: true,
  imports: [IconComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <button class="chip-btn" [attr.data-status]="overall()" (click)="open.set(!open())" [class.open]="open()">
        <span class="dot" [class]="overall()"></span>
        <span class="lbl">Counselor {{ overallLabel() }}</span>
        <va-icon name="chevron-down" [size]="14"></va-icon>
      </button>
      @if (open()) {
        <div class="pop" (click)="$event.stopPropagation()">
          <div class="pop-head">
            <span class="t-h4">{{ counselor.activeMeta().name }} status</span>
            <span class="t-cap t-muted">{{ counselor.activeMeta().title }} · {{ auth.institution().name }}</span>
          </div>
          <div class="chans">
            @for (c of channels(); track c.channel) {
              <div class="chan">
                <va-icon [name]="icon(c.channel)" [size]="16"></va-icon>
                <span class="cn">{{ label(c.channel) }}</span>
                <span class="cs" [attr.data-s]="c.status">
                  <span class="dot" [class]="c.status"></span>{{ statusText(c.status) }}
                </span>
              </div>
              @if (c.reason) { <div class="reason t-cap t-muted">{{ c.reason }}</div> }
            }
          </div>
          <a class="btn btn-ghost btn-block" routerLink="/app/ai-counselor" (click)="open.set(false)">
            <va-icon name="bot" [size]="16"></va-icon> Open Counselor Workbench
          </a>
        </div>
      }
    </div>`,
  styles: [`
    .wrap { position: relative; }
    .chip-btn { display: inline-flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: var(--r-pill);
      background: var(--color-surface-alt); border: 1px solid var(--color-border); font-size: var(--text-sm); font-weight: 600; color: var(--color-text); }
    .chip-btn:hover, .chip-btn.open { background: var(--color-surface); border-color: var(--color-border-strong); }
    .lbl { white-space: nowrap; }
    .pop { position: absolute; top: calc(100% + 8px); right: 0; width: 300px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e3); padding: 14px; z-index: 50;
      display: flex; flex-direction: column; gap: 12px; animation: va-fade-up .18s ease both; }
    .pop-head { display: flex; flex-direction: column; gap: 2px; }
    .chans { display: flex; flex-direction: column; gap: 8px; }
    .chan { display: flex; align-items: center; gap: 10px; }
    .cn { font-size: var(--text-sm); font-weight: 500; }
    .cs { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 600; }
    .cs[data-s='live'] { color: var(--color-success); }
    .cs[data-s='limited'] { color: var(--color-warning); }
    .cs[data-s='paused'] { color: var(--color-text-muted); }
    .cs[data-s='blocked'] { color: var(--color-danger); }
    .reason { margin: -4px 0 2px 26px; }
    @media (max-width: 900px) { .lbl { display: none; } }
  `],
})
export class AiStatusChipComponent {
  counselor = inject(CounselorService);
  auth = inject(AuthService);
  open = signal(false);
  channels = signal<ChannelStatus[]>([
    { channel: 'voice', status: 'live' },
    { channel: 'whatsapp', status: 'live' },
    { channel: 'email', status: 'limited', reason: 'Fee & scholarship answers pending approval' },
    { channel: 'vcon', status: 'paused', reason: 'Avatar meetings launch in Phase 2' },
  ]);
  overall = () => {
    const s = this.channels().map(c => c.status);
    if (s.includes('blocked')) return 'blocked';
    if (s.includes('limited')) return 'limited';
    if (s.every(x => x === 'paused')) return 'paused';
    return 'live';
  };
  overallLabel = () => ({ live: 'Live', limited: 'Limited', paused: 'Paused', blocked: 'Blocked' }[this.overall()]);
  statusText = (s: string) => ({ live: 'Live', limited: 'Limited', paused: 'Paused', blocked: 'Blocked' } as any)[s];
  icon = (c: any) => (CHANNEL_ICON as any)[c];
  label = (c: any) => (CHANNEL_LABEL as any)[c];
}
