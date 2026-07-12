import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { KnowledgeCandidate, WebrtcAvatarService } from './webrtc-avatar.service';

/**
 * KnowledgeCardComponent — READ-ONLY in-call knowledge deck.
 *
 * Arm-first flow: the director clicks Capture, speaks the fact, Aisha speaks a
 * status acknowledgment, and the conflict-check RESULT lands here as a
 * display-only, collapsible card (fact, heard-line, conflict evidence, status
 * chip). There are NO action buttons — approval/supersede/reject/edit happen on
 * the Knowledge Review screen (the footer links there). Cards auto-collapse to
 * a slim chip after ~15s and persist for the call; if a candidate is resolved
 * in Knowledge Review while the call is live, the status chip flips in place.
 *
 * The deck also renders the capture-flow chips ("Listening…", "Checking…",
 * transient failure) driven by the service's knowledgeCaptureState.
 */
@Component({
  selector: 'va-knowledge-card',
  standalone: true,
  imports: [IconComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  @if (svc.knowledgeCandidates().length || svc.knowledgeCaptureState() !== 'idle' || svc.knowledgeCaptureError()) {
    <div class="kc-deck">

      @if (svc.knowledgeCaptureState() === 'armed') {
        <div class="kc-chip kc-armed"><span class="kc-dot"></span> Listening — say the fact now</div>
      } @else if (svc.knowledgeCaptureState() === 'processing') {
        <div class="kc-chip"><va-icon name="refresh" [size]="12"></va-icon> Checking knowledge base…</div>
      }
      @if (svc.knowledgeCaptureError(); as err) {
        <div class="kc-chip kc-error"><va-icon name="alert-triangle" [size]="12"></va-icon> {{ err }}</div>
      }

      @for (c of svc.knowledgeCandidates(); track c.id) {
        @if (collapsed().has(c.id)) {
          <button class="kc-chip kc-mini" (click)="toggle(c.id)" [title]="c.text">
            <va-icon name="sparkles" [size]="12"></va-icon>
            <span class="kc-mini-text">{{ snippet(c) }}</span>
            <span class="kc-status" [class]="'kc-status ' + chip(c).cls">{{ chip(c).label }}</span>
          </button>
        } @else {
          <div class="kc-card" [class.blocking]="c.conflict.blocking && c.status === 'pending'">
            <div class="kc-head" (click)="toggle(c.id)" title="Collapse">
              <span class="kc-tag"><va-icon name="sparkles" [size]="12"></va-icon> New knowledge</span>
              <span class="kc-topic">{{ c.topic || 'general' }} · {{ c.suggested_kb }}</span>
              <span class="kc-status" [class]="'kc-status ' + chip(c).cls">{{ chip(c).label }}</span>
              <span class="kc-collapse" aria-label="Collapse">▾</span>
            </div>

            <div class="kc-body">
              @if (c.heading) { <p class="kc-heading">{{ c.heading }}</p> }
              <p class="kc-text">{{ c.text }}</p>
              @if (c.source_span && c.source_span !== c.text) {
                <p class="kc-span">heard: “{{ c.source_span }}”</p>
              }

              @if (c.conflict.items.length && c.conflict.score > 20) {
                <div class="kc-conflict" [class.block]="c.conflict.blocking">
                  <div class="kc-conflict-head">
                    <va-icon [name]="c.conflict.blocking ? 'alert-triangle' : 'info'" [size]="13"></va-icon>
                    {{ c.conflict.blocking ? 'Conflicts with the knowledge base' : 'Possible overlap' }} ({{ c.conflict.score }}%)
                  </div>
                  @for (it of c.conflict.items; track it.point_id) {
                    <div class="kc-conflict-item">
                      <b>{{ it.source_doc }}</b>
                      @if (it.old_value || it.new_value) { · {{ it.old_value }} → {{ it.new_value }} }
                      <span class="kc-rel">{{ it.relation }}</span>
                      @if (it.explanation) { — {{ it.explanation }} }
                      @if (it.span) { <div class="kc-passage">“{{ it.span }}”</div> }
                    </div>
                  }
                </div>
              }
            </div>

            <div class="kc-foot">
              <a routerLink="/app/knowledge-review" class="kc-review-link">
                Take action in Knowledge Review <va-icon name="arrow-right" [size]="12"></va-icon>
              </a>
            </div>
          </div>
        }
      }
    </div>
  }
  `,
  styles: [`
    /* Float over the RIGHT GUTTER of the stage (the avatar is only the centre
       third), vertically centred, so it never covers Aisha. pointer-events:none
       on the deck keeps the empty gutter click-through — only chips/cards are
       interactive. */
    .kc-deck { position: absolute; top: 50%; right: 2.5%; transform: translateY(-50%); z-index: 6;
               width: clamp(260px, 30%, 340px); max-height: 86%;
               display: flex; flex-direction: column; gap: 8px; overflow-y: auto; pointer-events: none; }
    /* Frosted glass, same language as the .controls bar. */
    .kc-chip, .kc-card { pointer-events: auto;
               background: color-mix(in srgb, var(--color-surface, #fff) 74%, transparent);
               backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
               border: 1px solid var(--color-border, #e3e3e8); border-radius: 14px;
               box-shadow: var(--e3, 0 8px 30px rgba(0,0,0,.28));
               color: var(--color-text, #222); font-size: 13px; }
    .kc-chip { display: flex; align-items: center; gap: 6px; padding: 7px 11px; font-weight: 500; }
    .kc-chip.kc-armed { color: var(--accent, #6b4eff); }
    .kc-chip.kc-error { color: #c0392b; }
    .kc-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent, #6b4eff);
              animation: kc-pulse 1.2s ease-in-out infinite; }
    @keyframes kc-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
    .kc-mini { cursor: pointer; text-align: left; font: inherit; width: 100%; }
    .kc-mini-text { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .kc-card { display: flex; flex-direction: column; max-height: 100%; padding: 10px 12px; }
    .kc-card.blocking { border-color: #e0a800; }
    .kc-head { flex-shrink: 0; display: flex; align-items: center; gap: 6px; margin-bottom: 6px; cursor: pointer; }
    .kc-tag { display: inline-flex; align-items: center; gap: 4px; font-weight: 600; color: var(--accent, #6b4eff); }
    .kc-topic { color: var(--color-text-muted, #777); font-size: 11px; }
    .kc-status { margin-left: auto; font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 9px;
                 background: var(--color-surface-alt, #f1f1f4); color: var(--color-text-muted, #666); white-space: nowrap; }
    .kc-status.ok { background: #e8f7ee; color: #1e7d43; }
    .kc-status.warn { background: #fff8e6; color: #9a7b00; }
    .kc-status.block { background: #fdecea; color: #c0392b; }
    .kc-collapse { color: var(--color-text-muted, #888); font-size: 12px; padding: 0 2px; }
    .kc-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
    .kc-heading { font-weight: 600; margin: 2px 0; }
    .kc-text { margin: 2px 0 6px; }
    .kc-span { color: var(--color-text-muted, #888); font-size: 12px; font-style: italic; margin: 2px 0 8px; }
    .kc-conflict { background: #fff8e6; border: 1px solid #f0d98a; border-radius: 8px; padding: 6px 8px; margin-bottom: 8px; font-size: 12px; }
    .kc-conflict.block { background: #fdecea; border-color: #f3a9a0; }
    .kc-conflict-head { display: flex; align-items: center; gap: 5px; font-weight: 600; margin-bottom: 3px; }
    .kc-conflict-item { color: #555; margin-bottom: 4px; }
    .kc-rel { color: #a06; margin-left: 4px; font-style: italic; }
    .kc-passage { color: #666; font-style: italic; margin-top: 2px; }
    .kc-foot { flex-shrink: 0; padding-top: 8px; margin-top: 6px; border-top: 1px solid var(--color-border, #eee); }
    .kc-review-link { display: inline-flex; align-items: center; gap: 4px; font-weight: 600;
                      color: var(--accent, #6b4eff); text-decoration: none; }
    .kc-review-link:hover { text-decoration: underline; }
  `],
})
export class KnowledgeCardComponent {
  readonly svc = inject(WebrtcAvatarService);
  /** Card ids currently collapsed to their slim-chip form. */
  readonly collapsed = signal<ReadonlySet<string>>(new Set<string>());
  private readonly seen = new Set<string>();

  constructor() {
    // Auto-collapse each card ~15s after it first appears so the stage stays
    // clean; the director can re-expand the chip at any time.
    effect(() => {
      for (const c of this.svc.knowledgeCandidates()) {
        if (this.seen.has(c.id)) continue;
        this.seen.add(c.id);
        setTimeout(() => {
          this.collapsed.update(s => new Set(s).add(c.id));
        }, 15_000);
      }
    });
  }

  toggle(id: string): void {
    this.collapsed.update(s => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  snippet(c: KnowledgeCandidate): string {
    const t = c.heading || c.text || '';
    return t.length > 44 ? t.slice(0, 44) + '…' : t;
  }

  chip(c: KnowledgeCandidate): { label: string; cls: string } {
    switch (c.status) {
      case 'approved':
      case 'superseded':
        return { label: 'Approved ✓', cls: 'ok' };
      case 'rejected':
        return { label: 'Rejected', cls: 'block' };
      case 'error':
        return { label: 'Failed', cls: 'block' };
    }
    if (c.conflict.blocking) return { label: `Conflict found (${c.conflict.score}%)`, cls: 'block' };
    if (c.conflict.items.length && c.conflict.score > 20) {
      return { label: `Possible overlap (${c.conflict.score}%)`, cls: 'warn' };
    }
    return { label: 'No conflicts', cls: 'ok' };
  }
}
