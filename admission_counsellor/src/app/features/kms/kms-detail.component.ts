import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { ProbabilityBadgeComponent } from '../../shared/ui/badges.component';
import { DataStore } from '../../data-access/data.store';
import { ToastService } from '../../core/toast.service';
import { fmtDate, relTime } from '../../shared/util/format';

@Component({
  selector: 'va-kms-detail',
  standalone: true,
  imports: [RouterLink, IconComponent, SectionCardComponent, EmptyStateComponent, ProbabilityBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page">
    @if (doc(); as d) {
      <div class="dh">
        <a class="back" routerLink="/app/kms"><va-icon name="arrow-left" [size]="16"></va-icon> Knowledge library</a>
        <div class="dh-main">
          <div class="dh-title">
            <span class="t-h2">{{ d.title }}</span>
            <div class="dh-meta">
              <span class="chip">{{ d.category }}</span>
              <span class="chip">v{{ d.version }}</span>
              <span class="status-pill" [attr.data-g]="statusGroup(d.status)">{{ d.status }}</span>
              @if (d.status === 'Active') { <span class="active-by"><va-icon name="bot" [size]="13"></va-icon> In use by Aisha</span> }
            </div>
          </div>
          <div class="dh-actions">
            <button class="btn btn-ghost" (click)="toast.info('Opening version history')"><va-icon name="layers" [size]="16"></va-icon> Versions</button>
            <button class="btn btn-ghost" (click)="diff.set(!diff())"><va-icon name="columns" [size]="16"></va-icon> {{ diff() ? 'Hide diff' : 'Compare versions' }}</button>
            <button class="btn btn-ghost btn-icon" (click)="toast.info('Downloading PDF')"><va-icon name="download" [size]="16"></va-icon></button>
          </div>
        </div>
      </div>

      <div class="grid">
        <!-- Preview + extracted -->
        <div class="stack gap-4">
          <va-section-card title="Document preview" [flush]="true">
            <span actions class="chip"><va-icon name="file-text" [size]="12"></va-icon> {{ kb(d.sizeKb) }}</span>
            <div class="pdf">
              <div class="pdf-page">
                <div class="pdf-h">{{ auth }} · {{ d.category }}</div>
                <h3 class="pdf-title">{{ d.title }}</h3>
                <p class="t-cap t-muted">Academic year {{ d.academicYear }} · Effective {{ d.effectiveDate ? date(d.effectiveDate) : '—' }}</p>
                @for (p of previewParas(d.category); track $index) { <p class="pdf-p">{{ p }}</p> }
                @if (d.category === 'Fee Structure') {
                  <table class="mini-fee">
                    <tr><th>Component</th><th class="num">Amount</th></tr>
                    <tr><td>Tuition fee</td><td class="num">₹4,80,000</td></tr>
                    <tr><td>Lab & technology</td><td class="num">₹85,000</td></tr>
                    <tr><td>Examination</td><td class="num">₹25,000</td></tr>
                    <tr><td>Hostel (optional)</td><td class="num">₹1,20,000</td></tr>
                    <tr class="total"><td>Total / year</td><td class="num">₹6,40,000</td></tr>
                  </table>
                }
              </div>
            </div>
          </va-section-card>

          @if (diff()) {
            <va-section-card title="Version diff" hint="v{{ doc()!.version }} vs proposed">
              <div class="diff">
                <div class="diff-col old"><span class="diff-h">Current</span><p>Merit waiver up to 30% for eligible candidates.</p></div>
                <div class="diff-col new"><span class="diff-h">Proposed</span><p>Merit waiver up to <mark>40%</mark> for candidates scoring 85%+ in 12th; need-based add-on up to 15%.</p></div>
              </div>
            </va-section-card>
          }

          <va-section-card title="Extracted knowledge summary" hint="What the AI learned">
            <ul class="ek">
              @for (k of extracted(d.category); track k) { <li><va-icon name="sparkles" [size]="14"></va-icon>{{ k }}</li> }
            </ul>
          </va-section-card>

          <div class="banner" [class.warning]="d.conflictScore > 20" [class.info]="d.conflictScore <= 20">
            <va-icon [name]="d.conflictScore > 20 ? 'alert-triangle' : 'check-circle'" [size]="18"></va-icon>
            <span>
              @if (d.conflictScore > 20) { <b>Possible conflict ({{ d.conflictScore }}%)</b> with “Fee Structure v2” — review before approving so the counselor never quotes conflicting figures. }
              @else { No conflicts detected with other active documents. }
            </span>
          </div>
        </div>

        <!-- Metadata + approval -->
        <div class="stack gap-4">
          <va-section-card title="Metadata">
            <dl class="dl">
              <dt>Uploaded by</dt><dd>{{ d.uploadedBy }}</dd>
              <dt>Uploaded</dt><dd>{{ date(d.uploadedAt) }}</dd>
              <dt>Approved by</dt><dd>{{ d.approvedBy || '—' }}</dd>
              <dt>Effective</dt><dd>{{ d.effectiveDate ? date(d.effectiveDate) : '—' }}</dd>
              <dt>Expiry</dt><dd>{{ d.expiryDate ? date(d.expiryDate) : '—' }}</dd>
              <dt>AI training</dt><dd>{{ d.aiTrainingStatus }}</dd>
              <dt>Usage</dt><dd>{{ d.usageCount }} answers</dd>
              <dt>Last used</dt><dd>{{ d.lastUsedAt ? rel(d.lastUsedAt) : '—' }}</dd>
            </dl>
            <div class="conf">
              <span class="t-sm">AI confidence</span>
              <va-probability-badge [value]="d.confidenceScore" [ai]="true"></va-probability-badge>
            </div>
            <div class="tags">@for (t of d.tags; track t) { <span class="chip">#{{ t }}</span> }</div>
          </va-section-card>

          <va-section-card title="Approval">
            @if (d.status === 'Active' || d.status === 'Approved') {
              <div class="banner info"><va-icon name="check-circle" [size]="16"></va-icon><span>This document is approved and active. The counselor may use it.</span></div>
            } @else {
              <div class="field"><span class="label">Reviewer comment</span><textarea class="textarea" rows="3" placeholder="Add a note for the requester…"></textarea></div>
              <div class="appr-actions">
                <button class="btn btn-primary btn-block" (click)="approve()"><va-icon name="check" [size]="16"></va-icon> Approve → make Active</button>
                <button class="btn btn-ghost btn-block" (click)="toast.info('Change requested — sent back to the Knowledge Manager.')"><va-icon name="edit" [size]="16"></va-icon> Request changes</button>
                <button class="btn btn-danger btn-block" (click)="toast.warning('Rejected. A reason is required and was recorded in the audit log.')"><va-icon name="x" [size]="16"></va-icon> Reject</button>
              </div>
              <p class="t-cap t-muted approval-note"><va-icon name="info" [size]="13"></va-icon> Approval gates the document to Active and logs an audit entry. Two-step routing: Knowledge Manager → Compliance.</p>
            }
          </va-section-card>
        </div>
      </div>
    } @else {
      <va-empty icon="book-open" title="Document not found" message="This document may have been archived or deleted." cta="Back to library" ctaIcon="arrow-left" (action)="router.navigateByUrl('/app/kms')"></va-empty>
    }
  </div>`,
  styles: [`
    :host { display: block; }
    .back { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-sm); font-weight: 600; color: var(--color-text-muted); margin-bottom: 14px; }
    .back:hover { color: var(--color-text); }
    .dh-main { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
    .dh-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .dh-actions { display: flex; gap: 8px; }
    .status-pill { font-size: var(--text-cap); font-weight: 700; padding: 4px 10px; border-radius: var(--r-pill); }
    .status-pill[data-g='active'] { background: var(--color-success-soft); color: var(--color-success); }
    .status-pill[data-g='warn'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .status-pill[data-g='danger'] { background: var(--color-danger-soft); color: var(--color-danger); }
    .status-pill[data-g='muted'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .active-by { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; color: var(--color-accent-2); }
    .grid { display: grid; grid-template-columns: minmax(0,1.6fr) minmax(0,1fr); gap: 18px; align-items: start; }
    .pdf { padding: 20px; background: var(--color-surface-alt); display: flex; justify-content: center; }
    .pdf-page { background: #fff; color: #0F172A; width: 100%; max-width: 540px; border-radius: 6px; box-shadow: var(--e2); padding: 32px; }
    .pdf-h { font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: #64748B; border-bottom: 1px solid #E2E8F0; padding-bottom: 8px; margin-bottom: 16px; }
    .pdf-title { font-size: 1.3rem; margin-bottom: 4px; color: #0F172A; }
    .pdf-p { font-size: 13px; line-height: 1.7; color: #334155; margin-top: 12px; }
    .mini-fee { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; color: #0F172A; }
    .mini-fee th, .mini-fee td { text-align: left; padding: 7px 4px; border-bottom: 1px solid #E2E8F0; }
    .mini-fee .num { text-align: right; }
    .mini-fee .total td { font-weight: 700; border-top: 2px solid #CBD5E1; border-bottom: none; }
    .diff { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .diff-col { padding: 12px; border-radius: var(--r-md); font-size: var(--text-sm); }
    .diff-col.old { background: var(--color-danger-soft); }
    .diff-col.new { background: var(--color-success-soft); }
    .diff-h { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; display: block; margin-bottom: 6px; }
    .diff-col mark { background: color-mix(in srgb, var(--color-success) 35%, transparent); padding: 0 3px; border-radius: 3px; }
    .ek { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 10px; }
    .ek li { display: flex; gap: 8px; font-size: var(--text-sm); }
    .ek va-icon { color: var(--color-accent-2); flex: none; margin-top: 2px; }
    .conf { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 14px 0 10px; padding-top: 12px; border-top: 1px solid var(--color-border); }
    .tags { display: flex; flex-wrap: wrap; gap: 6px; }
    .appr-actions { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
    .approval-note { display: flex; gap: 6px; margin-top: 12px; align-items: flex-start; }
    @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } .diff { grid-template-columns: 1fr; } }
  `],
})
export class KmsDetailComponent {
  private store = inject(DataStore);
  private route = inject(ActivatedRoute);
  router = inject(Router);
  toast = inject(ToastService);
  auth = 'Northgate University';
  diff = signal(false);

  private id = this.route.snapshot.paramMap.get('id');
  doc = computed(() => this.store.kmsDocs().find(d => d.documentId === this.id) ?? this.store.kmsDocs()[0]);

  date = fmtDate; rel = relTime;
  kb(n: number) { return n > 1024 ? (n / 1024).toFixed(1) + ' MB' : n + ' KB'; }
  statusGroup(s: string) {
    if (s === 'Active' || s === 'Approved') return 'active';
    if (/Rejected|Expired|Deletion/.test(s)) return 'danger';
    if (/Review|Approval|Processing/.test(s)) return 'warn';
    return 'muted';
  }
  previewParas(cat: string): string[] {
    if (cat === 'Scholarship Policy') return ['Northgate University offers merit-based and need-based scholarships for the 2026–27 admission cycle.', 'Merit scholarships are awarded on the basis of qualifying-examination performance and are renewable subject to academic standing.'];
    if (cat === 'Placement Report') return ['The placement report summarises verified offers for the most recent graduating batch across participating recruiters.', 'Figures reflect confirmed offers only and do not constitute a guarantee of employment for prospective candidates.'];
    return ['This official document forms part of Northgate University’s approved knowledge base for the 2026–27 admission cycle.', 'The AI admission counselor may reference this content only after compliance approval, and will always cite it as the source.'];
  }
  extracted(cat: string): string[] {
    if (cat === 'Fee Structure') return ['Total annual fee: ₹6,40,000', 'Tuition: ₹4,80,000 · Lab & technology: ₹85,000', 'Hostel is optional (₹1,20,000)', 'EMI options available via approved partners'];
    if (cat === 'Scholarship Policy') return ['Merit waiver up to 40% (85%+ in 12th)', 'Need-based add-on up to 15%', 'Renewable subject to CGPA ≥ 7.5', 'Application deadline aligns with admission cycle'];
    return ['Key entities and figures extracted for retrieval', 'Effective and expiry dates parsed', 'Source attribution preserved for every claim', 'No unsupported promises present'];
  }
  approve() { this.toast.success('Approved — document is now Active. The counselor may use it.'); this.router.navigateByUrl('/app/kms'); }
}
