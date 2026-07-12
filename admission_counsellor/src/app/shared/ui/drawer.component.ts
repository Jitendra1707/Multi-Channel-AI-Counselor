import { ChangeDetectionStrategy, Component, EventEmitter, HostListener, Input, Output } from '@angular/core';
import { IconComponent } from './icon.component';

/** Right-side slide-in drawer (focus-trapped feel, ESC-closable) — §35 Drawer. */
@Component({
  selector: 'va-drawer',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (open) {
      <div class="scrim" (click)="close.emit()"></div>
      <aside class="drawer" [style.width.px]="width" role="dialog" aria-modal="true">
        <header class="dh">
          <div class="dh-text"><div class="t-h4">{{ title }}</div>@if (subtitle){<div class="t-cap t-muted">{{ subtitle }}</div>}</div>
          <button class="btn btn-icon btn-ghost" (click)="close.emit()" aria-label="Close"><va-icon name="x" [size]="18"></va-icon></button>
        </header>
        <div class="db scroll-y"><ng-content></ng-content></div>
        <footer class="df"><ng-content select="[footer]"></ng-content></footer>
      </aside>
    }`,
  styles: [`
    .scrim { position: fixed; inset: 0; background: rgba(2,6,23,.45); z-index: 60; animation: va-fade-up .2s ease; backdrop-filter: blur(2px); }
    .drawer { position: fixed; top: 0; right: 0; bottom: 0; max-width: 96vw; background: var(--color-surface);
      border-left: 1px solid var(--color-border); box-shadow: var(--e3); z-index: 61; display: flex; flex-direction: column;
      animation: slide-in .25s cubic-bezier(.4,0,.2,1); }
    @keyframes slide-in { from { transform: translateX(24px); opacity: .6; } to { transform: none; opacity: 1; } }
    .dh { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 18px; border-bottom: 1px solid var(--color-border); }
    .db { flex: 1; padding: 18px; }
    .df { padding: 14px 18px; border-top: 1px solid var(--color-border); display: flex; gap: 8px; }
    .df:empty { display: none; }
  `],
})
export class DrawerComponent {
  @Input() open = false;
  @Input() title = '';
  @Input() subtitle = '';
  @Input() width = 440;
  @Output() close = new EventEmitter<void>();
  @HostListener('document:keydown.escape') onEsc() { if (this.open) this.close.emit(); }
}
