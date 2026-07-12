import { ChangeDetectionStrategy, Component, ViewChild, effect, inject, signal, untracked } from '@angular/core';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { filter, map, startWith } from 'rxjs';
import { SidebarComponent } from './sidebar.component';
import { TopbarComponent } from './topbar.component';
import { CommandPaletteComponent } from './command-palette.component';
import { ToastHostComponent } from '../../shared/ui/toast-host.component';
import { NAV } from '../nav';

@Component({
  selector: 'va-shell',
  standalone: true,
  imports: [RouterOutlet, SidebarComponent, TopbarComponent, CommandPaletteComponent, ToastHostComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="shell" [class.collapsed]="collapsed()" [class.mobile-open]="mobileOpen()">
      <div class="sb-wrap">
        <va-sidebar [collapsed]="collapsed()" (toggle)="collapsed.set(!collapsed())" (navigate)="mobileOpen.set(false)"></va-sidebar>
      </div>
      @if (mobileOpen()) { <div class="sb-scrim" (click)="mobileOpen.set(false)"></div> }

      <div class="main">
        <va-topbar (toggleSidebar)="mobileOpen.set(!mobileOpen())" (openPalette)="palette.show()"></va-topbar>
        <div class="crumbs">
          @for (c of crumbs(); track c.label; let last = $last) {
            <span class="crumb" [class.current]="last">{{ c.label }}</span>
            @if (!last) { <span class="sep">/</span> }
          }
        </div>
        <main class="content scroll-y"><router-outlet></router-outlet></main>
      </div>
    </div>
    <va-command-palette #palette></va-command-palette>
    <va-toast-host></va-toast-host>`,
  styles: [`
    .shell { display: flex; height: 100vh; overflow: hidden; }
    .sb-wrap { height: 100%; z-index: 40; }
    .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .crumbs { display: flex; align-items: center; gap: 8px; padding: 10px 24px 0; font-size: var(--text-cap); color: var(--color-text-muted); }
    .crumb.current { color: var(--color-text); font-weight: 600; }
    .sep { opacity: .5; }
    .content { flex: 1; overflow-y: auto; overflow-x: auto; }
    .sb-scrim { display: none; }
    @media (max-width: 1024px) {
      .sb-wrap { position: fixed; left: 0; top: 0; bottom: 0; transform: translateX(-100%); transition: transform .22s cubic-bezier(.4,0,.2,1); }
      .shell.mobile-open .sb-wrap { transform: none; }
      .shell.mobile-open .sb-scrim { display: block; position: fixed; inset: 0; background: rgba(2,6,23,.45); z-index: 38; }
    }
  `],
})
export class ShellComponent {
  @ViewChild('palette') palette!: CommandPaletteComponent;
  private router = inject(Router);
  collapsed = signal(false);
  mobileOpen = signal(false);

  crumbs = toSignal(
    this.router.events.pipe(
      filter(e => e instanceof NavigationEnd),
      startWith(null),
      map(() => this.buildCrumbs(this.router.url)),
    ), { initialValue: this.buildCrumbs(this.router.url) },
  );

  /** Current route URL — drives the V-Cons auto-collapse of the left sidebar. */
  private routeUrl = toSignal(
    this.router.events.pipe(
      filter(e => e instanceof NavigationEnd),
      map(() => this.router.url),
      startWith(this.router.url),
    ), { initialValue: this.router.url },
  );
  private wasOnVcons = false;
  private savedCollapsed = false;

  constructor() {
    // On V-Cons, auto-collapse the left nav to its icon rail (avatar gets more
    // room; tabs stay reachable as icons); restore the prior state on leaving.
    effect(() => {
      const onVcons = this.routeUrl().startsWith('/app/vcons');
      untracked(() => {
        if (onVcons && !this.wasOnVcons) {
          this.savedCollapsed = this.collapsed();
          this.collapsed.set(true);
        } else if (!onVcons && this.wasOnVcons) {
          this.collapsed.set(this.savedCollapsed);
        }
        this.wasOnVcons = onVcons;
      });
    });
  }

  private buildCrumbs(url: string): { label: string }[] {
    const clean = url.split('?')[0].split('#')[0];
    const item = NAV.find(n => clean.startsWith(n.route));
    const crumbs: { label: string }[] = [{ label: 'Admission Counsellor' }];
    if (item) {
      crumbs.push({ label: item.label });
      const rest = clean.slice(item.route.length).split('/').filter(Boolean);
      if (rest.length) {
        const map: Record<string, string> = { import: 'Import', candidate: 'Candidate', upload: 'Upload', document: 'Document',
          voice: 'Voice', whatsapp: 'WhatsApp', email: 'Email', schedule: 'Schedule', room: 'Meeting room', workspace: 'Workspace',
          users: 'Users', roles: 'Roles', institution: 'Institution', presentation: 'Presentation' };
        const seg = rest[0];
        crumbs.push({ label: map[seg] ?? (seg.length > 12 ? 'Detail' : seg) });
      }
    }
    return crumbs;
  }
}
