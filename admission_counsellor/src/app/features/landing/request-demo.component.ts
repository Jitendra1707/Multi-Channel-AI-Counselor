import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { LogoComponent } from '../../shared/ui/logo.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';

@Component({
  selector: 'va-request-demo',
  standalone: true,
  imports: [RouterLink, IconComponent, LogoComponent, AiAvatarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="rd">
      <header class="rd-nav">
        <a routerLink="/"><va-logo [size]="20" [markSize]="30"></va-logo></a>
        <a routerLink="/login" class="btn btn-ghost btn-sm">Sign in</a>
      </header>

      <div class="rd-grid">
        <!-- Value reassurance -->
        <aside class="rd-value">
          <span class="kicker">Request a demo</span>
          <h1 class="t-h1">See Admission Counsellor counsel a candidate — live.</h1>
          <p class="t-muted">An Admission Counsellor specialist will tailor the workbench to your admission scenario and walk your team through it end to end.</p>
          <ul class="vlist">
            <li><span class="vic"><va-icon name="check" [size]="15"></va-icon></span><div><b>Personalized walkthrough</b><span>Your courses, fees and scholarships — counseled by the AI in real time.</span></div></li>
            <li><span class="vic"><va-icon name="check" [size]="15"></va-icon></span><div><b>Responsible-AI deep dive</b><span>See guardrails, approvals and audit logs that keep messaging on-brand.</span></div></li>
            <li><span class="vic"><va-icon name="check" [size]="15"></va-icon></span><div><b>ROI & rollout plan</b><span>Channel-by-channel go-live mapped to your admission cycle.</span></div></li>
          </ul>
          <div class="vquote">
            <va-ai-avatar [size]="40"></va-ai-avatar>
            <p>“Admission Counsellor speaks only from knowledge your institution has approved — never a word more.”</p>
          </div>
        </aside>

        <!-- Form -->
        <main class="rd-form-wrap">
          @if (!submitted()) {
            <form class="rd-form card" (submit)="submit($event)">
              <h2 class="t-h3">Tell us about your institution</h2>
              <div class="grid2">
                <label class="field"><span class="label">Full name *</span><input class="input" required placeholder="Jane Doe" /></label>
                <label class="field"><span class="label">Work email *</span><input class="input" type="email" required placeholder="jane@university.edu" /></label>
                <label class="field"><span class="label">Institution *</span><input class="input" required placeholder="University name" /></label>
                <label class="field"><span class="label">Role</span>
                  <select class="select"><option>Admissions leadership</option><option>Admissions manager</option><option>IT / Solutions</option><option>Marketing</option><option>Other</option></select>
                </label>
                <label class="field"><span class="label">Country</span><input class="input" placeholder="India" /></label>
                <label class="field"><span class="label">Annual student volume</span>
                  <select class="select"><option>&lt; 1,000</option><option>1,000 – 5,000</option><option>5,000 – 20,000</option><option>20,000+</option></select>
                </label>
              </div>
              <div class="field">
                <span class="label">Channels of interest</span>
                <div class="chan-pick">
                  @for (c of channels; track c.label) {
                    <button type="button" class="chip pick" [class.on]="c.on" (click)="c.on = !c.on">
                      <va-icon [name]="c.icon" [size]="14"></va-icon>{{ c.label }}
                    </button>
                  }
                </div>
              </div>
              <label class="field"><span class="label">Anything specific you’d like to see?</span><textarea class="textarea" rows="3" placeholder="e.g. scholarship counseling, parent engagement, conversion analytics"></textarea></label>
              <button class="btn btn-primary btn-block btn-lg" type="submit" [disabled]="busy()">
                @if (busy()) { <va-icon name="refresh" [size]="16" class="spin"></va-icon> Submitting… } @else { <va-icon name="send" [size]="16"></va-icon> Request demo }
              </button>
              <p class="t-cap t-muted center">By submitting you agree to be contacted about Admission Counsellor. We respect your inbox.</p>
            </form>
          } @else {
            <div class="rd-success card">
              <span class="ok"><va-icon name="check" [size]="30"></va-icon></span>
              <h2 class="t-h2">Thanks — you’re on the list.</h2>
              <p class="t-muted">An Admission Counsellor specialist will reach out within one business day. In the meantime, explore the live platform demo.</p>
              <div class="what-next">
                <div class="wn"><span>1</span> We review your scenario</div>
                <div class="wn"><span>2</span> Tailor a workbench demo</div>
                <div class="wn"><span>3</span> Walk your team through go-live</div>
              </div>
              <div class="success-actions">
                <a routerLink="/app/overview" class="btn btn-primary"><va-icon name="play" [size]="16"></va-icon> Explore the platform</a>
                <button class="btn btn-ghost"><va-icon name="calendar" [size]="16"></va-icon> Book a slot now</button>
              </div>
            </div>
          }
        </main>
      </div>
    </div>`,
  styleUrl: './request-demo.component.scss',
})
export class RequestDemoComponent {
  busy = signal(false);
  submitted = signal(false);
  channels = [
    { icon: 'phone', label: 'Voice', on: true },
    { icon: 'message-circle', label: 'WhatsApp', on: true },
    { icon: 'mail', label: 'Email', on: false },
    { icon: 'video', label: 'V-Cons', on: false },
  ];
  submit(e: Event) { e.preventDefault(); this.busy.set(true); setTimeout(() => { this.busy.set(false); this.submitted.set(true); }, 800); }
}
