import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';

type RuleState = 'approved' | 'pending' | 'draft';

interface PolicyRule {
  id: string;
  text: string;
  state: RuleState;
  violations: number;
}

interface PolicyCategory {
  key: string;
  label: string;
  icon: string;
  /** Critical, non-negotiable guardrail (distress / self-harm) — visually emphasised. */
  critical?: boolean;
  /** Number of open change requests routed through approval for this category. */
  pending: number;
  always: PolicyRule[];
  never: PolicyRule[];
}

interface SimResult {
  ok: boolean;
  verdict: string;
  detail: string;
  category?: string;
  stage: 'pre-generation' | 'post-generation' | 'none';
}

@Component({
  selector: 'va-guardrails',
  standalone: true,
  imports: [IconComponent, ApprovalChipComponent, PageHeaderComponent, SectionCardComponent, AiAvatarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page page-grid">
      <va-page-header
        title="Guardrails & Policy Control"
        [subtitle]="'What ' + counselor.activeMeta().name + ' (' + counselor.activeMeta().title + ') must always — and must never — do. Every change routes through approval.'">
        <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
        <span class="chip guard"><va-icon name="shield-check" [size]="13"></va-icon> Approved-knowledge-only</span>
        <span class="chip ai-chip"><va-icon name="bot" [size]="13"></va-icon> {{ counselor.activeMeta().name }} · Fall 2026</span>
        <button class="btn btn-ghost btn-sm" (click)="exportPolicy()">
          <va-icon name="download" [size]="15"></va-icon><span class="hide-xs">Export policy</span>
        </button>
      </va-page-header>

      <!-- Governance banner -->
      <div class="banner ai gr-banner">
        <va-icon name="brain" [size]="18"></va-icon>
        <span>
          These guardrails are enforced <b>twice</b> — before {{ counselor.activeMeta().name }} drafts a reply (pre-generation) and again before it is
          sent (post-generation). {{ counselor.activeMeta().name }} always discloses it is an AI, speaks only from <b>{{ auth.institution().name }}</b>'s
          approved knowledge, and escalates to a human counsellor when unsure. No edit goes live without approval.
        </span>
      </div>

      <!-- Distress non-negotiable strip -->
      <div class="banner danger ng-banner">
        <va-icon name="alert-triangle" [size]="18"></va-icon>
        <div class="ng-text">
          <b>Non-negotiable:</b> if a {{ career() ? 'student' : 'candidate' }} or parent expresses <b>distress or self-harm</b>, {{ counselor.activeMeta().name }} stops counselling,
          shows crisis-helpline resources, and hands off to a human immediately. This guardrail is locked and cannot be
          weakened by any role.
          <span class="chip lock-chip"><va-icon name="lock" [size]="11"></va-icon> Locked by Compliance</span>
        </div>
      </div>

      <!-- Workspace: category list + detail -->
      <div class="gr-workspace">
        <!-- LEFT: category list -->
        <aside class="cat-rail card">
          <header class="cat-head">
            <span class="t-h4">Policy categories</span>
            <span class="t-cap t-muted">{{ categories().length }} areas · <b class="t-num">{{ totalPending() }}</b> in approval</span>
          </header>
          <div class="cat-list scroll-y">
            @for (c of categories(); track c.key) {
              <button type="button" class="cat-item"
                      [class.selected]="c.key === selectedKey()"
                      [class.critical]="c.critical"
                      (click)="select(c.key)">
                <span class="cat-ic" [class.critical]="c.critical"><va-icon [name]="c.icon" [size]="16"></va-icon></span>
                <span class="cat-l">
                  <span class="cat-name truncate">{{ c.label }}</span>
                  <span class="t-cap t-muted">{{ c.always.length }} always · {{ c.never.length }} never</span>
                </span>
                @if (c.critical) {
                  <va-icon name="lock" [size]="13" class="cat-lock"></va-icon>
                } @else if (c.pending > 0) {
                  <span class="cat-badge t-num">{{ c.pending }}</span>
                } @else {
                  <va-icon name="check" [size]="14" class="cat-ok"></va-icon>
                }
              </button>
            }
          </div>
        </aside>

        <!-- RIGHT: selected category detail -->
        <section class="cat-detail">
          @if (selected(); as c) {
            <!-- Category header -->
            <div class="card cd-head" [class.critical]="c.critical">
              <div class="row gap-3">
                <span class="cd-ic" [class.critical]="c.critical"><va-icon [name]="c.icon" [size]="20"></va-icon></span>
                <div class="stack">
                  <div class="row gap-2 wrap">
                    <span class="t-h3">{{ c.label }}</span>
                    @if (c.critical) { <span class="chip crit-chip"><va-icon name="lock" [size]="11"></va-icon> Non-negotiable</span> }
                  </div>
                  <span class="t-sm t-muted">{{ c.always.length + c.never.length }} rules govern how {{ counselor.activeMeta().name }} handles {{ c.label.toLowerCase() }}.</span>
                </div>
              </div>
              <div class="cd-stats">
                <div class="tile">
                  <span class="tv" [class.bad]="catViolations(c) > 0">{{ catViolations(c) }}</span>
                  <span class="tl">violations (30d)</span>
                </div>
                <div class="tile">
                  <span class="tv">{{ c.pending }}</span>
                  <span class="tl">in approval</span>
                </div>
              </div>
            </div>

            <!-- Two-column rules -->
            <div class="rules-grid">
              <!-- ALWAYS -->
              <va-section-card title="The AI must ALWAYS" hint="Mandatory behaviours" [flush]="true">
                <span actions class="leg leg-ok"><va-icon name="check-circle" [size]="13"></va-icon> {{ c.always.length }}</span>
                <div class="rule-list">
                  @for (r of c.always; track r.id) {
                    <div class="rule always">
                      <span class="r-mark ok"><va-icon name="check" [size]="13"></va-icon></span>
                      <div class="r-body">
                        <p class="r-text">{{ r.text }}</p>
                        <div class="r-meta">
                          <va-approval-chip [state]="r.state"></va-approval-chip>
                          <span class="viol" [class.bad]="r.violations > 0">
                            <va-icon name="flag" [size]="11"></va-icon> violations: <b class="t-num">{{ r.violations }}</b>
                          </span>
                        </div>
                      </div>
                      <button class="btn btn-ghost btn-sm r-edit" (click)="editRule(r)" [disabled]="c.critical">
                        <va-icon name="edit" [size]="13"></va-icon> Edit
                      </button>
                    </div>
                  } @empty {
                    <p class="t-sm t-muted center pad">No mandatory rules defined yet.</p>
                  }
                </div>
              </va-section-card>

              <!-- NEVER -->
              <va-section-card title="The AI must NEVER" hint="Hard blocks · enforced pre & post" [flush]="true">
                <span actions class="leg leg-no"><va-icon name="x" [size]="13"></va-icon> {{ c.never.length }}</span>
                <div class="rule-list">
                  @for (r of c.never; track r.id) {
                    <div class="rule never">
                      <span class="r-mark no"><va-icon name="x" [size]="13"></va-icon></span>
                      <div class="r-body">
                        <p class="r-text">{{ r.text }}</p>
                        <div class="r-meta">
                          <va-approval-chip [state]="r.state"></va-approval-chip>
                          <span class="viol" [class.bad]="r.violations > 0">
                            <va-icon name="flag" [size]="11"></va-icon> violations: <b class="t-num">{{ r.violations }}</b>
                          </span>
                        </div>
                      </div>
                      <button class="btn btn-ghost btn-sm r-edit" (click)="editRule(r)" [disabled]="c.critical">
                        <va-icon name="edit" [size]="13"></va-icon> Edit
                      </button>
                    </div>
                  } @empty {
                    <p class="t-sm t-muted center pad">No prohibitions defined yet.</p>
                  }
                </div>
              </va-section-card>
            </div>
          }

          <!-- Mandatory disclaimers manager -->
          <va-section-card title="Mandatory disclaimers" hint="Appended automatically to claim-bearing replies">
            <span actions class="chip ai-chip"><va-icon name="shield-check" [size]="12"></va-icon> Always shown</span>
            <div class="chip-mgr">
              <div class="chip-wrap">
                @for (d of disclaimers(); track d) {
                  <span class="mgr-chip disc">
                    <va-icon name="info" [size]="12"></va-icon>
                    <span class="truncate">{{ d }}</span>
                    <button class="x" (click)="removeDisclaimer(d)" title="Remove (routes for approval)"><va-icon name="x" [size]="11"></va-icon></button>
                  </span>
                } @empty {
                  <span class="t-sm t-muted">No disclaimers configured.</span>
                }
              </div>
              <form class="chip-add" (submit)="addDisclaimer($event)">
                <input class="input" [value]="newDisclaimer()" (input)="newDisclaimer.set(asValue($event))"
                       placeholder="Add a mandatory disclaimer…" maxlength="120" />
                <button type="submit" class="btn btn-subtle btn-sm" [disabled]="!newDisclaimer().trim()">
                  <va-icon name="plus" [size]="14"></va-icon> Add
                </button>
              </form>
            </div>
          </va-section-card>

          <!-- Do-not-say phrases -->
          <va-section-card title="Do-not-say phrases" hint="Blocked verbatim and as paraphrase across all channels">
            <span actions class="chip danger-chip"><va-icon name="alert-circle" [size]="12"></va-icon> {{ doNotSay().length }} blocked</span>
            <div class="chip-mgr">
              <div class="chip-wrap">
                @for (p of doNotSay(); track p) {
                  <span class="mgr-chip block">
                    <va-icon name="x" [size]="12"></va-icon>
                    <span class="truncate">{{ p }}</span>
                    <button class="x" (click)="removePhrase(p)" title="Remove (routes for approval)"><va-icon name="x" [size]="11"></va-icon></button>
                  </span>
                } @empty {
                  <span class="t-sm t-muted">No blocked phrases configured.</span>
                }
              </div>
              <form class="chip-add" (submit)="addPhrase($event)">
                <input class="input" [value]="newPhrase()" (input)="newPhrase.set(asValue($event))"
                       [attr.placeholder]="'Add a phrase ' + counselor.activeMeta().name + ' must never say…'" maxlength="80" />
                <button type="submit" class="btn btn-subtle btn-sm" [disabled]="!newPhrase().trim()">
                  <va-icon name="plus" [size]="14"></va-icon> Block
                </button>
              </form>
            </div>
          </va-section-card>

          <!-- Simulation panel -->
          <va-section-card title="Simulate against a sample conversation" hint="Dry-run a reply through pre & post-generation enforcement">
            <span actions class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> Sandbox</span>
            <div class="sim">
              <div class="sim-input">
                <textarea class="textarea" rows="3" [value]="sample()" (input)="sample.set(asValue($event))"
                          [attr.placeholder]="'Paste a draft reply ' + counselor.activeMeta().name + ' might send to a ' + (career() ? 'student' : 'candidate') + ' or parent…'"></textarea>
                <div class="sim-actions">
                  <div class="sim-samples">
                    <span class="t-cap t-muted">Try:</span>
                    @for (s of presets(); track s.label) {
                      <button class="btn btn-ghost btn-sm" (click)="loadSample(s.text)">{{ s.label }}</button>
                    }
                  </div>
                  <button class="btn btn-accent" (click)="runSim()" [disabled]="!sample().trim()">
                    <va-icon name="play" [size]="15"></va-icon> Run simulation
                  </button>
                </div>
              </div>

              @if (result(); as r) {
                <div class="sim-result" [class.ok]="r.ok" [class.blocked]="!r.ok">
                  <span class="sr-ic"><va-icon [name]="r.ok ? 'check-circle' : 'alert-triangle'" [size]="20"></va-icon></span>
                  <div class="sr-body">
                    <div class="sr-verdict">{{ r.ok ? '✓ ' : '⚠ ' }}{{ r.verdict }}</div>
                    <p class="t-sm sr-detail">{{ r.detail }}</p>
                    <div class="sr-meta">
                      @if (r.category) { <span class="chip">{{ r.category }}</span> }
                      @if (r.stage !== 'none') {
                        <span class="chip stage-chip"><va-icon name="shield" [size]="11"></va-icon> {{ r.stage }} enforcement</span>
                      }
                      <span class="chip"><va-icon name="bot" [size]="11"></va-icon> {{ counselor.activeMeta().name }} · sandbox · not sent</span>
                    </div>
                  </div>
                </div>
              } @else {
                <div class="sim-empty">
                  <va-icon name="shield-check" [size]="18"></va-icon>
                  <span class="t-sm t-muted">Run a draft to see whether it passes {{ counselor.activeMeta().name }}'s guardrails. Nothing here is sent to a real {{ career() ? 'student' : 'candidate' }}.</span>
                </div>
              }
            </div>
          </va-section-card>
        </section>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .hide-xs { display: inline; }

    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    .chip.guard { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .chip.ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .chip.danger-chip { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }

    .gr-banner { align-items: center; }
    .gr-banner span { flex: 1; }
    .gr-banner va-icon { color: var(--color-accent-2); flex: none; }

    .ng-banner { align-items: flex-start; }
    .ng-banner va-icon { color: var(--color-danger); flex: none; margin-top: 1px; }
    .ng-text { flex: 1; font-size: var(--text-sm); line-height: 1.55; }
    .lock-chip { background: var(--color-danger); color: #fff; border-color: transparent; margin-left: 8px; vertical-align: middle; }

    /* Workspace */
    .gr-workspace { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 18px; align-items: start; }

    /* Category rail */
    .cat-rail { padding: 0; overflow: hidden; position: sticky; top: 0; }
    .cat-head { padding: 16px 18px; border-bottom: 1px solid var(--color-border); display: flex; flex-direction: column; gap: 2px; }
    .cat-list { padding: 8px; display: flex; flex-direction: column; gap: 2px; max-height: calc(100vh - 180px); }
    .cat-item { display: flex; align-items: center; gap: 11px; padding: 9px 10px; border-radius: var(--r-md);
      border: 1px solid transparent; background: transparent; text-align: left; transition: background .12s, border-color .12s; }
    .cat-item:hover { background: var(--color-surface-alt); }
    .cat-item.selected { background: rgba(var(--color-primary-rgb), .08); border-color: rgba(var(--color-primary-rgb), .2); }
    .cat-item.critical.selected { background: var(--color-danger-soft); border-color: color-mix(in srgb, var(--color-danger) 40%, transparent); }
    .cat-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .cat-item.selected .cat-ic { background: rgba(var(--color-primary-rgb), .12); color: var(--color-primary); }
    .cat-ic.critical { background: var(--color-danger-soft); color: var(--color-danger); }
    .cat-l { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .cat-name { font-size: var(--text-sm); font-weight: 600; }
    .cat-badge { font-size: 11px; font-weight: 700; min-width: 20px; height: 20px; padding: 0 6px; border-radius: var(--r-pill);
      display: grid; place-items: center; background: var(--color-warning-soft); color: var(--color-warning); flex: none; }
    .cat-ok { color: var(--color-success); flex: none; opacity: .7; }
    .cat-lock { color: var(--color-danger); flex: none; }

    /* Detail column */
    .cat-detail { display: flex; flex-direction: column; gap: 18px; min-width: 0; }

    .cd-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .cd-head.critical { border-color: color-mix(in srgb, var(--color-danger) 35%, var(--color-border));
      background: linear-gradient(0deg, var(--color-surface), var(--color-surface)), var(--color-danger-soft); }
    .cd-ic { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-primary); }
    .cd-ic.critical { background: var(--color-danger-soft); color: var(--color-danger); }
    .crit-chip { background: var(--color-danger); color: #fff; border-color: transparent; }
    .cd-stats { display: flex; gap: 10px; }
    .cd-stats .tile { min-width: 110px; }
    .tile .tv.bad { color: var(--color-danger); }

    /* Rules */
    .rules-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    .leg { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 700; }
    .leg-ok { color: var(--color-success); }
    .leg-no { color: var(--color-danger); }
    .rule-list { display: flex; flex-direction: column; }
    .rule { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--color-border); }
    .rule:last-child { border-bottom: none; }
    .rule:hover { background: var(--color-surface-2); }
    .r-mark { width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center; flex: none; margin-top: 1px; }
    .r-mark.ok { background: var(--color-success-soft); color: var(--color-success); }
    .r-mark.no { background: var(--color-danger-soft); color: var(--color-danger); }
    .r-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
    .r-text { font-size: var(--text-sm); line-height: 1.45; }
    .r-meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .viol { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); color: var(--color-text-muted); }
    .viol.bad { color: var(--color-danger); }
    .viol va-icon { opacity: .7; }
    .r-edit { flex: none; }

    /* Chip managers */
    .chip-mgr { display: flex; flex-direction: column; gap: 14px; }
    .chip-wrap { display: flex; flex-wrap: wrap; gap: 8px; }
    .mgr-chip { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; font-size: var(--text-cap); font-weight: 600;
      padding: 6px 8px 6px 10px; border-radius: var(--r-pill); border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .mgr-chip .truncate { max-width: 360px; }
    .mgr-chip.disc { border-color: rgba(var(--color-accent-rgb), .3); color: var(--color-text); }
    .mgr-chip.disc va-icon { color: var(--color-accent); }
    .mgr-chip.block { background: var(--color-danger-soft); border-color: color-mix(in srgb, var(--color-danger) 30%, transparent); color: var(--color-danger); }
    .mgr-chip .x { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; border: none;
      background: transparent; color: inherit; opacity: .65; padding: 0; }
    .mgr-chip .x:hover { background: rgba(2,6,23,.08); opacity: 1; }
    .chip-add { display: flex; gap: 8px; align-items: center; }
    .chip-add .input { max-width: 420px; }

    /* Simulation */
    .sim { display: flex; flex-direction: column; gap: 16px; }
    .sim-input { display: flex; flex-direction: column; gap: 10px; }
    .sim-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .sim-samples { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .sim-result { display: flex; gap: 14px; padding: 16px; border-radius: var(--r-md); border: 1px solid var(--color-border); }
    .sim-result.ok { background: var(--color-success-soft); border-color: color-mix(in srgb, var(--color-success) 35%, transparent); }
    .sim-result.blocked { background: var(--color-danger-soft); border-color: color-mix(in srgb, var(--color-danger) 40%, transparent); }
    .sr-ic { flex: none; }
    .sim-result.ok .sr-ic { color: var(--color-success); }
    .sim-result.blocked .sr-ic { color: var(--color-danger); }
    .sr-body { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
    .sr-verdict { font-size: var(--text-h4); font-weight: 700; }
    .sim-result.ok .sr-verdict { color: var(--color-success); }
    .sim-result.blocked .sr-verdict { color: var(--color-danger); }
    .sr-detail { line-height: 1.5; }
    .sr-meta { display: flex; gap: 8px; flex-wrap: wrap; }
    .stage-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; text-transform: capitalize; }
    .sim-empty { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-radius: var(--r-md);
      border: 1px dashed var(--color-border-strong); background: var(--color-surface-2); }
    .sim-empty va-icon { color: var(--color-accent-2); flex: none; }

    .center { text-align: center; } .pad { padding: 16px; }

    @media (max-width: 1100px) {
      .gr-workspace { grid-template-columns: 1fr; }
      .cat-rail { position: static; }
      .cat-list { max-height: none; flex-direction: row; flex-wrap: wrap; }
      .cat-item { flex: 1 1 220px; }
      .rules-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .hide-xs { display: none; }
      .mgr-chip .truncate { max-width: 200px; }
    }
  `],
})
export class GuardrailsComponent {
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');
  private toast = inject(ToastService);

  selectedKey = signal<string>('institution-identity');
  /** Active rule set follows the focused counselor (Aisha=admission, Vera=career). */
  categories = computed<PolicyCategory[]>(() => this.career() ? this.careerCategories : this.admissionCategories);
  selected = computed(() => this.categories().find(c => c.key === this.selectedKey()));

  // Disclaimers / blocked phrases are editable per counselor; pick the active set.
  private admissionDisclaimers = signal<string[]>([
    'I’m Aisha, Northgate University’s AI admission counsellor — not a human.',
    'Fees, scholarships and placement figures are indicative and subject to official confirmation.',
    'Admission is never guaranteed; final decisions rest with the admissions committee.',
    'For anything I’m unsure about, I’ll connect you with a human counsellor.',
  ]);
  private careerDisclaimers = signal<string[]>([
    'I’m Vera, Northgate University’s AI career counsellor — not a human.',
    'Aptitude and assessment results are indicative guidance, not a deterministic verdict.',
    'No career pathway guarantees a job, salary or placement — outcomes depend on many factors.',
    'For anything I’m unsure about, I’ll connect you with a human mentor or counsellor.',
  ]);
  disclaimers = computed<string[]>(() => this.career() ? this.careerDisclaimers() : this.admissionDisclaimers());
  newDisclaimer = signal('');

  private admissionDoNotSay = signal<string[]>([
    'guaranteed admission', 'assured placement', '100% job guarantee',
    'best university in India', 'you will definitely get a scholarship',
    'free education', 'no entrance exam needed',
  ]);
  private careerDoNotSay = signal<string[]>([
    'guaranteed job', 'assured placement', 'you will definitely earn',
    'guaranteed salary', 'you are unfit for this career',
    'this aptitude score means you cannot', 'best career for you, no doubt',
  ]);
  doNotSay = computed<string[]>(() => this.career() ? this.careerDoNotSay() : this.admissionDoNotSay());
  newPhrase = signal('');

  sample = signal('');
  result = signal<SimResult | null>(null);

  presets = computed(() => this.career()
    ? [
      { label: 'Clean reply', text: 'Hi! I’m Vera, Northgate’s AI career counsellor. Based on your interests, a Data Analyst pathway could be a good fit to explore. I can share an approved skill plan and connect you with a human mentor.' },
      { label: 'Job guarantee', text: 'Follow this pathway and you are guaranteed a high-paying job and a salary of ₹18 LPA within a year.' },
      { label: 'Unfit label', text: 'Your aptitude score is low, so you are simply unfit for any engineering or data career.' },
      { label: 'Distress', text: 'Honestly I feel hopeless about my future and sometimes I don’t want to be here anymore.' },
    ]
    : [
      { label: 'Clean reply', text: 'Hi! I’m Aisha, Northgate’s AI admission counsellor. The B.Tech AI & Data Science programme runs for 4 years. I can share the approved fee structure and connect you with a human counsellor for anything specific.' },
      { label: 'Job guarantee', text: 'Don’t worry — once you join our B.Tech programme you are guaranteed a placement with a top company and an assured salary package.' },
      { label: 'Invented scholarship', text: 'You will definitely get a 50% scholarship, and the application fee is fully waived for you.' },
      { label: 'Distress', text: 'Honestly I feel hopeless about my future and sometimes I don’t want to be here anymore.' },
    ]);

  private admissionCategories: PolicyCategory[] = [
    {
      key: 'institution-identity', label: 'Institution identity', icon: 'building', pending: 0,
      always: [
        { id: 'ii-a1', text: 'Identify as Aisha, the AI admission counsellor for Northgate University, in the first message.', state: 'approved', violations: 0 },
        { id: 'ii-a2', text: 'Represent only Northgate University and its approved campuses, courses and cycle (Fall 2026).', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'ii-n1', text: 'Claim to be a human, a named staff member, or a counsellor from another institution.', state: 'approved', violations: 1 },
        { id: 'ii-n2', text: 'Compare Northgate negatively or positively against named competitor universities.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'course-claims', label: 'Course claims', icon: 'graduation-cap', pending: 1,
      always: [
        { id: 'cc-a1', text: 'Describe courses, duration and eligibility exactly as stated in approved KMS documents.', state: 'approved', violations: 0 },
        { id: 'cc-a2', text: 'Cite the source document and academic year when sharing curriculum details.', state: 'pending', violations: 0 },
      ],
      never: [
        { id: 'cc-n1', text: 'Invent specialisations, accreditations, rankings or curriculum that are not in approved knowledge.', state: 'approved', violations: 2 },
        { id: 'cc-n2', text: 'State that a course is “the best” or rank it without an approved, sourced claim.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'fees', label: 'Fees', icon: 'dollar-sign', pending: 2,
      always: [
        { id: 'fe-a1', text: 'Quote tuition and fees only from the approved, in-effect fee structure for the cycle.', state: 'approved', violations: 0 },
        { id: 'fe-a2', text: 'Add the indicative-fees disclaimer and direct payment queries to official channels.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'fe-n1', text: 'Invent, estimate, negotiate or discount any fee amount.', state: 'approved', violations: 3 },
        { id: 'fe-n2', text: 'Promise a fee freeze, instalment plan or refund not present in approved policy.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'scholarships', label: 'Scholarships', icon: 'star', pending: 0,
      always: [
        { id: 'sc-a1', text: 'List only scholarships and eligibility criteria published in approved knowledge.', state: 'approved', violations: 0 },
        { id: 'sc-a2', text: 'Frame eligibility as conditional and route the final decision to the scholarship committee.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'sc-n1', text: 'Promise, guarantee or pre-approve any scholarship, waiver or award amount.', state: 'approved', violations: 4 },
        { id: 'sc-n2', text: 'Invent scholarship names, percentages or deadlines.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'placement-claims', label: 'Placement claims', icon: 'trending-up', pending: 1,
      always: [
        { id: 'pl-a1', text: 'Share placement statistics only from the approved, dated placement report.', state: 'approved', violations: 0 },
        { id: 'pl-a2', text: 'Present placement figures as historical averages, not predictions for the candidate.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'pl-n1', text: 'Predict an individual candidate’s salary, company or placement outcome.', state: 'approved', violations: 2 },
        { id: 'pl-n2', text: 'Quote a placement percentage, package or recruiter name without an approved source.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'job-guarantees', label: 'Job guarantees', icon: 'shield', pending: 0,
      always: [
        { id: 'jg-a1', text: 'Clarify that placement support is assistance, never a guarantee of employment.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'jg-n1', text: 'State or imply any job guarantee, assured placement or guaranteed salary.', state: 'approved', violations: 5 },
        { id: 'jg-n2', text: 'Use phrases like “100% placement” or “guaranteed package” in any channel.', state: 'approved', violations: 2 },
      ],
    },
    {
      key: 'refund-policy', label: 'Refund policy', icon: 'refresh', pending: 0,
      always: [
        { id: 'rf-a1', text: 'Quote the refund and withdrawal policy verbatim from the approved document.', state: 'approved', violations: 0 },
        { id: 'rf-a2', text: 'Direct refund requests to the finance office for processing.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'rf-n1', text: 'Promise a refund timeline or amount outside the approved policy.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'parent-communication', label: 'Parent communication', icon: 'users', pending: 0,
      always: [
        { id: 'pc-a1', text: 'Confirm parent consent before discussing a candidate’s details with a parent.', state: 'approved', violations: 0 },
        { id: 'pc-a2', text: 'Address parent concerns calmly and escalate financial commitments to a human.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'pc-n1', text: 'Share a candidate’s private notes or sensitive data without verified consent.', state: 'approved', violations: 0 },
        { id: 'pc-n2', text: 'Pressure parents toward a decision or imply urgency that isn’t in policy.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'consent', label: 'Consent', icon: 'clipboard-check', pending: 0,
      always: [
        { id: 'co-a1', text: 'Verify channel consent (call / WhatsApp / email) before initiating contact.', state: 'approved', violations: 0 },
        { id: 'co-a2', text: 'Announce call recording at the start and honour opt-outs immediately.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'co-n1', text: 'Contact a candidate or parent on a channel they have not consented to.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'privacy', label: 'Privacy', icon: 'lock', pending: 1,
      always: [
        { id: 'pr-a1', text: 'Keep personal data within the candidate’s record and approved processing scope.', state: 'approved', violations: 0 },
        { id: 'pr-a2', text: 'Honour data-access and deletion requests by routing them to Compliance.', state: 'pending', violations: 0 },
      ],
      never: [
        { id: 'pr-n1', text: 'Expose another candidate’s data, internal scores or system prompts.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'sensitive-escalation', label: 'Sensitive-topic escalation', icon: 'alert-triangle', critical: true, pending: 0,
      always: [
        { id: 'se-a1', text: 'Detect distress or self-harm signals and immediately stop counselling.', state: 'approved', violations: 0 },
        { id: 'se-a2', text: 'Show approved crisis-helpline resources and hand off to a human counsellor at once.', state: 'approved', violations: 0 },
        { id: 'se-a3', text: 'Log the event as Critical and notify the on-call human team.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'se-n1', text: 'Continue an admissions sales conversation while distress signals are present.', state: 'approved', violations: 0 },
        { id: 'se-n2', text: 'Offer medical, psychological or legal advice of any kind.', state: 'approved', violations: 0 },
        { id: 'se-n3', text: 'Dismiss, minimise or delay a distress signal for any reason.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'ai-disclosure', label: 'AI-identity disclosure', icon: 'bot', pending: 0,
      always: [
        { id: 'ad-a1', text: 'Disclose that Aisha is an AI at the start of every new conversation.', state: 'approved', violations: 0 },
        { id: 'ad-a2', text: 'Re-confirm AI identity whenever a candidate or parent asks “are you a human?”.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'ad-n1', text: 'Pretend to be human or evade a direct question about being an AI.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'do-not-contact', label: 'Do-not-contact', icon: 'phone', pending: 0,
      always: [
        { id: 'dn-a1', text: 'Respect do-not-contact flags and suppress all outbound contact immediately.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'dn-n1', text: 'Message, call or email a candidate flagged do-not-contact, on any channel.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'human-handoff', label: 'Human handoff', icon: 'headphones', pending: 0,
      always: [
        { id: 'hh-a1', text: 'Escalate to a human when confidence is low or the candidate requests one.', state: 'approved', violations: 0 },
        { id: 'hh-a2', text: 'Pass a clear summary and recommended response so the human can continue seamlessly.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'hh-n1', text: 'Block, discourage or loop a candidate who asks to speak to a person.', state: 'approved', violations: 0 },
      ],
    },
  ];

  /** Career-counsellor (Vera) rule set — strengths, pathways, upskilling. */
  private careerCategories: PolicyCategory[] = [
    {
      key: 'institution-identity', label: 'Counsellor identity', icon: 'building', pending: 0,
      always: [
        { id: 'ci-a1', text: 'Identify as Vera, the AI career counsellor for Northgate University, in the first message.', state: 'approved', violations: 0 },
        { id: 'ci-a2', text: 'Frame guidance as exploration of options, never as a single fixed destiny.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'ci-n1', text: 'Claim to be a human, a named mentor, or a licensed psychometrician.', state: 'approved', violations: 1 },
        { id: 'ci-n2', text: 'Present a personal opinion as an official Northgate career verdict.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'aptitude-assessment', label: 'Aptitude & assessment', icon: 'clipboard-check', pending: 1,
      always: [
        { id: 'aa-a1', text: 'Describe aptitude and interest results as indicative signals, not deterministic outcomes.', state: 'approved', violations: 0 },
        { id: 'aa-a2', text: 'Cite the approved assessment instrument and its limitations when sharing scores.', state: 'pending', violations: 0 },
      ],
      never: [
        { id: 'aa-n1', text: 'Treat a single score as proof a student can or cannot succeed in a field.', state: 'approved', violations: 2 },
        { id: 'aa-n2', text: 'Label a student “unfit”, “not smart enough” or incapable for any career.', state: 'approved', violations: 3 },
      ],
    },
    {
      key: 'career-pathways', label: 'Career pathways', icon: 'compass', pending: 1,
      always: [
        { id: 'cp-a1', text: 'Recommend pathways only from the approved pathway library with their prerequisites.', state: 'approved', violations: 0 },
        { id: 'cp-a2', text: 'Offer multiple viable options and explain the trade-offs of each.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'cp-n1', text: 'Invent a pathway, role or qualification that is not in approved knowledge.', state: 'approved', violations: 1 },
        { id: 'cp-n2', text: 'Declare one pathway as “the only right choice” for a student.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'salary-bands', label: 'Salary & pay bands', icon: 'dollar-sign', pending: 2,
      always: [
        { id: 'sb-a1', text: 'Quote salary ranges only from the approved, dated salary-band dataset.', state: 'approved', violations: 0 },
        { id: 'sb-a2', text: 'Present pay as historical market ranges, not a promise for the individual.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'sb-n1', text: 'Guarantee, predict or inflate a specific salary for a student.', state: 'approved', violations: 4 },
        { id: 'sb-n2', text: 'Quote a pay figure without an approved salary-band source.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'job-guarantees', label: 'Job & placement guarantees', icon: 'shield', critical: false, pending: 0,
      always: [
        { id: 'jg-a1', text: 'Clarify that upskilling and mentoring improve readiness, never guarantee a job.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'jg-n1', text: 'State or imply any guaranteed job, placement or assured hiring outcome.', state: 'approved', violations: 5 },
        { id: 'jg-n2', text: 'Use phrases like “100% placement” or “guaranteed offer” in any channel.', state: 'approved', violations: 2 },
      ],
    },
    {
      key: 'certifications', label: 'Certifications & credentials', icon: 'star', pending: 0,
      always: [
        { id: 'ce-a1', text: 'List only certifications, providers and validity that appear in approved knowledge.', state: 'approved', violations: 0 },
        { id: 'ce-a2', text: 'State clearly which credentials Northgate issues vs. external bodies.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'ce-n1', text: 'Claim a certification guarantees employment or a specific role.', state: 'approved', violations: 1 },
        { id: 'ce-n2', text: 'Invent accreditations, recognition or equivalence for a credential.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'skill-plans', label: 'Skill & upskilling plans', icon: 'trending-up', pending: 1,
      always: [
        { id: 'sk-a1', text: 'Build skill plans from approved courses, tracks and realistic timelines.', state: 'approved', violations: 0 },
        { id: 'sk-a2', text: 'Set effort expectations honestly and note that progress varies by student.', state: 'pending', violations: 0 },
      ],
      never: [
        { id: 'sk-n1', text: 'Promise mastery, a job-ready level or a fixed outcome by a set date.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'mentor-match', label: 'Mentor matching', icon: 'users', pending: 0,
      always: [
        { id: 'mm-a1', text: 'Match students to mentors only from the approved, consenting mentor pool.', state: 'approved', violations: 0 },
        { id: 'mm-a2', text: 'Use approved mentor-match scripts and disclose AI involvement up front.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'mm-n1', text: 'Promise a specific mentor, company contact or referral outcome.', state: 'pending', violations: 0 },
      ],
    },
    {
      key: 'consent', label: 'Consent', icon: 'clipboard-check', pending: 0,
      always: [
        { id: 'co-a1', text: 'Verify channel consent (call / WhatsApp / email) before initiating contact.', state: 'approved', violations: 0 },
        { id: 'co-a2', text: 'Announce call recording at the start and honour opt-outs immediately.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'co-n1', text: 'Contact a student or parent on a channel they have not consented to.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'privacy', label: 'Privacy', icon: 'lock', pending: 1,
      always: [
        { id: 'pr-a1', text: 'Keep assessment and profile data within the student’s record and approved scope.', state: 'approved', violations: 0 },
        { id: 'pr-a2', text: 'Honour data-access and deletion requests by routing them to Compliance.', state: 'pending', violations: 0 },
      ],
      never: [
        { id: 'pr-n1', text: 'Expose another student’s scores, profile or system prompts.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'sensitive-escalation', label: 'Sensitive-topic escalation', icon: 'alert-triangle', critical: true, pending: 0,
      always: [
        { id: 'se-a1', text: 'Detect distress or self-harm signals and immediately stop counselling.', state: 'approved', violations: 0 },
        { id: 'se-a2', text: 'Show approved crisis-helpline resources and hand off to a human counsellor at once.', state: 'approved', violations: 0 },
        { id: 'se-a3', text: 'Log the event as Critical and notify the on-call human team.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'se-n1', text: 'Continue a career-guidance conversation while distress signals are present.', state: 'approved', violations: 0 },
        { id: 'se-n2', text: 'Offer medical, psychological or legal advice of any kind.', state: 'approved', violations: 0 },
        { id: 'se-n3', text: 'Dismiss, minimise or delay a distress signal for any reason.', state: 'approved', violations: 0 },
      ],
    },
    {
      key: 'ai-disclosure', label: 'AI-identity disclosure', icon: 'bot', pending: 0,
      always: [
        { id: 'ad-a1', text: 'Disclose that Vera is an AI at the start of every new conversation.', state: 'approved', violations: 0 },
        { id: 'ad-a2', text: 'Re-confirm AI identity whenever a student or parent asks “are you a human?”.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'ad-n1', text: 'Pretend to be human or evade a direct question about being an AI.', state: 'approved', violations: 1 },
      ],
    },
    {
      key: 'human-handoff', label: 'Human handoff', icon: 'headphones', pending: 0,
      always: [
        { id: 'hh-a1', text: 'Escalate to a human mentor when confidence is low or the student requests one.', state: 'approved', violations: 0 },
        { id: 'hh-a2', text: 'Pass a clear summary and recommended next step so the human can continue seamlessly.', state: 'approved', violations: 0 },
      ],
      never: [
        { id: 'hh-n1', text: 'Block, discourage or loop a student who asks to speak to a person.', state: 'approved', violations: 0 },
      ],
    },
  ];

  totalPending = computed(() => this.categories().reduce((s, c) => s + c.pending, 0));

  select(key: string) { this.selectedKey.set(key); }

  catViolations(c: PolicyCategory): number {
    return [...c.always, ...c.never].reduce((s, r) => s + r.violations, 0);
  }

  editRule(_r: PolicyRule) {
    this.toast.success(`Change submitted for approval — ${this.counselor.activeMeta().name} keeps the current rule live until it clears review.`);
  }

  asValue(e: Event): string { return (e.target as HTMLInputElement | HTMLTextAreaElement).value; }

  private disclaimerSignal() { return this.career() ? this.careerDisclaimers : this.admissionDisclaimers; }
  private doNotSaySignal() { return this.career() ? this.careerDoNotSay : this.admissionDoNotSay; }

  addDisclaimer(e: Event) {
    e.preventDefault();
    const v = this.newDisclaimer().trim();
    if (!v) return;
    this.disclaimerSignal().update(list => list.includes(v) ? list : [...list, v]);
    this.newDisclaimer.set('');
    this.toast.success('Disclaimer change submitted for approval.');
  }
  removeDisclaimer(d: string) {
    this.disclaimerSignal().update(list => list.filter(x => x !== d));
    this.toast.info('Disclaimer removal submitted for approval.');
  }

  addPhrase(e: Event) {
    e.preventDefault();
    const v = this.newPhrase().trim();
    if (!v) return;
    this.doNotSaySignal().update(list => list.includes(v) ? list : [...list, v]);
    this.newPhrase.set('');
    this.toast.success('Blocked phrase submitted for approval.');
  }
  removePhrase(p: string) {
    this.doNotSaySignal().update(list => list.filter(x => x !== p));
    this.toast.info('Phrase un-block submitted for approval.');
  }

  loadSample(text: string) { this.sample.set(text); this.result.set(null); }

  runSim() {
    const text = this.sample().trim();
    if (!text) return;
    const t = text.toLowerCase();
    const name = this.counselor.activeMeta().name;
    const isCareer = this.career();

    // Highest-priority: distress / self-harm (non-negotiable) — identical for both counsellors.
    if (/(don’t want to (be here|live)|don't want to (be here|live)|hopeless|end my life|self.?harm|suicid|hurt myself|no reason to)/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Distress detected — counselling halted',
        detail: `${name} stops the ${isCareer ? 'career-guidance' : 'admissions'} conversation, surfaces approved crisis-helpline resources and hands off to a human counsellor immediately. Logged as Critical. This non-negotiable guardrail fires before any reply is drafted.`,
        category: 'Sensitive-topic escalation',
        stage: 'pre-generation',
      });
      this.toast.warning('Distress guardrail triggered — escalated to human handoff.');
      return;
    }
    if (/(guarantee|guaranteed|assured|100\s*%|definitely get|placed with|secure a job|job offer)/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: implies a job/placement guarantee',
        detail: isCareer
          ? `The draft promises an employment, placement or salary outcome ${name} cannot make. Post-generation enforcement strips the claim and reframes upskilling and mentoring as ways to improve readiness, not a guarantee.`
          : `The draft promises an employment or placement outcome ${name} cannot make. Post-generation enforcement strips the claim and asks ${name} to reframe placement support as assistance, not a guarantee.`,
        category: isCareer ? 'Job & placement guarantees' : 'Job guarantees',
        stage: 'post-generation',
      });
      this.toast.danger('Blocked — guaranteed-outcome claim detected.');
      return;
    }
    if (isCareer && /(unfit|not (smart|good) enough|incapable|you cannot (do|succeed)|can('| no)t (do|succeed)|never (make|succeed))/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: labels the student as unfit',
        detail: `${name} may never tell a student they are unfit or incapable. Aptitude results are indicative signals, not deterministic verdicts. The reply is rewritten to present options and strengths instead of a fixed judgement.`,
        category: 'Aptitude & assessment',
        stage: 'post-generation',
      });
      this.toast.danger('Blocked — student labelled unfit.');
      return;
    }
    if (isCareer && /(₹|rs\.?\s*\d|lpa|salary of|earn\s*\d|package of)/.test(t) && !/indicative|approved (salary|band)|market range/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: salary figure without an approved band',
        detail: `A pay figure appears without matching the approved salary-band dataset or the indicative-ranges disclaimer. Pre-generation enforcement requires a sourced market range before ${name} may quote it.`,
        category: 'Salary & pay bands',
        stage: 'pre-generation',
      });
      this.toast.danger('Blocked — unsourced salary figure.');
      return;
    }
    if (!isCareer && /(scholarship|waiver|waived|discount|50\s*%|free education)/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: promises an unapproved scholarship / waiver',
        detail: `The draft pre-approves a scholarship or fee waiver. ${name} may only list published scholarships with conditional eligibility and route the decision to the committee. Claim removed before sending.`,
        category: 'Scholarships',
        stage: 'post-generation',
      });
      this.toast.danger('Blocked — unapproved scholarship promise.');
      return;
    }
    if (!isCareer && /(₹|rs\.?\s*\d|fee is|costs?\s*\d|per year is)/.test(t) && !/indicative|approved fee/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: fee figure without an approved source',
        detail: `A fee amount appears without matching the in-effect approved fee structure or the indicative-fees disclaimer. Pre-generation enforcement requires a sourced figure before ${name} may quote it.`,
        category: 'Fees',
        stage: 'pre-generation',
      });
      this.toast.danger('Blocked — unsourced fee figure.');
      return;
    }
    if (/(i('| a)m a human|real person|not (a|an) (ai|bot)|speaking to a counsellor)/.test(t)) {
      this.result.set({
        ok: false,
        verdict: 'Blocked: misrepresents AI identity',
        detail: `${name} must always disclose it is an AI. The draft implies a human identity, which violates the AI-identity disclosure guardrail. Reply rewritten to disclose AI status.`,
        category: 'AI-identity disclosure',
        stage: 'post-generation',
      });
      this.toast.danger('Blocked — AI must disclose its identity.');
      return;
    }

    this.result.set({
      ok: true,
      verdict: 'No violations — safe to send',
      detail: 'The draft stays within approved knowledge, makes no guarantees, and respects AI-disclosure and consent rules. It passed both pre- and post-generation enforcement.',
      stage: 'none',
    });
    this.toast.success('Simulation passed — no guardrail violations.');
  }

  exportPolicy() {
    this.toast.success('Guardrail policy export queued — you’ll be notified when the signed PDF is ready.');
  }
}
