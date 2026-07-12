import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { SectionCardComponent, PageHeaderComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { BandChipComponent } from '../../shared/ui/badges.component';
import { AiAvatarComponent, AvatarComponent } from '../../shared/ui/avatar.component';
import { SparklineComponent } from '../../shared/ui/charts.component';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { Band } from '../../domain/models';
import { fmtInt, relTime } from '../../shared/util/format';

type GapStatus = 'Open' | 'Assigned' | 'In review' | 'Resolved';
type ChainState = 'approved' | 'pending' | 'idle';

interface ChainStep {
  role: string;
  who: string;
  hue: number;
  state: ChainState;
  at?: string;
}
interface TestTurn {
  author: 'candidate' | 'ai';
  text: string;
  kind?: 'answer' | 'escalate';
}
interface KnowledgeGap {
  id: string;
  question: string;
  intent: string;
  course: string;
  frequency: number;
  trend: number[];
  severity: Band;
  firstSeen: string;
  lastSeen: string;
  avgConfidence: number;          // 0..100, the AI's confidence when it last tried
  status: GapStatus;
  assignedTo?: string;
  recommendedDoc: string;
  docStatus: 'Exists' | 'Needs new doc' | 'Outdated';
  trigger: string;
  source: string;
  relatedDoc: string;
  proposedAnswer: string;
  guardrails: { label: string; pass: boolean }[];
  testConversation: TestTurn[];
  knowledgeVersion: string;
  chain: ChainStep[];
}

@Component({
  selector: 'va-learning-review',
  standalone: true,
  imports: [
    RouterLink, IconComponent, SectionCardComponent, PageHeaderComponent, EmptyStateComponent,
    DrawerComponent, BandChipComponent, AiAvatarComponent, AvatarComponent, SparklineComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header
      title="AI Self-Learning & Knowledge-Gap Review"
      [subtitle]="headerSubtitle()">
      <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
      <span class="chip ai-chip"><va-icon name="shield-check" [size]="13"></va-icon> Approval-gated learning</span>
      <button class="btn btn-ghost" [routerLink]="'/app/kms'"><va-icon name="book-open" [size]="16"></va-icon><span class="hide-xs">Knowledge base</span></button>
      <button class="btn btn-primary" (click)="rescan()"><va-icon name="refresh" [size]="16"></va-icon>Re-scan conversations</button>
    </va-page-header>

    <!-- Governance banner -->
    <div class="banner ai gov-banner">
      <va-icon name="lock" [size]="18"></va-icon>
      <span><b>{{ counselor.activeMeta().name }} never learns unsupervised.</b> Every proposed answer is validated against guardrails, tested in a sandbox conversation, and approved by a Knowledge Manager and Compliance before it reaches {{ audience() }}.</span>
    </div>

    <!-- KPI tiles -->
    <section class="tiles">
      <div class="ktile" [attr.data-tone]="'warn'">
        <div class="kt-head"><span class="kt-label">Open knowledge gaps</span><span class="kt-ic"><va-icon name="brain" [size]="16"></va-icon></span></div>
        <div class="kt-val t-num">{{ openCount() }}</div>
        <div class="kt-foot t-cap t-muted">{{ unassigned() }} unassigned · {{ critical() }} high severity</div>
      </div>
      <div class="ktile">
        <div class="kt-head"><span class="kt-label">Questions this week</span><span class="kt-ic"><va-icon name="message-circle" [size]="16"></va-icon></span></div>
        <div class="kt-val t-num">{{ askedThisWeek() }}</div>
        <div class="kt-spark"><va-sparkline [data]="weekTrend" color="var(--color-primary)" [height]="28"></va-sparkline></div>
      </div>
      <div class="ktile">
        <div class="kt-head"><span class="kt-label">Avg AI confidence</span><span class="kt-ic"><va-icon name="gauge" [size]="16"></va-icon></span></div>
        <div class="kt-val t-num">{{ avgConfidence() }}%</div>
        <div class="kt-foot t-cap t-muted">on questions hitting a gap</div>
      </div>
      <div class="ktile" [attr.data-tone]="'ok'">
        <div class="kt-head"><span class="kt-label">Gaps closed (30d)</span><span class="kt-ic"><va-icon name="check-circle" [size]="16"></va-icon></span></div>
        <div class="kt-val t-num">{{ closedCount() }}</div>
        <div class="kt-foot t-cap t-muted">approved & published to {{ counselor.activeMeta().name }}</div>
      </div>
    </section>

    <!-- Gap table -->
    <va-section-card title="Detected knowledge gaps" [hint]="'Questions ' + counselor.activeMeta().name + ' could not answer from approved knowledge'" [flush]="true">
      <div actions class="seg">
        @for (f of filters; track f.k) {
          <button [class.active]="filter() === f.k" (click)="filter.set(f.k)">{{ f.l }}<span class="seg-count">{{ countFor(f.k) }}</span></button>
        }
      </div>

      @if (filtered().length) {
        <div class="tbl-wrap scroll-y">
          <table class="va-table">
            <thead>
              <tr>
                <th>Question &amp; intent</th>
                <th class="num">Asked</th>
                <th>Severity</th>
                <th>Recommended answer source</th>
                <th>Status</th>
                <th class="num">Actions</th>
              </tr>
            </thead>
            <tbody>
              @for (g of filtered(); track g.id) {
                <tr [class.selected]="selected()?.id === g.id" (click)="open(g)">
                  <td>
                    <div class="q-cell">
                      <span class="q-text">{{ g.question }}</span>
                      <span class="q-meta t-cap t-muted">{{ g.intent }} · {{ g.course }} · last asked {{ relTime(g.lastSeen) }}</span>
                    </div>
                  </td>
                  <td class="num">
                    <span class="freq t-num">{{ g.frequency }}</span>
                    <span class="freq-l t-cap t-muted">times</span>
                  </td>
                  <td><va-band-chip [band]="g.severity" [label]="sevLabel(g.severity)"></va-band-chip></td>
                  <td>
                    <div class="doc-cell">
                      <va-icon [name]="g.docStatus === 'Needs new doc' ? 'file-text' : 'file-check'" [size]="15"></va-icon>
                      <div class="doc-text">
                        <span class="truncate">{{ g.recommendedDoc }}</span>
                        <span class="t-cap" [attr.data-ds]="g.docStatus">{{ g.docStatus }}</span>
                      </div>
                    </div>
                  </td>
                  <td><span class="gstatus" [attr.data-s]="g.status">{{ g.status }}</span></td>
                  <td class="num" (click)="$event.stopPropagation()">
                    <div class="row-actions">
                      @if (g.status === 'Open' || g.status === 'Assigned') {
                        <button class="btn btn-sm btn-ghost" title="Assign to Knowledge Manager" (click)="assign(g)"><va-icon name="user" [size]="14"></va-icon></button>
                        <button class="btn btn-sm btn-ghost" title="Create document request" (click)="requestDoc(g)"><va-icon name="file-text" [size]="14"></va-icon></button>
                      }
                      @if (g.status !== 'Resolved') {
                        <button class="btn btn-sm btn-ghost" title="Mark resolved" (click)="markResolved(g)"><va-icon name="check" [size]="14"></va-icon></button>
                      } @else {
                        <span class="resolved-tag t-cap"><va-icon name="check-circle" [size]="13"></va-icon> Resolved</span>
                      }
                      <button class="btn btn-sm btn-subtle" (click)="open(g)">Review</button>
                    </div>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <va-empty
          icon="check-circle"
          title="No gaps in this view"
          [message]="counselor.activeMeta().name + ' is answering confidently from approved knowledge for this filter. New gaps appear here automatically as conversations are scanned.'">
        </va-empty>
      }
    </va-section-card>
  </div>

  <!-- Self-learning review drawer -->
  <va-drawer
    [open]="!!selected()"
    [title]="selected()?.question || ''"
    subtitle="Self-learning review · approval-gated"
    [width]="560"
    (close)="close()">
    @if (selected(); as g) {
      <div class="rev stack gap-4">
        <!-- meta strip -->
        <div class="meta-strip">
          <span class="chip"><va-icon name="message-circle" [size]="13"></va-icon> asked {{ g.frequency }}×</span>
          <va-band-chip [band]="g.severity" [label]="sevLabel(g.severity) + ' severity'"></va-band-chip>
          <span class="chip"><va-icon name="gauge" [size]="13"></va-icon> {{ g.avgConfidence }}% confidence</span>
          <span class="gstatus" [attr.data-s]="g.status">{{ g.status }}</span>
        </div>

        <!-- trigger / source -->
        <div class="dl rev-dl">
          <dt>Intent</dt><dd>{{ g.intent }}</dd>
          <dt>Trigger</dt><dd>{{ g.trigger }}</dd>
          <dt>Detected from</dt><dd>{{ g.source }}</dd>
          <dt>Related document</dt><dd class="link" [routerLink]="'/app/kms'">{{ g.relatedDoc }}</dd>
          <dt>Knowledge version</dt><dd class="t-num">{{ g.knowledgeVersion }}</dd>
        </div>

        <!-- proposed answer -->
        <div class="block">
          <div class="block-head">
            <span class="block-title"><va-ai-avatar [size]="22" [variant]="counselor.active()"></va-ai-avatar> Proposed answer</span>
            <span class="chip ai-chip"><va-icon name="sparkles" [size]="12"></va-icon> Drafted from approved sources</span>
          </div>
          <p class="proposed">{{ g.proposedAnswer }}</p>
        </div>

        <!-- guardrail validation -->
        <div class="block">
          <div class="block-head">
            <span class="block-title"><va-icon name="shield-check" [size]="16"></va-icon> Guardrail validation</span>
            <span class="gr-summary" [class.pass]="allGuardrailsPass(g)">
              {{ allGuardrailsPass(g) ? 'Passes guardrails' : 'Needs attention' }}
            </span>
          </div>
          <div class="guardrails">
            @for (r of g.guardrails; track r.label) {
              <div class="gr" [class.fail]="!r.pass">
                <va-icon [name]="r.pass ? 'check-circle' : 'alert-circle'" [size]="15"></va-icon>
                <span>{{ r.label }}</span>
              </div>
            }
          </div>
        </div>

        <!-- test conversation -->
        <div class="block">
          <div class="block-head">
            <span class="block-title"><va-icon name="message-square" [size]="16"></va-icon> Test conversation</span>
            <span class="chip"><va-icon name="bot" [size]="12"></va-icon> Sandbox · not sent to {{ audience() }}</span>
          </div>
          <div class="chat">
            @for (t of g.testConversation; track $index) {
              @if (t.author === 'candidate') {
                <div class="bubble-row left">
                  <va-avatar [name]="career() ? 'Test Student' : 'Test Candidate'" [hue]="200" [size]="26"></va-avatar>
                  <div class="bubble cand">{{ t.text }}</div>
                </div>
              } @else {
                <div class="bubble-row right">
                  <div class="bubble ai" [class.escalate]="t.kind === 'escalate'">
                    @if (t.kind === 'escalate') { <span class="esc-tag"><va-icon name="flag" [size]="12"></va-icon> Escalates to human</span> }
                    {{ t.text }}
                    <span class="ai-disc"><va-icon name="bot" [size]="11"></va-icon> AI {{ counselor.activeMeta().short }} counselor · approved knowledge only</span>
                  </div>
                  <va-ai-avatar [size]="26" [variant]="counselor.active()"></va-ai-avatar>
                </div>
              }
            }
          </div>
          <button class="btn btn-sm btn-ghost btn-block run-test" (click)="runTest(g)"><va-icon name="play" [size]="14"></va-icon> Re-run test conversation</button>
        </div>

        <!-- approval chain -->
        <div class="block">
          <div class="block-head">
            <span class="block-title"><va-icon name="git-branch" [size]="16"></va-icon> Approval chain</span>
          </div>
          <div class="chain">
            @for (s of g.chain; track s.role; let last = $last) {
              <div class="chain-step" [attr.data-state]="s.state">
                <va-avatar [name]="s.who" [hue]="s.hue" [size]="30"></va-avatar>
                <div class="chain-text">
                  <span class="chain-role">{{ s.role }}</span>
                  <span class="t-cap t-muted">{{ s.who }}</span>
                </div>
                <span class="chain-state">
                  @if (s.state === 'approved') { <va-icon name="check-circle" [size]="14"></va-icon> Approved @if (s.at) { <span class="t-muted">· {{ relTime(s.at) }}</span> } }
                  @else if (s.state === 'pending') { <va-icon name="clock" [size]="14"></va-icon> Awaiting }
                  @else { <va-icon name="circle" [size]="14"></va-icon> Queued }
                </span>
              </div>
              @if (!last) { <div class="chain-link"><va-icon name="chevron-down" [size]="14"></va-icon></div> }
            }
          </div>
          <div class="banner info audit-note">
            <va-icon name="scroll-text" [size]="15"></va-icon>
            <span>Approving publishes a new knowledge version and writes an entry to the <a class="link" [routerLink]="'/app/audit-logs'">audit log</a>. {{ counselor.activeMeta().name }} only uses it after both approvals.</span>
          </div>
        </div>
      </div>
    }
    @if (selected(); as g) {
      <div footer class="drawer-footer">
        <button class="btn btn-ghost" (click)="reject(g)"><va-icon name="x" [size]="16"></va-icon> Reject</button>
        <button class="btn btn-subtle" (click)="requestDoc(g)"><va-icon name="file-text" [size]="16"></va-icon> Request document</button>
        <button class="btn btn-accent grow" [disabled]="!allGuardrailsPass(g)" (click)="approve(g)">
          <va-icon name="shield-check" [size]="16"></va-icon> Approve &amp; publish to {{ counselor.activeMeta().name }}
        </button>
      </div>
    }
  </va-drawer>
  `,
  styles: [`
    :host { display: block; }
    .hide-xs { }

    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .gov-banner { align-items: center; }
    .gov-banner span { flex: 1; }
    .gov-banner va-icon { color: var(--color-accent-2); flex: none; }

    /* tiles */
    .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .ktile { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg);
      box-shadow: var(--e1); padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; min-height: 110px; }
    .kt-head { display: flex; align-items: center; justify-content: space-between; }
    .kt-label { font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .04em; }
    .kt-ic { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--color-text-muted); flex: none; }
    .kt-val { font-family: var(--font-display); font-size: var(--text-h1); font-weight: 700; line-height: 1.05; }
    .kt-foot { margin-top: auto; }
    .kt-spark { height: 28px; margin-top: auto; }
    .ktile[data-tone='warn'] .kt-ic { background: var(--color-warning-soft); color: var(--color-warning); }
    .ktile[data-tone='warn'] .kt-val { color: var(--color-warning); }
    .ktile[data-tone='ok'] .kt-ic { background: var(--color-success-soft); color: var(--color-success); }
    .ktile[data-tone='ok'] .kt-val { color: var(--color-success); }

    /* seg count badge */
    .seg-count { margin-left: 6px; font-size: 11px; background: var(--color-surface); padding: 1px 6px; border-radius: 999px; color: var(--color-text-muted); }
    .seg button.active .seg-count { background: var(--color-surface-alt); }

    /* table */
    .tbl-wrap { max-height: 560px; }
    .q-cell { display: flex; flex-direction: column; gap: 2px; max-width: 360px; }
    .q-text { font-weight: 600; }
    .freq { font-weight: 700; }
    .freq-l { display: block; }
    .doc-cell { display: flex; align-items: center; gap: 8px; max-width: 240px; }
    .doc-cell va-icon { color: var(--color-text-muted); flex: none; }
    .doc-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .doc-text [data-ds='Needs new doc'] { color: var(--color-warning); font-weight: 600; }
    .doc-text [data-ds='Outdated'] { color: var(--color-danger); font-weight: 600; }
    .doc-text [data-ds='Exists'] { color: var(--color-text-muted); }

    .gstatus { display: inline-flex; align-items: center; font-size: var(--text-cap); font-weight: 600; padding: 4px 9px; border-radius: var(--r-pill); white-space: nowrap; border: 1px solid transparent; }
    .gstatus[data-s='Open'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .gstatus[data-s='Assigned'] { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .gstatus[data-s='In review'] { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .gstatus[data-s='Resolved'] { background: var(--color-success-soft); color: var(--color-success); }

    .row-actions { display: inline-flex; align-items: center; gap: 6px; justify-content: flex-end; }
    .resolved-tag { display: inline-flex; align-items: center; gap: 4px; color: var(--color-success); font-weight: 600; }

    /* drawer body */
    .meta-strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .rev-dl dd.link, .link { color: var(--color-primary); font-weight: 600; cursor: pointer; }
    .rev-dl dd.link:hover, .link:hover { text-decoration: underline; }

    .block { background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 14px; display: flex; flex-direction: column; gap: 10px; }
    .block-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
    .block-title { display: inline-flex; align-items: center; gap: 8px; font-size: var(--text-sm); font-weight: 700; }
    .proposed { font-size: var(--text-sm); line-height: 1.55; margin: 0; padding: 12px 14px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-md); }

    .gr-summary { font-size: var(--text-cap); font-weight: 700; color: var(--color-warning); display: inline-flex; align-items: center; gap: 4px; }
    .gr-summary.pass { color: var(--color-success); }
    .guardrails { display: flex; flex-direction: column; gap: 7px; }
    .gr { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
    .gr va-icon { color: var(--color-success); flex: none; }
    .gr.fail { color: var(--color-danger); }
    .gr.fail va-icon { color: var(--color-danger); }

    /* chat */
    .chat { display: flex; flex-direction: column; gap: 10px; }
    .bubble-row { display: flex; align-items: flex-end; gap: 8px; }
    .bubble-row.left { justify-content: flex-start; }
    .bubble-row.right { justify-content: flex-end; }
    .bubble { max-width: 78%; font-size: var(--text-sm); line-height: 1.45; padding: 9px 12px; border-radius: 14px; }
    .bubble.cand { background: var(--color-surface); border: 1px solid var(--color-border); border-bottom-left-radius: 4px; }
    .bubble.ai { background: rgba(var(--color-accent-2-rgb), .08); border: 1px solid rgba(var(--color-accent-2-rgb), .22); border-bottom-right-radius: 4px; display: flex; flex-direction: column; gap: 6px; }
    .bubble.ai.escalate { background: var(--color-warning-soft); border-color: color-mix(in srgb, var(--color-warning) 35%, transparent); }
    .ai-disc { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; font-weight: 600; color: var(--color-text-muted); }
    .esc-tag { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; font-weight: 700; color: var(--color-warning); }
    .run-test { margin-top: 2px; }

    /* chain */
    .chain { display: flex; flex-direction: column; }
    .chain-step { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); }
    .chain-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .chain-role { font-size: var(--text-sm); font-weight: 600; }
    .chain-state { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600; white-space: nowrap; }
    .chain-step[data-state='approved'] { border-color: color-mix(in srgb, var(--color-success) 35%, var(--color-border)); }
    .chain-step[data-state='approved'] .chain-state { color: var(--color-success); }
    .chain-step[data-state='pending'] { border-color: color-mix(in srgb, var(--color-accent-2) 35%, var(--color-border)); }
    .chain-step[data-state='pending'] .chain-state { color: var(--color-accent-2); }
    .chain-step[data-state='idle'] { opacity: .65; }
    .chain-step[data-state='idle'] .chain-state { color: var(--color-text-muted); }
    .chain-link { display: grid; place-items: center; height: 18px; color: var(--color-text-muted); }

    .audit-note { align-items: flex-start; }
    .audit-note va-icon { color: var(--color-accent); flex: none; margin-top: 1px; }

    .drawer-footer { display: flex; align-items: center; gap: 8px; width: 100%; }
    .drawer-footer .grow { flex: 1; }

    @media (max-width: 1100px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 720px) { .tiles { grid-template-columns: 1fr; } .hide-xs { display: none; } }
  `],
})
export class LearningReviewComponent {
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');
  audience = computed(() => this.career() ? 'students' : 'candidates');

  headerSubtitle = computed(() =>
    this.career()
      ? 'Vera flagged career questions that need approved answers — salary bands for emerging roles, certifications recruiters value and remote-work prospects.'
      : 'Aisha detected questions that need approved answers before the AI can respond confidently.');

  relTime = relTime;
  fmtInt = fmtInt;

  filter = signal<GapStatus | 'all'>('all');
  selected = signal<KnowledgeGap | null>(null);

  filters = [
    { k: 'all' as const, l: 'All' },
    { k: 'Open' as const, l: 'Open' },
    { k: 'Assigned' as const, l: 'Assigned' },
    { k: 'In review' as const, l: 'In review' },
    { k: 'Resolved' as const, l: 'Resolved' },
  ];

  weekTrend = [9, 14, 11, 18, 22, 17, 24, 19];

  private admissionGaps = signal<KnowledgeGap[]>([
    {
      id: 'gap-001',
      question: 'Internship partners for the B.Tech AI & Data Science program?',
      intent: 'Placements & internships',
      course: 'B.Tech AI & Data Science',
      frequency: 41,
      trend: [4, 6, 5, 9, 12, 8],
      severity: 'high',
      firstSeen: '2026-06-02T10:00:00',
      lastSeen: '2026-06-14T08:10:00',
      avgConfidence: 38,
      status: 'In review',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Industry Partnerships & Internship MoUs 2026',
      docStatus: 'Exists',
      trigger: 'Aisha answered "I don\'t have approved information on that" and offered a human callback 41 times.',
      source: '41 WhatsApp & V-Con conversations · Fall 2026 cycle',
      relatedDoc: 'Placements Brochure 2025 (v3) — lacks current MoU list',
      proposedAnswer: 'Our B.Tech AI & Data Science program has active internship MoUs with TCS, Infosys, and two regional analytics firms. Placement support begins in the 3rd year. I can share the approved partnerships sheet, or connect you with a human counselor for the latest list — would that help?',
      knowledgeVersion: 'kb-2026.6 → kb-2026.7 (proposed)',
      guardrails: [
        { label: 'No invented placement statistics or salary figures', pass: true },
        { label: 'Cites only approved partnership documents', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No guaranteed-placement promises', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Which companies take interns from the AI program?' },
        { author: 'ai', text: 'We have approved internship partnerships with TCS, Infosys and regional analytics firms for the B.Tech AI & Data Science program. I can share the official partnerships sheet — shall I?', kind: 'answer' },
        { author: 'candidate', text: 'What salary do interns get?' },
        { author: 'ai', text: 'I don\'t have approved figures on internship stipends, so I won\'t guess. Let me connect you with a counselor who can share the verified details.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'approved', at: '2026-06-13T15:20:00' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'pending' },
      ],
    },
    {
      id: 'gap-002',
      question: 'Merit scholarship cutoff for Data Science?',
      intent: 'Scholarships & financial aid',
      course: 'B.Tech AI & Data Science',
      frequency: 33,
      trend: [3, 5, 6, 8, 7, 9],
      severity: 'high',
      firstSeen: '2026-06-04T09:00:00',
      lastSeen: '2026-06-14T07:40:00',
      avgConfidence: 31,
      status: 'Assigned',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Merit Scholarship Eligibility Matrix — Fall 2026',
      docStatus: 'Needs new doc',
      trigger: 'No approved cutoff document exists; Aisha declined to state a percentage 33 times.',
      source: '33 voice & WhatsApp conversations · scholarship intent',
      relatedDoc: 'No active document — new approved source required',
      proposedAnswer: 'Merit scholarships for B.Tech AI & Data Science are awarded on a band basis. I can\'t confirm an exact cutoff yet because we don\'t have an approved figure on file — I\'ll have a counselor share the verified scholarship matrix with you.',
      knowledgeVersion: 'kb-2026.6 (no change until doc approved)',
      guardrails: [
        { label: 'No invented scholarship percentages or amounts', pass: true },
        { label: 'Requires an approved source before stating cutoffs', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No financial commitments on behalf of the institution', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'What percentage do I need for the merit scholarship in Data Science?' },
        { author: 'ai', text: 'I don\'t yet have an approved cutoff on file, so I won\'t state a number I can\'t verify. I\'ll connect you with a counselor who can confirm the scholarship matrix.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'pending' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'gap-003',
      question: 'Hostel fee for girls and safety arrangements?',
      intent: 'Hostel & accommodation',
      course: 'All programs',
      frequency: 28,
      trend: [2, 4, 5, 6, 7, 6],
      severity: 'med',
      firstSeen: '2026-06-05T11:00:00',
      lastSeen: '2026-06-13T18:25:00',
      avgConfidence: 44,
      status: 'Open',
      recommendedDoc: 'Hostel Fee Schedule & Safety FAQ 2026',
      docStatus: 'Outdated',
      trigger: 'Aisha referenced a 2025 fee that is now expired; parents asked for the current figure 28 times.',
      source: '28 WhatsApp conversations · parent engagement',
      relatedDoc: 'Hostel Safety FAQ (v2) — fee section expired Mar 2026',
      proposedAnswer: 'Northgate provides separate girls\' hostels with 24/7 security, biometric entry and wardens. Hostel fees are published in the approved fee schedule — I can share the current sheet or connect you with a counselor for the exact amount, since I only quote approved figures.',
      knowledgeVersion: 'kb-2026.6 → kb-2026.7 (proposed)',
      guardrails: [
        { label: 'No invented or expired fee amounts', pass: true },
        { label: 'Cites only the active, approved fee schedule', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'Sensitive-topic (safety) tone reviewed', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Is the girls hostel safe and what is the fee?' },
        { author: 'ai', text: 'The girls\' hostels have 24/7 security, biometric entry and resident wardens. For the fee, I quote only the current approved schedule — I can share it or have a counselor confirm the exact amount for you.', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'idle' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'gap-004',
      question: 'EMI / installment options for tuition fees?',
      intent: 'Fees & payment',
      course: 'MBA',
      frequency: 24,
      trend: [3, 4, 4, 6, 5, 7],
      severity: 'med',
      firstSeen: '2026-06-03T13:00:00',
      lastSeen: '2026-06-13T16:05:00',
      avgConfidence: 40,
      status: 'In review',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Fee Financing & EMI Partners — Fall 2026',
      docStatus: 'Exists',
      trigger: 'Aisha had no approved financing partner list; routed 24 fee-sensitive candidates to humans.',
      source: '24 voice & email conversations · MBA · high budget sensitivity',
      relatedDoc: 'Fee Financing Note 2026 (v1) — pending publish',
      proposedAnswer: 'Tuition for the MBA can be paid in approved installments, and we have empanelled education-loan partners. I share only the approved financing options and won\'t quote interest rates I can\'t verify — would you like the official financing note, or a counselor to walk you through it?',
      knowledgeVersion: 'kb-2026.6 → kb-2026.7 (proposed)',
      guardrails: [
        { label: 'No invented interest rates or loan terms', pass: true },
        { label: 'Cites only empanelled, approved finance partners', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No financial advice beyond approved options', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Can I pay the MBA fee in instalments?' },
        { author: 'ai', text: 'Yes — the MBA fee can be paid in approved instalments, and we have empanelled loan partners. I\'ll share the official financing note; for exact interest rates I\'ll bring in a counselor, since I only quote verified figures.', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'approved', at: '2026-06-12T11:10:00' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'pending' },
      ],
    },
    {
      id: 'gap-005',
      question: 'Is there a portfolio requirement for B.Des UX admission?',
      intent: 'Admission requirements',
      course: 'B.Des UX',
      frequency: 19,
      trend: [1, 3, 4, 4, 5, 5],
      severity: 'med',
      firstSeen: '2026-06-06T10:00:00',
      lastSeen: '2026-06-13T12:40:00',
      avgConfidence: 47,
      status: 'Open',
      recommendedDoc: 'B.Des UX Admission Criteria & Portfolio Guidelines',
      docStatus: 'Needs new doc',
      trigger: 'Aisha lacked an approved portfolio guideline for B.Des UX; asked 19 times.',
      source: '19 web-chat & WhatsApp conversations · B.Des UX',
      relatedDoc: 'No active document — new approved source required',
      proposedAnswer: 'B.Des UX admission considers a design portfolio alongside the entrance assessment. I don\'t yet have the approved portfolio guidelines on file, so I\'ll have a counselor send you the verified requirements rather than guess at the details.',
      knowledgeVersion: 'kb-2026.6 (no change until doc approved)',
      guardrails: [
        { label: 'No invented admission criteria', pass: true },
        { label: 'Requires an approved source before stating requirements', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No commitments on admission outcomes', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Do I need a portfolio for B.Des UX?' },
        { author: 'ai', text: 'A design portfolio is considered, but I don\'t have the approved portfolio guidelines yet, so I won\'t guess the specifics. Let me connect you with a counselor for the verified requirements.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'idle' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'gap-006',
      question: 'Can international students apply, and what are the visa steps?',
      intent: 'International admissions',
      course: 'All programs',
      frequency: 14,
      trend: [1, 2, 2, 3, 3, 3],
      severity: 'low',
      firstSeen: '2026-06-07T09:30:00',
      lastSeen: '2026-06-12T20:15:00',
      avgConfidence: 52,
      status: 'Open',
      recommendedDoc: 'International Admissions & Visa Support Guide',
      docStatus: 'Needs new doc',
      trigger: 'Low-volume but repeated international queries with no approved guide.',
      source: '14 email & web-chat conversations · overseas region',
      relatedDoc: 'No active document — new approved source required',
      proposedAnswer: 'Northgate does welcome international applicants. I don\'t have an approved visa-process guide on file yet, so I\'ll route you to our international admissions counselor for the verified steps rather than share unconfirmed details.',
      knowledgeVersion: 'kb-2026.6 (no change until doc approved)',
      guardrails: [
        { label: 'No invented visa or eligibility steps', pass: true },
        { label: 'Requires an approved source before advising', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No legal/immigration advice', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'I am from abroad — what is the visa process?' },
        { author: 'ai', text: 'I can\'t advise on visa steps without an approved guide, and I won\'t guess on something this important. I\'ll connect you with our international admissions counselor for verified guidance.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'idle' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'gap-007',
      question: 'What is the refund policy if I withdraw after paying the fee?',
      intent: 'Fees & refunds',
      course: 'All programs',
      frequency: 11,
      trend: [1, 1, 2, 2, 3, 2],
      severity: 'low',
      firstSeen: '2026-06-08T14:00:00',
      lastSeen: '2026-06-11T10:50:00',
      avgConfidence: 49,
      status: 'Resolved',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Fee Refund Policy 2026 (approved)',
      docStatus: 'Exists',
      trigger: 'Resolved — approved refund policy published to Aisha on 11 Jun.',
      source: '11 email conversations · post-payment queries',
      relatedDoc: 'Fee Refund Policy 2026 (v1, Active)',
      proposedAnswer: 'Our approved refund policy applies on a sliding scale based on the withdrawal date. I can share the official refund policy document, which has the exact slabs — would you like me to send it?',
      knowledgeVersion: 'kb-2026.6 (published)',
      guardrails: [
        { label: 'No invented refund slabs or amounts', pass: true },
        { label: 'Cites the active, approved refund policy', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No financial commitments outside approved policy', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'If I withdraw after paying, do I get a refund?' },
        { author: 'ai', text: 'Refunds follow our approved policy on a sliding scale by withdrawal date. I\'ll share the official refund policy document so you have the exact slabs — sending it now.', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'approved', at: '2026-06-11T09:30:00' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'approved', at: '2026-06-11T13:05:00' },
      ],
    },
  ]);

  /** Career-flavoured gaps shown when Vera is the active counselor. */
  private careerGaps = signal<KnowledgeGap[]>([
    {
      id: 'cgap-001',
      question: 'What salary band can I expect for entry-level AI / ML roles?',
      intent: 'Salary bands & compensation',
      course: 'Data Science & AI',
      frequency: 47,
      trend: [5, 7, 6, 10, 13, 9],
      severity: 'high',
      firstSeen: '2026-06-02T10:00:00',
      lastSeen: '2026-06-14T08:10:00',
      avgConfidence: 34,
      status: 'In review',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Emerging-Role Salary Band Data — Q2 2026',
      docStatus: 'Needs new doc',
      trigger: 'Vera declined to quote a salary range without approved band data 47 times.',
      source: '47 web-chat & V-Con conversations · career-interest cycle',
      relatedDoc: 'No active salary-band document — approved source required',
      proposedAnswer: 'Entry-level AI / ML roles sit in a published salary band that varies by city and skill level. I won\'t quote a figure I can\'t verify — I can share the approved salary-band sheet once published, or connect you with a career counselor for the latest range. Would that help?',
      knowledgeVersion: 'kb-2026.6 (no change until doc approved)',
      guardrails: [
        { label: 'No invented salary figures or ranges', pass: true },
        { label: 'Requires approved salary-band data before quoting', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No guaranteed-earnings promises', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'What will I earn as a junior data scientist?' },
        { author: 'ai', text: 'I don\'t yet have approved salary-band data on file, so I won\'t guess a number. I\'ll connect you with a career counselor who can share the verified range.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'pending' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'cgap-002',
      question: 'Which certifications do recruiters actually value for cybersecurity?',
      intent: 'Certifications & upskilling',
      course: 'Cybersecurity',
      frequency: 35,
      trend: [3, 5, 6, 8, 8, 9],
      severity: 'high',
      firstSeen: '2026-06-04T09:00:00',
      lastSeen: '2026-06-14T07:40:00',
      avgConfidence: 41,
      status: 'Assigned',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Certifications Recruiters Value — Reference 2026',
      docStatus: 'Outdated',
      trigger: 'Vera referenced a 2025 ranking that is now under review; asked 35 times.',
      source: '35 web-chat & WhatsApp conversations · upskilling intent',
      relatedDoc: 'Certifications Recruiters Value (v2) — needs-review',
      proposedAnswer: 'Recruiters weight certifications differently by role, and our reference list is being refreshed. I quote only the approved, current ranking — I can share it once it\'s re-approved, or have a career counselor walk you through the verified picks.',
      knowledgeVersion: 'kb-2026.6 → kb-2026.7 (proposed)',
      guardrails: [
        { label: 'No invented or outdated certification rankings', pass: true },
        { label: 'Cites only the active, approved reference', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No endorsement of unapproved paid programmes', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Is a CISSP worth it for getting hired?' },
        { author: 'ai', text: 'Our certifications reference is being re-approved, so I won\'t rank them from stale data. Let me connect you with a career counselor for the verified guidance.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'pending' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'cgap-003',
      question: 'What are the remote-work prospects for product / UX roles?',
      intent: 'Remote-work prospects',
      course: 'Product / UX Design',
      frequency: 28,
      trend: [2, 4, 5, 6, 7, 6],
      severity: 'med',
      firstSeen: '2026-06-05T11:00:00',
      lastSeen: '2026-06-13T18:25:00',
      avgConfidence: 46,
      status: 'Open',
      recommendedDoc: 'Remote & Hybrid Work Outlook by Pathway 2026',
      docStatus: 'Needs new doc',
      trigger: 'Students asked about remote opportunities 28 times with no approved outlook on file.',
      source: '28 web-chat conversations · career-interest cycle',
      relatedDoc: 'No active document — new approved source required',
      proposedAnswer: 'Many product / UX roles now offer hybrid or remote options, but I don\'t have an approved outlook document yet, so I won\'t state specifics. A career counselor can share the verified remote-work picture for this pathway.',
      knowledgeVersion: 'kb-2026.6 (no change until doc approved)',
      guardrails: [
        { label: 'No invented remote-work statistics', pass: true },
        { label: 'Requires an approved outlook before advising', pass: false },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No guarantees on job format or availability', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Can I work fully remote as a UX designer?' },
        { author: 'ai', text: 'Remote and hybrid options exist, but I don\'t have an approved outlook to quote, so I won\'t guess. I\'ll connect you with a career counselor for the verified picture.', kind: 'escalate' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'idle' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'cgap-004',
      question: 'Which internship partners hire from the software pathway?',
      intent: 'Internships & mentorship',
      course: 'Software Engineering',
      frequency: 22,
      trend: [3, 4, 4, 6, 5, 7],
      severity: 'med',
      firstSeen: '2026-06-03T13:00:00',
      lastSeen: '2026-06-13T16:05:00',
      avgConfidence: 49,
      status: 'In review',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Internship & Mentor Match Guide 2026',
      docStatus: 'Exists',
      trigger: 'Vera had no approved partner list for some queries; routed 22 students to humans.',
      source: '22 web-chat & email conversations · internship intent',
      relatedDoc: 'Internship & Mentor Match Guide 2026 (v4, Active)',
      proposedAnswer: 'Our software pathway has active internship partners and a mentor-match track. I share only the approved partnerships sheet — would you like the official guide, or a career counselor to match you with a mentor?',
      knowledgeVersion: 'kb-2026.6 → kb-2026.7 (proposed)',
      guardrails: [
        { label: 'No invented partner names or placement stats', pass: true },
        { label: 'Cites only approved partnership documents', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No guaranteed-placement promises', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Where can I intern from the software track?' },
        { author: 'ai', text: 'We have approved internship partners and a mentor-match track for the software pathway. I\'ll share the official guide — for a mentor match I can bring in a career counselor.', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'approved', at: '2026-06-12T11:10:00' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'pending' },
      ],
    },
    {
      id: 'cgap-005',
      question: 'How long does it take to become career-ready in data science?',
      intent: 'Skill plans & readiness',
      course: 'Data Science & AI',
      frequency: 16,
      trend: [1, 3, 4, 4, 5, 5],
      severity: 'med',
      firstSeen: '2026-06-06T10:00:00',
      lastSeen: '2026-06-13T12:40:00',
      avgConfidence: 51,
      status: 'Open',
      recommendedDoc: 'Data Science & AI Skill Framework',
      docStatus: 'Exists',
      trigger: 'Students asked about time-to-readiness 16 times; framework exists but milestones unclear.',
      source: '16 web-chat conversations · skill-plan intent',
      relatedDoc: 'Data Science & AI Skill Framework (v2, Active)',
      proposedAnswer: 'Time-to-readiness depends on your starting skill level. Our approved skill framework maps the milestones — I can build a personalised plan from it, or a career counselor can review your profile with you.',
      knowledgeVersion: 'kb-2026.6 (published)',
      guardrails: [
        { label: 'No invented timelines or guarantees', pass: true },
        { label: 'Cites the active, approved skill framework', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No outcome promises', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'How fast can I be job-ready in data science?' },
        { author: 'ai', text: 'It depends on your current skills. Our approved framework maps the milestones — I can draft a personalised plan, or a career counselor can review it with you.', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'idle' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'idle' },
      ],
    },
    {
      id: 'cgap-006',
      question: 'Is my aptitude better suited to finance or product roles?',
      intent: 'Aptitude & pathway fit',
      course: 'All pathways',
      frequency: 12,
      trend: [1, 2, 2, 3, 3, 3],
      severity: 'low',
      firstSeen: '2026-06-08T14:00:00',
      lastSeen: '2026-06-11T10:50:00',
      avgConfidence: 48,
      status: 'Resolved',
      assignedTo: 'Kavya Iyer',
      recommendedDoc: 'Aptitude → Pathway Mapping 2026 (approved)',
      docStatus: 'Exists',
      trigger: 'Resolved — approved aptitude-to-pathway mapping published to Vera on 11 Jun.',
      source: '12 web-chat conversations · aptitude intent',
      relatedDoc: 'Aptitude → Pathway Mapping 2026 (v1, Active)',
      proposedAnswer: 'Your aptitude profile points toward certain pathways more strongly. I use only the approved aptitude-to-pathway mapping — I can walk you through your top matches, or connect you with a career counselor for a deeper review.',
      knowledgeVersion: 'kb-2026.6 (published)',
      guardrails: [
        { label: 'No invented aptitude interpretations', pass: true },
        { label: 'Cites the active, approved mapping', pass: true },
        { label: 'Discloses AI identity and offers human handoff', pass: true },
        { label: 'No deterministic career guarantees', pass: true },
      ],
      testConversation: [
        { author: 'candidate', text: 'Should I go into finance or product?' },
        { author: 'ai', text: 'Based on the approved aptitude-to-pathway mapping, I can show your strongest matches and explain why. Want me to walk through them, or bring in a career counselor?', kind: 'answer' },
      ],
      chain: [
        { role: 'Knowledge Manager', who: 'Kavya Iyer', hue: 268, state: 'approved', at: '2026-06-11T09:30:00' },
        { role: 'Compliance', who: 'Sneha Banerjee', hue: 14, state: 'approved', at: '2026-06-11T13:05:00' },
      ],
    },
  ]);

  /** Active gap set for the focused counselor. */
  gaps = computed(() => this.career() ? this.careerGaps() : this.admissionGaps());

  // ---- derived ----
  openGaps = computed(() => this.gaps().filter(g => g.status !== 'Resolved'));
  openCount = computed(() => this.openGaps().length);
  unassigned = computed(() => this.gaps().filter(g => g.status === 'Open').length);
  critical = computed(() => this.openGaps().filter(g => g.severity === 'high').length);
  closedCount = computed(() => this.gaps().filter(g => g.status === 'Resolved').length + 5);
  askedThisWeek = computed(() => this.gaps().reduce((a, g) => a + g.frequency, 0));
  avgConfidence = computed(() => {
    const o = this.openGaps();
    if (!o.length) return 0;
    return Math.round(o.reduce((a, g) => a + g.avgConfidence, 0) / o.length);
  });

  filtered = computed(() => {
    const f = this.filter();
    return f === 'all' ? this.gaps() : this.gaps().filter(g => g.status === f);
  });

  countFor(k: GapStatus | 'all'): number {
    return k === 'all' ? this.gaps().length : this.gaps().filter(g => g.status === k).length;
  }

  sevLabel(b: Band): string { return b === 'high' ? 'High' : b === 'med' ? 'Medium' : 'Low'; }
  allGuardrailsPass(g: KnowledgeGap): boolean { return g.guardrails.every(r => r.pass); }

  // ---- interactions ----
  open(g: KnowledgeGap) { this.selected.set(g); }
  close() { this.selected.set(null); }

  private patch(id: string, patch: Partial<KnowledgeGap>) {
    const target = this.career() ? this.careerGaps : this.admissionGaps;
    target.update(list => list.map(g => g.id === id ? { ...g, ...patch } : g));
    const sel = this.selected();
    if (sel?.id === id) this.selected.set({ ...sel, ...patch });
  }

  assign(g: KnowledgeGap) {
    this.patch(g.id, { status: 'Assigned', assignedTo: 'Kavya Iyer' });
    this.toast.success(`"${g.intent}" gap assigned to Knowledge Manager (Kavya Iyer).`, 'user');
  }

  requestDoc(g: KnowledgeGap) {
    this.patch(g.id, { status: 'In review' });
    this.toast.info(`Document request created for "${g.recommendedDoc}" — routed to KMS.`, 'file-text');
  }

  markResolved(g: KnowledgeGap) {
    this.patch(g.id, { status: 'Resolved' });
    this.toast.success(`Knowledge gap marked resolved — ${this.counselor.activeMeta().name} can now answer "${g.intent}".`, 'check-circle');
  }

  runTest(g: KnowledgeGap) {
    this.toast.info('Re-ran the sandbox test conversation — answer stayed within guardrails.', 'play');
  }

  approve(g: KnowledgeGap) {
    if (!this.allGuardrailsPass(g)) {
      this.toast.warning('Resolve the failing guardrail (approved source required) before publishing.', 'shield');
      return;
    }
    const role = this.auth.user().roleLabel;
    const newChain = g.chain.map(s =>
      s.state === 'pending'
        ? { ...s, state: 'approved' as ChainState, at: '2026-06-14T09:30:00' }
        : s,
    );
    const fullyApproved = newChain.every(s => s.state === 'approved');
    this.patch(g.id, { chain: newChain, status: fullyApproved ? 'Resolved' : 'In review' });
    if (fullyApproved) {
      this.toast.success(`Approved & published to ${this.counselor.activeMeta().name}. Knowledge version bumped (${g.knowledgeVersion}) and audit log updated.`, 'shield-check');
    } else {
      this.toast.success(`${role} approval recorded — now awaiting Compliance. Audit log updated.`, 'check-circle');
    }
  }

  reject(g: KnowledgeGap) {
    this.patch(g.id, { status: 'Open' });
    this.toast.warning(`Proposed answer rejected and returned to Open. ${this.counselor.activeMeta().name} will not learn this — audit log updated.`, 'x');
  }

  rescan() {
    this.toast.info('Re-scanning recent conversations for unanswered questions — new gaps will appear automatically.', 'refresh');
  }
}
