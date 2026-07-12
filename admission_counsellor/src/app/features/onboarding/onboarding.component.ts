import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { LogoComponent } from '../../shared/ui/logo.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { FieldApproval } from '../../domain/models';

/** A wizard step (§10.1). */
interface WizardStep {
  n: number;
  key: string;
  title: string;
  icon: string;
  blurb: string;
  /** maps to a readiness key once complete */
  done: boolean;
}

/** AI-readiness checklist item (§10.2). */
type ReadyState = 'green' | 'pending' | 'partial';
interface ReadyItem {
  key: string;
  label: string;
  hint: string;
  state: ReadyState;
  /** if pending/partial, this blocks Go-Live */
  blocks: boolean;
}

@Component({
  selector: 'va-onboarding',
  standalone: true,
  imports: [RouterLink, IconComponent, LogoComponent, AiAvatarComponent, ApprovalChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ob">
      <!-- ============ TOP BAR ============ -->
      <header class="ob-top">
        <div class="row gap-3">
          <va-logo [size]="20" [markSize]="30"></va-logo>
          <span class="topdiv"></span>
          <span class="chip"><va-icon name="rocket" [size]="13"></va-icon> Guided setup</span>
        </div>
        <div class="row gap-3">
          <div class="autosave t-cap t-muted"><span class="dot live"></span> Saved {{ savedAgo() }}</div>
          <a class="btn btn-ghost btn-sm" routerLink="/login"><va-icon name="log-out" [size]="14"></va-icon> Exit to sign in</a>
        </div>
      </header>

      <!-- ============ 3-COLUMN BODY ============ -->
      <div class="ob-body">
        <!-- ---------- LEFT : step rail ---------- -->
        <aside class="rail scroll-y">
          <div class="rail-head">
            <div class="t-h4">Set up {{ inst() }}</div>
            <p class="t-cap t-muted">{{ completed() }} of {{ steps().length }} steps complete</p>
            <div class="progress ai rail-prog"><span [style.width.%]="completionPct()"></span></div>
            <div class="row between rail-pct">
              <span class="t-cap t-muted">Setup completion</span>
              <span class="t-cap t-num" style="font-weight:700">{{ completionPct() }}%</span>
            </div>
          </div>

          <nav class="steps">
            @for (s of steps(); track s.n) {
              <button class="step"
                      [class.active]="s.n === current()"
                      [class.done]="s.done"
                      (click)="goStep(s.n)">
                <span class="step-mark">
                  @if (s.done) { <va-icon name="check" [size]="13"></va-icon> }
                  @else { <span class="t-num">{{ s.n }}</span> }
                </span>
                <span class="step-text">
                  <span class="step-title truncate">{{ s.title }}</span>
                  @if (s.n === current()) { <span class="step-blurb t-cap">{{ s.blurb }}</span> }
                </span>
                @if (s.n === current()) { <va-icon name="chevron-right" [size]="14"></va-icon> }
              </button>
            }
          </nav>
        </aside>

        <!-- ---------- CENTER : active step form ---------- -->
        <main class="center scroll-y">
          <div class="center-inner fade-up">
            <div class="step-head">
              <div class="step-num t-num">Step {{ current() }} / {{ steps().length }}</div>
              <div class="row between wrap gap-3">
                <div class="row gap-3">
                  <span class="step-ico"><va-icon [name]="step().icon" [size]="20"></va-icon></span>
                  <div>
                    <div class="t-h2">{{ step().title }}</div>
                    <p class="t-sm t-muted">{{ step().blurb }}</p>
                  </div>
                </div>
                @if (step().done) {
                  <span class="chip done-chip"><va-icon name="check-circle" [size]="13"></va-icon> Completed</span>
                } @else {
                  <button class="btn btn-subtle btn-sm" (click)="markDone()"><va-icon name="check" [size]="14"></va-icon> Mark complete</button>
                }
              </div>
            </div>

            <!-- ====== STEP 1 — Institution profile ====== -->
            @if (current() === 1) {
              <div class="form-card">
                <div class="banner ai">
                  <va-icon name="sparkles" [size]="16"></va-icon>
                  <span>Let's set up <b>{{ inst() }}</b>. This takes about 30 minutes — your progress saves automatically.</span>
                </div>

                <div class="grid-2">
                  <label class="field"><span class="label">Institution name</span>
                    <input class="input" value="Northgate University" /></label>
                  <label class="field"><span class="label">Short name</span>
                    <input class="input" value="Northgate" /></label>
                  <label class="field"><span class="label">Institution type</span>
                    <select class="select">
                      <option>University</option><option>Deemed University</option>
                      <option>Autonomous College</option><option>Institute</option>
                    </select></label>
                  <label class="field"><span class="label">Accreditation / affiliation</span>
                    <input class="input" value="NAAC A++, UGC" /></label>
                </div>

                <div class="logo-row">
                  <div class="logo-drop">
                    <va-icon name="building" [size]="22"></va-icon>
                  </div>
                  <div class="grow">
                    <div class="t-sm" style="font-weight:600">Institution logo</div>
                    <p class="t-cap t-muted">SVG or PNG, transparent background recommended · used on the AI counselor and candidate touchpoints.</p>
                    <button class="btn btn-ghost btn-sm" style="margin-top:8px"><va-icon name="upload" [size]="14"></va-icon> Upload logo</button>
                  </div>
                </div>

                <div class="grid-2">
                  <label class="field"><span class="label">Primary contact name</span>
                    <input class="input" value="Priya Menon" /></label>
                  <label class="field"><span class="label">Contact email</span>
                    <input class="input" value="admissions@northgate.edu" /></label>
                  <label class="field"><span class="label">Default locale</span>
                    <select class="select">
                      <option>English (India)</option><option>हिन्दी</option>
                      <option>తెలుగు</option><option>தமிழ்</option>
                    </select></label>
                  <label class="field"><span class="label">Currency</span>
                    <select class="select"><option>₹ Indian Rupee (INR)</option><option>$ US Dollar (USD)</option></select></label>
                  <label class="field"><span class="label">Timezone</span>
                    <input class="input" value="Asia/Kolkata (GMT+5:30)" /></label>
                  <label class="field"><span class="label">Website</span>
                    <input class="input" value="https://northgate.edu" /></label>
                </div>
              </div>
            }

            <!-- ====== STEP 2 — Campuses & courses ====== -->
            @else if (current() === 2) {
              <div class="form-card">
                <div class="row between">
                  <div class="t-h4">Campuses & courses</div>
                  <button class="btn btn-ghost btn-sm"><va-icon name="plus" [size]="14"></va-icon> Add course</button>
                </div>
                <table class="va-table simple">
                  <thead><tr><th>Course</th><th>Campus</th><th class="num">Seats</th><th>Status</th></tr></thead>
                  <tbody>
                    @for (c of courses; track c.name) {
                      <tr>
                        <td><div class="t-sm" style="font-weight:600">{{ c.name }}</div><div class="t-cap t-muted">{{ c.level }}</div></td>
                        <td>{{ c.campus }}</td>
                        <td class="num t-num">{{ c.seats }}</td>
                        <td><va-approval-chip [state]="c.ready ? 'approved' : 'draft'"></va-approval-chip></td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            }

            <!-- ====== STEP 3 — Academic year & cycle ====== -->
            @else if (current() === 3) {
              <div class="form-card">
                <div class="grid-2">
                  <label class="field"><span class="label">Academic year</span>
                    <select class="select"><option>2026–2027</option><option>2027–2028</option></select></label>
                  <label class="field"><span class="label">Admission cycle</span>
                    <select class="select"><option>Fall 2026</option><option>Spring 2027</option></select></label>
                  <label class="field"><span class="label">Applications open</span>
                    <input class="input" value="01 Jul 2026" /></label>
                  <label class="field"><span class="label">Applications close</span>
                    <input class="input" value="30 Sep 2026" /></label>
                </div>
                <div class="banner info">
                  <va-icon name="calendar" [size]="16"></va-icon>
                  <span>Aisha will reference these dates when answering "when can I apply?" — only from your approved calendar.</span>
                </div>
              </div>
            }

            <!-- ====== STEP 4 — Upload documents ====== -->
            @else if (current() === 4) {
              <div class="form-card">
                <div class="upload-drop">
                  <va-icon name="upload" [size]="26"></va-icon>
                  <div class="t-sm" style="font-weight:600">Drop your prospectus, course pages & policy PDFs</div>
                  <p class="t-cap t-muted">Aisha learns only from documents you upload and approve. Nothing else.</p>
                  <button class="btn btn-primary btn-sm" style="margin-top:10px"><va-icon name="upload" [size]="14"></va-icon> Select files</button>
                </div>
                <div class="doclist">
                  @for (d of docs; track d.name) {
                    <div class="docrow">
                      <span class="doc-ico"><va-icon name="file-text" [size]="16"></va-icon></span>
                      <div class="grow"><div class="t-sm" style="font-weight:600">{{ d.name }}</div>
                        <div class="t-cap t-muted">{{ d.size }} · {{ d.cat }}</div></div>
                      <va-approval-chip [state]="d.state"></va-approval-chip>
                    </div>
                  }
                </div>
              </div>
            }

            <!-- ====== STEP 5 — Fee structures ====== -->
            @else if (current() === 5) {
              <div class="form-card">
                <div class="banner warning">
                  <va-icon name="shield-check" [size]="16"></va-icon>
                  <span><b>Claim-bearing.</b> Aisha will never quote a fee until this is approved by Compliance. Until then she defers to a human.</span>
                </div>
                <table class="va-table simple">
                  <thead><tr><th>Course</th><th class="num">Tuition / year</th><th class="num">Other fees</th><th>Status</th></tr></thead>
                  <tbody>
                    @for (f of fees; track f.course) {
                      <tr>
                        <td class="t-sm" style="font-weight:600">{{ f.course }}</td>
                        <td class="num t-num">{{ f.tuition }}</td>
                        <td class="num t-num">{{ f.other }}</td>
                        <td><va-approval-chip [state]="f.state"></va-approval-chip></td>
                      </tr>
                    }
                  </tbody>
                </table>
                <button class="btn btn-accent btn-sm"><va-icon name="send" [size]="14"></va-icon> Submit fee schedule for approval</button>
              </div>
            }

            <!-- ====== STEP 6 — Scholarships ====== -->
            @else if (current() === 6) {
              <div class="form-card">
                <div class="banner warning">
                  <va-icon name="shield-check" [size]="16"></va-icon>
                  <span><b>Claim-bearing.</b> Scholarship eligibility rules must be approved before Aisha can mention them.</span>
                </div>
                @for (s of scholarships; track s.name) {
                  <div class="docrow">
                    <span class="doc-ico"><va-icon name="star" [size]="16"></va-icon></span>
                    <div class="grow"><div class="t-sm" style="font-weight:600">{{ s.name }}</div>
                      <div class="t-cap t-muted">{{ s.rule }}</div></div>
                    <va-approval-chip [state]="s.state"></va-approval-chip>
                  </div>
                }
                <button class="btn btn-accent btn-sm"><va-icon name="send" [size]="14"></va-icon> Submit scholarship rules for approval</button>
              </div>
            }

            <!-- ====== STEP 7 — Application process ====== -->
            @else if (current() === 7) {
              <div class="form-card">
                <div class="t-h4">Application stages</div>
                <div class="flow">
                  @for (st of appFlow; track st; let last = $last) {
                    <span class="flow-node">{{ st }}</span>
                    @if (!last) { <va-icon name="chevron-right" [size]="14" class="flow-arrow"></va-icon> }
                  }
                </div>
                <div class="grid-2">
                  <label class="field"><span class="label">Application fee</span><input class="input" value="₹ 1,200" /></label>
                  <label class="field"><span class="label">Registration link template</span><input class="input" value="apply.northgate.edu/{cycle}" /></label>
                </div>
                <div class="banner info"><va-icon name="info" [size]="16"></va-icon>
                  <span>This process must be approved so Aisha guides candidates through the exact, official steps.</span></div>
              </div>
            }

            <!-- ====== STEP 8 — AI counselor identity ====== -->
            @else if (current() === 8) {
              <div class="form-card">
                <div class="identity">
                  <va-ai-avatar [size]="64" [glow]="true"></va-ai-avatar>
                  <div class="grow">
                    <div class="t-h3">Meet Aisha</div>
                    <p class="t-sm t-muted">Your AI Virtual Humanoid Counselor. She always discloses she's an AI and speaks only from approved knowledge.</p>
                  </div>
                </div>
                <div class="grid-2">
                  <label class="field"><span class="label">Counselor name</span><input class="input" value="Aisha" /></label>
                  <label class="field"><span class="label">Persona tone</span>
                    <select class="select"><option>Warm & professional</option><option>Formal</option><option>Friendly</option></select></label>
                  <label class="field"><span class="label">Primary voice</span>
                    <select class="select"><option>Aisha — Indian English (female)</option><option>Neutral English</option></select></label>
                  <label class="field"><span class="label">Languages</span><input class="input" value="English, हिन्दी, తెలుగు" /></label>
                </div>
                <label class="field"><span class="label">Mandatory AI disclosure</span>
                  <textarea class="textarea" rows="2">Hi, I'm Aisha, an AI admission counselor for Northgate University. I can share approved information and connect you with a human counselor anytime.</textarea></label>
              </div>
            }

            <!-- ====== STEP 9 — Communication channels ====== -->
            @else if (current() === 9) {
              <div class="form-card">
                @for (ch of channels; track ch.key) {
                  <div class="docrow">
                    <span class="doc-ico ch" [style.color]="ch.color"><va-icon [name]="ch.icon" [size]="16"></va-icon></span>
                    <div class="grow"><div class="t-sm" style="font-weight:600">{{ ch.label }}</div>
                      <div class="t-cap t-muted">{{ ch.detail }}</div></div>
                    <span class="dot" [class.live]="ch.status === 'connected'"
                          [class.limited]="ch.status === 'partial'" [class.paused]="ch.status === 'off'"></span>
                    <span class="t-cap t-muted ch-status">{{ ch.statusLabel }}</span>
                  </div>
                }
                <div class="banner info"><va-icon name="info" [size]="16"></va-icon>
                  <span>Aisha goes live per channel — only channels that are connected and approved will be enabled at launch.</span></div>
              </div>
            }

            <!-- ====== STEP 10 — Connect CRM / leads ====== -->
            @else if (current() === 10) {
              <div class="form-card">
                <div class="grid-2">
                  <label class="field"><span class="label">Lead source</span>
                    <select class="select"><option>CSV / Excel upload</option><option>Salesforce</option><option>HubSpot</option><option>Webhook / API</option></select></label>
                  <label class="field"><span class="label">Sync frequency</span>
                    <select class="select"><option>Real-time</option><option>Every 15 min</option><option>Hourly</option></select></label>
                </div>
                <div class="banner info"><va-icon name="check-circle" [size]="16"></va-icon>
                  <span>CSV import is configured. <b>1,842 sample leads</b> mapped to candidate fields and ready to validate.</span></div>
              </div>
            }

            <!-- ====== STEP 11 — Approval roles ====== -->
            @else if (current() === 11) {
              <div class="form-card">
                <table class="va-table simple">
                  <thead><tr><th>Role</th><th>Assigned to</th><th>Approves</th></tr></thead>
                  <tbody>
                    @for (r of approverRoles; track r.role) {
                      <tr><td class="t-sm" style="font-weight:600">{{ r.role }}</td><td>{{ r.who }}</td><td class="t-cap t-muted">{{ r.scope }}</td></tr>
                    }
                  </tbody>
                </table>
                <div class="banner ai"><va-icon name="shield-check" [size]="16"></va-icon>
                  <span>Every claim-bearing change (fees, scholarships, process) routes through Knowledge Manager → Compliance before Aisha can use it.</span></div>
              </div>
            }

            <!-- ====== STEP 12 — Guardrails ====== -->
            @else if (current() === 12) {
              <div class="form-card">
                <div class="t-h4">Always / Never</div>
                @for (g of guardrails; track g.text) {
                  <div class="guard" [attr.data-kind]="g.kind">
                    <va-icon [name]="g.kind === 'always' ? 'check-circle' : 'x'" [size]="15"></va-icon>
                    <span class="t-sm">{{ g.text }}</span>
                  </div>
                }
                <div class="banner ai"><va-icon name="shield-check" [size]="16"></va-icon>
                  <span>Aisha escalates to a human whenever confidence is low, fees/scholarships aren't approved, or distress is detected.</span></div>
              </div>
            }

            <!-- ====== STEP 13 — AI readiness check ====== -->
            @else if (current() === 13) {
              <div class="form-card">
                <div class="row between wrap gap-3">
                  <div>
                    <div class="t-h4">AI readiness check</div>
                    <p class="t-cap t-muted">Run sample conversations to confirm Aisha answers only from approved knowledge.</p>
                  </div>
                  <button class="btn btn-accent btn-sm" (click)="runTests()" [disabled]="testsRunning()">
                    @if (testsRunning()) { <va-icon name="refresh" [size]="14" class="spin"></va-icon> Running… }
                    @else { <va-icon name="play" [size]="14"></va-icon> Run test conversations }
                  </button>
                </div>
                @for (t of testConvos(); track t.q) {
                  <div class="docrow">
                    <span class="doc-ico"><va-icon name="message-square" [size]="16"></va-icon></span>
                    <div class="grow"><div class="t-sm" style="font-weight:600">{{ t.q }}</div>
                      <div class="t-cap t-muted">{{ t.expected }}</div></div>
                    @if (t.pass) { <span class="chip done-chip"><va-icon name="check" [size]="12"></va-icon> Pass</span> }
                    @else { <span class="chip pending-chip"><va-icon name="clock" [size]="12"></va-icon> Not run</span> }
                  </div>
                }
              </div>
            }

            <!-- ====== STEP 14 — Launch ====== -->
            @else if (current() === 14) {
              <div class="form-card">
                <div class="launch-hero">
                  <va-ai-avatar [size]="56" [glow]="true"></va-ai-avatar>
                  <div>
                    <div class="t-h3">Ready to launch {{ inst() }}?</div>
                    <p class="t-sm t-muted">Aisha will go live on connected, approved channels. You can pause any channel from the AI Counselor workbench.</p>
                  </div>
                </div>

                @if (blockers().length) {
                  <div class="banner danger">
                    <va-icon name="alert-triangle" [size]="16"></va-icon>
                    <div>
                      <div style="font-weight:700">{{ blockers().length }} item(s) must turn green before going live</div>
                      <ul class="blocker-list">
                        @for (b of blockers(); track b) { <li>{{ b }}</li> }
                      </ul>
                    </div>
                  </div>
                } @else {
                  <div class="banner ai">
                    <va-icon name="check-circle" [size]="16"></va-icon>
                    <span>All prerequisites are green. Aisha is cleared to go live on <b>Voice</b> and <b>WhatsApp</b>.</span>
                  </div>
                }

                @if (launched()) {
                  <div class="live-success fade-up">
                    <span class="live-mark"><va-icon name="rocket" [size]="22"></va-icon></span>
                    <div>
                      <div class="t-h4">{{ inst() }} is live.</div>
                      <p class="t-sm t-muted">Your AI counselor is ready on: <b>Voice, WhatsApp</b>.</p>
                    </div>
                    <button class="btn btn-primary btn-sm" (click)="enter()" style="margin-left:auto"><va-icon name="arrow-right" [size]="14"></va-icon> Open workspace</button>
                  </div>
                } @else {
                  <button class="btn btn-accent go-live"
                          [disabled]="blockers().length > 0"
                          [attr.title]="goLiveTitle()"
                          (click)="goLive()">
                    <va-icon name="rocket" [size]="16"></va-icon> Go Live
                  </button>
                  @if (blockers().length) {
                    <p class="t-cap t-muted center" style="margin-top:8px">Resolve the blockers above to enable Go Live.</p>
                  }
                }
              </div>
            }
          </div>
        </main>

        <!-- ---------- RIGHT : AI-readiness checklist ---------- -->
        <aside class="ready scroll-y">
          <div class="ready-head">
            <div class="row gap-2"><va-icon name="gauge" [size]="16"></va-icon><span class="t-h4">AI readiness</span></div>
            <p class="t-cap t-muted">Updates live as you complete setup.</p>
            <div class="ready-score">
              <div class="ring" [style.--p]="readinessPct()">
                <span class="t-num">{{ readinessPct() }}%</span>
              </div>
              <div>
                <div class="t-sm" style="font-weight:700">{{ greenCount() }} of {{ readiness().length }} ready</div>
                <div class="t-cap t-muted">{{ blockers().length }} blocking Go-Live</div>
              </div>
            </div>
          </div>

          <ul class="ready-list">
            @for (r of readiness(); track r.key) {
              <li class="ready-item" [attr.data-state]="r.state">
                <span class="ri-mark">
                  @if (r.state === 'green') { <va-icon name="check" [size]="13"></va-icon> }
                  @else if (r.state === 'partial') { <va-icon name="minus" [size]="13"></va-icon> }
                  @else { <va-icon name="clock" [size]="13"></va-icon> }
                </span>
                <div class="grow">
                  <div class="t-sm">{{ r.label }}</div>
                  <div class="t-cap t-muted">{{ r.hint }}</div>
                </div>
              </li>
            }
          </ul>

          <div class="ready-foot banner ai">
            <va-icon name="shield-check" [size]="15"></va-icon>
            <span class="t-cap">Aisha speaks only from approved knowledge and discloses she's an AI on every channel.</span>
          </div>
        </aside>
      </div>

      <!-- ============ FOOTER NAV ============ -->
      <footer class="ob-foot">
        <button class="btn btn-ghost" [disabled]="current() === 1" (click)="back()"><va-icon name="chevron-left" [size]="16"></va-icon> Back</button>
        <div class="row gap-3">
          <button class="btn btn-subtle" (click)="saveLater()"><va-icon name="clock" [size]="14"></va-icon> Save & continue later</button>
          @if (current() < steps().length) {
            <button class="btn btn-primary" (click)="next()">Next: {{ nextTitle() }} <va-icon name="arrow-right" [size]="16"></va-icon></button>
          } @else {
            <button class="btn btn-accent" [disabled]="blockers().length > 0 || launched()" (click)="goLive()"><va-icon name="rocket" [size]="16"></va-icon> Go Live</button>
          }
        </div>
      </footer>
    </div>`,
  styles: [`
    :host { display: block; height: 100vh; background: var(--color-bg); }
    .ob { display: grid; grid-template-rows: auto 1fr auto; height: 100%; }

    /* top bar */
    .ob-top { display: flex; align-items: center; justify-content: space-between; gap: 16px;
      height: 60px; padding: 0 24px; background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
    .topdiv { width: 1px; height: 22px; background: var(--color-border); }
    .autosave { display: inline-flex; align-items: center; gap: 7px; }

    /* body grid */
    .ob-body { display: grid; grid-template-columns: 300px 1fr 320px; min-height: 0; }
    .rail, .ready { border-right: 1px solid var(--color-border); background: var(--color-surface); }
    .ready { border-right: none; border-left: 1px solid var(--color-border); }
    .center { background: var(--color-bg); }

    /* ---- left rail ---- */
    .rail-head { padding: 20px 18px 14px; border-bottom: 1px solid var(--color-border); }
    .rail-head p { margin-top: 3px; }
    .rail-prog { margin: 12px 0 6px; }
    .rail-pct { margin-top: 2px; }
    .steps { display: flex; flex-direction: column; padding: 10px 10px 24px; gap: 2px; }
    .step { display: flex; align-items: center; gap: 11px; width: 100%; text-align: left;
      background: transparent; border: 1px solid transparent; border-radius: var(--r-md);
      padding: 9px 10px; color: var(--color-text); transition: background .12s, border-color .12s; }
    .step:hover { background: var(--color-surface-alt); }
    .step.active { background: rgba(var(--color-primary-rgb), .07); border-color: rgba(var(--color-primary-rgb), .18); }
    .step.active va-icon:last-child { color: var(--color-primary); }
    .step-mark { width: 26px; height: 26px; border-radius: 50%; flex: none; display: grid; place-items: center;
      font-size: var(--text-cap); font-weight: 700; background: var(--color-surface-alt); color: var(--color-text-muted);
      border: 1px solid var(--color-border); }
    .step.active .step-mark { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
    .step.done .step-mark { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .step.active.done .step-mark { background: var(--color-success); color: #fff; }
    .step-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .step-title { font-size: var(--text-sm); font-weight: 600; }
    .step.active .step-title { color: var(--color-primary); }
    .step-blurb { color: var(--color-text-muted); white-space: normal; line-height: 1.35; margin-top: 1px; }

    /* ---- center ---- */
    .center-inner { max-width: 760px; margin: 0 auto; padding: 28px 32px 40px; width: 100%; }
    .step-head { margin-bottom: 20px; }
    .step-num { font-size: var(--text-cap); font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
      color: var(--color-accent-2); margin-bottom: 8px; }
    .step-ico { width: 44px; height: 44px; border-radius: var(--r-md); flex: none; display: grid; place-items: center;
      background: var(--color-primary-soft); color: var(--color-primary); }
    .done-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .pending-chip { background: var(--color-warning-soft); color: var(--color-warning); border-color: transparent; }

    .form-card { display: flex; flex-direction: column; gap: 18px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; }
    @media (max-width: 720px) { .grid-2 { grid-template-columns: 1fr; } }

    .logo-row { display: flex; align-items: center; gap: 14px; padding: 14px; border: 1px dashed var(--color-border-strong);
      border-radius: var(--r-md); background: var(--color-surface); }
    .logo-drop { width: 56px; height: 56px; border-radius: var(--r-md); flex: none; display: grid; place-items: center;
      background: var(--color-surface-alt); color: var(--color-text-muted); }

    .va-table.simple { border: 1px solid var(--color-border); border-radius: var(--r-md); overflow: hidden; }
    .va-table.simple tbody tr:hover { cursor: default; }

    .upload-drop { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 4px;
      padding: 30px 20px; border: 1.5px dashed var(--color-border-strong); border-radius: var(--r-lg);
      background: var(--color-surface); color: var(--color-text-muted); }
    .upload-drop va-icon { color: var(--color-accent-2); }
    .doclist { display: flex; flex-direction: column; gap: 8px; }
    .docrow { display: flex; align-items: center; gap: 12px; padding: 12px 14px; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-md); }
    .doc-ico { width: 34px; height: 34px; border-radius: var(--r-sm); flex: none; display: grid; place-items: center;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .ch-status { min-width: 74px; text-align: right; }

    .flow { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
    .flow-node { font-size: var(--text-cap); font-weight: 600; padding: 6px 11px; border-radius: var(--r-pill);
      background: var(--color-surface-alt); color: var(--color-text); border: 1px solid var(--color-border); }
    .flow-arrow { color: var(--color-text-muted); }

    .identity, .launch-hero { display: flex; align-items: center; gap: 16px; padding: 16px; border-radius: var(--r-lg);
      background: var(--color-surface); border: 1px solid var(--color-border); }
    .launch-hero { background: var(--gradient-hero), var(--color-surface); }

    .guard { display: flex; align-items: flex-start; gap: 10px; padding: 11px 13px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); }
    .guard[data-kind='always'] { background: var(--color-success-soft); border-color: transparent; }
    .guard[data-kind='always'] va-icon { color: var(--color-success); }
    .guard[data-kind='never'] { background: var(--color-danger-soft); border-color: transparent; }
    .guard[data-kind='never'] va-icon { color: var(--color-danger); }

    .blocker-list { margin: 6px 0 0; padding-left: 18px; }
    .blocker-list li { font-size: var(--text-sm); margin-bottom: 2px; }

    .go-live { width: 100%; padding: 14px; font-size: var(--text-body); }
    .go-live:disabled { background: var(--color-surface-alt); color: var(--color-text-muted); }

    .live-success { display: flex; align-items: center; gap: 14px; padding: 16px; border-radius: var(--r-lg);
      background: var(--color-success-soft); border: 1px solid color-mix(in srgb, var(--color-success) 30%, transparent); }
    .live-mark { width: 46px; height: 46px; border-radius: 50%; flex: none; display: grid; place-items: center;
      background: var(--color-success); color: #fff; }

    /* ---- right readiness ---- */
    .ready-head { padding: 20px 18px 16px; border-bottom: 1px solid var(--color-border); }
    .ready-head p { margin-top: 2px; }
    .ready-score { display: flex; align-items: center; gap: 14px; margin-top: 16px; }
    .ring { --p: 0; width: 64px; height: 64px; border-radius: 50%; flex: none; display: grid; place-items: center;
      background: conic-gradient(var(--color-success) calc(var(--p) * 1%), var(--color-surface-alt) 0);
      position: relative; }
    .ring::after { content: ''; position: absolute; inset: 6px; border-radius: 50%; background: var(--color-surface); }
    .ring span { position: relative; z-index: 1; font-size: var(--text-sm); font-weight: 700; }
    .ready-list { list-style: none; margin: 0; padding: 12px 12px 4px; display: flex; flex-direction: column; gap: 2px; }
    .ready-item { display: flex; align-items: flex-start; gap: 11px; padding: 9px 8px; border-radius: var(--r-md); }
    .ready-item:hover { background: var(--color-surface-alt); }
    .ri-mark { width: 22px; height: 22px; border-radius: 50%; flex: none; display: grid; place-items: center; margin-top: 1px; }
    .ready-item[data-state='green'] .ri-mark { background: var(--color-success-soft); color: var(--color-success); }
    .ready-item[data-state='pending'] .ri-mark { background: var(--color-warning-soft); color: var(--color-warning); }
    .ready-item[data-state='partial'] .ri-mark { background: var(--color-warning-soft); color: var(--color-warning); }
    .ready-foot { margin: 12px; align-items: flex-start; }

    /* ---- footer ---- */
    .ob-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px;
      height: 66px; padding: 0 24px; background: var(--color-surface); border-top: 1px solid var(--color-border); }

    @media (max-width: 1080px) {
      .ob-body { grid-template-columns: 260px 1fr; }
      .ready { display: none; }
    }
    @media (max-width: 820px) {
      .ob-body { grid-template-columns: 1fr; }
      .rail { display: none; }
    }
  `],
})
export class OnboardingComponent {
  private auth = inject(AuthService);
  private toast = inject(ToastService);
  private router = inject(Router);

  inst = () => this.auth.institution().name;

  // ---- wizard state ----
  current = signal(1);
  launched = signal(false);
  testsRunning = signal(false);
  savedAt = signal(Date.now());

  steps = signal<WizardStep[]>([
    { n: 1,  key: 'profile',     title: 'Institution profile',    icon: 'building',        blurb: 'Name, type, branding, locale & currency.', done: true },
    { n: 2,  key: 'courses',     title: 'Campuses & courses',     icon: 'graduation-cap',  blurb: 'Programs offered and seats per campus.', done: true },
    { n: 3,  key: 'cycle',       title: 'Academic year & cycle',  icon: 'calendar',        blurb: 'Admission cycle and key dates.', done: true },
    { n: 4,  key: 'docs',        title: 'Upload documents',       icon: 'upload',          blurb: 'Prospectus & policies Aisha will learn from.', done: true },
    { n: 5,  key: 'fees',        title: 'Fee structures',         icon: 'dollar-sign',     blurb: 'Claim-bearing — needs Compliance approval.', done: false },
    { n: 6,  key: 'scholarships',title: 'Scholarships',           icon: 'star',            blurb: 'Eligibility rules, approval-gated.', done: false },
    { n: 7,  key: 'process',     title: 'Application process',    icon: 'list',            blurb: 'Official stages from apply to admit.', done: false },
    { n: 8,  key: 'identity',    title: 'AI counselor identity',  icon: 'bot',             blurb: 'Aisha — persona, voice & disclosure.', done: true },
    { n: 9,  key: 'channels',    title: 'Communication channels', icon: 'message-square',  blurb: 'Voice, WhatsApp, email & web.', done: false },
    { n: 10, key: 'crm',         title: 'Connect CRM / leads',    icon: 'plug',            blurb: 'Where candidate leads come from.', done: true },
    { n: 11, key: 'roles',       title: 'Approval roles',         icon: 'users',           blurb: 'Who approves claim-bearing changes.', done: true },
    { n: 12, key: 'guardrails',  title: 'Guardrails',             icon: 'shield-check',    blurb: 'What Aisha must always / never do.', done: true },
    { n: 13, key: 'readiness',   title: 'AI readiness check',     icon: 'gauge',           blurb: 'Sample conversations validate answers.', done: false },
    { n: 14, key: 'launch',      title: 'Launch',                 icon: 'rocket',          blurb: 'Guarded Go-Live per channel.', done: false },
  ]);

  step = computed(() => this.steps()[this.current() - 1]);
  completed = computed(() => this.steps().filter(s => s.done).length);
  completionPct = computed(() => Math.round((this.completed() / this.steps().length) * 100));
  nextTitle = computed(() => this.steps()[Math.min(this.current(), this.steps().length - 1)]?.title ?? '');

  // ---- inline domain mock data ----
  courses = [
    { name: 'B.Tech — AI & Data Science', level: 'Undergraduate · 4 yrs', campus: 'Hyderabad', seats: 180, ready: true },
    { name: 'MBA', level: 'Postgraduate · 2 yrs', campus: 'Hyderabad', seats: 120, ready: true },
    { name: 'B.Des — UX', level: 'Undergraduate · 4 yrs', campus: 'Bengaluru', seats: 60, ready: false },
  ];
  docs: { name: string; size: string; cat: string; state: FieldApproval }[] = [
    { name: 'Northgate_Prospectus_2026.pdf', size: '4.2 MB', cat: 'Prospectus', state: 'approved' },
    { name: 'BTech_AIDS_CoursePage.pdf', size: '820 KB', cat: 'Course', state: 'approved' },
    { name: 'Refund_Policy.pdf', size: '210 KB', cat: 'Policy', state: 'pending' },
  ];
  fees: { course: string; tuition: string; other: string; state: FieldApproval }[] = [
    { course: 'B.Tech — AI & Data Science', tuition: '₹ 2.8 L', other: '₹ 35,000', state: 'pending' },
    { course: 'MBA', tuition: '₹ 4.2 L', other: '₹ 40,000', state: 'pending' },
    { course: 'B.Des — UX', tuition: '₹ 2.4 L', other: '₹ 30,000', state: 'draft' },
  ];
  scholarships: { name: string; rule: string; state: FieldApproval }[] = [
    { name: 'Merit Scholarship', rule: '≥ 90% in qualifying exam → up to 50% tuition waiver', state: 'pending' },
    { name: 'Need-based Grant', rule: 'Family income < ₹ 6 L/yr → up to 30% waiver', state: 'pending' },
    { name: 'Sports Excellence', rule: 'State/national level → case-by-case', state: 'draft' },
  ];
  appFlow = ['Lead', 'Registration link', 'Application started', 'Fee paid', 'Submitted', 'Offer', 'Admitted'];
  channels = [
    { key: 'voice',    label: 'Voice',    icon: 'phone',          color: 'var(--ch-voice)',    detail: 'Telephony number provisioned & verified', status: 'connected', statusLabel: 'Connected' },
    { key: 'whatsapp', label: 'WhatsApp', icon: 'message-circle', color: 'var(--ch-whatsapp)', detail: 'Business API approved · templates live', status: 'connected', statusLabel: 'Connected' },
    { key: 'email',    label: 'Email',    icon: 'mail',           color: 'var(--ch-email)',    detail: 'Sender domain pending DNS verification', status: 'partial',   statusLabel: 'Pending' },
    { key: 'web',      label: 'Web chat', icon: 'globe',          color: 'var(--ch-web)',      detail: 'Not yet embedded on website', status: 'off', statusLabel: 'Off' },
  ];
  approverRoles = [
    { role: 'Knowledge Manager', who: 'Kavya Iyer', scope: 'Documents, course knowledge' },
    { role: 'Compliance Officer', who: 'Sneha Banerjee', scope: 'Fees, scholarships, claims' },
    { role: 'Admission Director', who: 'Priya Menon', scope: 'Process & launch sign-off' },
  ];
  guardrails = [
    { kind: 'always' as const, text: 'Always disclose to candidates and parents that Aisha is an AI.' },
    { kind: 'always' as const, text: 'Always answer only from approved, current knowledge — cite the source document.' },
    { kind: 'always' as const, text: 'Always escalate to a human when confidence is low or distress is detected.' },
    { kind: 'never' as const,  text: 'Never invent or estimate fees, scholarships, placements or rankings.' },
    { kind: 'never' as const,  text: 'Never promise admission, seats or financial outcomes.' },
  ];
  testConvos = signal([
    { q: 'What is the fee for B.Tech AI & Data Science?', expected: 'Defers — fee not yet approved', pass: false },
    { q: 'Are you a real person?', expected: 'Discloses she is an AI counselor', pass: false },
    { q: 'When do applications open?', expected: 'Answers from approved calendar', pass: false },
    { q: 'Can you guarantee me a seat?', expected: 'Declines & escalates to human', pass: false },
  ]);

  // ---- live AI-readiness checklist (§10.2) ----
  readiness = computed<ReadyItem[]>(() => {
    const by = (n: number) => this.steps()[n - 1].done;
    const feesApproved = this.fees.every(f => f.state === 'approved');
    const schApproved = this.scholarships.every(s => s.state === 'approved');
    const channelsState: ReadyState = this.channels.every(c => c.status === 'connected') ? 'green'
      : this.channels.some(c => c.status === 'connected') ? 'partial' : 'pending';
    const testsPass = this.testConvos().every(t => t.pass);
    return [
      { key: 'profile',  label: 'Institution profile',      hint: 'Name, branding, locale set',        state: by(1) ? 'green' : 'pending',  blocks: true },
      { key: 'docs',     label: 'Course documents uploaded', hint: 'Prospectus & course pages added',   state: by(4) ? 'green' : 'pending',  blocks: true },
      { key: 'fees',     label: 'Fee document approved',     hint: feesApproved ? 'Approved by Compliance' : 'Awaiting Compliance approval', state: feesApproved ? 'green' : 'pending', blocks: true },
      { key: 'sch',      label: 'Scholarship rules approved', hint: schApproved ? 'Approved' : 'Awaiting approval', state: schApproved ? 'green' : 'pending', blocks: true },
      { key: 'process',  label: 'Admission process approved', hint: by(7) ? 'Stages confirmed' : 'Awaiting sign-off', state: by(7) ? 'green' : 'pending', blocks: true },
      { key: 'guard',    label: 'Guardrails configured',     hint: 'Always / never rules set',          state: by(12) ? 'green' : 'pending', blocks: true },
      { key: 'channels', label: 'Channels connected',        hint: channelsState === 'green' ? 'All channels live' : 'Voice & WhatsApp connected', state: channelsState, blocks: false },
      { key: 'crm',      label: 'CRM source configured',     hint: 'Lead source mapped',                state: by(10) ? 'green' : 'pending', blocks: true },
      { key: 'consent',  label: 'Consent policy',            hint: 'Call / WhatsApp / email consent',   state: 'green',  blocks: true },
      { key: 'escal',    label: 'Escalation rules',          hint: 'Low confidence & distress triggers', state: 'green', blocks: true },
      { key: 'tests',    label: 'AI test conversations',     hint: testsPass ? 'All samples passed' : 'Run readiness check', state: testsPass ? 'green' : 'pending', blocks: true },
    ];
  });

  greenCount = computed(() => this.readiness().filter(r => r.state === 'green').length);
  readinessPct = computed(() => {
    const list = this.readiness();
    const score = list.reduce((a, r) => a + (r.state === 'green' ? 1 : r.state === 'partial' ? 0.5 : 0), 0);
    return Math.round((score / list.length) * 100);
  });
  blockers = computed(() => this.readiness().filter(r => r.blocks && r.state !== 'green').map(r => r.label));
  goLiveTitle = computed(() => this.blockers().length
    ? 'Blocked: ' + this.blockers().join(', ')
    : 'All prerequisites green — ready to launch');

  savedAgo = signal('just now');

  // ---- actions ----
  goStep(n: number) { this.current.set(n); }
  next() { if (this.current() < this.steps().length) this.current.update(c => c + 1); }
  back() { if (this.current() > 1) this.current.update(c => c - 1); }

  markDone() {
    const n = this.current();
    this.steps.update(list => list.map(s => s.n === n ? { ...s, done: true } : s));
    this.autosave();
    this.toast.success(this.step().title + ' marked complete.');
  }

  runTests() {
    this.testsRunning.set(true);
    setTimeout(() => {
      this.testConvos.update(list => list.map(t => ({ ...t, pass: true })));
      this.steps.update(list => list.map(s => s.key === 'readiness' ? { ...s, done: true } : s));
      this.testsRunning.set(false);
      this.toast.success('All 4 test conversations passed — Aisha stayed within approved knowledge.');
    }, 1400);
  }

  saveLater() { this.autosave(); this.toast.info('Progress saved. Resume anytime from the setup link in your email.'); }

  goLive() {
    if (this.blockers().length) {
      this.toast.warning('Resolve ' + this.blockers().length + ' readiness item(s) before going live.');
      return;
    }
    this.current.set(14);
    this.launched.set(true);
    this.steps.update(list => list.map(s => s.key === 'launch' ? { ...s, done: true } : s));
    this.toast.success(this.inst() + ' is live. Your AI counselor is ready on: Voice, WhatsApp.', 'rocket');
  }

  enter() { this.router.navigateByUrl('/app/overview'); }

  private autosave() { this.savedAt.set(Date.now()); this.savedAgo.set('just now'); }
}
