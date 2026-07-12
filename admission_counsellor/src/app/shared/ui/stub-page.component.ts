import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { IconComponent } from './icon.component';
import { PageHeaderComponent } from './layout.component';

/** Tasteful placeholder for screens specified in the blueprint but beyond this prototype's depth. */
@Component({
  selector: 'va-stub-page',
  standalone: true,
  imports: [IconComponent, PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <va-page-header [title]="data.title" [subtitle]="data.subtitle"></va-page-header>
      <div class="stub card">
        <div class="badge" [class.phase2]="data.phase2">
          <va-icon [name]="data.phase2 ? 'sparkles' : 'wand'" [size]="14"></va-icon>
          {{ data.phase2 ? 'Phase 2 capability' : 'Specified — prototype stub' }}
        </div>
        <div class="ill"><va-icon [name]="data.icon" [size]="34"></va-icon></div>
        <h2 class="t-h2">{{ data.title }}</h2>
        <p class="t-muted">{{ data.blurb }}</p>
        @if (data.features?.length) {
          <ul class="feat">
            @for (f of data.features; track f) { <li><va-icon name="check" [size]="15"></va-icon>{{ f }}</li> }
          </ul>
        }
        <p class="ref t-cap t-muted">Blueprint reference: {{ data.ref }}</p>
      </div>
    </div>`,
  styles: [`
    .stub { max-width: 720px; margin: 0 auto; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 48px 32px; }
    .badge { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700; padding: 5px 12px;
      border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); }
    .badge.phase2 { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .ill { width: 76px; height: 76px; border-radius: 22px; display: grid; place-items: center; background: var(--gradient-ai); color: #06121A; margin: 8px 0; }
    .stub p { max-width: 52ch; }
    .feat { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; text-align: left; max-width: 460px; width: 100%; }
    .feat li { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); }
    .feat li va-icon { color: var(--color-success); flex: none; }
    .ref { margin-top: 8px; }
  `],
})
export class StubPageComponent {
  private route = inject(ActivatedRoute);
  data = (this.route.snapshot.data['stub'] ?? { title: 'Screen', subtitle: '', blurb: '', icon: 'wand', ref: '', features: [] }) as {
    title: string; subtitle: string; blurb: string; icon: string; ref: string; phase2?: boolean; features?: string[];
  };
}
