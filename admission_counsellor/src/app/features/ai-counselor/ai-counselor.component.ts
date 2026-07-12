import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import { ToastService } from '../../core/toast.service';
import { CounselorService, CounselorType } from '../../core/counselor.service';
import { FieldApproval } from '../../domain/models';

interface BehaviorMode { key: string; label: string; icon: string; fields: { label: string; value: string; approval: FieldApproval; claim?: boolean }[]; }
interface CounselorConfig {
  greeting: string;
  voices: { id: string; label: string; meta: string }[];
  modes: BehaviorMode[];
  approvedPhrases: string[];
  doNotSay: string[];
  escalationTriggers: string[];
  perf: { label: string; value: string; ai?: boolean }[];
  unanswered: { q: string; n: number }[];
  paths: { label: string; rate: number }[];
}

@Component({
  selector: 'va-ai-counselor',
  standalone: true,
  imports: [IconComponent, AiAvatarComponent, ApprovalChipComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <!-- Counselor selector (when both enabled) -->
    @if (counselor.both()) {
      <div class="cnsl-switch">
        @for (m of counselor.enabledMetas(); track m.type) {
          <button class="cnsl-tab" [class.active]="active() === m.type" [attr.data-v]="m.type" (click)="counselor.setActive(m.type)">
            <va-ai-avatar [size]="26" [variant]="m.type"></va-ai-avatar>
            <span class="cnsl-tab-text"><span class="cnsl-tab-name">{{ m.name }}</span><span class="t-cap t-muted">{{ m.title }}</span></span>
          </button>
        }
      </div>
    }

    <!-- Status header -->
    <section class="status card" [attr.data-v]="active()">
      <div class="status-id">
        <va-ai-avatar [size]="60" [glow]="true" [variant]="active()"></va-ai-avatar>
        <div>
          <div class="t-h3">{{ meta().name }} — {{ meta().title }}</div>
          <div class="t-sm t-muted">Representing <b>Northgate University</b> · Fall 2026 cycle</div>
        </div>
      </div>
      <div class="channels">
        @for (c of channels(); track c.key) {
          <div class="chan" [attr.data-s]="c.status">
            <va-icon [name]="c.icon" [size]="16"></va-icon>
            <span class="cn">{{ c.label }}</span>
            <span class="cstat"><span class="dot" [class]="c.status"></span>{{ statusLabel(c.status) }}</span>
            <button class="switch" [class.on]="c.status === 'live'" (click)="toggleChannel(c.key)" [attr.aria-label]="'Toggle ' + c.label"></button>
          </div>
        }
      </div>
    </section>

    <div class="banner warning">
      <va-icon name="alert-triangle" [size]="18"></va-icon>
      <span>{{ meta().name }} is live with <b>limited knowledge</b> — {{ active() === 'career' ? 'salary-band and emerging-role data are' : 'fee and scholarship answers are' }} pending approval. <a class="ilink" (click)="go('/app/approvals')">Review approvals →</a></span>
    </div>

    <!-- Sub-nav -->
    <div class="seg subnav">
      @for (t of tabs; track t) { <button [class.active]="tab() === t" (click)="tab.set(t)">{{ t }}</button> }
    </div>

    <!-- PROFILE -->
    @if (tab() === 'Profile') {
      <div class="grid2">
        <va-section-card title="Avatar & identity" hint="Persistent “AI” badge cannot be removed">
          <div class="avatars">
            @for (a of avatars(); track a.id) {
              <button class="av-tile" [class.sel]="selectedAvatar() === a.id" (click)="selectedAvatar.set(a.id)">
                <va-ai-avatar [size]="48" [glow]="selectedAvatar() === a.id" [variant]="active()"></va-ai-avatar>
                <span class="t-cap">{{ a.label }}</span>
              </button>
            }
          </div>
          <div class="preview-greeting banner ai">
            <va-icon name="volume" [size]="16"></va-icon>
            <span>“{{ cfg().greeting }}”</span>
          </div>
        </va-section-card>

        <va-section-card title="Voice & language">
          <div class="field"><span class="label">Voice</span>
            <div class="voice-list">
              @for (v of cfg().voices; track v.id) {
                <button class="voice" [class.sel]="selectedVoice() === v.id" (click)="selectedVoice.set(v.id)">
                  <va-icon name="play" [size]="14"></va-icon><span>{{ v.label }}</span><span class="t-cap t-muted">{{ v.meta }}</span>
                  <button class="play-sample" (click)="playSample(v.label); $event.stopPropagation()"><va-icon name="volume" [size]="14"></va-icon></button>
                </button>
              }
            </div>
          </div>
          <div class="field"><span class="label">Languages</span>
            <div class="lang-chips">
              @for (l of languages; track l.name) {
                <button class="chip lang" [class.on]="l.on" (click)="l.on = !l.on">{{ l.name }}</button>
              }
            </div>
          </div>
          <div class="field"><span class="label">Accent</span>
            <select class="select"><option>Indian English (neutral)</option><option>British</option><option>American</option></select>
          </div>
        </va-section-card>
      </div>

      <va-section-card title="Personality & tone">
        <div class="sliders">
          @for (s of personality; track s.label) {
            <div class="slider">
              <div class="between"><span class="t-sm">{{ s.label }}</span><span class="t-cap t-muted">{{ s.value }}%</span></div>
              <input type="range" min="0" max="100" [value]="s.value" (input)="s.value = +$any($event.target).value" />
              <div class="between t-cap t-muted"><span>{{ s.low }}</span><span>{{ s.high }}</span></div>
            </div>
          }
        </div>
        <div class="save-row">
          <span class="t-cap t-muted"><va-icon name="info" [size]="13"></va-icon> Changes to spoken identity route through approval.</span>
          <button class="btn btn-primary" (click)="save()"><va-icon name="check" [size]="16"></va-icon> Save changes</button>
        </div>
      </va-section-card>
    }

    <!-- BEHAVIOR -->
    @if (tab() === 'Behavior') {
      <div class="grid2">
        <div class="stack gap-4">
          <va-section-card title="Conversation modes" hint="Each mode has its own opening, allowed knowledge & escalation">
            <div class="modes">
              @for (m of cfg().modes; track m.key) {
                <div class="mode" [class.open]="openMode() === m.key">
                  <button class="mode-head" (click)="openMode.set(openMode() === m.key ? '' : m.key)">
                    <va-icon [name]="m.icon" [size]="16"></va-icon>
                    <span class="mode-label">{{ m.label }}</span>
                    <va-icon [name]="openMode() === m.key ? 'chevron-up' : 'chevron-down'" [size]="16"></va-icon>
                  </button>
                  @if (openMode() === m.key) {
                    <div class="mode-body">
                      @for (f of m.fields; track f.label) {
                        <div class="bfield">
                          <div class="between">
                            <span class="t-sm bf-label">{{ f.label }} @if (f.claim) { <span class="claim-tag" title="Claim-bearing — routes through approval">claim</span> }</span>
                            <va-approval-chip [state]="f.approval"></va-approval-chip>
                          </div>
                          <p class="bf-value">{{ f.value }}</p>
                          <button class="link-btn" (click)="editField(f.label, f.claim)">Edit</button>
                        </div>
                      }
                    </div>
                  }
                </div>
              }
            </div>
          </va-section-card>
        </div>

        <div class="stack gap-4">
          <va-section-card title="Approved phrases">
            <div class="phrase-list">
              @for (p of cfg().approvedPhrases; track p) { <span class="chip phrase ok"><va-icon name="check" [size]="12"></va-icon>{{ p }}</span> }
            </div>
          </va-section-card>
          <va-section-card title="Do-not-say phrases">
            <div class="phrase-list">
              @for (p of cfg().doNotSay; track p) { <span class="chip phrase no"><va-icon name="x" [size]="12"></va-icon>{{ p }}</span> }
            </div>
          </va-section-card>
          <div class="banner danger">
            <va-icon name="alert-circle" [size]="18"></va-icon>
            <span><b>Guardrail warning:</b> {{ active() === 'career' ? '“You will definitely get this job” implies a job guarantee' : '“We guarantee placement” implies a job guarantee' }} — not permitted. The counselor will decline and escalate.</span>
          </div>
          <va-section-card title="Escalation triggers">
            <div class="phrase-list">
              @for (t of cfg().escalationTriggers; track t) { <span class="chip"><va-icon name="headphones" [size]="12"></va-icon>{{ t }}</span> }
            </div>
          </va-section-card>
        </div>
      </div>
    }

    <!-- PERFORMANCE -->
    @if (tab() === 'Performance') {
      <div class="perf-tiles">
        @for (p of cfg().perf; track p.label) {
          <div class="tile">
            <div class="tv t-num" [class.ai]="p.ai">{{ p.value }}</div>
            <div class="tl">{{ p.label }}</div>
          </div>
        }
      </div>
      <div class="grid2">
        <va-section-card title="Most common unanswered questions" hint="These become knowledge gaps">
          <button actions class="btn btn-sm btn-primary" (click)="go('/app/learning-review')">Go to knowledge gaps <va-icon name="arrow-right" [size]="14"></va-icon></button>
          <div class="q-list">
            @for (q of cfg().unanswered; track q.q) {
              <div class="q-item">
                <span class="q-text">{{ q.q }}</span>
                <span class="q-count chip">{{ q.n }}×</span>
              </div>
            }
          </div>
        </va-section-card>
        <va-section-card title="Most successful conversation paths">
          <div class="path-list">
            @for (p of cfg().paths; track p.label) {
              <div class="path">
                <div class="between"><span class="t-sm">{{ p.label }}</span><span class="t-sm t-num path-rate">{{ p.rate }}%</span></div>
                <div class="progress success"><span [style.width.%]="p.rate"></span></div>
              </div>
            }
          </div>
        </va-section-card>
      </div>
    }
  </div>`,
  styles: [`
    :host { display: block; }
    .cnsl-switch { display: flex; gap: 10px; }
    .cnsl-tab { display: flex; align-items: center; gap: 10px; padding: 10px 16px 10px 12px; border-radius: var(--r-lg);
      border: 1px solid var(--color-border); background: var(--color-surface); text-align: left; transition: all .15s; }
    .cnsl-tab:hover { background: var(--color-surface-alt); }
    .cnsl-tab.active[data-v='admission'] { border-color: color-mix(in srgb, var(--color-accent-2) 50%, var(--color-border)); box-shadow: 0 0 0 3px rgba(var(--color-accent-2-rgb), .12); }
    .cnsl-tab.active[data-v='career'] { border-color: color-mix(in srgb, var(--color-career) 50%, var(--color-border)); box-shadow: 0 0 0 3px rgba(var(--color-career-rgb), .14); }
    .cnsl-tab-text { display: flex; flex-direction: column; gap: 1px; }
    .cnsl-tab-name { font-weight: 700; font-size: var(--text-sm); }
    .status { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
    .status-id { display: flex; align-items: center; gap: 14px; }
    .channels { display: grid; grid-template-columns: repeat(2, minmax(190px, 1fr)); gap: 8px; }
    .chan { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .chan .cn { font-size: var(--text-sm); font-weight: 600; }
    .chan .cstat { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; }
    .chan[data-s='live'] .cstat { color: var(--color-success); }
    .chan[data-s='limited'] .cstat { color: var(--color-warning); }
    .chan[data-s='paused'] .cstat { color: var(--color-text-muted); }
    .ilink, .link-btn { color: var(--color-primary); font-weight: 600; cursor: pointer; background: none; border: none; font-size: inherit; }
    .subnav { width: max-content; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }
    .avatars { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
    .av-tile { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface); }
    .av-tile.sel { border-color: var(--color-accent); box-shadow: var(--ring); }
    .preview-greeting { align-items: center; }
    .voice-list { display: flex; flex-direction: column; gap: 6px; }
    .voice { display: flex; align-items: center; gap: 8px; padding: 9px 11px; border-radius: var(--r-md); border: 1px solid var(--color-border); background: var(--color-surface); text-align: left; }
    .voice.sel { border-color: var(--color-accent); background: rgba(var(--color-accent-rgb), .06); }
    .voice .t-cap { margin-left: auto; }
    .play-sample { margin-left: 8px; background: var(--color-surface-alt); border: none; border-radius: 6px; padding: 5px; color: var(--color-text-muted); }
    .field { margin-bottom: 14px; }
    .lang-chips, .phrase-list { display: flex; flex-wrap: wrap; gap: 7px; }
    .chip.lang { cursor: pointer; }
    .chip.lang.on { background: rgba(var(--color-primary-rgb), .1); color: var(--color-primary); border-color: rgba(var(--color-primary-rgb), .3); }
    .sliders { display: grid; grid-template-columns: 1fr 1fr; gap: 18px 28px; }
    .slider input[type=range] { width: 100%; accent-color: var(--color-primary); }
    .save-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--color-border); flex-wrap: wrap; }
    .save-row .t-cap { display: inline-flex; align-items: center; gap: 5px; }
    .modes { display: flex; flex-direction: column; gap: 6px; }
    .mode { border: 1px solid var(--color-border); border-radius: var(--r-md); overflow: hidden; }
    .mode.open { border-color: var(--color-border-strong); }
    .mode-head { width: 100%; display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: var(--color-surface); border: none; text-align: left; }
    .mode-head:hover { background: var(--color-surface-alt); }
    .mode-label { flex: 1; font-weight: 600; font-size: var(--text-sm); }
    .mode-body { padding: 4px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
    .bfield { padding: 12px; border-radius: var(--r-md); background: var(--color-surface-2); }
    .bf-value { font-size: var(--text-sm); color: var(--color-text-muted); margin: 6px 0; }
    .claim-tag { font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 1px 6px; border-radius: var(--r-pill); background: var(--color-warning-soft); color: var(--color-warning); }
    .phrase { font-weight: 500; }
    .phrase.ok { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .phrase.no { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; }
    .perf-tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
    .tile .tv.ai { background: var(--gradient-ai); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
    .status[data-v='career'] ~ * .tile .tv.ai, .tile .tv.ai { }
    .q-list { display: flex; flex-direction: column; gap: 4px; }
    .q-item { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border-radius: var(--r-md); }
    .q-item:hover { background: var(--color-surface-alt); }
    .q-text { font-size: var(--text-sm); }
    .q-count { background: var(--color-warning-soft); color: var(--color-warning); border-color: transparent; }
    .path-list { display: flex; flex-direction: column; gap: 14px; }
    .path-rate { color: var(--color-success); font-weight: 700; }
    @media (max-width: 1000px) { .grid2 { grid-template-columns: 1fr; } .perf-tiles { grid-template-columns: repeat(2, 1fr); } .channels { grid-template-columns: 1fr; } .cnsl-switch { flex-direction: column; } }
  `],
})
export class AiCounselorComponent {
  private toast = inject(ToastService);
  private router = inject(Router);
  counselor = inject(CounselorService);

  active = this.counselor.active;
  meta = this.counselor.activeMeta;

  tabs = ['Profile', 'Behavior', 'Performance'] as const;
  tab = signal<'Profile' | 'Behavior' | 'Performance'>('Profile');

  selectedAvatar = signal('a1');
  selectedVoice = signal('v1');
  languages = [{ name: 'English', on: true }, { name: 'Hindi', on: true }, { name: 'Telugu', on: true }, { name: 'Tamil', on: false }, { name: 'Marathi', on: false }];
  personality = [
    { label: 'Formality', value: 60, low: 'Casual', high: 'Formal' },
    { label: 'Empathy', value: 82, low: 'Neutral', high: 'Warm' },
    { label: 'Proactiveness', value: 70, low: 'Reactive', high: 'Proactive' },
    { label: 'Response length', value: 45, low: 'Concise', high: 'Detailed' },
  ];
  openMode = signal('m0');

  avatars = computed(() => this.active() === 'career'
    ? [{ id: 'a1', label: 'Vera' }, { id: 'a2', label: 'Cael' }, { id: 'a3', label: 'Iris' }]
    : [{ id: 'a1', label: 'Aisha' }, { id: 'a2', label: 'Maya' }, { id: 'a3', label: 'Neo' }]);

  // per-counselor config
  private ADMISSION: CounselorConfig = {
    greeting: 'Hello, I’m Aisha, an AI admission counselor for Northgate University. Is now a good time to talk about your study options?',
    voices: [{ id: 'v1', label: 'Warm female', meta: 'EN-IN' }, { id: 'v2', label: 'Confident male', meta: 'EN-IN' }, { id: 'v3', label: 'Soft neutral', meta: 'EN' }],
    modes: [
      { key: 'm0', label: 'Candidate discovery', icon: 'search', fields: [
        { label: 'Opening introduction', value: '“Hello, I’m Aisha, an AI admission counselor for Northgate University…”', approval: 'approved' },
        { label: 'Consent message', value: '“Is now a good time? I can stop any time you like.”', approval: 'approved' },
        { label: 'Career-discovery questions', value: 'Academic background → interests → preferred area → budget → family expectations.', approval: 'approved' },
      ] },
      { key: 'm1', label: 'Fee & scholarship explanation', icon: 'dollar-sign', fields: [
        { label: 'Fee explanation', value: 'Quotes only figures from the active approved Fee Structure document.', approval: 'pending', claim: true },
        { label: 'Scholarship eligibility', value: 'Merit waiver up to 40% for 85%+ in 12th (pending approval).', approval: 'pending', claim: true },
        { label: 'Uncertain-answer tone', value: '“I’d rather give you the official figure — let me confirm and follow up.”', approval: 'approved' },
      ] },
      { key: 'm2', label: 'Parent counseling', icon: 'users', fields: [
        { label: 'Institution-representation statement', value: 'States it represents Northgate University and is an AI counselor.', approval: 'approved' },
        { label: 'Safety & credibility', value: 'Shares accreditation, hostel safety policy, official placement report.', approval: 'draft' },
      ] },
      { key: 'm3', label: 'Objection handling', icon: 'shield', fields: [
        { label: 'Approved phrases', value: 'Empathize → clarify → share official document → offer human counselor.', approval: 'approved' },
        { label: 'Restricted claims', value: 'Never promise admission or jobs; never invent figures.', approval: 'approved', claim: true },
      ] },
    ],
    approvedPhrases: ['I can share the official document', 'Let me confirm and follow up', 'I’m an AI counselor for Northgate', 'Would your parents like to join?'],
    doNotSay: ['We guarantee placement', 'You will definitely get admission', 'This fee might change — not sure', 'Other colleges are worse'],
    escalationTriggers: ['Low AI confidence', 'Fee negotiation', 'Scholarship exception', 'Sensitive question', 'Parent requests human', 'Emotional distress'],
    perf: [
      { label: 'Conversations', value: '2,188' }, { label: 'Avg duration', value: '4m 12s' }, { label: 'Candidate sentiment', value: '+0.62' },
      { label: 'AI confidence', value: '91%', ai: true }, { label: 'Knowledge coverage', value: '88%', ai: true }, { label: 'Compliance score', value: '99%' },
      { label: 'Escalation rate', value: '6.4%' }, { label: 'Conversion impact', value: '+24%', ai: true }, { label: 'Parent sentiment', value: '+0.41' }, { label: 'Completion rate', value: '78%' },
    ],
    unanswered: [
      { q: 'Internship partners for the B.Tech AI program?', n: 34 }, { q: 'Merit scholarship cutoff for Data Science?', n: 28 },
      { q: 'Hostel fee for girls?', n: 19 }, { q: 'EMI / instalment options for tuition?', n: 17 }, { q: 'Lateral entry eligibility for diploma holders?', n: 11 },
    ],
    paths: [
      { label: 'Discovery → course → V-Con with parents', rate: 71 }, { label: 'WhatsApp brochure → fee → registration link', rate: 64 },
      { label: 'Scholarship guide → parent call → apply', rate: 58 }, { label: 'Inbound enquiry → counseling → register', rate: 49 },
    ],
  };
  private CAREER: CounselorConfig = {
    greeting: 'Hi, I’m Vera, an AI career counselor for Northgate University. Shall we explore the careers and pathways that fit your strengths?',
    voices: [{ id: 'v1', label: 'Warm female', meta: 'EN-IN' }, { id: 'v2', label: 'Encouraging neutral', meta: 'EN-IN' }, { id: 'v3', label: 'Calm mentor', meta: 'EN' }],
    modes: [
      { key: 'm0', label: 'Career discovery', icon: 'compass', fields: [
        { label: 'Opening introduction', value: '“Hi, I’m Vera, an AI career counselor for Northgate…”', approval: 'approved' },
        { label: 'Strengths & interest questions', value: 'Subjects enjoyed → activities → values → work style → aspirations.', approval: 'approved' },
        { label: 'Consent message', value: '“I’ll only suggest pathways backed by approved guidance.”', approval: 'approved' },
      ] },
      { key: 'm1', label: 'Aptitude & interest profiling', icon: 'target', fields: [
        { label: 'Aptitude framing', value: 'Explains the assessment is indicative, not deterministic.', approval: 'approved' },
        { label: 'Interpretation tone', value: 'Strengths-based, never labels a student as “unfit”.', approval: 'approved' },
      ] },
      { key: 'm2', label: 'Pathway & course mapping', icon: 'route', fields: [
        { label: 'Pathway recommendation', value: 'Maps interests → approved course list → career outcomes.', approval: 'pending', claim: true },
        { label: 'Salary / role outlook', value: 'Cites only approved placement & salary-band data (pending).', approval: 'pending', claim: true },
      ] },
      { key: 'm3', label: 'Skill-gap & upskilling', icon: 'lightbulb', fields: [
        { label: 'Skill-gap analysis', value: 'Compares current skills to target-role requirements.', approval: 'approved' },
        { label: 'Upskilling guidance', value: 'Recommends approved courses/certifications only.', approval: 'draft' },
      ] },
      { key: 'm4', label: 'Mentorship & placement', icon: 'briefcase', fields: [
        { label: 'Mentor matching', value: 'Suggests approved mentors/alumni by interest area.', approval: 'approved' },
        { label: 'Restricted claims', value: 'Never guarantees a job, salary, or placement.', approval: 'approved', claim: true },
      ] },
    ],
    approvedPhrases: ['Based on your strengths, you might explore…', 'Here’s the approved pathway and what it leads to', 'Let me share the official placement data', 'Would you like a skill-gap plan?'],
    doNotSay: ['You will definitely get this job', 'This pathway guarantees a high salary', 'You’re not suited for this', 'Skip college — just do this course'],
    escalationTriggers: ['Low AI confidence', 'Salary / offer negotiation guidance', 'Mental-health / stress signals', 'Parent requests human', 'Conflicting pathway advice', 'High-stakes decision'],
    perf: [
      { label: 'Career conversations', value: '3,142', ai: true }, { label: 'Pathways recommended', value: '1,876', ai: true }, { label: 'Skill assessments', value: '1,294' },
      { label: 'Career-readiness avg', value: '74%', ai: true }, { label: 'Upskilling enrolments', value: '642' }, { label: 'Placements influenced', value: '311' },
      { label: 'AI confidence', value: '89%', ai: true }, { label: 'Mentor matches', value: '196' }, { label: 'Student sentiment', value: '+0.58' }, { label: 'Compliance score', value: '99%' },
    ],
    unanswered: [
      { q: 'Salary bands for emerging AI / ML roles?', n: 41 }, { q: 'Which certifications do recruiters value most?', n: 33 },
      { q: 'Career options after B.Des UX besides design?', n: 22 }, { q: 'Is a master’s needed for data science roles?', n: 18 }, { q: 'Remote-work prospects for cybersecurity?', n: 12 },
    ],
    paths: [
      { label: 'Discovery → aptitude → pathway → skill plan', rate: 73 }, { label: 'Interest profile → course mapping → upskilling', rate: 66 },
      { label: 'Skill-gap plan → mentor match → placement-ready', rate: 61 }, { label: 'Parent career talk → pathway → apply', rate: 52 },
    ],
  };

  cfg = computed<CounselorConfig>(() => (this.active() === 'career' ? this.CAREER : this.ADMISSION));

  private adminChannels = signal<{ key: string; label: string; icon: string; status: 'live' | 'limited' | 'paused' }[]>([
    { key: 'voice', label: 'Voice', icon: 'phone', status: 'live' },
    { key: 'whatsapp', label: 'WhatsApp', icon: 'message-circle', status: 'live' },
    { key: 'email', label: 'Email', icon: 'mail', status: 'limited' },
    { key: 'vcon', label: 'V-Cons', icon: 'video', status: 'paused' },
  ]);
  private careerChannels = signal<{ key: string; label: string; icon: string; status: 'live' | 'limited' | 'paused' }[]>([
    { key: 'voice', label: 'Voice', icon: 'phone', status: 'live' },
    { key: 'whatsapp', label: 'WhatsApp', icon: 'message-circle', status: 'live' },
    { key: 'email', label: 'Email', icon: 'mail', status: 'limited' },
    { key: 'vcon', label: 'V-Cons', icon: 'video', status: 'paused' },
  ]);
  channels = computed(() => (this.active() === 'career' ? this.careerChannels() : this.adminChannels()));

  statusLabel(s: string) { return { live: 'Live', limited: 'Limited', paused: 'Paused' }[s as 'live']; }
  toggleChannel(key: string) {
    const sig = this.active() === 'career' ? this.careerChannels : this.adminChannels;
    sig.update(list => list.map(c => c.key === key ? { ...c, status: c.status === 'live' ? 'paused' : 'live' } : c));
    this.toast.info('Channel updated. Going live runs readiness checks first.');
  }
  playSample(v: string) { this.toast.info('Playing ' + this.meta().name + ' voice sample — ' + v); }
  save() { this.toast.success(this.meta().name + ' profile saved. Spoken-identity changes were routed for approval.'); }
  editField(label: string, claim?: boolean) {
    if (claim) this.toast.warning('“' + label + '” is claim-bearing — change submitted for approval.');
    else this.toast.info('Editing “' + label + '”.');
  }
  go(url: string) { this.router.navigateByUrl(url); }
}
