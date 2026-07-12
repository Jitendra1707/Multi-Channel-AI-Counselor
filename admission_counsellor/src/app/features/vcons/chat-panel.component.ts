import {
  ChangeDetectionStrategy, Component, ElementRef, computed, effect, input, output, signal, viewChild,
} from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { TranscriptEntry } from './webrtc-avatar.service';

/**
 * ChatPanelComponent — the in-call chat window (Teams / Google Meet "chat
 * during the meeting" style). Replaces the old read-only transcript panel: it
 * shows the SAME conversation thread (spoken turns still appear, badged
 * "voice") AND lets the director chat by typing and by sharing documents —
 * showcasing that the avatar handles audio + text + file uploads together.
 *
 * Pure presentational: it renders `messages` and emits `send` / `attach`; the
 * parent (VconsComponent) wires those to WebrtcAvatarService.
 */
@Component({
  selector: 'va-chat-panel',
  standalone: true,
  imports: [IconComponent, AvatarComponent, AiAvatarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cp" [class.drag]="dragging()"
         (dragover)="onDragOver($event)" (dragleave)="onDragLeave($event)" (drop)="onDrop($event)">
      <div class="cp-head">
        <va-icon name="message-square" [size]="16"></va-icon>
        <span class="t-sm" style="font-weight:600">Chat</span>
        <span class="cp-count t-cap t-muted">
          {{ chatMessages().length }} {{ chatMessages().length === 1 ? 'message' : 'messages' }}
        </span>
      </div>

      <div class="cp-body scroll-y" #body>
        @if (chatMessages().length === 0) {
          <div class="cp-empty">
            <va-icon name="message-square" [size]="28"></va-icon>
            <p class="t-cap t-muted">Chat with Aisha here — type a message or share a document.</p>
          </div>
        } @else {
          @for (e of chatMessages(); track e.id) {
            <div class="bubble" [class.user]="e.role === 'user'">
              <div class="ava">
                @if (e.role === 'user') {
                  <va-avatar name="You" [hue]="222" [size]="26"></va-avatar>
                } @else {
                  <va-ai-avatar [size]="26"></va-ai-avatar>
                }
              </div>
              <div class="msg">
                <span class="who t-cap t-muted">
                  {{ e.role === 'user' ? 'You · Director' : 'Aisha · AI' }}
                </span>

                @if (e.attachment; as att) {
                  <a class="file-card" [href]="att.url" [download]="att.name" target="_blank" rel="noopener">
                    <span class="file-ico"><va-icon name="file-text" [size]="18"></va-icon></span>
                    <span class="file-meta">
                      <span class="file-name t-sm">{{ att.name }}</span>
                      <span class="file-sub t-cap t-muted">{{ prettyType(att.mime) }} · {{ prettySize(att.size) }}</span>
                    </span>
                    <va-icon class="file-dl" name="download" [size]="15"></va-icon>
                  </a>
                  @if (e.text) { <div class="text t-sm">{{ e.text }}</div> }
                } @else {
                  <div class="text t-sm">{{ e.text }}</div>
                }
              </div>
            </div>
          }
        }

        @if (dragging()) {
          <div class="drop-hint">
            <va-icon name="upload" [size]="26"></va-icon>
            <p class="t-sm">Drop to share with Aisha</p>
          </div>
        }
      </div>

      <!-- Composer -->
      <div class="cp-composer">
        <button class="comp-btn" type="button" (click)="picker.click()" [disabled]="disabled()"
                aria-label="Attach a document" title="Attach a document">
          <va-icon name="paperclip" [size]="18"></va-icon>
        </button>
        <input #picker type="file" class="hidden-input" multiple
               accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.png,.jpg,.jpeg"
               (change)="onPick($event)" />
        <textarea #ta class="comp-input" rows="1"
                  [placeholder]="disabled() ? 'Chat unavailable' : 'Message Aisha…'"
                  [disabled]="disabled()"
                  (input)="autoGrow(ta)" (keydown)="onKeydown($event, ta)"></textarea>
        <button class="comp-send" type="button" (click)="submit(ta)" [disabled]="disabled()"
                aria-label="Send message" title="Send">
          <va-icon name="send" [size]="17"></va-icon>
        </button>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .cp { display: flex; flex-direction: column; height: 100%; position: relative;
      background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e1); overflow: hidden; }
    .cp.drag { outline: 2px dashed var(--color-accent); outline-offset: -6px; }

    .cp-head { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--color-border); }
    .cp-head va-icon { color: var(--color-primary); flex: none; }
    .cp-count { margin-left: auto; }

    .cp-body { flex: 1; min-height: 0; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
    .cp-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 10px; }
    .cp-empty va-icon { color: var(--color-border-strong); }
    .cp-empty p { max-width: 32ch; margin: 0; }

    .bubble { display: flex; gap: 10px; }
    .bubble.user { flex-direction: row-reverse; }
    .ava { flex: none; margin-top: 2px; }
    .msg { display: flex; flex-direction: column; gap: 4px; max-width: 82%; }
    .bubble.user .msg { align-items: flex-end; }
    .who { padding: 0 2px; text-transform: uppercase; letter-spacing: .04em; display: inline-flex; align-items: center; gap: 6px; }

    .text { padding: 9px 12px; border-radius: var(--r-md); line-height: 1.45;
      background: var(--color-surface-alt); border: 1px solid var(--color-border); border-top-left-radius: 4px; white-space: pre-wrap; word-break: break-word; }
    .bubble.user .text { background: var(--color-primary); color: #fff; border: 1px solid transparent;
      border-top-left-radius: var(--r-md); border-top-right-radius: 4px; }

    /* Shared document card */
    .file-card { display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: var(--r-md);
      background: var(--color-surface-alt); border: 1px solid var(--color-border); text-decoration: none; color: var(--color-text); min-width: 200px; max-width: 280px; }
    .file-card:hover { border-color: var(--color-border-strong); }
    .bubble.user .file-card { background: color-mix(in srgb, var(--color-primary) 12%, var(--color-surface)); }
    .file-ico { flex: none; width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-sm);
      background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .file-meta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .file-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file-dl { color: var(--color-text-muted); flex: none; margin-left: auto; }

    .drop-hint { position: absolute; inset: 46px 8px 70px; border-radius: var(--r-md);
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
      background: color-mix(in srgb, var(--color-surface) 82%, transparent); backdrop-filter: blur(2px); pointer-events: none; }
    .drop-hint va-icon { color: var(--color-accent); }
    .drop-hint p { margin: 0; font-weight: 600; }

    /* Composer */
    .cp-composer { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--color-border); background: var(--color-surface); }
    .hidden-input { display: none; }
    .comp-input { flex: 1; resize: none; max-height: 120px; min-height: 38px; padding: 9px 12px; line-height: 1.4;
      border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface-alt); color: var(--color-text);
      font: inherit; font-size: var(--text-sm); }
    .comp-input:focus { outline: none; border-color: var(--color-accent); background: var(--color-surface); }
    .comp-input:disabled { opacity: .6; }
    .comp-btn, .comp-send { flex: none; width: 38px; height: 38px; display: grid; place-items: center; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); }
    .comp-btn:hover:not(:disabled) { background: var(--color-surface-alt); }
    .comp-send { background: var(--color-primary); color: #fff; border-color: transparent; }
    .comp-send:hover:not(:disabled) { filter: brightness(1.05); }
    .comp-btn:disabled, .comp-send:disabled { opacity: .5; cursor: not-allowed; }
  `],
})
export class ChatPanelComponent {
  /** The conversation thread (spoken + typed + shared docs). */
  readonly messages = input.required<TranscriptEntry[]>();
  /** Disable the composer until the call is connected. */
  readonly disabled = input<boolean>(false);

  /** Chat shows ONLY typed/uploaded turns — the spoken voice transcript is
   *  excluded so the chat window stays a pure text/document channel. */
  readonly chatMessages = computed(() => this.messages().filter(e => e.via !== 'voice'));

  /** Emitted when the user sends a typed message. */
  readonly send = output<string>();
  /** Emitted once per file the user attaches/drops. */
  readonly attach = output<File>();

  private body = viewChild<ElementRef<HTMLDivElement>>('body');
  readonly dragging = signal(false);

  constructor() {
    // Auto-scroll to newest whenever the thread grows.
    effect(() => {
      this.chatMessages(); // track
      const el = this.body()?.nativeElement;
      if (el) queueMicrotask(() => { el.scrollTop = el.scrollHeight; });
    });
  }

  submit(ta: HTMLTextAreaElement): void {
    const text = ta.value.trim();
    if (!text || this.disabled()) return;
    this.send.emit(text);
    ta.value = '';
    this.autoGrow(ta);
  }

  onKeydown(ev: KeyboardEvent, ta: HTMLTextAreaElement): void {
    // Enter sends; Shift+Enter inserts a newline (Teams/Meet convention).
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      this.submit(ta);
    }
  }

  autoGrow(ta: HTMLTextAreaElement): void {
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
  }

  onPick(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = input.files;
    if (files) { Array.from(files).forEach(f => this.attach.emit(f)); }
    input.value = ''; // allow re-selecting the same file
  }

  onDragOver(ev: DragEvent): void {
    if (this.disabled()) return;
    ev.preventDefault();
    this.dragging.set(true);
  }
  onDragLeave(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
  }
  onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
    if (this.disabled()) return;
    const files = ev.dataTransfer?.files;
    if (files) { Array.from(files).forEach(f => this.attach.emit(f)); }
  }

  prettySize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  prettyType(mime: string): string {
    const m = mime.toLowerCase();
    if (m.includes('pdf')) return 'PDF';
    if (m.includes('word') || m.includes('msword') || m.includes('officedocument.wordprocessing')) return 'Word';
    if (m.includes('presentation') || m.includes('powerpoint')) return 'Slides';
    if (m.includes('sheet') || m.includes('excel') || m.includes('csv')) return 'Sheet';
    if (m.startsWith('image/')) return 'Image';
    if (m.startsWith('text/')) return 'Text';
    return 'File';
  }
}
