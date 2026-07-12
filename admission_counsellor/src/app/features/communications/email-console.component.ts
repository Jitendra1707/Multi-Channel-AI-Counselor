import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { SectionCardComponent, PageHeaderComponent } from '../../shared/ui/layout.component';
import { BarListComponent } from '../../shared/ui/charts.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { BarDatum } from '../../domain/models';
import { fmtInt } from '../../shared/util/format';

type SendState = 'Draft' | 'Pending approval' | 'Scheduled' | 'Sent';

interface EmailTemplate {
  id: string;
  name: string;
  icon: string;
  desc: string;
  subject: string;
  body: string;
  /** whether this template carries claim-bearing content (fees/scholarships) → human approval */
  claimBearing: boolean;
}

interface PersonalizationField {
  token: string;
  label: string;
  sample: string;
}

interface RecipientSegment {
  id: string;
  label: string;
  count: number;
  detail: string;
}

interface EmailStat {
  key: string;
  label: string;
  value: number;
  pct?: number;
  tone: 'default' | 'success' | 'warning' | 'danger' | 'ai';
}

@Component({
  selector: 'va-email-console',
  standalone: true,
  imports: [
    IconComponent, SectionCardComponent, PageHeaderComponent,
    BarListComponent, ApprovalChipComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="page page-grid">
  <va-page-header
    title="Email campaign console"
    subtitle="Compose, personalize and route admissions email — Aisha drafts from approved knowledge only; claim-bearing sends require human approval.">
    <span class="chip ch-email-chip"><va-icon name="mail" [size]="13"></va-icon> Email channel</span>
    <button class="btn btn-ghost btn-sm" (click)="go('/app/approvals')">
      <va-icon name="shield-check" [size]="15"></va-icon> Approval queue
    </button>
    <button class="btn btn-primary btn-sm" (click)="go('/app/communications/whatsapp')">
      All campaigns <va-icon name="arrow-up-right" [size]="14"></va-icon>
    </button>
  </va-page-header>

  <!-- Three-column workspace -->
  <div class="ec-grid">

    <!-- ============ LEFT: template gallery ============ -->
    <aside class="ec-col ec-left">
      <va-section-card title="Templates" hint="Approved layouts" [flush]="true">
        <button actions class="btn btn-ghost btn-sm" (click)="newBlank()">
          <va-icon name="plus" [size]="14"></va-icon> Blank
        </button>
        <div class="tmpl-list">
          @for (t of templates; track t.id) {
            <button class="tmpl" [class.active]="activeTemplateId() === t.id" (click)="selectTemplate(t)">
              <span class="tmpl-ic"><va-icon [name]="t.icon" [size]="16"></va-icon></span>
              <span class="tmpl-body">
                <span class="tmpl-name">{{ t.name }}</span>
                <span class="t-cap t-muted truncate">{{ t.desc }}</span>
              </span>
              @if (t.claimBearing) {
                <span class="tmpl-flag" title="Contains fee / scholarship claims — requires approval">
                  <va-icon name="shield" [size]="13"></va-icon>
                </span>
              }
            </button>
          }
        </div>
      </va-section-card>

      <div class="banner ai">
        <va-icon name="brain" [size]="16"></va-icon>
        <span>Templates render only institution-approved content. Aisha never invents fees, scholarships or placement figures.</span>
      </div>
    </aside>

    <!-- ============ CENTER: editor + preview ============ -->
    <section class="ec-col ec-center">
      <va-section-card title="Compose" [hint]="activeTemplateName()" [flush]="true">
        <div actions class="row gap-2">
          <button class="btn btn-accent btn-sm" (click)="aiDraft()">
            <va-icon name="sparkles" [size]="15"></va-icon> AI draft
          </button>
        </div>

        <div class="composer">
          <!-- subject -->
          <div class="field">
            <label class="label" for="ec-subj">Subject line</label>
            <input id="ec-subj" class="input" type="text" [value]="subject()"
              placeholder="Write a subject — or let Aisha draft it"
              (input)="subject.set(asValue($event))" />
          </div>

          <!-- personalization inserter -->
          <div class="field">
            <span class="label">Insert personalization</span>
            <div class="chips-row">
              @for (f of fields; track f.token) {
                <button class="ins-chip" (click)="insertField(f)" [title]="'Inserts ' + f.token">
                  <va-icon name="zap" [size]="12"></va-icon>{{ f.token }}
                </button>
              }
            </div>
          </div>

          <!-- body -->
          <div class="field grow">
            <label class="label" for="ec-body">Email body</label>
            <textarea id="ec-body" class="textarea body-area" rows="11"
              placeholder="Compose the email body. Use personalization tokens above to merge candidate details."
              [value]="body()"
              (input)="body.set(asValue($event))"></textarea>
            <span class="t-cap t-muted">{{ charCount() }} characters · merges resolved against the selected segment on send.</span>
          </div>
        </div>
      </va-section-card>

      <!-- Live preview -->
      <va-section-card title="Live preview" [hint]="previewMode() === 'desktop' ? 'Desktop inbox' : 'Mobile inbox'" [flush]="true">
        <div actions class="seg">
          <button [class.active]="previewMode() === 'desktop'" (click)="previewMode.set('desktop')">
            <va-icon name="columns" [size]="14"></va-icon> Desktop
          </button>
          <button [class.active]="previewMode() === 'mobile'" (click)="previewMode.set('mobile')">
            <va-icon name="message-square" [size]="14"></va-icon> Mobile
          </button>
        </div>

        <div class="preview-stage" [attr.data-mode]="previewMode()">
          <div class="mail" [class.mobile]="previewMode() === 'mobile'">
            <!-- mail header / logo -->
            <div class="mail-head">
              <div class="brand">
                <span class="brand-mark"><va-icon name="graduation-cap" [size]="18"></va-icon></span>
                <div class="brand-text">
                  <span class="brand-name">{{ institutionName() }}</span>
                  <span class="t-cap t-muted">Office of Admissions · {{ cycle() }}</span>
                </div>
              </div>
              <span class="ai-from"><va-icon name="bot" [size]="12"></va-icon> via Aisha</span>
            </div>

            <!-- subject -->
            <div class="mail-subject">{{ resolvedSubject() || 'Your subject line will appear here' }}</div>

            <!-- body -->
            <div class="mail-body">
              @for (p of previewParagraphs(); track $index) {
                <p>{{ p }}</p>
              } @empty {
                <p class="t-muted">Your message preview renders here. Pick a template or draft with Aisha to begin.</p>
              }
            </div>

            <!-- CTA -->
            <div class="mail-cta">
              <span class="cta-btn">{{ ctaLabel() }}</span>
            </div>

            <!-- footer -->
            <div class="mail-foot">
              <p class="ai-disclose"><va-icon name="bot" [size]="11"></va-icon> This message was prepared by Aisha, an AI admission counselor, and reviewed under {{ institutionName() }} policy.</p>
              <p>{{ institutionName() }}, Hyderabad · You receive this as a registered enquiry. <span class="link">Unsubscribe</span> · <span class="link">Talk to a human counselor</span></p>
            </div>
          </div>
        </div>
      </va-section-card>

      <!-- Send / approval control -->
      <div class="surface send-bar">
        <div class="send-status">
          <span class="t-cap t-muted">Status</span>
          <span class="status-chip" [attr.data-s]="sendState()">
            <va-icon [name]="stateIcon()" [size]="13"></va-icon>{{ sendState() }}
          </span>
          @if (requiresApproval()) {
            <va-approval-chip state="pending"></va-approval-chip>
          }
        </div>
        <div class="send-actions">
          <button class="btn btn-ghost btn-sm" (click)="saveDraft()">
            <va-icon name="file-text" [size]="15"></va-icon> Save draft
          </button>
          <button class="btn btn-subtle btn-sm" (click)="sendForApproval()" [disabled]="!canCompose()">
            <va-icon name="shield-check" [size]="15"></va-icon> Send for approval
          </button>
          <button class="btn btn-primary btn-sm" (click)="send()" [disabled]="!canSendNow()"
            [title]="requiresApproval() ? 'AI / claim-bearing content must be approved before sending' : 'Send campaign'">
            <va-icon name="send" [size]="15"></va-icon> Send
          </button>
        </div>
      </div>
    </section>

    <!-- ============ RIGHT: recipients + analytics ============ -->
    <aside class="ec-col ec-right">
      <va-section-card title="Recipients" hint="Audience segment">
        <div class="seg-list">
          @for (s of segments; track s.id) {
            <button class="seg-row" [class.active]="activeSegmentId() === s.id" (click)="activeSegmentId.set(s.id)">
              <span class="seg-radio" [class.on]="activeSegmentId() === s.id"></span>
              <span class="seg-text">
                <span class="seg-label">{{ s.label }}</span>
                <span class="t-cap t-muted">{{ s.detail }}</span>
              </span>
              <span class="seg-count t-num">{{ fmt(s.count) }}</span>
            </button>
          }
        </div>
        <div class="recip-summary">
          <va-icon name="users" [size]="15"></va-icon>
          <span><b class="t-num">{{ fmt(activeSegment().count) }}</b> recipients · {{ activeSegment().label }}</span>
        </div>
        <div class="banner info deliver-note">
          <va-icon name="shield-check" [size]="15"></va-icon>
          <span>Deliverability healthy — domain authenticated (SPF · DKIM · DMARC). Consent verified for all recipients.</span>
        </div>
      </va-section-card>

      <va-section-card title="Campaign analytics" hint="Last 30 days · email">
        <span actions class="chip ch-email-chip"><va-icon name="activity" [size]="12"></va-icon> Live</span>
        <div class="stat-grid">
          @for (st of stats(); track st.key) {
            <div class="tile" [attr.data-tone]="st.tone">
              <span class="tv t-num">{{ fmt(st.value) }}</span>
              <span class="tl">{{ st.label }}</span>
              @if (st.pct !== undefined) { <span class="t-cap rate t-num">{{ st.pct }}%</span> }
            </div>
          }
        </div>

        <div class="funnel-bars">
          <div class="fb-head t-cap t-muted">Engagement funnel</div>
          <va-bar-list [data]="engagementBars()"></va-bar-list>
        </div>
      </va-section-card>

      <va-section-card title="Course-wise performance" hint="Open rate by program">
        <div class="course-list">
          @for (c of coursePerf; track c.label) {
            <div class="course-row">
              <span class="course-name truncate">{{ c.label }}</span>
              <span class="course-track"><span class="course-fill" [style.width.%]="c.value"></span></span>
              <span class="course-val t-num">{{ c.value }}%</span>
            </div>
          }
        </div>
      </va-section-card>
    </aside>
  </div>
</div>
  `,
  styles: [`
    :host { display: block; }

    .ch-email-chip { color: var(--ch-email); border-color: color-mix(in srgb, var(--ch-email) 30%, var(--color-border)); background: color-mix(in srgb, var(--ch-email) 8%, var(--color-surface)); }

    .ec-grid { display: grid; grid-template-columns: 264px minmax(0, 1fr) 340px; gap: 18px; align-items: start; }
    .ec-col { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
    .ec-left, .ec-right { position: sticky; top: 0; }

    /* ----- template gallery ----- */
    .tmpl-list { display: flex; flex-direction: column; padding: 8px; gap: 4px; }
    .tmpl { display: flex; align-items: center; gap: 10px; padding: 10px 11px; border-radius: var(--r-md);
      border: 1px solid transparent; background: transparent; text-align: left; transition: background .12s, border-color .12s; }
    .tmpl:hover { background: var(--color-surface-alt); }
    .tmpl.active { background: color-mix(in srgb, var(--ch-email) 10%, var(--color-surface)); border-color: color-mix(in srgb, var(--ch-email) 35%, var(--color-border)); }
    .tmpl-ic { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .tmpl.active .tmpl-ic { background: color-mix(in srgb, var(--ch-email) 16%, var(--color-surface)); color: var(--ch-email); }
    .tmpl-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .tmpl-name { font-size: var(--text-sm); font-weight: 600; }
    .tmpl-flag { color: var(--color-warning); flex: none; }

    /* ----- composer ----- */
    .composer { display: flex; flex-direction: column; gap: 16px; padding: 18px; }
    .chips-row { display: flex; flex-wrap: wrap; gap: 6px; }
    .ins-chip { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600;
      padding: 6px 10px; border-radius: var(--r-pill); border: 1px dashed color-mix(in srgb, var(--ch-email) 40%, var(--color-border));
      background: color-mix(in srgb, var(--ch-email) 7%, var(--color-surface)); color: var(--ch-email); transition: background .12s, transform .1s; }
    .ins-chip:hover { background: color-mix(in srgb, var(--ch-email) 14%, var(--color-surface)); }
    .ins-chip:active { transform: translateY(1px); }
    .body-area { resize: vertical; min-height: 200px; line-height: 1.55; font-family: var(--font-ui); }

    /* ----- preview ----- */
    .preview-stage { padding: 22px; display: flex; justify-content: center; background:
      repeating-linear-gradient(45deg, var(--color-surface-2), var(--color-surface-2) 12px, var(--color-bg) 12px, var(--color-bg) 24px);
      background-blend-mode: normal; }
    .preview-stage[data-mode='mobile'] { padding: 22px 14px; }
    .mail { width: 100%; max-width: 600px; background: var(--color-surface); border: 1px solid var(--color-border);
      border-radius: var(--r-lg); overflow: hidden; box-shadow: var(--e2); transition: max-width .25s ease; }
    .mail.mobile { max-width: 340px; }
    .mail-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 16px 18px;
      background: linear-gradient(135deg, color-mix(in srgb, var(--ch-email) 14%, var(--color-surface)), var(--color-surface)); border-bottom: 1px solid var(--color-border); }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-mark { width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; flex: none;
      background: var(--gradient-ai); color: #06121A; }
    .brand-text { display: flex; flex-direction: column; gap: 1px; }
    .brand-name { font-family: var(--font-display); font-weight: 700; font-size: var(--text-sm); }
    .ai-from { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600;
      color: var(--color-accent-2); background: rgba(var(--color-accent-2-rgb), .1); padding: 3px 8px; border-radius: var(--r-pill); flex: none; }
    .mail-subject { padding: 16px 18px 4px; font-size: var(--text-h4); font-weight: 700; font-family: var(--font-display); }
    .mail-body { padding: 6px 18px 4px; display: flex; flex-direction: column; gap: 10px; font-size: var(--text-sm); line-height: 1.6; }
    .mail-body p { margin: 0; }
    .mail-cta { padding: 14px 18px 18px; }
    .cta-btn { display: inline-flex; align-items: center; font-size: var(--text-sm); font-weight: 700; color: #fff;
      background: var(--color-primary); padding: 11px 22px; border-radius: var(--r-md); box-shadow: var(--e1); }
    .mail-foot { padding: 14px 18px; border-top: 1px solid var(--color-border); background: var(--color-surface-2);
      font-size: 11px; color: var(--color-text-muted); display: flex; flex-direction: column; gap: 6px; }
    .mail-foot p { margin: 0; line-height: 1.5; }
    .ai-disclose { display: flex; align-items: flex-start; gap: 5px; color: var(--color-accent-2); font-weight: 500; }
    .ai-disclose va-icon { margin-top: 1px; flex: none; }
    .link { color: var(--ch-email); font-weight: 600; }

    /* ----- send bar ----- */
    .send-bar { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px 16px; flex-wrap: wrap; }
    .send-status { display: flex; align-items: center; gap: 10px; }
    .status-chip { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 700;
      padding: 5px 11px; border-radius: var(--r-pill); border: 1px solid transparent; }
    .status-chip[data-s='Draft'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .status-chip[data-s='Pending approval'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .status-chip[data-s='Scheduled'] { background: rgba(var(--color-accent-rgb), .12); color: var(--ch-email); }
    .status-chip[data-s='Sent'] { background: var(--color-success-soft); color: var(--color-success); }
    .send-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

    /* ----- recipients ----- */
    .seg-list { display: flex; flex-direction: column; gap: 6px; }
    .seg-row { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface); text-align: left; transition: background .12s, border-color .12s; }
    .seg-row:hover { background: var(--color-surface-alt); }
    .seg-row.active { border-color: color-mix(in srgb, var(--ch-email) 45%, var(--color-border)); background: color-mix(in srgb, var(--ch-email) 8%, var(--color-surface)); }
    .seg-radio { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--color-border-strong); flex: none; transition: border-color .12s; position: relative; }
    .seg-radio.on { border-color: var(--ch-email); }
    .seg-radio.on::after { content: ''; position: absolute; inset: 2px; border-radius: 50%; background: var(--ch-email); }
    .seg-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
    .seg-label { font-size: var(--text-sm); font-weight: 600; }
    .seg-count { font-size: var(--text-sm); font-weight: 700; color: var(--ch-email); flex: none; }
    .recip-summary { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding: 10px 12px;
      border-radius: var(--r-md); background: var(--color-surface-2); font-size: var(--text-sm); }
    .recip-summary va-icon { color: var(--ch-email); flex: none; }
    .deliver-note { margin-top: 12px; align-items: center; }
    .deliver-note va-icon { color: var(--color-success); flex: none; }

    /* ----- analytics ----- */
    .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .tile { position: relative; }
    .tile .rate { position: absolute; top: 12px; right: 12px; font-weight: 700; color: var(--color-text-muted); }
    .tile[data-tone='success'] .tv { color: var(--color-success); }
    .tile[data-tone='warning'] .tv { color: var(--color-warning); }
    .tile[data-tone='danger'] .tv { color: var(--color-danger); }
    .tile[data-tone='ai'] .tv { color: var(--ch-email); }
    .funnel-bars { margin-top: 16px; }
    .fb-head { text-transform: uppercase; letter-spacing: .04em; font-weight: 600; margin-bottom: 10px; }

    /* ----- course perf ----- */
    .course-list { display: flex; flex-direction: column; gap: 11px; }
    .course-row { display: grid; grid-template-columns: 1fr 70px 38px; align-items: center; gap: 10px; }
    .course-name { font-size: var(--text-sm); font-weight: 500; }
    .course-track { height: 8px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; }
    .course-fill { display: block; height: 100%; border-radius: 999px; background: var(--gradient-ai); }
    .course-val { font-size: var(--text-sm); font-weight: 700; text-align: right; color: var(--ch-email); }

    @media (max-width: 1240px) {
      .ec-grid { grid-template-columns: 220px minmax(0, 1fr); }
      .ec-right { grid-column: 1 / -1; position: static; }
      .ec-left { position: static; }
    }
    @media (max-width: 860px) {
      .ec-grid { grid-template-columns: 1fr; }
      .stat-grid { grid-template-columns: 1fr 1fr; }
    }
  `],
})
export class EmailConsoleComponent {
  private router = inject(Router);
  private toast = inject(ToastService);
  private auth = inject(AuthService);

  institutionName = computed(() => this.auth.institution().name);
  cycle = computed(() => this.auth.admissionCycle());

  fmt = fmtInt;
  asValue(e: Event): string { return (e.target as HTMLInputElement | HTMLTextAreaElement).value; }

  // ---- state ----
  subject = signal('');
  body = signal('');
  previewMode = signal<'desktop' | 'mobile'>('desktop');
  sendState = signal<SendState>('Draft');
  activeTemplateId = signal<string>('');
  activeSegmentId = signal('seg-2');
  /** marks the current draft as AI-generated (always requires human approval) */
  aiGenerated = signal(false);
  /** marks the active template as claim-bearing (fees/scholarships) */
  claimBearing = signal(false);

  // ---- personalization fields ----
  fields: PersonalizationField[] = [
    { token: '{{firstName}}', label: 'First name', sample: 'Ananya' },
    { token: '{{course}}', label: 'Course', sample: 'B.Tech AI & Data Science' },
    { token: '{{fee}}', label: 'Programme fee', sample: '₹2.4 L / year' },
    { token: '{{scholarship}}', label: 'Scholarship', sample: 'Merit Scholarship (up to 40%)' },
  ];

  // ---- templates ----
  templates: EmailTemplate[] = [
    {
      id: 't-course', name: 'Course-specific', icon: 'graduation-cap', desc: 'Programme detail + curriculum',
      claimBearing: false,
      subject: '{{firstName}}, your guide to {{course}} at Northgate',
      body: `Hi {{firstName}},\n\nThank you for your interest in {{course}} at Northgate University. Based on what you shared with Aisha, here is a quick overview of the programme, curriculum highlights and our approved placement report.\n\nYou can explore the full curriculum and faculty profiles in the brochure below. If you have questions Aisha couldn't answer from approved sources, a human counselor will be happy to help.`,
    },
    {
      id: 't-scholarship', name: 'Scholarship campaign', icon: 'star', desc: 'Merit & need-based aid',
      claimBearing: true,
      subject: 'You may be eligible for {{scholarship}}',
      body: `Hi {{firstName}},\n\nGood news — based on your profile for {{course}}, you may be eligible for {{scholarship}}.\n\nScholarship outcomes are confirmed only after document verification by our admissions office. The figures shown are from Northgate's approved {{course}} scholarship policy for ${'{{cycle}}'} and are subject to review.`,
    },
    {
      id: 't-reminder', name: 'Application reminder', icon: 'clock', desc: 'Nudge incomplete applications',
      claimBearing: false,
      subject: 'A gentle reminder to complete your {{course}} application',
      body: `Hi {{firstName}},\n\nYou started an application for {{course}} but haven't submitted it yet. The application window for ${'{{cycle}}'} is closing soon.\n\nIt takes about 10 minutes to finish. Tap below to continue exactly where you left off.`,
    },
    {
      id: 't-parent', name: 'Parent information', icon: 'users', desc: 'Reassure parents & guardians',
      claimBearing: false,
      subject: 'Information for {{firstName}}’s parents — {{course}} at Northgate',
      body: `Dear Parent / Guardian,\n\n{{firstName}} has been exploring {{course}} at Northgate University. We know choosing a university is a family decision, so here is approved information on campus safety, hostel facilities and placement support.\n\nIf you'd prefer to speak with a human counselor, you can schedule a video counseling session at a time that suits you.`,
    },
    {
      id: 't-meeting', name: 'Meeting confirmation', icon: 'video', desc: 'V-Con session details',
      claimBearing: false,
      subject: 'Your video counseling session is confirmed, {{firstName}}',
      body: `Hi {{firstName}},\n\nYour video counseling (V-Con) session for {{course}} is confirmed. A human counselor will join you to answer any questions Aisha could not address from approved knowledge.\n\nPlease add the session to your calendar using the button below. You'll receive a reminder one hour before.`,
    },
    {
      id: 't-followup', name: 'Follow-up', icon: 'send', desc: 'Re-engage quiet leads',
      claimBearing: false,
      subject: 'Still considering {{course}}, {{firstName}}?',
      body: `Hi {{firstName}},\n\nWe noticed it's been a little while since we last connected about {{course}}. No pressure at all — we're here whenever you're ready.\n\nIf anything is holding you back, reply to this email or talk to a human counselor. Aisha can also answer common questions from our approved knowledge base.`,
    },
  ];

  // ---- recipient segments ----
  segments: RecipientSegment[] = [
    { id: 'seg-1', label: 'All active leads · Fall 2026', count: 1840, detail: 'Consent verified · email opt-in' },
    { id: 'seg-2', label: 'High-intent · MBA · Fall 2026', count: 412, detail: 'Probability ≥ 70% · fee-discussion stage' },
    { id: 'seg-3', label: 'B.Tech AI & DS · Interested', count: 638, detail: 'Stage: Interested → Needs more info' },
    { id: 'seg-4', label: 'Incomplete applications', count: 274, detail: 'Application started · fee pending' },
    { id: 'seg-5', label: 'Parent contacts · concerns raised', count: 96, detail: 'Parent engagement flagged' },
  ];

  activeSegment = computed<RecipientSegment>(() =>
    this.segments.find(s => s.id === this.activeSegmentId()) ?? this.segments[0]);

  // ---- analytics ----
  stats = signal<EmailStat[]>([
    { key: 'sent', label: 'Sent', value: 8420, tone: 'default' },
    { key: 'delivered', label: 'Delivered', value: 8197, pct: 97.4, tone: 'success' },
    { key: 'opened', label: 'Opened', value: 4836, pct: 59.0, tone: 'ai' },
    { key: 'clicked', label: 'Clicked', value: 1944, pct: 23.7, tone: 'ai' },
    { key: 'replied', label: 'Replied', value: 612, pct: 7.5, tone: 'default' },
    { key: 'bounced', label: 'Bounced', value: 223, pct: 2.6, tone: 'warning' },
    { key: 'unsub', label: 'Unsubscribed', value: 41, pct: 0.5, tone: 'danger' },
    { key: 'escalated', label: 'To human', value: 88, pct: 1.1, tone: 'default' },
  ]);

  engagementBars = computed<BarDatum[]>(() => {
    const s = this.stats();
    const get = (k: string) => s.find(x => x.key === k)?.value ?? 0;
    return [
      { label: 'Delivered', value: get('delivered'), tone: 'high' },
      { label: 'Opened', value: get('opened'), tone: 'ai' },
      { label: 'Clicked', value: get('clicked'), tone: 'ai' },
      { label: 'Replied', value: get('replied'), tone: 'med' },
    ];
  });

  coursePerf: BarDatum[] = [
    { label: 'B.Tech AI & Data Science', value: 64 },
    { label: 'MBA', value: 58 },
    { label: 'B.Des UX', value: 53 },
    { label: 'B.Tech CSE', value: 49 },
    { label: 'BBA', value: 41 },
  ];

  // ---- derived ----
  activeTemplateName = computed(() => {
    const t = this.templates.find(x => x.id === this.activeTemplateId());
    return t ? t.name + ' template' : 'Untitled draft';
  });
  charCount = computed(() => this.body().length);
  requiresApproval = computed(() => this.aiGenerated() || this.claimBearing());
  canCompose = computed(() => this.subject().trim().length > 0 && this.body().trim().length > 0);
  canSendNow = computed(() => this.canCompose() && !this.requiresApproval() && this.sendState() !== 'Sent');

  stateIcon = computed(() => {
    switch (this.sendState()) {
      case 'Sent': return 'check-circle';
      case 'Scheduled': return 'calendar';
      case 'Pending approval': return 'clock';
      default: return 'edit';
    }
  });

  // ---- preview resolution (token merge against sample data) ----
  private resolve(text: string): string {
    const samples: Record<string, string> = {};
    for (const f of this.fields) samples[f.token] = f.sample;
    samples['{{cycle}}'] = this.cycle();
    return text.replace(/\{\{\s*\w+\s*\}\}/g, (m) => samples[m.replace(/\s/g, '')] ?? m);
  }
  resolvedSubject = computed(() => this.resolve(this.subject()));
  previewParagraphs = computed(() =>
    this.resolve(this.body()).split('\n').map(p => p.trim()).filter(Boolean));
  ctaLabel = computed(() => {
    const id = this.activeTemplateId();
    switch (id) {
      case 't-scholarship': return 'Check my eligibility';
      case 't-reminder': return 'Complete my application';
      case 't-parent': return 'Schedule a counseling call';
      case 't-meeting': return 'Add to calendar';
      case 't-followup': return 'Talk to a counselor';
      default: return 'View programme details';
    }
  });

  // ---- actions ----
  selectTemplate(t: EmailTemplate) {
    this.activeTemplateId.set(t.id);
    this.subject.set(t.subject);
    this.body.set(t.body);
    this.claimBearing.set(t.claimBearing);
    this.aiGenerated.set(false);
    this.sendState.set('Draft');
  }

  newBlank() {
    this.activeTemplateId.set('');
    this.subject.set('');
    this.body.set('');
    this.claimBearing.set(false);
    this.aiGenerated.set(false);
    this.sendState.set('Draft');
  }

  insertField(f: PersonalizationField) {
    this.body.update(b => (b && !/\s$/.test(b) ? b + ' ' : b) + f.token + ' ');
    this.toast.info(`Inserted ${f.token}`, 'zap');
  }

  aiDraft() {
    const seg = this.activeSegment();
    this.subject.set('{{firstName}}, {{scholarship}} could make {{course}} more affordable');
    this.body.set(
      `Hi {{firstName}},\n\n` +
      `Thank you for exploring {{course}} at ${this.institutionName()}. I'm Aisha, an AI admission counselor, and I've put together a few approved details that may help you decide.\n\n` +
      `Based on your profile, you may be eligible for {{scholarship}}, which could bring the programme fee of {{fee}} down meaningfully. These figures come only from Northgate's approved ${this.cycle()} policy — final eligibility is confirmed by our admissions office.\n\n` +
      `If you'd like to talk anything through, a human counselor is one tap away. There's no pressure — we're here to help you make the right choice.`
    );
    this.aiGenerated.set(true);
    this.claimBearing.set(true);
    this.sendState.set('Draft');
    this.toast.success('AI draft generated — review before sending', 'sparkles');
    void seg;
  }

  saveDraft() {
    if (!this.canCompose()) { this.toast.warning('Add a subject and body before saving.'); return; }
    this.sendState.set('Draft');
    this.toast.success('Draft saved.', 'file-text');
  }

  sendForApproval() {
    if (!this.canCompose()) { this.toast.warning('Add a subject and body first.'); return; }
    this.sendState.set('Pending approval');
    this.toast.info('Sent to Compliance for approval — you’ll be notified when reviewed.', 'shield-check');
  }

  send() {
    if (this.requiresApproval()) {
      this.toast.warning('This draft needs human approval before sending. Use “Send for approval”.');
      this.sendState.set('Pending approval');
      return;
    }
    if (!this.canCompose()) { this.toast.warning('Add a subject and body first.'); return; }
    this.sendState.set('Sent');
    this.toast.success(`Campaign sent to ${fmtInt(this.activeSegment().count)} recipients.`, 'send');
  }

  go(url: string) { this.router.navigateByUrl(url); }
}
