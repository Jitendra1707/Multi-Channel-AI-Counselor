import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { IconComponent } from './icon.component';

/** Empty state: illustration + one-line explanation + primary CTA (§34.5). */
@Component({
  selector: 'va-empty',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty">
      <div class="ill"><va-icon [name]="icon" [size]="28"></va-icon></div>
      <div class="t-h4">{{ title }}</div>
      @if (message) { <p class="t-sm t-muted">{{ message }}</p> }
      @if (cta) { <button class="btn btn-primary" (click)="action.emit()"><va-icon [name]="ctaIcon" [size]="16"></va-icon>{{ cta }}</button> }
    </div>`,
  styles: [`
    .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
      gap: 10px; padding: 48px 24px; }
    .ill { width: 64px; height: 64px; border-radius: 18px; display: grid; place-items: center;
      background: var(--color-surface-alt); color: var(--color-text-muted); margin-bottom: 4px; }
    p { max-width: 380px; margin: 0; }
    .btn { margin-top: 8px; }
  `],
})
export class EmptyStateComponent {
  @Input() icon = 'inbox';
  @Input({ required: true }) title = '';
  @Input() message = '';
  @Input() cta = '';
  @Input() ctaIcon = 'plus';
  @Output() action = new EventEmitter<void>();
}

/** Standard page header: title, subtitle, breadcrumb slot, actions slot. */
@Component({
  selector: 'va-page-header',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="ph">
      <div class="ph-text">
        <div class="t-h2">{{ title }}</div>
        @if (subtitle) { <p class="t-sm t-muted">{{ subtitle }}</p> }
      </div>
      <div class="ph-actions"><ng-content></ng-content></div>
    </header>`,
  styles: [`
    .ph { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
    .ph-text p { margin-top: 4px; max-width: 60ch; }
    .ph-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  `],
})
export class PageHeaderComponent {
  @Input({ required: true }) title = '';
  @Input() subtitle = '';
}

/** Card with header (title + optional action) and projected body. */
@Component({
  selector: 'va-section-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sc" [class.flush]="flush">
      @if (title) {
        <div class="sc-head">
          <div class="sc-title">
            <span class="t-h4">{{ title }}</span>
            @if (hint) { <span class="t-cap t-muted">{{ hint }}</span> }
          </div>
          <div class="sc-action"><ng-content select="[actions]"></ng-content></div>
        </div>
      }
      <div class="sc-body" [class.pad]="!flush"><ng-content></ng-content></div>
    </section>`,
  styles: [`
    .sc { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e1); overflow: hidden; display: flex; flex-direction: column; }
    .sc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 18px; border-bottom: 1px solid var(--color-border); }
    .sc-title { display: flex; flex-direction: column; gap: 1px; }
    .sc-body.pad { padding: 18px; }
    .sc-body { flex: 1; min-height: 0; }
  `],
})
export class SectionCardComponent {
  @Input() title = '';
  @Input() hint = '';
  @Input() flush = false;
}
