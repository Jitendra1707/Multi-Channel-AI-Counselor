import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { IconComponent } from './icon.component';
import { ToastService } from '../../core/toast.service';

@Component({
  selector: 'va-toast-host',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="toasts" aria-live="polite">
      @for (t of toast.toasts(); track t.id) {
        <div class="toast" [attr.data-kind]="t.kind" role="status">
          <va-icon [name]="t.icon || 'check-circle'" [size]="18"></va-icon>
          <span class="t-sm">{{ t.text }}</span>
          <button class="x" (click)="toast.dismiss(t.id)" aria-label="Dismiss"><va-icon name="x" [size]="14"></va-icon></button>
        </div>
      }
    </div>`,
  styles: [`
    .toasts { position: fixed; bottom: 20px; right: 20px; z-index: 90; display: flex; flex-direction: column; gap: 10px; max-width: min(92vw, 420px); }
    .toast { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: var(--r-md);
      background: var(--color-surface); border: 1px solid var(--color-border); box-shadow: var(--e3);
      animation: va-fade-up .25s ease both; }
    .toast .x { margin-left: auto; background: transparent; border: none; color: var(--color-text-muted); padding: 2px; border-radius: 6px; display: inline-flex; }
    .toast .x:hover { background: var(--color-surface-alt); color: var(--color-text); }
    .toast[data-kind='success'] va-icon { color: var(--color-success); }
    .toast[data-kind='info'] va-icon { color: var(--color-accent); }
    .toast[data-kind='warning'] va-icon { color: var(--color-warning); }
    .toast[data-kind='danger'] va-icon { color: var(--color-danger); }
    .toast[data-kind='success'] { border-left: 3px solid var(--color-success); }
    .toast[data-kind='info'] { border-left: 3px solid var(--color-accent); }
    .toast[data-kind='warning'] { border-left: 3px solid var(--color-warning); }
    .toast[data-kind='danger'] { border-left: 3px solid var(--color-danger); }
  `],
})
export class ToastHostComponent {
  toast = inject(ToastService);
}
