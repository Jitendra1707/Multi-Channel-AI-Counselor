import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { JourneyEvent } from '../../domain/models';
import { IconComponent } from './icon.component';
import { SentimentBadgeComponent } from './badges.component';
import { CHANNEL_ICON, relTime, fmtTime } from '../util/format';

@Component({
  selector: 'va-timeline',
  standalone: true,
  imports: [IconComponent, SentimentBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ol class="tl">
      @for (e of events; track e.id) {
        <li class="ev" [attr.data-channel]="e.channel">
          <span class="node"><va-icon [name]="iconFor(e)" [size]="14"></va-icon></span>
          <div class="body">
            <div class="row1">
              <span class="label">{{ e.label }}</span>
              <span class="owner" [attr.data-owner]="e.owner">{{ ownerLabel(e) }}</span>
              <span class="time t-cap t-muted">{{ time(e.ts) }}</span>
            </div>
            <p class="summary t-sm">{{ e.summary }}</p>
            <div class="row2">
              @if (e.sentiment) { <va-sentiment-badge [value]="e.sentiment"></va-sentiment-badge> }
              @if (e.probabilityDelta) {
                <span class="delta" [class.neg]="e.probabilityDelta < 0">
                  <va-icon [name]="e.probabilityDelta < 0 ? 'arrow-down' : 'arrow-up'" [size]="11"></va-icon>{{ absDelta(e) }}% probability
                </span>
              }
              @for (d of e.docsShared || []; track d) { <span class="chip doc"><va-icon name="paperclip" [size]="11"></va-icon>{{ d }}</span> }
              @if (clickable) { <button class="open" (click)="openEvent.emit(e)">Open <va-icon name="chevron-right" [size]="12"></va-icon></button> }
            </div>
          </div>
        </li>
      }
    </ol>`,
  styles: [`
    .tl { list-style: none; margin: 0; padding: 0; position: relative; }
    .tl::before { content: ''; position: absolute; left: 15px; top: 8px; bottom: 8px; width: 2px; background: var(--color-border); }
    .ev { position: relative; display: grid; grid-template-columns: 32px 1fr; gap: 12px; padding: 6px 0 18px; }
    .node { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; z-index: 1;
      background: var(--color-surface); border: 2px solid var(--color-border); color: var(--color-text-muted); }
    .ev[data-channel='voice'] .node { color: var(--ch-voice); border-color: color-mix(in srgb, var(--ch-voice) 40%, var(--color-border)); }
    .ev[data-channel='whatsapp'] .node { color: var(--ch-whatsapp); border-color: color-mix(in srgb, var(--ch-whatsapp) 40%, var(--color-border)); }
    .ev[data-channel='email'] .node { color: var(--ch-email); border-color: color-mix(in srgb, var(--ch-email) 40%, var(--color-border)); }
    .ev[data-channel='vcon'] .node { color: var(--ch-vcon); border-color: color-mix(in srgb, var(--ch-vcon) 40%, var(--color-border)); }
    .body { min-width: 0; }
    .row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .label { font-weight: 600; font-size: var(--text-sm); }
    .owner { font-size: 10px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; padding: 2px 6px; border-radius: 999px; }
    .owner[data-owner='ai'] { background: rgba(var(--color-accent-2-rgb), .14); color: var(--color-accent-2); }
    .owner[data-owner='human'] { background: rgba(var(--color-primary-rgb), .12); color: var(--color-primary); }
    .owner[data-owner='system'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .time { margin-left: auto; }
    .summary { margin: 4px 0 0; color: var(--color-text); }
    .row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .delta { display: inline-flex; align-items: center; gap: 2px; font-size: var(--text-cap); font-weight: 700; color: var(--color-success); }
    .delta.neg { color: var(--color-danger); }
    .chip.doc { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .open { margin-left: auto; display: inline-flex; align-items: center; gap: 2px; background: transparent; border: none;
      color: var(--color-primary); font-size: var(--text-cap); font-weight: 600; }
  `],
})
export class TimelineComponent {
  @Input({ required: true }) events: JourneyEvent[] = [];
  @Input() clickable = false;
  @Output() openEvent = new EventEmitter<JourneyEvent>();
  iconFor(e: JourneyEvent) { return e.channel === 'system' ? 'flag' : (CHANNEL_ICON as any)[e.channel] ?? 'dot'; }
  ownerLabel(e: JourneyEvent) { return e.owner === 'ai' ? 'AI' : e.owner === 'human' ? 'Human' : 'System'; }
  time(ts: string) { return relTime(ts) + ' · ' + fmtTime(ts); }
  absDelta(e: JourneyEvent) { return Math.abs(e.probabilityDelta ?? 0); }
}
