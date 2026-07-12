import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { BandChipComponent } from '../../shared/ui/badges.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { EmptyStateComponent } from '../../shared/ui/layout.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { ApprovalRequest, Band } from '../../domain/models';
import { relFuture, relTime, fmtDate } from '../../shared/util/format';

interface HistoryStep {
  label: string;
  by: string;
  state: 'done' | 'current' | 'pending';
  note: string;
  ts?: string;
}

@Component({
  selector: 'va-approvals',
  standalone: true,
  imports: [IconComponent, BandChipComponent, AvatarComponent, AiAvatarComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page page-grid">
      <!-- Header -->
      <header class="ap-head">
        <div class="ap-head-text">
          <div class="t-h2">Approval workflow</div>
          <p class="t-sm t-muted">
            Review and approve changes before {{ counselor.activeMeta().name }} ({{ counselor.activeMeta().title }}) can use them — <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }}
          </p>
        </div>
        <div class="ap-head-actions">
          <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
          <span class="chip guard"><va-icon name="shield-check" [size]="13"></va-icon> Approved-knowledge-only</span>
          <span class="ap-count t-sm">
            <b class="t-num">{{ pending().length }}</b> awaiting · <b class="t-num breach">{{ breachedCount() }}</b> SLA breached
          </span>
        </div>
      </header>

      <!-- Guardrail banner -->
      <div class="banner ai guard-banner">
        <va-icon name="brain" [size]="18"></va-icon>
        <span>
          {{ counselor.activeMeta().name }} speaks only from approved knowledge. Every {{ career() ? 'salary-band figure, pathway recommendation and certification claim' : 'fee, scholarship and placement claim' }} passes a
          <b>two-step review</b> — Knowledge Manager, then Compliance — before going live across all channels.
        </span>
      </div>

      <!-- Two-pane workspace -->
      <div class="ap-workspace">
        <!-- LEFT queue -->
        <section class="ap-queue card">
          <header class="queue-head">
            <div class="queue-title">
              <span class="t-h4">Approval queue</span>
              <span class="t-cap t-muted">{{ pending().length }} item{{ pending().length === 1 ? '' : 's' }}</span>
            </div>
            <button class="btn btn-sm btn-subtle"
                    [disabled]="lowRiskCount() === 0"
                    (click)="bulkApproveLowRisk()"
                    title="Approve all low-risk items in one action">
              <va-icon name="check-square" [size]="14"></va-icon>
              Bulk-approve low-risk
              @if (lowRiskCount() > 0) { <span class="count">{{ lowRiskCount() }}</span> }
            </button>
          </header>

          <div class="queue-list scroll-y">
            @for (a of pending(); track a.requestId) {
              <button type="button"
                      class="queue-item"
                      [class.selected]="a.requestId === selectedId()"
                      [class.breached]="isBreached(a)"
                      (click)="select(a.requestId)">
                <span class="qi-rail" [attr.data-band]="a.riskLevel"></span>
                <div class="qi-body">
                  <div class="qi-row1">
                    <span class="qi-type">{{ a.requestType }}</span>
                    <va-band-chip [band]="a.riskLevel" [label]="riskLabel(a.riskLevel)"></va-band-chip>
                  </div>
                  <div class="qi-title truncate">{{ a.title }}</div>
                  <div class="qi-meta">
                    <span class="qi-by">
                      @if (isAi(a.requestedBy)) {
                        <va-ai-avatar [size]="18"></va-ai-avatar>
                      } @else {
                        <va-avatar [name]="a.requestedBy" [hue]="hueFor(a.requestedBy)" [size]="18"></va-avatar>
                      }
                      {{ a.requestedBy }}
                    </span>
                    <span class="qi-sla" [class.breach]="isBreached(a)">
                      <va-icon [name]="isBreached(a) ? 'alert-triangle' : 'clock'" [size]="12"></va-icon>
                      {{ isBreached(a) ? 'SLA breached' : 'SLA ' + relFuture(a.slaDueAt) }}
                    </span>
                  </div>
                </div>
              </button>
            } @empty {
              <va-empty icon="check-circle"
                        title="Nothing awaiting approval"
                        [message]="counselor.activeMeta().name + ' is fully up to date. New change requests from Knowledge and Compliance will appear here.'">
              </va-empty>
            }
          </div>
        </section>

        <!-- RIGHT detail -->
        <section class="ap-detail">
          @if (selected(); as a) {
            <div class="card detail-card">
              <!-- Detail header -->
              <header class="detail-head" [class.breached]="isBreached(a)">
                <div class="dh-top">
                  <span class="dh-type"><va-icon [name]="typeIcon(a.requestType)" [size]="13"></va-icon> {{ a.requestType }}</span>
                  <va-band-chip [band]="a.riskLevel" [label]="riskLabel(a.riskLevel) + ' risk'"></va-band-chip>
                  <span class="dh-status" [attr.data-s]="a.status">{{ a.status }}</span>
                  <span class="dh-sla" [class.breach]="isBreached(a)">
                    <va-icon [name]="isBreached(a) ? 'alert-triangle' : 'clock'" [size]="13"></va-icon>
                    {{ isBreached(a) ? 'SLA breached · ' + relFuture(a.slaDueAt) : 'Due ' + relFuture(a.slaDueAt) }}
                  </span>
                </div>
                <h2 class="t-h3 dh-title">{{ a.title }}</h2>
                <div class="dh-by">
                  @if (isAi(a.requestedBy)) {
                    <va-ai-avatar [size]="22"></va-ai-avatar>
                  } @else {
                    <va-avatar [name]="a.requestedBy" [hue]="hueFor(a.requestedBy)" [size]="22"></va-avatar>
                  }
                  <span>Requested by <b>{{ a.requestedBy }}</b> · {{ relTime(a.createdAt) }} · {{ a.entityType }}</span>
                </div>
              </header>

              <div class="detail-scroll scroll-y">
                <!-- AI impact callout -->
                <div class="callout ai-impact">
                  <span class="ci-icon"><va-icon name="bot" [size]="16"></va-icon></span>
                  <div>
                    <div class="ci-label">AI impact on {{ counselor.activeMeta().name }}</div>
                    <p class="t-sm">{{ a.aiImpact }}</p>
                    @if (isClaimBearing(a)) {
                      <span class="claim-flag"><va-icon name="alert-circle" [size]="12"></va-icon> Claim-bearing — never auto-approved</span>
                    }
                  </div>
                </div>

                <!-- Change summary -->
                <div class="block">
                  <div class="block-label"><va-icon name="file-text" [size]="14"></va-icon> Change summary</div>
                  <p class="t-sm summary-text">{{ a.changeSummary }}</p>
                </div>

                <!-- Current vs Proposed diff -->
                <div class="block">
                  <div class="block-label"><va-icon name="git-branch" [size]="14"></va-icon> Current vs proposed</div>
                  <div class="diff">
                    <div class="diff-col current">
                      <div class="diff-head">
                        <span class="diff-tag cur">Current</span>
                        <span class="t-cap t-muted">What {{ counselor.activeMeta().name }} says today</span>
                      </div>
                      <p class="diff-body">{{ a.current || 'No existing approved answer.' }}</p>
                    </div>
                    <div class="diff-arrow"><va-icon name="arrow-right" [size]="16"></va-icon></div>
                    <div class="diff-col proposed">
                      <div class="diff-head">
                        <span class="diff-tag prop">Proposed</span>
                        <span class="t-cap t-muted">Pending your approval</span>
                      </div>
                      <p class="diff-body">{{ a.proposed || 'No proposed text supplied.' }}</p>
                    </div>
                  </div>
                </div>

                <!-- Approval history -->
                <div class="block">
                  <div class="block-label"><va-icon name="scroll-text" [size]="14"></va-icon> Approval history</div>
                  <ol class="hist">
                    @for (h of history(a); track h.label) {
                      <li class="hist-step" [attr.data-state]="h.state">
                        <span class="hist-node">
                          <va-icon [name]="h.state === 'done' ? 'check' : h.state === 'current' ? 'dot' : 'circle'" [size]="12"></va-icon>
                        </span>
                        <div class="hist-body">
                          <div class="hist-row1">
                            <span class="hist-label">{{ h.label }}</span>
                            @if (h.state === 'current') { <span class="hist-badge cur">In review</span> }
                            @if (h.state === 'pending') { <span class="hist-badge pen">Pending</span> }
                            @if (h.ts) { <span class="hist-ts t-cap t-muted">{{ h.ts }}</span> }
                          </div>
                          <p class="hist-note t-sm t-muted">{{ h.note }}</p>
                          <span class="hist-by t-cap t-muted">{{ h.by }}</span>
                        </div>
                      </li>
                    }
                  </ol>
                </div>

                <!-- Reject reason (conditional) -->
                @if (rejecting()) {
                  <div class="block reject-block">
                    <div class="block-label danger"><va-icon name="alert-circle" [size]="14"></va-icon> Reason for rejection (required)</div>
                    <textarea class="textarea reject-input"
                              rows="3"
                              placeholder="Explain why this change can't be approved — sent back to the requester."
                              [value]="rejectReason()"
                              (input)="onRejectReason($event)"></textarea>
                    <div class="reject-actions">
                      <button class="btn btn-sm btn-ghost" (click)="cancelReject()">Cancel</button>
                      <button class="btn btn-sm btn-danger" [disabled]="!rejectReason().trim()" (click)="confirmReject(a)">
                        <va-icon name="x" [size]="14"></va-icon> Confirm rejection
                      </button>
                    </div>
                  </div>
                }
              </div>

              <!-- Action footer -->
              <footer class="detail-foot">
                <div class="foot-step t-cap t-muted">
                  <va-icon name="git-branch" [size]="13"></va-icon>
                  Step <b>{{ stepIndex(a) }} of 2</b> · {{ a.step }}
                  @if (a.step === 'Knowledge Manager') { <span>→ next: Compliance</span> }
                  @else { <span>→ final sign-off</span> }
                </div>
                <div class="foot-actions">
                  <button class="btn btn-ghost btn-sm" (click)="requestChanges(a)">
                    <va-icon name="refresh" [size]="14"></va-icon> Request changes
                  </button>
                  <button class="btn btn-danger btn-sm" (click)="startReject()" [class.active-danger]="rejecting()">
                    <va-icon name="x" [size]="14"></va-icon> Reject
                  </button>
                  <button class="btn btn-primary btn-sm" (click)="approve(a)">
                    <va-icon name="check" [size]="14"></va-icon>
                    {{ a.step === 'Knowledge Manager' ? 'Approve & send to Compliance' : 'Approve' }}
                  </button>
                </div>
              </footer>
            </div>
          } @else {
            <div class="card detail-empty">
              <va-empty icon="clipboard-check"
                        title="Select a request to review"
                        message="Pick an item from the queue to see its AI impact, the current vs proposed diff and the approval trail.">
              </va-empty>
            </div>
          }
        </section>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }

    .ap-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .ap-head-text p { margin-top: 4px; max-width: 70ch; }
    .ap-head-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}
    .chip.guard { display: inline-flex; align-items: center; gap: 5px; background: var(--color-success-soft);
      color: var(--color-success); font-weight: 600; }
    .ap-count { color: var(--color-text-muted); }
    .ap-count b { color: var(--color-text); }
    .ap-count .breach { color: var(--color-danger); }

    .guard-banner { align-items: center; }
    .guard-banner b { font-weight: 700; }

    /* Workspace layout */
    .ap-workspace { display: grid; grid-template-columns: minmax(330px, 380px) 1fr; gap: var(--s-5);
      align-items: stretch; min-height: 560px; }
    @media (max-width: 1080px) { .ap-workspace { grid-template-columns: 1fr; } }

    /* Queue */
    .ap-queue { display: flex; flex-direction: column; overflow: hidden; min-height: 0; padding: 0; }
    .queue-head { display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 14px 16px; border-bottom: 1px solid var(--color-border); }
    .queue-title { display: flex; flex-direction: column; gap: 1px; }
    .queue-head .count { display: inline-grid; place-items: center; min-width: 18px; height: 18px; padding: 0 5px;
      margin-left: 4px; border-radius: 999px; background: var(--color-success-soft); color: var(--color-success);
      font-size: 11px; font-weight: 700; }
    .queue-list { padding: 8px; display: flex; flex-direction: column; gap: 6px; overflow-y: auto; max-height: 660px; }

    .queue-item { position: relative; text-align: left; width: 100%; background: var(--color-surface);
      border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 11px 12px 11px 16px;
      cursor: pointer; transition: border-color .14s ease, background .14s ease, box-shadow .14s ease; overflow: hidden; }
    .queue-item:hover { border-color: var(--color-border-strong); background: var(--color-surface-alt); }
    .queue-item.selected { border-color: var(--color-primary);
      box-shadow: 0 0 0 1px var(--color-primary), var(--e1); background: rgba(var(--color-primary-rgb), .04); }
    .queue-item.breached { border-color: color-mix(in srgb, var(--color-danger) 35%, var(--color-border)); }
    .queue-item.breached.selected { box-shadow: 0 0 0 1px var(--color-danger), var(--e1); }
    .qi-rail { position: absolute; left: 0; top: 0; bottom: 0; width: 4px; }
    .qi-rail[data-band='low'] { background: var(--band-low); }
    .qi-rail[data-band='med'] { background: var(--band-med); }
    .qi-rail[data-band='high'] { background: var(--band-high); }
    .qi-body { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .qi-row1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .qi-type { font-size: 11px; font-weight: 700; letter-spacing: .02em; text-transform: uppercase;
      color: var(--color-text-muted); }
    .qi-title { font-size: var(--text-sm); font-weight: 600; line-height: 1.35; color: var(--color-text); }
    .qi-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .qi-by { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap);
      color: var(--color-text-muted); min-width: 0; }
    .qi-sla { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600;
      color: var(--color-text-muted); white-space: nowrap; }
    .qi-sla.breach { color: var(--color-danger); }

    /* Detail */
    .ap-detail { display: flex; min-height: 0; }
    .detail-card, .detail-empty { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0; padding: 0; }
    .detail-empty { align-items: stretch; justify-content: center; }

    .detail-head { padding: 18px 20px; border-bottom: 1px solid var(--color-border); }
    .detail-head.breached { background: var(--color-danger-soft);
      border-bottom-color: color-mix(in srgb, var(--color-danger) 30%, var(--color-border)); }
    .dh-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .dh-type { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700;
      letter-spacing: .02em; text-transform: uppercase; color: var(--color-text-muted); }
    .dh-status { font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: var(--r-pill);
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .dh-status[data-s='Submitted'] { background: rgba(var(--color-accent-rgb), .12); color: var(--color-accent); }
    .dh-status[data-s='Under Review'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .dh-status[data-s='Changes Requested'] { background: rgba(var(--color-accent-2-rgb), .14); color: var(--color-accent-2); }
    .dh-sla { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; font-size: var(--text-cap);
      font-weight: 600; color: var(--color-text-muted); }
    .dh-sla.breach { color: var(--color-danger); }
    .dh-title { margin: 12px 0 10px; }
    .dh-by { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); color: var(--color-text-muted); }
    .dh-by b { color: var(--color-text); font-weight: 600; }

    .detail-scroll { flex: 1; overflow-y: auto; padding: 18px 20px; display: flex; flex-direction: column; gap: 20px; }

    .callout { display: flex; gap: 12px; padding: 14px; border-radius: var(--r-md); border: 1px solid transparent; }
    .ai-impact { background: rgba(var(--color-accent-2-rgb), .07);
      border-color: rgba(var(--color-accent-2-rgb), .22); }
    .ci-icon { width: 30px; height: 30px; flex: none; border-radius: 9px; display: grid; place-items: center;
      background: var(--gradient-ai); color: #06121A; }
    .ci-label { font-size: var(--text-cap); font-weight: 700; letter-spacing: .02em; text-transform: uppercase;
      color: var(--color-accent-2); margin-bottom: 3px; }
    .ai-impact p { margin: 0; color: var(--color-text); }
    .claim-flag { display: inline-flex; align-items: center; gap: 4px; margin-top: 8px; font-size: 11px;
      font-weight: 700; color: var(--color-danger); }

    .block { display: flex; flex-direction: column; gap: 10px; }
    .block-label { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700;
      letter-spacing: .02em; text-transform: uppercase; color: var(--color-text-muted); }
    .block-label.danger { color: var(--color-danger); }
    .summary-text { margin: 0; color: var(--color-text); line-height: 1.5; }

    /* Diff */
    .diff { display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: stretch; }
    @media (max-width: 720px) { .diff { grid-template-columns: 1fr; }
      .diff-arrow { transform: rotate(90deg); justify-self: center; } }
    .diff-col { display: flex; flex-direction: column; border: 1px solid var(--color-border);
      border-radius: var(--r-md); overflow: hidden; }
    .diff-col.proposed { border-color: color-mix(in srgb, var(--color-success) 45%, var(--color-border));
      background: var(--color-success-soft); }
    .diff-head { display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding: 8px 12px; border-bottom: 1px solid var(--color-border); }
    .diff-col.proposed .diff-head { border-bottom-color: color-mix(in srgb, var(--color-success) 30%, var(--color-border)); }
    .diff-tag { font-size: 10px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase;
      padding: 2px 7px; border-radius: var(--r-pill); }
    .diff-tag.cur { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .diff-tag.prop { background: var(--color-success); color: #fff; }
    .diff-body { margin: 0; padding: 12px; font-size: var(--text-sm); line-height: 1.55; color: var(--color-text); }
    .diff-col.current .diff-body { color: var(--color-text-muted); }
    .diff-arrow { display: grid; place-items: center; color: var(--color-text-muted); }

    /* History */
    .hist { list-style: none; margin: 0; padding: 0; position: relative; }
    .hist::before { content: ''; position: absolute; left: 11px; top: 10px; bottom: 14px; width: 2px;
      background: var(--color-border); }
    .hist-step { position: relative; display: grid; grid-template-columns: 24px 1fr; gap: 12px; padding-bottom: 16px; }
    .hist-step:last-child { padding-bottom: 0; }
    .hist-node { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; z-index: 1;
      background: var(--color-surface); border: 2px solid var(--color-border); color: var(--color-text-muted); }
    .hist-step[data-state='done'] .hist-node { background: var(--color-success); border-color: var(--color-success); color: #fff; }
    .hist-step[data-state='current'] .hist-node { border-color: var(--color-warning); color: var(--color-warning);
      box-shadow: 0 0 0 3px var(--color-warning-soft); }
    .hist-body { min-width: 0; }
    .hist-row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .hist-label { font-size: var(--text-sm); font-weight: 600; }
    .hist-badge { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: var(--r-pill); }
    .hist-badge.cur { background: var(--color-warning-soft); color: var(--color-warning); }
    .hist-badge.pen { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .hist-ts { margin-left: auto; }
    .hist-note { margin: 3px 0 2px; line-height: 1.45; }
    .hist-by { font-weight: 600; }

    /* Reject */
    .reject-block { padding: 14px; border-radius: var(--r-md); background: var(--color-danger-soft);
      border: 1px solid color-mix(in srgb, var(--color-danger) 30%, transparent); }
    .reject-input { width: 100%; resize: vertical; }
    .reject-actions { display: flex; justify-content: flex-end; gap: 8px; }

    /* Footer */
    .detail-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
      padding: 14px 20px; border-top: 1px solid var(--color-border); background: var(--color-surface-alt); }
    .foot-step { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .foot-step b { color: var(--color-text); }
    .foot-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .btn.active-danger { box-shadow: 0 0 0 2px var(--color-danger-soft); }
  `],
})
export class ApprovalsComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');

  relFuture = relFuture;
  relTime = relTime;
  fmtDate = fmtDate;

  /** Career-counsellor (Vera) approval queue — salary bands, pathways, certifications, mentor scripts. */
  private careerApprovals = signal<ApprovalRequest[]>([
    {
      requestId: 'capr-001', title: 'Update Data Analyst salary band to FY26 market data',
      requestType: 'Salary-band data', entityType: 'Salary Band', requestedBy: 'Vera (AI)',
      status: 'Under Review', riskLevel: 'high',
      aiImpact: 'Vera would quote the refreshed ₹6–11 LPA range when students ask about Data Analyst pay.',
      changeSummary: 'Raise the approved Data Analyst band from ₹5–9 LPA to ₹6–11 LPA per the FY26 market dataset.',
      current: 'Data Analyst: ₹5–9 LPA (FY25 band, indicative market range).',
      proposed: 'Data Analyst: ₹6–11 LPA (FY26 band, indicative market range — varies by location and skills).',
      slaDueAt: '2026-06-15T18:00:00', createdAt: '2026-06-14T08:10:00', step: 'Compliance',
    },
    {
      requestId: 'capr-002', title: 'Add “Cloud Engineering” to the approved pathway library',
      requestType: 'Pathway recommendation', entityType: 'Career Pathway', requestedBy: 'Kavya Iyer',
      status: 'Submitted', riskLevel: 'med',
      aiImpact: 'Vera could recommend the Cloud Engineering pathway, with prerequisites, to suitable students.',
      changeSummary: 'Publish a new Cloud Engineering pathway with prerequisites, milestones and linked skill tracks.',
      current: 'No Cloud Engineering pathway exists in the approved library.',
      proposed: 'Cloud Engineering pathway — prerequisites: Linux, networking basics; tracks: AWS/Azure fundamentals.',
      slaDueAt: '2026-06-16T12:00:00', createdAt: '2026-06-14T07:40:00', step: 'Knowledge Manager',
    },
    {
      requestId: 'capr-003', title: 'Certification claim: “AWS SAA recognised by employers”',
      requestType: 'Certification claim', entityType: 'Certification', requestedBy: 'Vera (AI)',
      status: 'Under Review', riskLevel: 'high',
      aiImpact: 'Controls how Vera describes the value of the AWS Solutions Architect Associate certificate.',
      changeSummary: 'Soften an outcome claim so the certificate is framed as recognised, never as a hiring guarantee.',
      current: 'Earning AWS SAA gets you hired as a cloud engineer.',
      proposed: 'AWS SAA is widely recognised by employers and strengthens a cloud profile; it does not guarantee a job.',
      slaDueAt: '2026-06-14T07:00:00', createdAt: '2026-06-13T15:20:00', step: 'Compliance',
    },
    {
      requestId: 'capr-004', title: 'Mentor-match outreach script — final-year students',
      requestType: 'Mentor-match script', entityType: 'Outreach Template', requestedBy: 'Meera Nair',
      status: 'Changes Requested', riskLevel: 'low',
      aiImpact: 'The script Vera uses when introducing a student to a matched mentor on WhatsApp.',
      changeSummary: 'Add AI disclosure and remove any wording that implies a guaranteed referral or placement.',
      current: 'Hi! I’ve matched you with a mentor who will help you land a role.',
      proposed: 'Hi! I’m Vera, an AI career counsellor. I’ve matched you with a mentor for guidance — outcomes aren’t guaranteed.',
      slaDueAt: '2026-06-17T10:00:00', createdAt: '2026-06-13T11:05:00', step: 'Knowledge Manager',
    },
    {
      requestId: 'capr-005', title: 'Aptitude wording: present scores as indicative',
      requestType: 'Guardrail change', entityType: 'Guardrail', requestedBy: 'Imran Sheikh',
      status: 'Under Review', riskLevel: 'med',
      aiImpact: 'Stops Vera from presenting an aptitude score as a fixed verdict on a student’s ability.',
      changeSummary: 'Require Vera to frame aptitude and interest results as indicative signals, never deterministic.',
      current: 'Vera may state a recommended field directly from the aptitude score.',
      proposed: 'Vera must describe aptitude results as indicative guidance and offer multiple options to explore.',
      slaDueAt: '2026-06-16T16:00:00', createdAt: '2026-06-13T09:30:00', step: 'Compliance',
    },
  ]);

  pending = computed(() => this.career() ? this.careerApprovals() : this.store.approvals());

  private _selectedId = signal<string | null>(this.route.snapshot.paramMap.get('id'));
  selectedId = this._selectedId.asReadonly();

  selected = computed<ApprovalRequest | undefined>(() => {
    const list = this.pending();
    if (list.length === 0) return undefined;
    const id = this._selectedId();
    return list.find(a => a.requestId === id) ?? list[0];
  });

  breachedCount = computed(() => this.pending().filter(a => this.isBreached(a)).length);
  lowRiskCount = computed(() => this.pending().filter(a => a.riskLevel === 'low').length);

  rejecting = signal(false);
  rejectReason = signal('');

  private hues = [222, 268, 12, 192, 142, 318];
  private readonly nowMs = new Date('2026-06-14T09:30:00').getTime();

  select(id: string) {
    this._selectedId.set(id);
    this.rejecting.set(false);
    this.rejectReason.set('');
    this.router.navigate(['/app/approvals', id]);
  }

  isBreached(a: ApprovalRequest): boolean {
    return new Date(a.slaDueAt).getTime() < this.nowMs;
  }

  isAi(by: string): boolean { return /aria|ai/i.test(by); }

  hueFor(name: string): number {
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h + name.charCodeAt(i)) % this.hues.length;
    return this.hues[h];
  }

  riskLabel(b: Band): string { return b === 'high' ? 'High' : b === 'med' ? 'Medium' : 'Low'; }

  isClaimBearing(a: ApprovalRequest): boolean {
    return /fee|scholarship|placement|refund/i.test(a.requestType + ' ' + a.title) || a.riskLevel === 'high';
  }

  typeIcon(type: string): string {
    if (/document|kms/i.test(type)) return 'file-text';
    if (/guardrail/i.test(type)) return 'shield-check';
    if (/whatsapp/i.test(type)) return 'message-circle';
    if (/email/i.test(type)) return 'mail';
    if (/voice/i.test(type)) return 'mic';
    if (/placement/i.test(type)) return 'graduation-cap';
    if (/scholarship|fee/i.test(type)) return 'dollar-sign';
    return 'file-check';
  }

  stepIndex(a: ApprovalRequest): number { return a.step === 'Knowledge Manager' ? 1 : 2; }

  history(a: ApprovalRequest): HistoryStep[] {
    const atCompliance = a.step === 'Compliance';
    return [
      {
        label: 'Submitted',
        by: a.requestedBy,
        state: 'done',
        note: `Change request raised — ${a.changeSummary.toLowerCase()}`,
        ts: relTime(a.createdAt),
      },
      {
        label: 'Knowledge Manager review',
        by: 'Kavya Iyer · Knowledge Manager',
        state: atCompliance ? 'done' : 'current',
        note: atCompliance
          ? 'Verified against source documents and version history. Forwarded to Compliance.'
          : 'Checking the change against approved source documents and existing answers.',
        ts: atCompliance ? relTime(a.createdAt) : undefined,
      },
      {
        label: 'Compliance sign-off',
        by: 'Sneha Banerjee · Compliance Officer',
        state: atCompliance ? 'current' : 'pending',
        note: atCompliance
          ? `Final review for claim accuracy and regulatory wording before ${this.counselor.activeMeta().name} goes live with it.`
          : 'Awaiting Knowledge Manager approval before compliance review begins.',
      },
    ];
  }

  private selectNext(currentId: string) {
    const list = this.pending();
    if (list.length === 0) { this._selectedId.set(null); return; }
    const idx = list.findIndex(a => a.requestId === currentId);
    const next = list[idx] ?? list[Math.max(0, idx - 1)] ?? list[0];
    this._selectedId.set(next.requestId);
    this.router.navigate(['/app/approvals', next.requestId]);
  }

  /** Remove a request from whichever counsellor's queue is active. */
  private removeRequest(id: string) {
    if (this.career()) {
      this.careerApprovals.update(list => list.filter(a => a.requestId !== id));
    } else {
      this.store.approve(id);
    }
  }

  approve(a: ApprovalRequest) {
    const next = a.step === 'Knowledge Manager';
    this.removeRequest(a.requestId);
    this.toast.success(
      next
        ? `Approved — "${a.title}" sent to Compliance for final sign-off.`
        : `Approved — ${this.counselor.activeMeta().name} can now use "${a.title}".`,
    );
    this.rejecting.set(false);
    this.rejectReason.set('');
    this.selectNext(a.requestId);
  }

  startReject() {
    this.rejecting.set(true);
    this.rejectReason.set('');
  }
  cancelReject() {
    this.rejecting.set(false);
    this.rejectReason.set('');
  }
  onRejectReason(e: Event) { this.rejectReason.set((e.target as HTMLTextAreaElement).value); }

  confirmReject(a: ApprovalRequest) {
    if (!this.rejectReason().trim()) return;
    if (this.career()) {
      this.careerApprovals.update(list => list.filter(x => x.requestId !== a.requestId));
    } else {
      this.store.reject(a.requestId);
    }
    this.toast.danger(`Rejected — "${a.title}" returned to ${a.requestedBy} with your note.`);
    this.rejecting.set(false);
    this.rejectReason.set('');
    this.selectNext(a.requestId);
  }

  requestChanges(a: ApprovalRequest) {
    this.toast.info(`Changes requested on "${a.title}" — ${a.requestedBy} notified to revise and resubmit.`);
  }

  bulkApproveLowRisk() {
    const low = this.pending().filter(a => a.riskLevel === 'low');
    if (low.length === 0) return;
    low.forEach(a => this.removeRequest(a.requestId));
    this.toast.success(`Bulk-approved ${low.length} low-risk request${low.length === 1 ? '' : 's'}.`);
    const remaining = this.pending();
    if (remaining.length === 0) { this._selectedId.set(null); }
    else if (!remaining.some(a => a.requestId === this._selectedId())) {
      this._selectedId.set(remaining[0].requestId);
    }
  }
}
