import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { IconComponent } from './icon.component';

/** Deep-linkable filter/segment bar with search + projected filter controls + saved views. */
@Component({
  selector: 'va-filter-bar',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="fb">
      <div class="search">
        <va-icon name="search" [size]="16"></va-icon>
        <input class="search-input" type="search" [placeholder]="placeholder" [value]="query"
               (input)="queryChange.emit($any($event.target).value)" />
      </div>
      <div class="filters"><ng-content select="[filters]"></ng-content></div>
      <div class="spacer"></div>
      @if (savedViews.length) {
        <div class="views">
          @for (v of savedViews; track v) {
            <button class="chip view" [class.active]="v === activeView" (click)="selectView.emit(v)">{{ v }}</button>
          }
        </div>
      }
      <div class="fb-actions"><ng-content select="[actions]"></ng-content></div>
    </div>`,
  styles: [`
    .fb { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 14px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e1); position: sticky; top: 0; z-index: 5; }
    .search { display: flex; align-items: center; gap: 8px; background: var(--color-surface-alt); border: 1px solid transparent;
      border-radius: var(--r-md); padding: 0 10px; min-width: 220px; color: var(--color-text-muted); transition: border-color .15s, box-shadow .15s; }
    .search:focus-within { border-color: var(--color-accent); box-shadow: var(--ring); background: var(--color-surface); }
    .search-input { border: none; background: transparent; padding: 9px 0; font: inherit; font-size: var(--text-sm); color: var(--color-text); width: 200px; outline: none; }
    .filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .spacer { flex: 1; }
    .views { display: flex; gap: 6px; }
    .view { cursor: pointer; }
    .view.active { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); border-color: rgba(var(--color-primary-rgb), .25); }
    .fb-actions { display: flex; align-items: center; gap: 8px; }
  `],
})
export class FilterBarComponent {
  @Input() query = '';
  @Input() placeholder = 'Search…';
  @Input() savedViews: string[] = [];
  @Input() activeView = '';
  @Output() queryChange = new EventEmitter<string>();
  @Output() selectView = new EventEmitter<string>();
}
