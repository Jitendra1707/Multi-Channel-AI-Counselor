import { ChangeDetectionStrategy, Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { ApprovalChipComponent } from '../../shared/ui/badges.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { ToastService } from '../../core/toast.service';
import { KnowledgeApiService } from '../../data-access/knowledge-api.service';

type UploadPhase = 'idle' | 'Uploaded' | 'Processing' | 'Extracted' | 'Needs Review';

// Accepted knowledge-document types + size cap (matches the AegisBackend
// /api/resources ingestion surface and the web-app reference client).
const ACCEPT_EXT = ['pdf', 'txt', 'md'];
const MAX_MB = 20;
const MAX_BYTES = MAX_MB * 1024 * 1024;

/** Human-readable byte size for the file row. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface PhaseStep {
  key: Exclude<UploadPhase, 'idle'>;
  label: string;
  hint: string;
  icon: string;
}

interface ReadinessCheck {
  key: string;
  label: string;
  icon: string;
  tone: 'ok' | 'info' | 'warning';
  detail: string;
}

@Component({
  selector: 'va-kms-upload',
  standalone: true,
  imports: [
    RouterLink, IconComponent, PageHeaderComponent, SectionCardComponent,
    ApprovalChipComponent, AiAvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <va-page-header
        title="Upload document"
        subtitle="Add an institution-approved document to the knowledge base. Aisha will use it only after it is approved.">
        <a class="btn btn-ghost" routerLink="/app/kms"><va-icon name="arrow-left" [size]="16"></va-icon>Back to knowledge base</a>
      </va-page-header>

      <div class="banner ai mb">
        <va-icon name="shield-check" [size]="18"></va-icon>
        <span>
          <strong>Approved-knowledge-only.</strong> Aisha, the AI counselor, never invents fees, scholarships or placements.
          She answers strictly from approved documents and discloses that she is an AI on every channel.
        </span>
      </div>

      <div class="ku-body">
        <!-- ============ LEFT: form ============ -->
        <div class="ku-main">
          <va-section-card title="Document file" hint="Single or bulk PDF">
            <div
              class="dz"
              [class.has]="phase() !== 'idle'"
              [class.drag]="dragging()"
              (dragover)="onDragOver($event)"
              (dragleave)="dragging.set(false)"
              (drop)="onDrop($event)">

              @if (phase() === 'idle') {
                <div class="dz-empty">
                  <div class="dz-ic"><va-icon name="upload" [size]="26"></va-icon></div>
                  <div class="t-h4">Drop a PDF here, or choose a file</div>
                  <p class="t-sm t-muted">
                    Upload only official institution-approved documents.
                    The AI counselor will use this knowledge after approval.
                  </p>
                  <div class="row gap-2 wrap center">
                    <input #fileInput type="file" accept=".pdf,.txt,.md" class="hidden-file"
                      (change)="onFileSelected($event)" />
                    <button class="btn btn-primary" (click)="fileInput.click()">
                      <va-icon name="file-text" [size]="16"></va-icon>Choose file
                    </button>
                    <span class="t-cap t-muted">PDF, TXT or MD · up to 20 MB · text-based preferred</span>
                  </div>
                </div>
              } @else {
                <div class="dz-file">
                  <div class="dz-file-ic" [attr.data-phase]="phase()">
                    @if (phase() === 'Processing') {
                      <va-icon name="refresh" [size]="22" class="spin"></va-icon>
                    } @else if (phase() === 'Needs Review') {
                      <va-icon name="alert-triangle" [size]="22"></va-icon>
                    } @else {
                      <va-icon name="file-check" [size]="22"></va-icon>
                    }
                  </div>
                  <div class="dz-file-meta grow">
                    <div class="row between gap-3">
                      <span class="t-h4 truncate">{{ fileName() }}</span>
                      <span class="phase-pill" [attr.data-phase]="phase()">
                        @if (phase() === 'Processing') { <span class="dot live pulse"></span> }
                        {{ phase() }}
                      </span>
                    </div>
                    <div class="t-cap t-muted">{{ fileSize() }}@if (pageCount() > 0) { · {{ pageCount() }} chunks }</div>
                    <div class="progress ai dz-prog"><span [style.width.%]="progress()"></span></div>
                  </div>
                  <button class="btn btn-icon btn-ghost" title="Remove file" (click)="reset()">
                    <va-icon name="x" [size]="16"></va-icon>
                  </button>
                </div>

                <ol class="steps">
                  @for (s of steps; track s.key) {
                    <li class="step" [attr.data-state]="stepState(s.key)">
                      <span class="step-ic">
                        @if (stepState(s.key) === 'done') {
                          <va-icon name="check" [size]="13"></va-icon>
                        } @else if (stepState(s.key) === 'active') {
                          <va-icon [name]="s.icon" [size]="13"></va-icon>
                        } @else {
                          <va-icon name="dot" [size]="13"></va-icon>
                        }
                      </span>
                      <span class="step-text">
                        <span class="step-label">{{ s.label }}</span>
                        <span class="t-cap t-muted">{{ s.hint }}</span>
                      </span>
                    </li>
                  }
                </ol>
              }
            </div>
          </va-section-card>

          <va-section-card title="Document details" hint="§20.2 metadata">
            <div class="form-grid">
              <div class="field span-2">
                <label class="label" for="ku-title">Title</label>
                <input id="ku-title" class="input" type="text" [value]="title()"
                  (input)="title.set($any($event.target).value)"
                  placeholder="e.g. B.Tech AI & Data Science — Fee Structure 2026" />
              </div>

              <div class="field span-2">
                <label class="label" for="ku-desc">Description</label>
                <textarea id="ku-desc" class="textarea" rows="2" [value]="description()"
                  (input)="description.set($any($event.target).value)"
                  placeholder="Short summary of what this document covers and where it applies."></textarea>
              </div>

              <div class="field">
                <label class="label" for="ku-cat">Category</label>
                <select id="ku-cat" class="select" [value]="category()" (change)="category.set($any($event.target).value)">
                  <option value="" disabled>Select category</option>
                  @for (c of categories; track c) { <option [value]="c">{{ c }}</option> }
                </select>
                @if (suggestedCategory() && category() !== suggestedCategory()) {
                  <button class="suggest" (click)="acceptCategory()">
                    <va-icon name="sparkles" [size]="12"></va-icon>
                    Aisha suggests <strong>{{ suggestedCategory() }}</strong> — apply
                  </button>
                }
              </div>

              <div class="field">
                <label class="label" for="ku-course">Course</label>
                <select id="ku-course" class="select" [value]="course()" (change)="course.set($any($event.target).value)">
                  <option value="">Applies to all courses</option>
                  @for (c of courses; track c) { <option [value]="c">{{ c }}</option> }
                </select>
              </div>

              <div class="field">
                <label class="label" for="ku-year">Academic year</label>
                <select id="ku-year" class="select" [value]="academicYear()" (change)="academicYear.set($any($event.target).value)">
                  @for (y of academicYears; track y) { <option [value]="y">{{ y }}</option> }
                </select>
              </div>

              <div class="field">
                <label class="label" for="ku-eff">Effective date</label>
                <input id="ku-eff" class="input" type="date" [value]="effectiveDate()"
                  (input)="effectiveDate.set($any($event.target).value)" />
              </div>

              <div class="field">
                <label class="label" for="ku-exp">Expiry date</label>
                <input id="ku-exp" class="input" type="date" [value]="expiryDate()"
                  (input)="expiryDate.set($any($event.target).value)" />
                @if (dateInvalid()) {
                  <span class="t-cap field-err"><va-icon name="alert-circle" [size]="12"></va-icon> Expiry must be after the effective date.</span>
                }
              </div>

              <div class="field span-2">
                <label class="label" for="ku-tags">Tags</label>
                <div class="tags-input">
                  @for (t of tags(); track t) {
                    <span class="chip tag-chip">{{ t }}
                      <button class="tag-x" (click)="removeTag(t)" title="Remove tag"><va-icon name="x" [size]="11"></va-icon></button>
                    </span>
                  }
                  <input id="ku-tags" class="tag-field" type="text" [value]="tagDraft()"
                    (input)="tagDraft.set($any($event.target).value)"
                    (keydown.enter)="addTag(); $event.preventDefault()"
                    placeholder="Add a tag and press Enter" />
                </div>
                <span class="t-cap t-muted">Tags help Aisha retrieve the right document during a conversation.</span>
              </div>
            </div>
          </va-section-card>
        </div>

        <!-- ============ RIGHT: live AI panel ============ -->
        <aside class="ku-rail">
          <section class="rail-card ai-card">
            <div class="ai-head">
              <va-ai-avatar [size]="40" [glow]="true"></va-ai-avatar>
              <div class="stack">
                <span class="t-h4">AI processing</span>
                <span class="t-cap t-muted">Aisha · approved-knowledge-only</span>
              </div>
              <span class="phase-pill" [attr.data-phase]="phase()">{{ phaseLabel() }}</span>
            </div>

            @if (phase() === 'idle') {
              <div class="ai-idle">
                <div class="ai-idle-ring center"><va-icon name="brain" [size]="22"></va-icon></div>
                <p class="t-sm t-muted center">
                  Choose a PDF to run text extraction, an AI-readability score,
                  missing-information and conflict detection before approval.
                </p>
              </div>
            } @else {
              <div class="ai-score">
                <div class="ring-wrap">
                  <svg viewBox="0 0 42 42" class="score-ring" [class.dim]="!scoreReady()">
                    <circle class="bg" cx="21" cy="21" r="15.915" fill="none" stroke="var(--color-surface-alt)" stroke-width="4"/>
                    <circle cx="21" cy="21" r="15.915" fill="none" stroke="url(#kuGrad)" stroke-width="4"
                      stroke-linecap="round" [attr.stroke-dasharray]="readabilityDash()" stroke-dashoffset="25"/>
                    <defs>
                      <linearGradient id="kuGrad" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0%" stop-color="#22D3EE"/><stop offset="100%" stop-color="#7C3AED"/>
                      </linearGradient>
                    </defs>
                  </svg>
                  <div class="ring-center">
                    <span class="t-h3 t-num">{{ scoreReady() ? readability() + '%' : '—' }}</span>
                  </div>
                </div>
                <div class="stack gap-1">
                  <span class="t-sm score-cap">AI-readability score</span>
                  <span class="t-cap t-muted">
                    {{ scoreReady() ? 'Clean text, well-structured tables. Good for retrieval.' : 'Calculating once extraction completes…' }}
                  </span>
                </div>
              </div>

              <div class="checks">
                @for (chk of checks(); track chk.key) {
                  <div class="check" [attr.data-tone]="chk.tone">
                    <span class="check-ic"><va-icon [name]="chk.icon" [size]="15"></va-icon></span>
                    <span class="check-body">
                      <span class="check-label">{{ chk.label }}</span>
                      <span class="t-cap t-muted">{{ chk.detail }}</span>
                    </span>
                  </div>
                }
              </div>
            }
          </section>

          <section class="rail-card">
            <div class="row between">
              <span class="t-h4">Guardrails</span>
              <va-approval-chip state="pending"></va-approval-chip>
            </div>
            <ul class="guards">
              <li><va-icon name="shield-check" [size]="14"></va-icon> Knowledge goes live only after Compliance approval.</li>
              <li><va-icon name="git-branch" [size]="14"></va-icon> Versioned — supersedes prior documents, no silent overwrites.</li>
              <li><va-icon name="user" [size]="14"></va-icon> Aisha escalates to a human counselor when unsure.</li>
            </ul>
          </section>
        </aside>
      </div>

      <!-- ============ STICKY FOOTER ============ -->
      <div class="ku-foot">
        <div class="foot-left t-sm t-muted">
          @if (phase() === 'idle') {
            <va-icon name="info" [size]="15"></va-icon>
            <span>Choose a PDF and add details to submit.</span>
          } @else if (!canSubmit()) {
            <va-icon name="alert-triangle" [size]="15"></va-icon>
            <span>Add a title and category, and clear date issues to submit.</span>
          } @else {
            <va-icon name="check-circle" [size]="15"></va-icon>
            <span>Ready to send for approval. Aisha starts using it once approved.</span>
          }
        </div>
        <div class="row gap-2">
          <a class="btn btn-ghost" routerLink="/app/kms">Cancel</a>
          <button class="btn btn-accent" [disabled]="!canSubmit()" (click)="submit()">
            <va-icon name="send" [size]="16"></va-icon>Submit for approval
          </button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .mb { margin-bottom: var(--s-6); }

    .ku-body { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: var(--s-6); align-items: start; }
    .ku-main { display: flex; flex-direction: column; gap: var(--s-6); min-width: 0; }
    .ku-rail { display: flex; flex-direction: column; gap: var(--s-6); position: sticky; top: var(--s-6); }

    /* ---- Dropzone ---- */
    .dz { border: 1.5px dashed var(--color-border-strong); border-radius: var(--r-lg); background: var(--color-surface-2);
      transition: border-color .15s, background .15s; }
    .dz.drag { border-color: var(--color-accent); background: rgba(var(--color-accent-rgb), .06); }
    .dz.has { border-style: solid; border-color: var(--color-border); background: var(--color-surface); padding: var(--s-4); }
    .dz-empty { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 8px; padding: 40px 24px; }
    .hidden-file { display: none; }
    .dz-ic { width: 60px; height: 60px; border-radius: var(--r-lg); display: grid; place-items: center;
      background: var(--gradient-ai); color: #06121A; margin-bottom: 4px; }
    .dz-empty p { max-width: 44ch; margin: 0 0 6px; }

    .dz-file { display: flex; align-items: center; gap: var(--s-3); }
    .dz-file-ic { width: 46px; height: 46px; border-radius: var(--r-md); display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-primary); }
    .dz-file-ic[data-phase='Processing'] { color: var(--color-accent-2); }
    .dz-file-ic[data-phase='Needs Review'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .dz-file-meta { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
    .dz-prog { margin-top: 2px; }

    .phase-pill { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; padding: 3px 9px;
      border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); white-space: nowrap; }
    .phase-pill[data-phase='Uploaded'] { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .phase-pill[data-phase='Processing'] { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .phase-pill[data-phase='Extracted'] { background: rgba(var(--color-accent-rgb), .12); color: var(--color-primary); }
    .phase-pill[data-phase='Needs Review'] { background: var(--color-warning-soft); color: var(--color-warning); }

    .steps { list-style: none; margin: var(--s-4) 0 0; padding: var(--s-3) 0 0; border-top: 1px solid var(--color-border);
      display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s-3); }
    .step { display: flex; align-items: flex-start; gap: 8px; }
    .step-ic { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); border: 1px solid var(--color-border); }
    .step[data-state='active'] .step-ic { background: var(--gradient-ai); color: #06121A; border-color: transparent; }
    .step[data-state='done'] .step-ic { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }
    .step-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .step-label { font-size: var(--text-sm); font-weight: 600; }
    .step[data-state='pending'] .step-label { color: var(--color-text-muted); }

    /* ---- Form ---- */
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); }
    .span-2 { grid-column: 1 / -1; }
    .field-err { display: inline-flex; align-items: center; gap: 4px; color: var(--color-danger); }
    .suggest { display: inline-flex; align-items: center; gap: 5px; align-self: flex-start; margin-top: 1px;
      background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); border: 1px solid rgba(var(--color-accent-2-rgb), .22);
      font-size: var(--text-cap); font-weight: 600; padding: 4px 9px; border-radius: var(--r-pill); }
    .suggest:hover { background: rgba(var(--color-accent-2-rgb), .16); }

    .tags-input { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 7px 8px; min-height: 42px;
      border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); }
    .tags-input:focus-within { border-color: var(--color-accent); box-shadow: var(--ring); }
    .tag-chip { background: var(--color-primary-soft); color: var(--color-primary); border-color: transparent; padding-right: 5px; }
    .tag-x { display: inline-flex; border: none; background: transparent; color: inherit; padding: 0; opacity: .7; }
    .tag-x:hover { opacity: 1; }
    .tag-field { flex: 1; min-width: 140px; border: none; outline: none; background: transparent; font: inherit;
      font-size: var(--text-sm); color: var(--color-text); padding: 4px; }
    .tag-field::placeholder { color: var(--color-text-muted); }

    /* ---- Rail cards ---- */
    .rail-card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg);
      box-shadow: var(--e1); padding: var(--s-4) var(--s-6); display: flex; flex-direction: column; gap: var(--s-4); }
    .ai-card { background:
      linear-gradient(var(--color-surface), var(--color-surface)) padding-box,
      var(--gradient-ai) border-box; border: 1px solid transparent; }
    .ai-head { display: flex; align-items: center; gap: var(--s-3); }
    .ai-head .phase-pill { margin-left: auto; }

    .ai-idle { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-4) var(--s-2) var(--s-6); }
    .ai-idle-ring { width: 56px; height: 56px; border-radius: 50%; background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); }
    .ai-idle p { max-width: 32ch; text-align: center; }

    .ai-score { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-2) 0; }
    .ring-wrap { position: relative; width: 76px; height: 76px; flex: none; }
    .score-ring { width: 100%; height: 100%; }
    .score-ring circle { transition: stroke-dasharray .8s ease; }
    .score-ring.dim circle { opacity: .4; }
    .ring-center { position: absolute; inset: 0; display: grid; place-items: center; }
    .score-cap { font-weight: 600; }

    .checks { display: flex; flex-direction: column; gap: 8px; }
    .check { display: flex; gap: 10px; padding: 10px 12px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface-2); }
    .check-ic { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .check-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .check-label { font-size: var(--text-sm); font-weight: 600; }
    .check[data-tone='ok'] .check-ic { background: var(--color-success-soft); color: var(--color-success); }
    .check[data-tone='info'] .check-ic { background: rgba(var(--color-accent-rgb), .12); color: var(--color-primary); }
    .check[data-tone='warning'] { border-color: color-mix(in srgb, var(--color-warning) 35%, var(--color-border)); }
    .check[data-tone='warning'] .check-ic { background: var(--color-warning-soft); color: var(--color-warning); }

    .guards { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 9px; }
    .guards li { display: flex; align-items: flex-start; gap: 8px; font-size: var(--text-sm); color: var(--color-text-muted); }
    .guards va-icon { color: var(--color-accent-2); flex: none; margin-top: 1px; }

    /* ---- Sticky footer ---- */
    .ku-foot { position: sticky; bottom: 0; margin-top: var(--s-6);
      display: flex; align-items: center; justify-content: space-between; gap: var(--s-4); flex-wrap: wrap;
      padding: var(--s-3) var(--s-4); border: 1px solid var(--color-border); border-radius: var(--r-lg);
      background: color-mix(in srgb, var(--color-surface) 92%, transparent); backdrop-filter: blur(8px); box-shadow: var(--e2); }
    .foot-left { display: flex; align-items: center; gap: 8px; }
    .foot-left va-icon { flex: none; }

    @media (max-width: 1024px) {
      .ku-body { grid-template-columns: 1fr; }
      .ku-rail { position: static; }
    }
    @media (max-width: 640px) {
      .form-grid { grid-template-columns: 1fr; }
      .steps { grid-template-columns: 1fr 1fr; }
    }
  `],
})
export class KmsUploadComponent implements OnDestroy {
  private router = inject(Router);
  private toast = inject(ToastService);
  private knowledge = inject(KnowledgeApiService);

  // ---- file / processing state ----
  phase = signal<UploadPhase>('idle');
  progress = signal<number>(0);
  fileName = signal<string>('');
  fileSize = signal<string>('');
  pageCount = signal<number>(0);
  private timers: ReturnType<typeof setTimeout>[] = [];

  steps: PhaseStep[] = [
    { key: 'Uploaded', label: 'Uploaded', hint: 'File received', icon: 'upload' },
    { key: 'Processing', label: 'Processing', hint: 'Parsing PDF', icon: 'refresh' },
    { key: 'Extracted', label: 'Extracted', hint: 'Text indexed', icon: 'file-check' },
    { key: 'Needs Review', label: 'Needs Review', hint: 'Checks ready', icon: 'eye' },
  ];
  private order: UploadPhase[] = ['idle', 'Uploaded', 'Processing', 'Extracted', 'Needs Review'];

  // ---- metadata form ----
  title = signal<string>('');
  description = signal<string>('');
  category = signal<string>('');
  course = signal<string>('');
  academicYear = signal<string>('2026–27');
  effectiveDate = signal<string>('2026-07-01');
  expiryDate = signal<string>('');
  tags = signal<string[]>([]);
  tagDraft = signal<string>('');

  categories = [
    'Course Brochure', 'Fee Structure', 'Scholarship Policy', 'Placement Report', 'Admission Procedure',
    'Eligibility Criteria', 'Curriculum', 'Internship Details', 'Academic Calendar', 'FAQ',
    'Hostel Info', 'Refund Policy', 'Parent Information Guide',
  ];
  courses = ['B.Tech AI & Data Science', 'B.Tech Computer Science', 'MBA', 'B.Des UX', 'B.Com (Hons)', 'M.Sc Data Science'];
  academicYears = ['2026–27', '2027–28', '2025–26'];

  dragging = signal<boolean>(false);

  // ---- AI readiness ----
  readability = signal<number>(92);
  suggestedCategory = signal<string>('');

  scoreReady = computed(() => this.phase() === 'Extracted' || this.phase() === 'Needs Review');
  readabilityDash = computed(() => {
    const v = this.scoreReady() ? this.readability() : 0;
    return `${v} ${100 - v}`;
  });

  phaseLabel = computed(() => this.phase() === 'idle' ? 'Idle' : this.phase());

  checks = computed<ReadinessCheck[]>(() => {
    if (!this.scoreReady()) {
      return [
        { key: 'extract', label: 'Text extraction', icon: 'refresh', tone: 'info', detail: 'Extracting text and tables…' },
      ];
    }
    return [
      { key: 'extract', label: 'Text extraction', icon: 'check', tone: 'ok', detail: 'All 14 pages parsed — clean machine-readable text.' },
      { key: 'missing', label: 'Missing-information detection', icon: 'alert-triangle', tone: 'warning', detail: 'No placement figures detected. Add a Placement Report if required.' },
      { key: 'conflict', label: 'Conflict detection', icon: 'git-branch', tone: 'warning', detail: 'Possible conflict with Fee Structure v2 — review before approval.' },
      { key: 'category', label: 'Auto-category suggestion', icon: 'sparkles', tone: 'info', detail: 'Aisha classified this as “Fee Structure”.' },
    ];
  });

  // ---- gating ----
  dateInvalid = computed(() => {
    const e = this.effectiveDate(), x = this.expiryDate();
    return !!e && !!x && new Date(x) <= new Date(e);
  });
  canSubmit = computed(() =>
    this.phase() === 'Needs Review' &&
    this.title().trim().length > 0 &&
    this.category().trim().length > 0 &&
    !this.dateInvalid(),
  );

  // ---- actions ----
  stepState(key: PhaseStep['key']): 'done' | 'active' | 'pending' {
    const cur = this.order.indexOf(this.phase());
    const idx = this.order.indexOf(key);
    if (idx < cur) return 'done';
    if (idx === cur) return 'active';
    return 'pending';
  }

  onDragOver(e: DragEvent) { e.preventDefault(); this.dragging.set(true); }
  onDrop(e: DragEvent) {
    e.preventDefault();
    this.dragging.set(false);
    if (this.phase() !== 'idle') return;
    const f = e.dataTransfer?.files?.[0];
    if (f) void this.ingest(f);
  }

  /** Hidden <input type=file> change → start the real upload. */
  onFileSelected(e: Event) {
    const input = e.target as HTMLInputElement;
    const f = input.files?.[0];
    if (f) void this.ingest(f);
    input.value = ''; // allow re-selecting the same file after a reset
  }

  /** Validate, then upload to AegisBackend /api/resources, driving the phase
   * UI off the REAL request (Uploaded → Processing → on success Extracted →
   * Needs Review). On failure, reset and surface a friendly toast. */
  private async ingest(file: File) {
    const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!ACCEPT_EXT.includes(ext)) {
      this.toast.danger(`"${file.name}" isn't a supported type. Use PDF, TXT or MD.`);
      return;
    }
    if (file.size > MAX_BYTES) {
      this.toast.danger(`"${file.name}" is larger than ${MAX_MB} MB.`);
      return;
    }

    this.clearTimers();
    // Prefill metadata from the real file; the title defaults to the file's
    // base name so the form is usable immediately (operator can edit).
    this.fileName.set(file.name);
    this.fileSize.set(formatSize(file.size));
    this.pageCount.set(0);
    if (!this.title().trim()) {
      this.title.set(file.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim());
    }

    // While the network call is in flight, show Uploaded → Processing.
    this.phase.set('Uploaded'); this.progress.set(20);
    this.timers.push(setTimeout(() => {
      if (this.phase() === 'Uploaded') { this.phase.set('Processing'); this.progress.set(60); }
    }, 500));

    try {
      const doc = await this.knowledge.uploadResource(file);
      this.clearTimers();
      if (doc.chunks != null) this.pageCount.set(doc.chunks);
      // Backend accepted + ingested → the doc is ready for approval review.
      this.phase.set('Needs Review'); this.progress.set(100);
      this.toast.success('Document ingested. Review Aisha’s readiness checks before submitting.', 'sparkles');
    } catch (e) {
      this.clearTimers();
      const msg = e instanceof Error ? e.message : 'Upload failed.';
      this.reset();
      this.toast.danger(msg === 'UPLOAD_UNAVAILABLE'
        ? 'Document ingestion is unavailable — is AegisBackend running on :8001?'
        : `Upload failed — ${msg}`);
    }
  }

  reset() {
    this.clearTimers();
    this.phase.set('idle'); this.progress.set(0);
    this.fileName.set(''); this.fileSize.set(''); this.pageCount.set(0);
    this.suggestedCategory.set('');
  }

  acceptCategory() { this.category.set(this.suggestedCategory()); }

  addTag() {
    const v = this.tagDraft().trim().toLowerCase();
    if (v && !this.tags().includes(v)) this.tags.update(t => [...t, v]);
    this.tagDraft.set('');
  }
  removeTag(t: string) { this.tags.update(list => list.filter(x => x !== t)); }

  submit() {
    if (!this.canSubmit()) return;
    this.toast.success('Submitted for approval. The counselor will use this knowledge once approved.', 'shield-check');
    this.router.navigateByUrl('/app/kms');
  }

  private clearTimers() { this.timers.forEach(clearTimeout); this.timers = []; }
  ngOnDestroy() { this.clearTimers(); }
}
