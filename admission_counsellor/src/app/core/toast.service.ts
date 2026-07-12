import { Injectable, signal } from '@angular/core';

export interface Toast {
  id: number;
  text: string;
  kind: 'success' | 'info' | 'warning' | 'danger';
  icon?: string;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly toasts = signal<Toast[]>([]);
  private id = 0;

  show(text: string, kind: Toast['kind'] = 'success', icon?: string) {
    const t: Toast = { id: ++this.id, text, kind, icon };
    this.toasts.update(list => [...list, t]);
    setTimeout(() => this.dismiss(t.id), 4200);
  }
  success(text: string, icon = 'check-circle') { this.show(text, 'success', icon); }
  info(text: string, icon = 'info') { this.show(text, 'info', icon); }
  warning(text: string, icon = 'alert-triangle') { this.show(text, 'warning', icon); }
  danger(text: string, icon = 'alert-circle') { this.show(text, 'danger', icon); }

  dismiss(id: number) { this.toasts.update(list => list.filter(t => t.id !== id)); }
}
