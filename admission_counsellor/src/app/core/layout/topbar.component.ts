import { ChangeDetectionStrategy, Component, EventEmitter, Output, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AiStatusChipComponent } from './ai-status-chip.component';
import { ThemeService } from '../theme.service';
import { AuthService } from '../auth.service';
import { DataStore } from '../../data-access/data.store';
import { ToastService } from '../toast.service';
import { CounselorService, CounselorType } from '../counselor.service';
import { relTime } from '../../shared/util/format';

type Menu = 'none' | 'cycle' | 'quick' | 'notif' | 'user';

@Component({
  selector: 'va-topbar',
  standalone: true,
  imports: [IconComponent, AvatarComponent, AiAvatarComponent, AiStatusChipComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="tb" (click)="menu.set('none')">
      <button class="btn btn-icon mobile-menu" (click)="toggleSidebar.emit(); $event.stopPropagation()" aria-label="Menu">
        <va-icon name="menu" [size]="20"></va-icon>
      </button>

      <button class="search" (click)="openPalette.emit(); $event.stopPropagation()">
        <va-icon name="search" [size]="16"></va-icon>
        <span class="ph">Search candidates, screens…</span>
        <kbd>⌘K</kbd>
      </button>

      <div class="spacer"></div>

      <!-- Tenant identity (resolved from the sign-in email domain — not switchable) -->
      <div class="tenant-id" [title]="auth.institution().name + ' · ' + auth.tenantDomain()">
        <va-icon name="building" [size]="16"></va-icon>
        <span class="seg-lbl ti-text">
          <span class="ti-name">{{ auth.institution().shortName }}</span>
          <span class="ti-domain t-cap t-muted">{{ auth.tenantDomain() }}</span>
        </span>
      </div>

      <!-- Admission cycle selector -->
      <div class="dd cycle" (click)="$event.stopPropagation()">
        <button class="sel" (click)="flip('cycle')">
          <va-icon name="calendar" [size]="16"></va-icon>
          <span class="seg-lbl">{{ auth.admissionCycle() }}</span>
          <va-icon name="chevron-down" [size]="14"></va-icon>
        </button>
        @if (menu() === 'cycle') {
          <div class="pop">
            <div class="pop-title t-cap t-muted">Admission cycle</div>
            @for (c of cycles; track c) {
              <button class="pop-item" [class.active]="c === auth.admissionCycle()" (click)="pickCycle(c)">
                <va-icon name="calendar" [size]="15"></va-icon>{{ c }}
              </button>
            }
          </div>
        }
      </div>

      <!-- Counselor selector — always-visible two-section toggle -->
      <div class="cnsl-seg" role="group" aria-label="AI counselor in focus" (click)="$event.stopPropagation()">
        @if (counselor.both()) {
          @for (m of counselor.enabledMetas(); track m.type) {
            <button class="cnsl-seg-btn" [class.active]="counselor.active() === m.type" [attr.data-v]="m.type"
                    (click)="pickCounselor(m.type)" [title]="m.name + ' — ' + m.title">
              <va-ai-avatar [size]="20" [variant]="m.type"></va-ai-avatar>
              <span class="cnsl-seg-lbl"><span class="cnsl-seg-name">{{ m.name }}</span><span class="cnsl-seg-sub">{{ m.short }}</span></span>
            </button>
          }
        } @else {
          <span class="cnsl-seg-btn active solo" [attr.data-v]="counselor.active()">
            <va-ai-avatar [size]="20" [variant]="counselor.active()"></va-ai-avatar>
            <span class="cnsl-seg-lbl"><span class="cnsl-seg-name">{{ counselor.activeMeta().name }}</span><span class="cnsl-seg-sub">{{ counselor.activeMeta().short }}</span></span>
          </span>
        }
      </div>

      <!-- Quick action -->
      <div class="dd" (click)="$event.stopPropagation()">
        <button class="btn btn-primary qa" (click)="flip('quick')"><va-icon name="plus" [size]="16"></va-icon><span class="qa-lbl">Create</span></button>
        @if (menu() === 'quick') {
          <div class="pop right">
            <div class="pop-title t-cap t-muted">Quick actions</div>
            @for (a of quickActions; track a.label) {
              <button class="pop-item" (click)="runQuick(a)"><va-icon [name]="a.icon" [size]="15"></va-icon>{{ a.label }}</button>
            }
          </div>
        }
      </div>

      <va-ai-status-chip class="hide-sm"></va-ai-status-chip>

      <!-- Notifications -->
      <div class="dd" (click)="$event.stopPropagation()">
        <button class="btn btn-icon btn-ghost notif-btn" (click)="flip('notif')" aria-label="Notifications">
          <va-icon name="bell" [size]="19"></va-icon>
          @if (store.unreadCount() > 0) { <span class="nb">{{ store.unreadCount() }}</span> }
        </button>
        @if (menu() === 'notif') {
          <div class="pop right wide">
            <div class="pop-head">
              <span class="t-h4">Notifications</span>
              <button class="link" (click)="store.markAllRead()">Mark all read</button>
            </div>
            <div class="notif-list scroll-y">
              @for (n of store.notifications(); track n.id) {
                <a class="notif" [class.unread]="!n.read" [routerLink]="n.link" (click)="store.markRead(n.id); menu.set('none')">
                  <span class="ni" [attr.data-p]="n.priority"><va-icon [name]="n.icon" [size]="15"></va-icon></span>
                  <span class="nt">
                    <span class="ntt">{{ n.title }}</span>
                    <span class="ntb t-cap t-muted">{{ n.body }}</span>
                    <span class="ntm t-cap t-muted">{{ rel(n.ts) }} · {{ n.priority }}</span>
                  </span>
                </a>
              }
            </div>
            <a class="btn btn-ghost btn-block" routerLink="/app/settings" (click)="menu.set('none')">Notification preferences</a>
          </div>
        }
      </div>

      <button class="btn btn-icon btn-ghost" (click)="theme.toggle(); $event.stopPropagation()" aria-label="Toggle theme">
        <va-icon [name]="theme.theme() === 'dark' ? 'sun' : 'moon'" [size]="18"></va-icon>
      </button>

      <!-- User menu -->
      <div class="dd" (click)="$event.stopPropagation()">
        <button class="user" (click)="flip('user')">
          <va-avatar [name]="auth.user().name" [hue]="auth.user().hue" [size]="32"></va-avatar>
          <span class="user-meta hide-sm">
            <span class="un">{{ auth.user().name }}</span>
            <span class="ur t-cap t-muted">{{ auth.user().roleLabel }}</span>
          </span>
          <va-icon name="chevron-down" [size]="14" class="hide-sm"></va-icon>
        </button>
        @if (menu() === 'user') {
          <div class="pop right">
            <div class="user-card">
              <va-avatar [name]="auth.user().name" [hue]="auth.user().hue" [size]="40"></va-avatar>
              <div><div class="un">{{ auth.user().name }}</div><div class="t-cap t-muted">{{ auth.user().email }}</div></div>
            </div>
            <div class="pop-title t-cap t-muted">Switch role (demo)</div>
            @for (r of roles; track r.role) {
              <button class="pop-item" [class.active]="r.role === auth.user().role" (click)="switchRole(r.role)">
                <va-icon name="user" [size]="15"></va-icon>{{ r.label }}
              </button>
            }
            <div class="div"></div>
            <a class="pop-item" routerLink="/" (click)="menu.set('none')"><va-icon name="log-out" [size]="15"></va-icon>Sign out</a>
          </div>
        }
      </div>
    </header>`,
  styles: [`
    .tb { height: var(--topbar-h); display: flex; align-items: center; gap: 10px; padding: 0 16px;
      background: color-mix(in srgb, var(--color-surface) 86%, transparent); backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--color-border); position: sticky; top: 0; z-index: 30; }
    .mobile-menu { display: none; color: var(--color-text-muted); }
    .search { display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 12px; min-width: 160px; max-width: 380px; flex: 1 1 160px;
      background: var(--color-surface-alt); border: 1px solid transparent; border-radius: var(--r-md); color: var(--color-text-muted); }
    .search:hover { border-color: var(--color-border); }
    .search .ph { font-size: var(--text-sm); flex: 1; text-align: left; }
    .search kbd { margin-left: auto; }
    .spacer { flex: 1; }
    .dd { position: relative; }
    .sel { display: inline-flex; align-items: center; gap: 7px; height: 38px; padding: 0 10px; border-radius: var(--r-md);
      background: transparent; border: 1px solid var(--color-border); color: var(--color-text); font-size: var(--text-sm); font-weight: 600; }
    .sel:hover { background: var(--color-surface-alt); }
    .tenant-id { display: inline-flex; align-items: center; gap: 8px; height: 38px; padding: 0 12px; border-radius: var(--r-md);
      background: var(--color-surface-alt); border: 1px solid var(--color-border); color: var(--color-text); }
    .ti-text { display: flex; flex-direction: column; line-height: 1.1; text-align: left; }
    .ti-name { font-size: var(--text-sm); font-weight: 700; }
    .ti-domain { font-size: 10px; }
    .qa { height: 38px; }
    .notif-btn { position: relative; color: var(--color-text-muted); }
    .nb { position: absolute; top: 2px; right: 2px; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px;
      background: var(--color-danger); color: #fff; font-size: 10px; font-weight: 700; display: grid; place-items: center; }
    .user { display: flex; align-items: center; gap: 8px; padding: 4px 8px 4px 4px; border-radius: var(--r-pill);
      background: transparent; border: 1px solid var(--color-border); }
    .user:hover { background: var(--color-surface-alt); }
    .user-meta { display: flex; flex-direction: column; line-height: 1.2; text-align: left; }
    .un { font-size: var(--text-sm); font-weight: 600; }
    .pop { position: absolute; top: calc(100% + 8px); left: 0; min-width: 220px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e3); padding: 8px; z-index: 50;
      display: flex; flex-direction: column; gap: 2px; animation: va-fade-up .16s ease both; }
    .pop.right { left: auto; right: 0; }
    .pop.wide { width: 360px; }
    .cnsl-seg { display: inline-flex; align-items: stretch; gap: 4px; padding: 4px; border-radius: var(--r-pill); background: var(--color-surface-alt); border: 1px solid var(--color-border); }
    .cnsl-seg-btn { display: inline-flex; align-items: center; gap: 8px; padding: 4px 12px 4px 6px; border-radius: var(--r-pill); border: 1px solid transparent; background: transparent; color: var(--color-text-muted); transition: background .15s, color .15s, border-color .15s; }
    .cnsl-seg-btn:hover { color: var(--color-text); }
    .cnsl-seg-btn.active { background: var(--color-surface); box-shadow: var(--e1); color: var(--color-text); }
    .cnsl-seg-btn.active[data-v='admission'] { border-color: color-mix(in srgb, var(--color-accent-2) 40%, transparent); }
    .cnsl-seg-btn.active[data-v='career'] { border-color: color-mix(in srgb, var(--color-career) 45%, transparent); }
    .cnsl-seg-btn.solo { cursor: default; }
    .cnsl-seg-lbl { display: flex; flex-direction: column; line-height: 1.05; text-align: left; }
    .cnsl-seg-name { font-size: var(--text-sm); font-weight: 700; }
    .cnsl-seg-sub { font-size: 10px; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .04em; }
    .pop-title { padding: 6px 10px 2px; }
    .pop-head { display: flex; align-items: center; justify-content: space-between; padding: 6px 10px; }
    .pop-item { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--r-md); border: none;
      background: transparent; font-size: var(--text-sm); font-weight: 500; color: var(--color-text); text-align: left; }
    .pop-item:hover { background: var(--color-surface-alt); }
    .pop-item.active { color: var(--color-primary); font-weight: 600; }
    .link { background: none; border: none; color: var(--color-primary); font-size: var(--text-cap); font-weight: 600; }
    .user-card { display: flex; align-items: center; gap: 10px; padding: 8px 10px 10px; }
    .div { height: 1px; background: var(--color-border); margin: 6px 0; }
    .notif-list { max-height: 360px; display: flex; flex-direction: column; }
    .notif { display: flex; gap: 10px; padding: 11px 10px; border-radius: var(--r-md); }
    .notif:hover { background: var(--color-surface-alt); }
    .notif.unread { background: rgba(var(--color-accent-rgb), .05); }
    .ni { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .ni[data-p='Critical'] { background: var(--color-danger-soft); color: var(--color-danger); }
    .ni[data-p='High'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .nt { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .ntt { font-size: var(--text-sm); font-weight: 600; }
    .ntb { white-space: normal; }
    /* Progressively condense the topbar so its single non-wrapping row never
       overflows / clips on smaller viewports. Generic breakpoints (not tuned
       to any one device); icons + tooltips remain when labels are hidden. */
    @media (max-width: 1366px) { .seg-lbl { display: none; } .cnsl-seg-sub { display: none; } }
    @media (max-width: 1200px) { .hide-sm { display: none !important; } .qa-lbl { display: none; } .cnsl-seg-lbl { display: none; } .cnsl-seg-btn { padding: 4px; } }
    @media (max-width: 1024px) { .mobile-menu { display: inline-flex; } }
    @media (max-width: 768px) { .search { min-width: 0; } }
  `],
})
export class TopbarComponent {
  @Output() toggleSidebar = new EventEmitter<void>();
  @Output() openPalette = new EventEmitter<void>();
  theme = inject(ThemeService);
  auth = inject(AuthService);
  store = inject(DataStore);
  counselor = inject(CounselorService);
  private toast = inject(ToastService);
  private router = inject(Router);

  menu = signal<Menu>('none');
  cycles = ['Fall 2026', 'Spring 2026', 'Fall 2025'];
  quickActions = [
    { label: 'Upload CRM Excel', icon: 'upload', route: '/app/crm/import' },
    { label: 'Add candidate', icon: 'users', route: '/app/crm' },
    { label: 'Start outbound campaign', icon: 'megaphone', route: '/app/campaigns' },
    { label: 'Upload KMS document', icon: 'book-open', route: '/app/kms/upload' },
    { label: 'Schedule V-Con', icon: 'video', route: '/app/vcons' },
    { label: 'Create WhatsApp campaign', icon: 'message-circle', route: '/app/communications/whatsapp' },
    { label: 'Create email campaign', icon: 'mail', route: '/app/communications/email' },
    { label: 'Review knowledge gaps', icon: 'brain', route: '/app/learning-review' },
  ];
  roles = [
    { role: 'institution-admin' as const, label: 'Institution Admin (tenant admin)' },
    { role: 'admission-director' as const, label: 'Admission Director' },
    { role: 'admission-manager' as const, label: 'Admission Manager' },
    { role: 'knowledge-manager' as const, label: 'Knowledge Manager' },
    { role: 'compliance-officer' as const, label: 'Compliance Officer' },
    { role: 'ai-supervisor' as const, label: 'AI Supervisor' },
    { role: 'human-counselor' as const, label: 'Human Counselor' },
  ];

  flip(m: Menu) { this.menu.update(c => (c === m ? 'none' : m)); }
  rel = relTime;

  pickCycle(c: string) { this.auth.admissionCycle.set(c); this.menu.set('none'); this.toast.info('Viewing ' + c + ' admission cycle'); }
  pickCounselor(t: CounselorType) {
    if (this.counselor.active() === t) return;
    this.counselor.setActive(t);
    this.menu.set('none');
    // keep content in context: leave routes that don't belong to the new counselor
    const url = this.router.url.split('?')[0];
    const leaving = t === 'admission'
      ? url.startsWith('/app/career')
      : ['/app/applications', '/app/references'].some(r => url.startsWith(r));
    if (leaving) this.router.navigateByUrl('/app/overview');
    this.toast.info('Now viewing ' + this.counselor.meta(t).name + ' — ' + this.counselor.meta(t).title);
  }
  runQuick(a: { label: string; route: string }) { this.menu.set('none'); this.router.navigateByUrl(a.route); }
  switchRole(r: any) { this.auth.signInAs(r); this.menu.set('none'); this.toast.success('Viewing as ' + this.auth.user().roleLabel); }
}
