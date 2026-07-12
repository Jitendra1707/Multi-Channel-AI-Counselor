import { ChangeDetectionStrategy, Component, HostListener, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { NAV } from '../nav';
import { DataStore } from '../../data-access/data.store';
import { ToastService } from '../../core/toast.service';

interface Cmd { kind: 'nav' | 'candidate' | 'action'; label: string; sub?: string; icon: string; run: () => void; }

@Component({
  selector: 'va-command-palette',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (open()) {
      <div class="scrim" (click)="close()">
        <div class="palette" (click)="$event.stopPropagation()" role="dialog" aria-modal="true" aria-label="Command palette">
          <div class="p-search">
            <va-icon name="search" [size]="18"></va-icon>
            <input #box class="p-input" placeholder="Search screens, candidates, or run an action…" [value]="q()"
                   (input)="q.set($any($event.target).value); active.set(0)" (keydown)="onKey($event)" autofocus />
            <kbd>esc</kbd>
          </div>
          <div class="p-results scroll-y">
            @for (c of results(); track $index; let i = $index) {
              <button class="p-item" [class.active]="i === active()" (mouseenter)="active.set(i)" (click)="exec(c)">
                <span class="p-ic" [attr.data-kind]="c.kind"><va-icon [name]="c.icon" [size]="16"></va-icon></span>
                <span class="p-text"><span class="p-label">{{ c.label }}</span>@if(c.sub){<span class="p-sub t-cap t-muted">{{ c.sub }}</span>}</span>
                <span class="p-kind t-cap">{{ kindLabel(c.kind) }}</span>
              </button>
            }
            @if (!results().length) { <div class="p-empty t-sm t-muted">No matches for “{{ q() }}”.</div> }
          </div>
          <div class="p-foot t-cap t-muted">
            <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span>Search powered by Admission Counsellor</span>
          </div>
        </div>
      </div>
    }`,
  styles: [`
    .scrim { position: fixed; inset: 0; background: rgba(2,6,23,.5); backdrop-filter: blur(3px); z-index: 80;
      display: flex; align-items: flex-start; justify-content: center; padding-top: 12vh; animation: va-fade-up .15s ease; }
    .palette { width: min(640px, 92vw); background: var(--color-surface); border: 1px solid var(--color-border);
      border-radius: var(--r-lg); box-shadow: var(--e3); overflow: hidden; display: flex; flex-direction: column; max-height: 70vh; }
    .p-search { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--color-border); color: var(--color-text-muted); }
    .p-input { flex: 1; border: none; background: transparent; font: inherit; font-size: var(--text-h4); color: var(--color-text); outline: none; }
    .p-results { padding: 8px; overflow-y: auto; }
    .p-item { width: 100%; display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: none; background: transparent;
      border-radius: var(--r-md); text-align: left; }
    .p-item.active { background: var(--color-surface-alt); }
    .p-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .p-ic[data-kind='action'] { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .p-ic[data-kind='candidate'] { background: rgba(var(--color-primary-rgb), .1); color: var(--color-primary); }
    .p-text { display: flex; flex-direction: column; min-width: 0; }
    .p-label { font-size: var(--text-sm); font-weight: 600; }
    .p-kind { margin-left: auto; text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-muted); }
    .p-empty { padding: 24px; text-align: center; }
    .p-foot { display: flex; gap: 16px; padding: 10px 16px; border-top: 1px solid var(--color-border); }
    .p-foot kbd { margin-right: 3px; }
  `],
})
export class CommandPaletteComponent {
  private router = inject(Router);
  private store = inject(DataStore);
  private toast = inject(ToastService);

  open = signal(false);
  q = signal('');
  active = signal(0);

  private actions: Cmd[] = [
    { kind: 'action', label: 'Upload CRM Excel', sub: 'Import & de-duplicate leads', icon: 'upload', run: () => this.go('/app/crm/import') },
    { kind: 'action', label: 'Add candidate', icon: 'plus', run: () => this.go('/app/crm') },
    { kind: 'action', label: 'Upload KMS document', sub: 'Add approved knowledge', icon: 'book-open', run: () => this.go('/app/kms/upload') },
    { kind: 'action', label: 'Schedule a V-Con', icon: 'video', run: () => this.go('/app/vcons') },
    { kind: 'action', label: 'Review AI knowledge gaps', icon: 'brain', run: () => this.go('/app/learning-review') },
    { kind: 'action', label: 'Export report', icon: 'download', run: () => this.toast.success('Report export queued — you’ll be notified when ready.') },
  ];

  private base = computed<Cmd[]>(() => {
    const nav: Cmd[] = NAV.map(n => ({ kind: 'nav', label: n.label, sub: n.group, icon: n.icon, run: () => this.go(n.route) }));
    const cands: Cmd[] = this.store.candidates().slice(0, 30).map(c => ({
      kind: 'candidate', label: c.name, sub: `${c.preferredCourse} · ${c.city}`, icon: 'user',
      run: () => this.go('/app/crm/candidate/' + c.candidateId),
    }));
    return [...this.actions, ...nav, ...cands];
  });

  results = computed<Cmd[]>(() => {
    const term = this.q().trim().toLowerCase();
    const all = this.base();
    if (!term) return all.filter(c => c.kind !== 'candidate').slice(0, 8);
    return all.filter(c => (c.label + ' ' + (c.sub ?? '')).toLowerCase().includes(term)).slice(0, 12);
  });

  @HostListener('document:keydown', ['$event'])
  onGlobalKey(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); this.toggle(); }
  }
  toggle() { this.open.update(v => !v); this.q.set(''); this.active.set(0); }
  show() { this.open.set(true); this.q.set(''); this.active.set(0); }
  close() { this.open.set(false); }

  onKey(e: KeyboardEvent) {
    const r = this.results();
    if (e.key === 'ArrowDown') { e.preventDefault(); this.active.update(i => Math.min(i + 1, r.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.active.update(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); const c = r[this.active()]; if (c) this.exec(c); }
    else if (e.key === 'Escape') { this.close(); }
  }
  exec(c: Cmd) { c.run(); this.close(); }
  kindLabel(k: Cmd['kind']) { return k === 'nav' ? 'Screen' : k === 'candidate' ? 'Candidate' : 'Action'; }
  private go(route: string) { this.router.navigateByUrl(route); }
}
