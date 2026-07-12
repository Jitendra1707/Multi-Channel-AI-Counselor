import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { LogoComponent } from '../../shared/ui/logo.component';
import { navFor, groupsFor } from '../nav';
import { DataStore } from '../../data-access/data.store';
import { CounselorService } from '../counselor.service';
import { AuthService } from '../auth.service';

@Component({
  selector: 'va-sidebar',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, IconComponent, LogoComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="sb" [class.collapsed]="collapsed">
      <div class="sb-top">
        <a routerLink="/app/overview" class="sb-logo" aria-label="Admission Counsellor home">
          <va-logo [markOnly]="collapsed" [size]="20" [markSize]="30"></va-logo>
        </a>
        <button class="collapse btn btn-icon" (click)="toggle.emit()" [attr.aria-label]="collapsed ? 'Expand' : 'Collapse'">
          <va-icon name="panel-left" [size]="18"></va-icon>
        </button>
      </div>

      <nav class="sb-nav scroll-y">
        @for (g of groups(); track g) {
          <div class="group" [class.admin-group]="g === 'Admin'">
            @if (!collapsed) { <div class="group-label">{{ g === 'Admin' ? 'Admin · tenant' : g }}</div> }
            @for (item of itemsIn(g); track item.route) {
              <a class="nav-item" [routerLink]="item.route" routerLinkActive="active"
                 [title]="collapsed ? item.label : ''" (click)="navigate.emit()">
                <span class="accent"></span>
                <va-icon [name]="item.icon" [size]="19"></va-icon>
                @if (!collapsed) { <span class="ni-label">{{ item.label }}</span> }
                @if (badgeCount(item.badge); as n) {
                  @if (n > 0) { <span class="ni-badge" [class.dot-only]="collapsed">{{ n }}</span> }
                }
              </a>
            }
          </div>
        }
      </nav>

      <div class="sb-foot">
        <a class="nav-item help" routerLink="/app/settings" [title]="collapsed ? 'Help & docs' : ''">
          <span class="accent"></span>
          <va-icon name="help-circle" [size]="19"></va-icon>
          @if (!collapsed) { <span class="ni-label">Help & docs</span> }
        </a>
      </div>
    </aside>`,
  styles: [`
    .sb { width: var(--sidebar-w); height: 100%; background: var(--color-surface); border-right: 1px solid var(--color-border);
      display: flex; flex-direction: column; transition: width .2s cubic-bezier(.4,0,.2,1); flex: none; }
    .sb.collapsed { width: var(--sidebar-w-collapsed); }
    .sb-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; height: var(--topbar-h); padding: 0 12px 0 16px; border-bottom: 1px solid var(--color-border); }
    .sb.collapsed .sb-top { flex-direction: column; height: auto; padding: 12px 8px; gap: 10px; justify-content: center; }
    .collapse { color: var(--color-text-muted); }
    .collapse:hover { background: var(--color-surface-alt); }
    .sb-nav { flex: 1; padding: 10px 10px 20px; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column; }
    .group { margin-bottom: 14px; }
    .admin-group { margin-top: auto; border-top: 1px solid var(--color-border); padding-top: 12px; }
    .admin-group .group-label { color: var(--color-primary); }
    .group-label { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); padding: 6px 10px; }
    .nav-item { position: relative; display: flex; align-items: center; gap: 11px; padding: 9px 12px; border-radius: var(--r-md);
      color: var(--color-text-muted); font-size: var(--text-sm); font-weight: 500; transition: background .12s, color .12s; }
    .sb.collapsed .nav-item { justify-content: center; padding: 10px; }
    .nav-item:hover { background: var(--color-surface-alt); color: var(--color-text); }
    .nav-item.active { background: rgba(var(--color-primary-rgb), .09); color: var(--color-primary); font-weight: 600; }
    .accent { position: absolute; left: -10px; top: 50%; transform: translateY(-50%); width: 3px; height: 20px; border-radius: 0 3px 3px 0; background: var(--color-primary); opacity: 0; transition: opacity .15s; }
    .nav-item.active .accent { opacity: 1; }
    .ni-label { flex: 1; white-space: nowrap; overflow: hidden; }
    .ni-badge { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px; background: var(--color-danger); color: #fff;
      font-size: 10px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
    .ni-badge.dot-only { position: absolute; top: 4px; right: 6px; min-width: 8px; height: 8px; padding: 0; font-size: 0; }
    .sb-foot { padding: 10px; border-top: 1px solid var(--color-border); }
  `],
})
export class SidebarComponent {
  @Input() collapsed = false;
  @Output() toggle = new EventEmitter<void>();
  @Output() navigate = new EventEmitter<void>();
  private store = inject(DataStore);
  private counselor = inject(CounselorService);
  private auth = inject(AuthService);
  isTenantAdmin = computed(() => ['institution-admin', 'super-admin'].includes(this.auth.user().role));
  groups = computed(() => groupsFor(this.counselor.active()).filter(g => g !== 'Admin' || this.isTenantAdmin()));
  navItems = computed(() => navFor(this.counselor.active()));
  itemsIn(g: string) { return this.navItems().filter(n => n.group === g && (!n.adminOnly || this.isTenantAdmin())); }
  badgeCount(badge?: string): number {
    if (badge === 'escalations') return this.store.escalations().filter(e => e.status === 'Open' || e.status === 'Claimed').length;
    if (badge === 'approvals') return this.store.approvals().length;
    if (badge === 'gaps') return 7;
    return 0;
  }
}
