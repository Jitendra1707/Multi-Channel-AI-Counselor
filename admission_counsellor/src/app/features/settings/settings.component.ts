import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent } from '../../shared/ui/avatar.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { CounselorService, CounselorType, COUNSELORS } from '../../core/counselor.service';

type SectionKey =
  | 'institution' | 'counselors' | 'branding' | 'users' | 'permissions'
  | 'channels' | 'consent' | 'notifications' | 'appearance';

interface NavItem { key: SectionKey; label: string; icon: string; hint: string; }

interface UserRow {
  userId: string;
  name: string;
  email: string;
  hue: number;
  role: string;
  status: 'active' | 'invited' | 'disabled';
  mfa: boolean;
}

interface ChannelRow {
  channel: 'voice' | 'whatsapp' | 'email' | 'vcon';
  label: string;
  icon: string;
  provider: string;
  connected: boolean;
  detail: string;
}

interface ConsentRow { key: string; label: string; desc: string; on: boolean; }
interface NotifRow {
  priority: 'Critical' | 'High' | 'Medium' | 'Low' | 'Informational';
  inApp: boolean;
  email: boolean;
}

@Component({
  selector: 'va-settings',
  standalone: true,
  imports: [IconComponent, AvatarComponent, AiAvatarComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="page page-grid">
  <!-- Header -->
  <header class="st-head">
    <div>
      <div class="t-h2">Settings</div>
      <p class="t-sm t-muted">Institution, users, roles & policies for <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }}</p>
    </div>
    <div class="st-head-actions">
      <span class="chip"><va-icon name="check-circle" [size]="13"></va-icon> Autosave on</span>
      <span class="chip ai-chip"><va-icon name="shield-check" [size]="13"></va-icon> Institution-controlled</span>
    </div>
  </header>

  <div class="st-body">
    <!-- Section nav -->
    <nav class="st-nav surface" aria-label="Settings sections">
      @for (n of nav; track n.key) {
        <button class="nav-item" [class.active]="section() === n.key" (click)="select(n.key)">
          <span class="nav-ic"><va-icon [name]="n.icon" [size]="17"></va-icon></span>
          <span class="nav-text">
            <span class="nav-label">{{ n.label }}</span>
            <span class="nav-hint t-cap t-muted truncate">{{ n.hint }}</span>
          </span>
          <va-icon class="nav-chev" name="chevron-right" [size]="15"></va-icon>
        </button>
      }
      <div class="nav-foot banner ai">
        <va-icon name="lock" [size]="15"></va-icon>
        <span class="t-cap">Least-privilege by default. Every role grants only what it needs; widen access deliberately.</span>
      </div>
    </nav>

    <!-- Panel -->
    <div class="st-panel">

      <!-- ───────── Institution ───────── -->
      @if (section() === 'institution') {
        <va-section-card title="Institution profile" hint="Changes save automatically">
          <span actions class="chip"><va-icon name="building" [size]="12"></va-icon> Tenant</span>
          <div class="form-grid">
            <label class="field">
              <span class="label">Institution name</span>
              <input class="input" [value]="inst().name" (change)="patchInst('name', $any($event.target).value)" />
            </label>
            <label class="field">
              <span class="label">Institution type</span>
              <select class="select" [value]="inst().type" (change)="patchInst('type', $any($event.target).value)">
                @for (t of instTypes; track t) { <option [value]="t">{{ t }}</option> }
              </select>
            </label>
            <label class="field">
              <span class="label">Default locale</span>
              <select class="select" [value]="inst().locale" (change)="patchInst('locale', $any($event.target).value)">
                @for (l of locales; track l) { <option [value]="l">{{ l }}</option> }
              </select>
            </label>
            <label class="field">
              <span class="label">Currency</span>
              <select class="select" [value]="inst().currency" (change)="patchInst('currency', $any($event.target).value)">
                @for (c of currencies; track c) { <option [value]="c">{{ c }}</option> }
              </select>
            </label>
            <label class="field">
              <span class="label">Active admission cycle</span>
              <select class="select" [value]="inst().cycle" (change)="patchInst('cycle', $any($event.target).value)">
                @for (c of cycles; track c) { <option [value]="c">{{ c }}</option> }
              </select>
            </label>
            <label class="field">
              <span class="label">Primary contact email</span>
              <input class="input" [value]="inst().contact" (change)="patchInst('contact', $any($event.target).value)" />
            </label>
          </div>
          <p class="autosave t-cap t-muted"><va-icon name="check" [size]="13"></va-icon> All changes are saved automatically. Last saved {{ lastSaved() }}.</p>
        </va-section-card>
      }

      <!-- ───────── AI Counselors ───────── -->
      @if (section() === 'counselors') {
        <va-section-card title="AI Virtual Humanoid Counselors" hint="Choose the counselor(s) your institution runs">
          <p class="t-sm t-muted cnsl-intro">Enable one or both. Each enabled counselor gets its own workbench, dashboards, analytics and reports. At least one must stay enabled.</p>
          <div class="cnsl-cards">
            @for (m of allCounselors; track m.type) {
              <div class="cnsl-card" [attr.data-v]="m.type" [class.on]="counselor.isEnabled(m.type)">
                <div class="cnsl-card-head">
                  <va-ai-avatar [size]="44" [variant]="m.type" [glow]="counselor.isEnabled(m.type)"></va-ai-avatar>
                  <div class="cnsl-card-id">
                    <span class="cnsl-card-name">{{ m.name }}</span>
                    <span class="t-cap t-muted">{{ m.title }}</span>
                  </div>
                  <button class="switch" [class.on]="counselor.isEnabled(m.type)" (click)="toggleCounselor(m.type)" [attr.aria-label]="'Toggle ' + m.title"></button>
                </div>
                <p class="t-sm t-muted">{{ m.tagline }}</p>
                <div class="cnsl-feats">
                  @for (f of features(m.type); track f) { <span class="chip"><va-icon name="check" [size]="11"></va-icon>{{ f }}</span> }
                </div>
                <div class="cnsl-status" [class.active]="counselor.isEnabled(m.type)">
                  <span class="dot" [class.live]="counselor.isEnabled(m.type)"></span>
                  {{ counselor.isEnabled(m.type) ? 'Enabled — live for this institution' : 'Not enabled' }}
                </div>
              </div>
            }
          </div>
          <div class="banner info cnsl-note">
            <va-icon name="info" [size]="16"></va-icon>
            <span>When both are enabled, staff switch focus from the top bar. Dashboards, analytics and reports are kept separate per counselor.</span>
          </div>
        </va-section-card>
      }

      <!-- ───────── Branding ───────── -->
      @if (section() === 'branding') {
        <div class="brand-grid">
          <va-section-card title="Brand color" hint="Used across the counselor & portal">
            <div class="swatches">
              @for (s of brandColors; track s.hex) {
                <button class="swatch" [class.on]="brand().color === s.hex" [style.background]="s.hex"
                        [title]="s.name" (click)="setBrand(s.hex)">
                  @if (brand().color === s.hex) { <va-icon name="check" [size]="16"></va-icon> }
                </button>
              }
            </div>
            <label class="field hex-field">
              <span class="label">Custom hex</span>
              <input class="input" [value]="brand().color" (change)="setBrand($any($event.target).value)" />
            </label>
          </va-section-card>

          <va-section-card title="Logo" hint="SVG or PNG, max 1 MB">
            <div class="logo-drop center" (click)="uploadLogo()">
              <va-icon name="upload" [size]="22"></va-icon>
              <span class="t-sm">Drop logo here or <b>browse</b></span>
              <span class="t-cap t-muted">Transparent background recommended</span>
            </div>
          </va-section-card>

          <va-section-card title="Live preview" hint="How Aisha introduces your institution" [flush]="true">
            <div class="brand-preview" [style.--bp]="brand().color">
              <div class="bp-bar">
                <span class="bp-mark" [style.background]="brand().color">{{ auth.institution().shortName.slice(0,2).toUpperCase() }}</span>
                <span class="bp-name">{{ auth.institution().name }}</span>
              </div>
              <div class="bp-chat">
                <va-ai-avatar [size]="34" [glow]="true"></va-ai-avatar>
                <div class="bp-bubble">
                  <span class="bp-who">Aisha · <span class="ai-tag">AI counselor</span></span>
                  <p>Hi! I’m Aisha, the AI admission counselor for {{ auth.institution().name }}. I answer only from approved information — how can I help with {{ auth.admissionCycle() }} admissions?</p>
                </div>
              </div>
              <button class="btn btn-sm bp-cta" [style.background]="brand().color">Start your application</button>
            </div>
          </va-section-card>
        </div>
      }

      <!-- ───────── Users & roles ───────── -->
      @if (section() === 'users') {
        <va-section-card title="Users & roles" [hint]="users().length + ' members'" [flush]="true">
          <button actions class="btn btn-sm btn-primary" (click)="invite()"><va-icon name="plus" [size]="14"></va-icon> Invite user</button>
          <div class="scroll-x">
            <table class="va-table">
              <thead>
                <tr>
                  <th>User</th><th>Role</th><th>Status</th><th>MFA</th><th class="num">Actions</th>
                </tr>
              </thead>
              <tbody>
                @for (u of users(); track u.userId) {
                  <tr [class.dim]="u.status === 'disabled'">
                    <td>
                      <div class="u-cell">
                        <va-avatar [name]="u.name" [hue]="u.hue" [size]="32"></va-avatar>
                        <div class="u-meta">
                          <span class="u-name">{{ u.name }}</span>
                          <span class="t-cap t-muted">{{ u.email }}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <select class="select sel-sm" [value]="u.role" (change)="setUserRole(u, $any($event.target).value)">
                        @for (r of roleOptions; track r) { <option [value]="r">{{ r }}</option> }
                      </select>
                    </td>
                    <td><span class="ustat" [attr.data-s]="u.status">{{ statusLabel(u.status) }}</span></td>
                    <td>
                      <button class="switch" [class.on]="u.mfa" (click)="toggleMfa(u)"
                              [attr.aria-label]="'Toggle MFA for ' + u.name"></button>
                    </td>
                    <td class="num">
                      <div class="row-actions">
                        @if (u.status === 'invited') {
                          <button class="btn btn-sm btn-ghost" (click)="resend(u)">Resend</button>
                        }
                        @if (u.status === 'disabled') {
                          <button class="btn btn-sm btn-ghost" (click)="setUserStatus(u, 'active')">Reactivate</button>
                        } @else {
                          <button class="btn btn-sm btn-ghost danger-link" (click)="setUserStatus(u, 'disabled')">Deactivate</button>
                        }
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </va-section-card>
      }

      <!-- ───────── Permissions (RBAC matrix §38.5) ───────── -->
      @if (section() === 'permissions') {
        <va-section-card title="Permission matrix" hint="Role-based access control · §38.5" [flush]="true">
          <div class="matrix-legend" actions>
            <span class="lg"><span class="cell c-full">F</span> Full</span>
            <span class="lg"><span class="cell c-limited">L</span> Limited</span>
            <span class="lg"><span class="cell c-none">–</span> None</span>
          </div>
          <div class="scroll-x">
            <table class="va-table matrix">
              <thead>
                <tr>
                  <th class="perm-col">Permission</th>
                  @for (r of matrixRoles; track r) { <th class="role-col" [title]="r">{{ r }}</th> }
                </tr>
              </thead>
              <tbody>
                @for (p of permMatrix; track p.label) {
                  <tr>
                    <td class="perm-col"><span class="perm-name">{{ p.label }}</span></td>
                    @for (c of p.cells; track $index) {
                      <td class="matrix-cell">
                        <span class="cell" [class.c-full]="c === 'F'" [class.c-limited]="c === 'L'" [class.c-none]="c === '-'">{{ c === '-' ? '–' : c }}</span>
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <div class="banner info matrix-foot">
            <va-icon name="shield" [size]="16"></va-icon>
            <span class="t-sm">Permissions follow least-privilege. Approving KMS / guardrail changes and editing guardrails are deliberately restricted to Knowledge Manager and Compliance — the AI only speaks from approved knowledge.</span>
          </div>
        </va-section-card>
      }

      <!-- ───────── Channels ───────── -->
      @if (section() === 'channels') {
        <va-section-card title="Channel connections" hint="Where Aisha can engage candidates">
          <div class="chan-list">
            @for (c of channels(); track c.channel) {
              <div class="chan" [attr.data-ch]="c.channel">
                <span class="chan-ic"><va-icon [name]="c.icon" [size]="18"></va-icon></span>
                <div class="chan-text">
                  <span class="chan-name">{{ c.label }}</span>
                  <span class="t-cap t-muted">{{ c.provider }} · {{ c.detail }}</span>
                </div>
                <span class="chip" [class.live-chip]="c.connected">{{ c.connected ? 'Connected' : 'Not connected' }}</span>
                <button class="switch" [class.on]="c.connected" (click)="toggleChannel(c)"
                        [attr.aria-label]="'Toggle ' + c.label"></button>
              </div>
            }
          </div>
          <p class="autosave t-cap t-muted"><va-icon name="info" [size]="13"></va-icon> Secrets are stored in a vault. Manage providers & field mapping in Integrations.</p>
        </va-section-card>
      }

      <!-- ───────── Consent & retention ───────── -->
      @if (section() === 'consent') {
        <div class="brand-grid">
          <va-section-card title="Consent policy" hint="Captured before first contact">
            <div class="toggle-list">
              @for (p of consentPolicies(); track p.key) {
                <div class="toggle-row">
                  <div class="tr-text">
                    <span class="tr-name">{{ p.label }}</span>
                    <span class="t-cap t-muted">{{ p.desc }}</span>
                  </div>
                  <button class="switch" [class.on]="p.on" (click)="toggleConsent(p)" [attr.aria-label]="p.label"></button>
                </div>
              }
            </div>
          </va-section-card>

          <va-section-card title="Data retention" hint="Applies tenant-wide">
            <label class="field">
              <span class="label">Retention period (days)</span>
              <input class="input ret-input t-num" type="number" min="30" max="3650"
                     [value]="retentionDays()" (change)="setRetention($any($event.target).value)" />
              <span class="t-cap t-muted">Conversations & recordings are purged after this window.</span>
            </label>
            <label class="field">
              <span class="label">Call & V-Con recording policy</span>
              <select class="select" [value]="recordingPolicy()" (change)="setRecording($any($event.target).value)">
                @for (r of recordingOptions; track r) { <option [value]="r">{{ r }}</option> }
              </select>
            </label>
            <div class="banner warning">
              <va-icon name="alert-triangle" [size]="16"></va-icon>
              <span class="t-sm">Reducing retention permanently deletes data older than the new window. This action is logged in audit logs.</span>
            </div>
          </va-section-card>
        </div>
      }

      <!-- ───────── Notifications ───────── -->
      @if (section() === 'notifications') {
        <va-section-card title="Notification preferences" hint="Per priority · per channel" [flush]="true">
          <div class="scroll-x">
            <table class="va-table">
              <thead>
                <tr><th>Priority</th><th class="num">In-app</th><th class="num">Email</th></tr>
              </thead>
              <tbody>
                @for (n of notifPrefs(); track n.priority) {
                  <tr>
                    <td><span class="prio" [attr.data-p]="n.priority"><span class="dot-p"></span>{{ n.priority }}</span></td>
                    <td class="num"><button class="switch" [class.on]="n.inApp" (click)="toggleNotif(n, 'inApp')" [attr.aria-label]="n.priority + ' in-app'"></button></td>
                    <td class="num"><button class="switch" [class.on]="n.email" (click)="toggleNotif(n, 'email')" [attr.aria-label]="n.priority + ' email'"></button></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="autosave t-cap t-muted"><va-icon name="bell" [size]="13"></va-icon> Critical handoffs and distress flags always notify, regardless of these settings.</p>
        </va-section-card>
      }

      <!-- ───────── Appearance ───────── -->
      @if (section() === 'appearance') {
        <va-section-card title="Appearance" hint="Display preferences">
          <div class="banner info">
            <va-icon name="sun" [size]="16"></va-icon>
            <span class="t-sm">Switch between light and dark themes using the theme toggle in the top bar. Your choice is remembered on this device.</span>
          </div>
          <div class="theme-cards">
            <div class="theme-card light">
              <div class="tc-preview tc-light"><span></span><span></span><span></span></div>
              <span class="t-sm"><va-icon name="sun" [size]="14"></va-icon> Light</span>
            </div>
            <div class="theme-card dark">
              <div class="tc-preview tc-dark"><span></span><span></span><span></span></div>
              <span class="t-sm"><va-icon name="moon" [size]="14"></va-icon> Dark</span>
            </div>
          </div>
          <label class="toggle-row density">
            <div class="tr-text">
              <span class="tr-name">Compact density</span>
              <span class="t-cap t-muted">Tighten spacing across tables and lists.</span>
            </div>
            <button class="switch" [class.on]="compact()" (click)="toggleCompact()" aria-label="Compact density"></button>
          </label>
        </va-section-card>
      }

    </div>
  </div>
</div>
  `,
  styles: [`
    :host { display: block; }

    .st-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .st-head p { margin-top: 4px; }
    .st-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }

    .st-body { display: grid; grid-template-columns: 264px minmax(0, 1fr); gap: 18px; align-items: start; }

    /* nav */
    .st-nav { padding: 8px; display: flex; flex-direction: column; gap: 2px; position: sticky; top: 0; }
    .nav-item { display: flex; align-items: center; gap: 11px; padding: 9px 10px; border: none; background: transparent;
      border-radius: var(--r-md); text-align: left; color: var(--color-text); transition: background .12s; width: 100%; }
    .nav-item:hover { background: var(--color-surface-alt); }
    .nav-item.active { background: rgba(var(--color-primary-rgb), .10); }
    .nav-item.active .nav-label { color: var(--color-primary); }
    .nav-item.active .nav-ic { color: var(--color-primary); }
    .nav-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .nav-item.active .nav-ic { background: rgba(var(--color-primary-rgb), .14); }
    .nav-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .nav-label { font-size: var(--text-sm); font-weight: 600; }
    .nav-hint { max-width: 100%; }
    .nav-chev { color: var(--color-text-muted); opacity: 0; transition: opacity .12s; }
    .nav-item:hover .nav-chev, .nav-item.active .nav-chev { opacity: .7; }
    .nav-foot { margin-top: 8px; align-items: flex-start; }
    .nav-foot va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }

    .st-panel { min-width: 0; display: flex; flex-direction: column; gap: 18px; }

    /* forms */
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .autosave { display: inline-flex; align-items: center; gap: 6px; margin-top: 16px; }
    .autosave va-icon { color: var(--color-success); }

    /* branding */
    .brand-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    .brand-grid > va-section-card:last-child { grid-column: 1 / -1; }
    .swatches { display: flex; flex-wrap: wrap; gap: 10px; }
    .swatch { width: 40px; height: 40px; border-radius: var(--r-md); border: 2px solid var(--color-border); cursor: pointer;
      display: grid; place-items: center; color: #fff; box-shadow: var(--e1); transition: transform .1s; }
    .swatch:hover { transform: translateY(-1px); }
    .swatch.on { border-color: var(--color-text); box-shadow: var(--ring); }
    .hex-field { max-width: 200px; margin-top: 16px; }
    .logo-drop { flex-direction: column; gap: 6px; padding: 28px; border: 1.5px dashed var(--color-border-strong);
      border-radius: var(--r-md); color: var(--color-text-muted); cursor: pointer; transition: border-color .12s, background .12s; text-align: center; }
    .logo-drop:hover { border-color: var(--color-accent); background: var(--color-surface-alt); }

    .brand-preview { padding: 18px; display: flex; flex-direction: column; gap: 14px; background: var(--color-surface-2); }
    .bp-bar { display: flex; align-items: center; gap: 10px; }
    .bp-mark { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 13px; flex: none; }
    .bp-name { font-weight: 700; font-family: var(--font-display); }
    .bp-chat { display: flex; gap: 10px; align-items: flex-start; }
    .bp-bubble { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-md);
      border-top-left-radius: 4px; padding: 10px 12px; box-shadow: var(--e1); }
    .bp-who { font-size: var(--text-cap); font-weight: 700; }
    .ai-tag { color: var(--color-accent-2); }
    .bp-bubble p { font-size: var(--text-sm); margin-top: 4px; }
    .bp-cta { color: #fff; border: none; align-self: flex-start; }

    /* users table */
    .scroll-x { overflow-x: auto; }
    .u-cell { display: flex; align-items: center; gap: 10px; }
    .u-meta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .u-name { font-weight: 600; }
    .sel-sm { padding: 6px 28px 6px 10px; font-size: var(--text-cap); min-width: 150px; }
    .va-table tbody tr.dim td { opacity: .55; }
    .va-table tbody tr { cursor: default; }
    .va-table tbody tr:hover { background: transparent; }
    .ustat { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; padding: 3px 9px; border-radius: var(--r-pill); }
    .ustat::before { content: ''; width: 7px; height: 7px; border-radius: 50%; }
    .ustat[data-s='active'] { background: var(--color-success-soft); color: var(--color-success); }
    .ustat[data-s='active']::before { background: var(--color-success); }
    .ustat[data-s='invited'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .ustat[data-s='invited']::before { background: var(--color-warning); }
    .ustat[data-s='disabled'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .ustat[data-s='disabled']::before { background: var(--color-text-muted); }
    .row-actions { display: inline-flex; gap: 6px; justify-content: flex-end; }
    .danger-link { color: var(--color-danger); }
    .danger-link:hover { background: var(--color-danger-soft); }

    /* permission matrix */
    .matrix-legend { display: flex; gap: 14px; }
    .matrix-legend .lg { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); }
    .matrix th.perm-col, .matrix td.perm-col { position: sticky; left: 0; background: var(--color-surface); z-index: 2; min-width: 190px; }
    .matrix thead th.perm-col { z-index: 3; }
    .matrix .role-col { text-align: center; font-size: 10px; max-width: 70px; }
    .matrix .matrix-cell { text-align: center; }
    .perm-name { font-weight: 600; font-size: var(--text-sm); white-space: nowrap; }
    .cell { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 7px;
      font-size: var(--text-cap); font-weight: 800; }
    .c-full { background: var(--color-success-soft); color: var(--color-success); }
    .c-limited { background: var(--color-warning-soft); color: var(--color-warning); }
    .c-none { background: transparent; color: var(--color-text-muted); }
    .matrix-foot { margin: 14px 18px 4px; align-items: flex-start; }
    .matrix-foot va-icon { color: var(--color-accent); flex: none; margin-top: 1px; }

    /* channels */
    .chan-list { display: flex; flex-direction: column; gap: 10px; }
    .chan { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface-2); }
    .chan-ic { width: 36px; height: 36px; border-radius: 9px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .chan[data-ch='voice'] .chan-ic { color: var(--ch-voice); }
    .chan[data-ch='whatsapp'] .chan-ic { color: var(--ch-whatsapp); }
    .chan[data-ch='email'] .chan-ic { color: var(--ch-email); }
    .chan[data-ch='vcon'] .chan-ic { color: var(--ch-vcon); }
    .chan-text { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }
    .chan-name { font-weight: 600; font-size: var(--text-sm); }
    .live-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }

    /* toggle lists */
    .toggle-list { display: flex; flex-direction: column; }
    .toggle-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--color-border); }
    .toggle-row:last-child { border-bottom: none; }
    .tr-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .tr-name { font-weight: 600; font-size: var(--text-sm); }
    .ret-input { max-width: 160px; }
    .field + .field { margin-top: 16px; }
    .field .banner, .field + .banner { margin-top: 8px; }

    /* notifications */
    .prio { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; font-size: var(--text-sm); }
    .dot-p { width: 8px; height: 8px; border-radius: 50%; flex: none; }
    .prio[data-p='Critical'] .dot-p { background: var(--color-danger); }
    .prio[data-p='High'] .dot-p { background: var(--color-warning); }
    .prio[data-p='Medium'] .dot-p { background: var(--color-primary); }
    .prio[data-p='Low'] .dot-p { background: var(--color-text-muted); }
    .prio[data-p='Informational'] .dot-p { background: var(--color-border-strong); }
    .va-table .num .switch { display: inline-flex; }

    /* appearance */
    .theme-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 16px; }
    .theme-card { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 12px; display: flex; flex-direction: column; gap: 10px; }
    .theme-card span { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
    .tc-preview { height: 56px; border-radius: var(--r-sm); display: flex; flex-direction: column; justify-content: center; gap: 5px; padding: 10px; }
    .tc-preview span { height: 7px; border-radius: 999px; display: block; }
    .tc-light { background: #F7F8FB; border: 1px solid #E2E8F0; }
    .tc-light span:nth-child(1) { width: 60%; background: #1E3A8A; }
    .tc-light span:nth-child(2) { width: 90%; background: #CBD5E1; }
    .tc-light span:nth-child(3) { width: 40%; background: #22D3EE; }
    .tc-dark { background: #0B1020; border: 1px solid #2A3450; }
    .tc-dark span:nth-child(1) { width: 60%; background: #3B5BD6; }
    .tc-dark span:nth-child(2) { width: 90%; background: #2A3450; }
    .tc-dark span:nth-child(3) { width: 40%; background: #22D3EE; }
    .density { margin-top: 16px; border-top: 1px solid var(--color-border); padding-top: 16px; }

    .cnsl-intro { max-width: 64ch; margin-bottom: 16px; }
    .cnsl-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .cnsl-card { border: 1px solid var(--color-border); border-radius: var(--r-lg); padding: 18px; display: flex; flex-direction: column; gap: 10px; transition: border-color .15s, box-shadow .15s; }
    .cnsl-card.on[data-v='admission'] { border-color: color-mix(in srgb, var(--color-accent-2) 45%, var(--color-border)); box-shadow: 0 6px 20px rgba(var(--color-accent-2-rgb), .08); }
    .cnsl-card.on[data-v='career'] { border-color: color-mix(in srgb, var(--color-career) 45%, var(--color-border)); box-shadow: 0 6px 20px rgba(var(--color-career-rgb), .10); }
    .cnsl-card-head { display: flex; align-items: center; gap: 12px; }
    .cnsl-card-id { display: flex; flex-direction: column; gap: 1px; }
    .cnsl-card-name { font-weight: 700; font-size: var(--text-h4); }
    .cnsl-card-head .switch { margin-left: auto; }
    .cnsl-feats { display: flex; flex-wrap: wrap; gap: 6px; }
    .cnsl-status { display: inline-flex; align-items: center; gap: 7px; font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); margin-top: 2px; }
    .cnsl-status.active { color: var(--color-success); }
    .cnsl-note { margin-top: 16px; }

    @media (max-width: 1100px) {
      .cnsl-cards { grid-template-columns: 1fr; }
      .st-body { grid-template-columns: 1fr; }
      .st-nav { position: static; flex-direction: row; flex-wrap: wrap; }
      .st-nav .nav-item { flex: 1 1 auto; }
      .nav-foot { flex-basis: 100%; }
      .form-grid, .brand-grid, .theme-cards { grid-template-columns: 1fr; }
    }
  `],
})
export class SettingsComponent {
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  private toast = inject(ToastService);
  private route = inject(ActivatedRoute);

  readonly allCounselors = [COUNSELORS.admission, COUNSELORS.career];
  features(t: CounselorType): string[] {
    return t === 'admission'
      ? ['Lead CRM & follow-up', 'Fee & scholarship counseling', 'Applications & registration', 'Admissions funnel & reports']
      : ['Career discovery & aptitude', 'Pathway & course mapping', 'Skill-gap & upskilling', 'Career-readiness & reports'];
  }
  toggleCounselor(t: CounselorType) {
    const wasOn = this.counselor.isEnabled(t);
    this.counselor.toggleEnabled(t);
    const nowOn = this.counselor.isEnabled(t);
    if (wasOn === nowOn && wasOn) this.toast.warning('At least one counselor must stay enabled.');
    else this.toast.success(COUNSELORS[t].title + (nowOn ? ' enabled for this institution.' : ' disabled.'));
  }

  readonly nav: NavItem[] = [
    { key: 'institution', label: 'Institution', icon: 'building', hint: 'Profile & cycle' },
    { key: 'counselors', label: 'AI Counselors', icon: 'bot', hint: 'Choose Admission / Career' },
    { key: 'branding', label: 'Branding', icon: 'sparkles', hint: 'Color & logo' },
    { key: 'users', label: 'Users & roles', icon: 'users', hint: 'Members & access' },
    { key: 'permissions', label: 'Permissions', icon: 'shield-check', hint: 'RBAC matrix' },
    { key: 'channels', label: 'Channels', icon: 'plug', hint: 'Voice · WhatsApp · email' },
    { key: 'consent', label: 'Consent & retention', icon: 'lock', hint: 'Policies & purge' },
    { key: 'notifications', label: 'Notifications', icon: 'bell', hint: 'Per-priority alerts' },
    { key: 'appearance', label: 'Appearance', icon: 'sun', hint: 'Theme & density' },
  ];

  private validSections = new Set<SectionKey>(this.nav.map(n => n.key));
  section = signal<SectionKey>(this.readSection());

  private readSection(): SectionKey {
    const p = this.route.snapshot.paramMap.get('section') as SectionKey | null;
    return p && this.validSections.has(p) ? p : 'institution';
  }

  select(k: SectionKey) { this.section.set(k); }

  private saved = signal<number>(Date.now());
  lastSaved = computed(() => { this.saved(); return 'just now'; });
  private touch() { this.saved.set(Date.now()); }

  /* ---------- Institution ---------- */
  inst = signal({
    name: 'Northgate University',
    type: 'University',
    locale: 'English (India)',
    currency: 'Indian Rupee (₹)',
    cycle: 'Fall 2026',
    contact: 'admissions@northgate.edu',
  });
  instTypes = ['University', 'Deemed University', 'Autonomous College', 'Institute of Technology', 'Business School'];
  locales = ['English (India)', 'English (US)', 'Hindi', 'Tamil', 'Telugu', 'Kannada'];
  currencies = ['Indian Rupee (₹)', 'US Dollar ($)', 'Euro (€)'];
  cycles = ['Fall 2026', 'Spring 2026', 'Fall 2025', 'Spring 2027'];

  patchInst(key: keyof ReturnType<typeof this.inst>, value: string) {
    this.inst.update(v => ({ ...v, [key]: value }));
    this.touch();
    this.toast.success('Saved');
  }

  /* ---------- Branding ---------- */
  brandColors = [
    { name: 'Northgate Blue', hex: '#1E3A8A' },
    { name: 'Cyan', hex: '#0891B2' },
    { name: 'Violet', hex: '#7C3AED' },
    { name: 'Emerald', hex: '#0E9F6E' },
    { name: 'Crimson', hex: '#B91C1C' },
    { name: 'Amber', hex: '#D97706' },
    { name: 'Slate', hex: '#334155' },
  ];
  brand = signal({ color: '#1E3A8A' });
  setBrand(hex: string) { this.brand.update(b => ({ ...b, color: hex })); this.touch(); this.toast.success('Saved'); }
  uploadLogo() { this.toast.info('Logo upload — connect storage to enable in this build.'); }

  /* ---------- Users & roles ---------- */
  roleOptions = [
    'Institution Admin', 'Admission Director', 'Admission Manager', 'AI Counselor Supervisor',
    'Knowledge Manager', 'Compliance Officer', 'CRM / Data Manager', 'Human Counselor', 'University Management',
  ];
  users = signal<UserRow[]>([
    { userId: 'u1', name: 'Priya Menon', email: 'priya.menon@northgate.edu', hue: 222, role: 'Admission Director', status: 'active', mfa: true },
    { userId: 'u2', name: 'Rahul Desai', email: 'rahul.desai@northgate.edu', hue: 18, role: 'Admission Manager', status: 'active', mfa: true },
    { userId: 'u3', name: 'Kavya Iyer', email: 'kavya.iyer@northgate.edu', hue: 280, role: 'Knowledge Manager', status: 'active', mfa: true },
    { userId: 'u4', name: 'Sneha Banerjee', email: 'sneha.banerjee@northgate.edu', hue: 330, role: 'Compliance Officer', status: 'active', mfa: true },
    { userId: 'u5', name: 'Imran Sheikh', email: 'imran.sheikh@northgate.edu', hue: 150, role: 'AI Counselor Supervisor', status: 'active', mfa: false },
    { userId: 'u6', name: 'Meera Nair', email: 'meera.nair@northgate.edu', hue: 200, role: 'Human Counselor', status: 'active', mfa: true },
    { userId: 'u7', name: 'Arjun Reddy', email: 'arjun.reddy@northgate.edu', hue: 40, role: 'CRM / Data Manager', status: 'invited', mfa: false },
    { userId: 'u8', name: 'Divya Pillai', email: 'divya.pillai@northgate.edu', hue: 95, role: 'University Management', status: 'disabled', mfa: false },
  ]);

  statusLabel(s: UserRow['status']) { return s === 'active' ? 'Active' : s === 'invited' ? 'Invited' : 'Disabled'; }
  invite() { this.toast.success('Invitation sent — the user will appear once they accept.'); }
  resend(u: UserRow) { this.toast.info('Invite resent to ' + u.email); }
  setUserRole(u: UserRow, role: string) {
    this.users.update(l => l.map(x => x.userId === u.userId ? { ...x, role } : x));
    this.touch(); this.toast.success('Saved');
  }
  setUserStatus(u: UserRow, status: UserRow['status']) {
    this.users.update(l => l.map(x => x.userId === u.userId ? { ...x, status } : x));
    this.touch();
    this.toast.success(status === 'disabled' ? u.name + ' deactivated' : 'Saved');
  }
  toggleMfa(u: UserRow) {
    this.users.update(l => l.map(x => x.userId === u.userId ? { ...x, mfa: !x.mfa } : x));
    this.touch(); this.toast.success('Saved');
  }

  /* ---------- Permissions (§38.5) ---------- */
  matrixRoles = ['Inst Admin', 'Director', 'Adm Mgr', 'AI Sup', 'Know Mgr', 'Compliance', 'CRM Mgr', 'Human'];
  permMatrix: { label: string; cells: ('F' | 'L' | '-')[] }[] = [
    { label: 'View dashboards',        cells: ['F', 'F', 'F', 'F', 'L', 'L', 'L', 'L'] },
    { label: 'Manage candidates',      cells: ['F', 'F', 'F', 'L', '-', '-', 'F', 'L'] },
    { label: 'Import leads',           cells: ['F', 'F', 'L', '-', '-', '-', 'F', '-'] },
    { label: 'Configure counselor',    cells: ['F', 'L', '-', 'F', 'L', 'L', '-', '-'] },
    { label: 'Upload KMS docs',        cells: ['F', 'L', '-', 'L', 'F', 'L', '-', '-'] },
    { label: 'Approve KMS / guardrails', cells: ['L', '-', '-', '-', 'F', 'F', '-', '-'] },
    { label: 'Edit guardrails',        cells: ['L', '-', '-', 'L', 'L', 'F', '-', '-'] },
    { label: 'Handle escalations',     cells: ['F', 'F', 'F', 'L', '-', '-', '-', 'F'] },
    { label: 'Manage users / roles',   cells: ['F', 'L', '-', '-', '-', '-', '-', '-'] },
    { label: 'Manage integrations',    cells: ['F', 'L', '-', 'L', '-', '-', 'L', '-'] },
    { label: 'View audit logs',        cells: ['F', 'L', '-', 'L', 'L', 'F', '-', '-'] },
  ];

  /* ---------- Channels ---------- */
  channels = signal<ChannelRow[]>([
    { channel: 'voice', label: 'Voice telephony', icon: 'phone', provider: 'Exotel', connected: true, detail: '+91 80 4710 2200' },
    { channel: 'whatsapp', label: 'WhatsApp Business', icon: 'message-circle', provider: 'Meta Cloud API', connected: true, detail: 'Verified · 2 templates live' },
    { channel: 'email', label: 'Email', icon: 'mail', provider: 'Amazon SES', connected: true, detail: 'admissions@northgate.edu' },
    { channel: 'vcon', label: 'V-Con video', icon: 'video', provider: 'WebRTC (Phase 2)', connected: false, detail: 'Awaiting provider setup' },
  ]);
  toggleChannel(c: ChannelRow) {
    this.channels.update(l => l.map(x => x.channel === c.channel ? { ...x, connected: !x.connected } : x));
    this.touch();
    this.toast.success(c.connected ? c.label + ' disconnected' : c.label + ' connected');
  }

  /* ---------- Consent & retention ---------- */
  consentPolicies = signal<ConsentRow[]>([
    { key: 'call', label: 'Voice call consent required', desc: 'Capture explicit consent before AI places calls.', on: true },
    { key: 'whatsapp', label: 'WhatsApp opt-in required', desc: 'Honor Meta opt-in before messaging.', on: true },
    { key: 'email', label: 'Email opt-in required', desc: 'Send only to candidates who opted in.', on: true },
    { key: 'recording', label: 'Recording disclosure', desc: 'Announce recording at call start.', on: true },
    { key: 'parent', label: 'Parent contact consent', desc: 'Require candidate consent before contacting parents.', on: false },
  ]);
  toggleConsent(p: ConsentRow) {
    this.consentPolicies.update(l => l.map(x => x.key === p.key ? { ...x, on: !x.on } : x));
    this.touch(); this.toast.success('Saved');
  }
  retentionDays = signal<number>(365);
  setRetention(v: string) {
    const n = Math.max(30, Math.min(3650, Number(v) || 365));
    this.retentionDays.set(n); this.touch();
    this.toast.success('Retention set to ' + n + ' days');
  }
  recordingOptions = ['Record all (with disclosure)', 'Record voice only', 'Record on consent', 'Do not record'];
  recordingPolicy = signal<string>('Record all (with disclosure)');
  setRecording(v: string) { this.recordingPolicy.set(v); this.touch(); this.toast.success('Saved'); }

  /* ---------- Notifications ---------- */
  notifPrefs = signal<NotifRow[]>([
    { priority: 'Critical', inApp: true, email: true },
    { priority: 'High', inApp: true, email: true },
    { priority: 'Medium', inApp: true, email: false },
    { priority: 'Low', inApp: false, email: false },
    { priority: 'Informational', inApp: false, email: false },
  ]);
  toggleNotif(n: NotifRow, kind: 'inApp' | 'email') {
    this.notifPrefs.update(l => l.map(x => x.priority === n.priority ? { ...x, [kind]: !x[kind] } : x));
    this.touch(); this.toast.success('Saved');
  }

  /* ---------- Appearance ---------- */
  compact = signal<boolean>(false);
  toggleCompact() { this.compact.update(v => !v); this.touch(); this.toast.success('Saved'); }
}
