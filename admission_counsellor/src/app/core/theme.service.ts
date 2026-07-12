import { Injectable, signal, effect } from '@angular/core';

export type Theme = 'light' | 'dark';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly theme = signal<Theme>(this.initial());

  constructor() {
    effect(() => {
      const t = this.theme();
      document.documentElement.setAttribute('data-theme', t);
      try { localStorage.setItem('ac-theme', t); } catch { /* ignore */ }
    });
  }

  toggle() { this.theme.update(t => (t === 'light' ? 'dark' : 'light')); }

  private initial(): Theme {
    try {
      const saved = localStorage.getItem('ac-theme') as Theme | null;
      if (saved === 'light' || saved === 'dark') return saved;
    } catch { /* ignore */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
}
