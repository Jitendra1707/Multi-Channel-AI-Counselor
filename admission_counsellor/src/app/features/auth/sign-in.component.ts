import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { LogoComponent } from '../../shared/ui/logo.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AuthService, ROLE_HOME, TENANTS, TenantRecord } from '../../core/auth.service';
import { Role } from '../../domain/models';

@Component({
  selector: 'va-sign-in',
  standalone: true,
  imports: [RouterLink, IconComponent, LogoComponent, AiAvatarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="auth">
      <!-- Branding panel -->
      <aside class="brand">
        <div class="brand-top"><va-logo [size]="22" [markSize]="34" [onDark]="true"></va-logo></div>
        <div class="brand-mid">
          <va-ai-avatar [size]="64" [glow]="true"></va-ai-avatar>
          <h1 class="t-h1">Welcome back to Admission Counsellor.</h1>
          <p>Continue guiding candidates with intelligent admission automation — governed, auditable, and always honest about being AI.</p>
          <ul class="brand-points">
            <li><va-icon name="shield-check" [size]="16"></va-icon> Approved-knowledge-only counseling</li>
            <li><va-icon name="lock" [size]="16"></va-icon> MFA, SSO & full audit trail</li>
            <li><va-icon name="activity" [size]="16"></va-icon> Live pipeline & conversion intelligence</li>
          </ul>
        </div>
        <div class="brand-foot t-cap">© 2026 Admission Counsellor · Responsible AI for admissions</div>
        <div class="brand-glow" aria-hidden="true"></div>
      </aside>

      <!-- Form -->
      <main class="form-wrap">
        <div class="form">
          <div class="form-mobile-logo"><va-logo [size]="20" [markSize]="30"></va-logo></div>
          <h2 class="t-h2">Sign in</h2>
          <p class="t-sm t-muted">Enter your institution workspace.</p>

          <div class="tenant">
            <va-icon name="building" [size]="16"></va-icon>
            <input class="tenant-input" placeholder="institution code or workspace URL" value="northgate" />
            <span class="tenant-suffix t-cap t-muted">.admissioncounsellor.com</span>
          </div>

          <div class="seg method-seg">
            @for (m of methods; track m) {
              <button [class.active]="method() === m" (click)="method.set(m)">{{ m }}</button>
            }
          </div>

          <div class="detected" [attr.data-known]="!!resolved()">
            <va-icon name="building" [size]="14"></va-icon>
            <span>Workspace: <b>{{ resolved()?.name || 'Unknown domain' }}</b> @if (resolved()) { <span class="t-muted">· branding applied securely</span> }</span>
          </div>

          @if (method() === 'Password') {
            <form class="fields" (submit)="submit($event)">
              <label class="field"><span class="label">Work email</span>
                <input class="input" type="email" [value]="email()" (input)="email.set($any($event.target).value)" autocomplete="username" placeholder="you@your-institution.edu" />
              </label>
              <label class="field"><span class="label">Password</span>
                <div class="pw">
                  <input class="input" [type]="showPw() ? 'text' : 'password'" value="demo-password" autocomplete="current-password" />
                  <button type="button" class="reveal" (click)="showPw.set(!showPw())" [attr.aria-label]="showPw() ? 'Hide password' : 'Show password'"><va-icon name="eye" [size]="16"></va-icon></button>
                </div>
              </label>
              <div class="row-between">
                <label class="remember"><input type="checkbox" checked /> <span class="t-sm">Remember institution</span></label>
                <a class="link" href="#">Forgot password?</a>
              </div>
              @if (error()) { <div class="banner danger"><va-icon name="alert-circle" [size]="16"></va-icon><span>{{ error() }}</span></div> }
              <button class="btn btn-primary btn-block btn-lg" type="submit" [disabled]="busy()">
                @if (busy()) { <va-icon name="refresh" [size]="16" class="spin"></va-icon> Signing in… } @else { Sign in }
              </button>
            </form>
          } @else if (method() === 'SSO') {
            <div class="fields">
              <button class="btn btn-ghost btn-block btn-lg" (click)="go()"><va-icon name="lock" [size]="16"></va-icon> Continue with SAML / OIDC</button>
              <p class="t-cap t-muted center">You’ll be redirected to your institution’s identity provider.</p>
            </div>
          } @else {
            <div class="fields">
              <label class="field"><span class="label">Mobile or email</span><input class="input" value="+91 98765 43210" /></label>
              <button class="btn btn-primary btn-block btn-lg" (click)="go()"><va-icon name="send" [size]="16"></va-icon> Send one-time code</button>
              <p class="t-cap t-muted center">A 6-digit code keeps your account secure.</p>
            </div>
          }

          <div class="role-pick">
            <span class="t-cap t-muted">Demo — explore as a role:</span>
            <div class="role-chips">
              @for (r of roles; track r.role) {
                <button class="chip role" (click)="quick(r.role)">{{ r.label }}</button>
              }
            </div>
          </div>

          <div class="badges">
            <span class="badge"><va-icon name="shield-check" [size]="14"></va-icon> SOC 2</span>
            <span class="badge"><va-icon name="lock" [size]="14"></va-icon> Encrypted</span>
            <span class="badge"><va-icon name="globe" [size]="14"></va-icon> Data residency</span>
          </div>

          <p class="help t-cap t-muted">Trouble signing in? <a class="link" href="#">Contact support</a> · <a class="link" routerLink="/">Back to home</a></p>
        </div>
      </main>
    </div>`,
  styleUrl: './sign-in.component.scss',
})
export class SignInComponent {
  private auth = inject(AuthService);
  private router = inject(Router);
  methods = ['Password', 'SSO', 'OTP'] as const;
  method = signal<'Password' | 'SSO' | 'OTP'>('Password');
  showPw = signal(false);
  busy = signal(false);
  error = signal('');
  email = signal('priya.menon@northgate.edu');
  resolved = computed<TenantRecord | undefined>(() => TENANTS[(this.email().split('@')[1] || '').toLowerCase().trim()]);
  roles = [
    { role: 'admission-director' as Role, label: 'Director' },
    { role: 'admission-manager' as Role, label: 'Manager' },
    { role: 'knowledge-manager' as Role, label: 'Knowledge Mgr' },
    { role: 'compliance-officer' as Role, label: 'Compliance' },
    { role: 'human-counselor' as Role, label: 'Counselor' },
  ];

  submit(e: Event) {
    e.preventDefault();
    this.busy.set(true); this.error.set('');
    setTimeout(() => {
      this.busy.set(false);
      this.auth.signInWithEmail(this.email(), 'institution-admin');
      this.router.navigateByUrl(ROLE_HOME[this.auth.user().role]);
    }, 700);
  }
  go() { this.router.navigateByUrl(ROLE_HOME[this.auth.user().role]); }
  quick(r: Role) { this.auth.signInAs(r); this.router.navigateByUrl(ROLE_HOME[r]); }
}
