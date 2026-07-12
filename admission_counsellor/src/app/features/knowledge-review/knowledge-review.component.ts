import { ChangeDetectionStrategy, Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../shared/ui/icon.component';
import { environment } from '../../../environments/environment';

/**
 * KnowledgeReviewComponent — the POST-CALL governance queue for facts the
 * director captured during a video briefing. Reads pending candidates from
 * AegisBackend (which proxies the BusinessLayer store) and edits/resolves them.
 *
 * Ingest lives in AegisBackend (it owns Qdrant), so this screen talks to
 * AegisBackend's /api/knowledge surface — NOT the BusinessLayer directly. A
 * BLOCKING conflict disables one-click Approve (Supersede / Keep both instead),
 * mirroring the in-call card.
 */
const API = environment.aegisUrl.replace(/\/$/, '');

interface ConflictItem {
  point_id: string | null; source_doc: string; version: string; relation: string;
  confidence: number; attribute: string; old_value: string; new_value: string;
  span: string; explanation: string;
}
interface Candidate {
  candidate_id: string; conversation_id: string; tenant_id: string;
  status: string; text: string; heading: string; topic: string; kb: string;
  source_span?: string; trigger: string; confidence: number;
  conflict_score: number; blocking: boolean; conflict_items: ConflictItem[];
  version: number; created_at: string;
}

@Component({
  selector: 'va-knowledge-review',
  standalone: true,
  imports: [IconComponent, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="kr">
    <header class="kr-head">
      <div>
        <h1 class="t-h2">Knowledge review</h1>
        <p class="t-sm t-muted">Facts captured from director briefings, awaiting approval into the knowledge base.</p>
      </div>
      <button class="btn btn-ghost btn-sm" (click)="load()"><va-icon name="refresh" [size]="14"></va-icon> Refresh</button>
    </header>

    @if (notice(); as n) {
      <div class="kr-notice" [class.err]="n.kind === 'error'">
        <va-icon [name]="n.kind === 'error' ? 'alert-triangle' : 'check-circle'" [size]="14"></va-icon>
        {{ n.text }}
        <button class="kr-notice-x" (click)="notice.set(null)" aria-label="Dismiss">×</button>
      </div>
    }

    @if (loading()) {
      <p class="t-sm t-muted">Loading…</p>
    } @else if (error()) {
      <p class="t-sm" style="color:#c0392b">{{ error() }}</p>
    } @else if (!candidates().length) {
      <div class="kr-empty"><va-icon name="check-circle" [size]="28"></va-icon><p>No pending knowledge to review.</p></div>
    } @else {
      <div class="kr-list">
        @for (c of candidates(); track c.candidate_id) {
          <div class="kr-card" [class.blocking]="c.blocking">
            <div class="kr-row">
              <span class="chip">{{ c.topic || 'general' }}</span>
              <span class="chip">{{ c.kb }}</span>
              <span class="chip">{{ c.trigger }}</span>
              <span class="kr-conf">conf {{ c.confidence }}% · {{ fmt(c.created_at) }}</span>
            </div>

            @if (editingId() === c.candidate_id) {
              <textarea class="kr-edit" rows="3" [(ngModel)]="draft"></textarea>
              <div class="kr-actions">
                <button class="btn btn-primary btn-sm" (click)="saveEdit(c)"><va-icon name="check" [size]="14"></va-icon> Save &amp; re-check</button>
                <button class="btn btn-ghost btn-sm" (click)="editingId.set(null)">Cancel</button>
              </div>
            } @else {
              <p class="kr-heading">{{ c.heading }}</p>
              <p class="kr-text">{{ c.text }}</p>
              @if (c.source_span && c.source_span !== c.text) { <p class="kr-span">heard: “{{ c.source_span }}”</p> }

              @if (c.conflict_score > 20 && c.conflict_items.length) {
                <div class="kr-conflict" [class.block]="c.blocking">
                  <div class="kr-conflict-head">
                    <va-icon [name]="c.blocking ? 'alert-triangle' : 'info'" [size]="13"></va-icon>
                    {{ c.blocking ? 'Conflict' : 'Possible overlap' }} ({{ c.conflict_score }}%)
                  </div>
                  @for (it of c.conflict_items; track it.point_id) {
                    <div class="kr-conflict-item"><b>{{ it.source_doc }}</b>
                      @if (it.old_value || it.new_value) { · {{ it.old_value }} → {{ it.new_value }} }
                      <span class="kr-rel">{{ it.relation }}</span> — {{ it.explanation }}
                    </div>
                  }
                </div>
              }

              <div class="kr-actions">
                <button class="btn btn-primary btn-sm" [disabled]="c.blocking || busyId() === c.candidate_id" (click)="resolve(c, 'approve')"><va-icon name="check-circle" [size]="14"></va-icon> Approve</button>
                @if (c.blocking || c.conflict_items.length) {
                  <button class="btn btn-accent btn-sm" [disabled]="busyId() === c.candidate_id" (click)="resolve(c, 'supersede')">Supersede</button>
                  <button class="btn btn-ghost btn-sm" [disabled]="busyId() === c.candidate_id" (click)="resolve(c, 'keep_both')">Keep both</button>
                }
                <button class="btn btn-ghost btn-sm" (click)="startEdit(c)"><va-icon name="edit" [size]="14"></va-icon> Edit</button>
                <button class="btn btn-ghost btn-sm kr-reject" [disabled]="busyId() === c.candidate_id" (click)="resolve(c, 'reject')">Reject</button>
              </div>
            }
          </div>
        }
      </div>
    }
  </div>
  `,
  styles: [`
    .kr { padding: 18px; max-width: 820px; }
    .kr-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
    .kr-notice { display: flex; align-items: center; gap: 7px; margin-bottom: 12px; padding: 8px 12px;
                 border-radius: 10px; font-size: 13px; background: #e8f7ee; border: 1px solid #bfe6cd; color: #1e7d43; }
    .kr-notice.err { background: #fdecea; border-color: #f3a9a0; color: #c0392b; }
    .kr-notice-x { margin-left: auto; border: none; background: transparent; cursor: pointer;
                   font-size: 16px; line-height: 1; color: inherit; padding: 0 2px; }
    .kr-empty { text-align: center; color: var(--muted, #888); padding: 40px; }
    .kr-list { display: flex; flex-direction: column; gap: 12px; }
    .kr-card { border: 1px solid var(--border, #e3e3e8); border-radius: 12px; padding: 12px 14px; background: var(--surface, #fff); }
    .kr-card.blocking { border-color: #e0a800; }
    .kr-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
    .kr-conf { margin-left: auto; color: var(--muted, #888); font-size: 12px; }
    .kr-heading { font-weight: 600; margin: 2px 0; }
    .kr-text { margin: 2px 0; }
    .kr-span { color: var(--muted, #888); font-size: 12px; font-style: italic; margin: 2px 0 8px; }
    .kr-edit { width: 100%; box-sizing: border-box; border: 1px solid var(--border, #ccc); border-radius: 8px; padding: 8px; font: inherit; resize: vertical; }
    .kr-conflict { background: #fff8e6; border: 1px solid #f0d98a; border-radius: 8px; padding: 7px 9px; margin: 6px 0 10px; font-size: 12px; }
    .kr-conflict.block { background: #fdecea; border-color: #f3a9a0; }
    .kr-conflict-head { display: flex; align-items: center; gap: 5px; font-weight: 600; margin-bottom: 3px; }
    .kr-rel { color: #a06; font-style: italic; }
    .kr-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
    .kr-reject { color: #c0392b; margin-left: auto; }
  `],
})
export class KnowledgeReviewComponent implements OnInit {
  readonly candidates = signal<Candidate[]>([]);
  readonly loading = signal(true);
  /** Candidate currently being resolved/edited — only that card's buttons disable. */
  readonly busyId = signal<string | null>(null);
  readonly error = signal<string | null>(null);
  /** Transient outcome banner — this screen is the only action surface, so
   *  failures (version conflict, ingest error) must be visible, not silent. */
  readonly notice = signal<{ kind: 'success' | 'error'; text: string } | null>(null);
  readonly editingId = signal<string | null>(null);
  draft = '';
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void { void this.load(); }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const res = await fetch(`${API}/api/knowledge/candidates?status=pending`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.candidates.set((data?.items ?? []) as Candidate[]);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Failed to load the review queue.');
    } finally {
      this.loading.set(false);
    }
  }

  fmt(iso: string): string {
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  }

  startEdit(c: Candidate): void { this.draft = c.text; this.editingId.set(c.candidate_id); }

  private flash(kind: 'success' | 'error', text: string): void {
    this.notice.set({ kind, text });
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => this.notice.set(null), 8_000);
  }

  /** Shared body handling for edit/resolve — surfaces the backend's soft errors
   *  (returned with HTTP 200) instead of silently ignoring them. Returns the
   *  parsed body, or null when an error was already reported. */
  private async parseOutcome(res: Response): Promise<Record<string, unknown> | null> {
    let body: Record<string, unknown> = {};
    try { body = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      this.flash('error', `Request failed (HTTP ${res.status}) — please try again.`);
      return null;
    }
    if (body['error'] === 'version_conflict') {
      this.flash('error', 'This item was changed elsewhere — the list has been refreshed.');
      return null;
    }
    if (body['error'] === 'not_found') {
      this.flash('error', 'This item no longer exists — the list has been refreshed.');
      return null;
    }
    return body;
  }

  async saveEdit(c: Candidate): Promise<void> {
    const text = this.draft.trim();
    this.editingId.set(null);
    if (!text || text === c.text) return;
    this.busyId.set(c.candidate_id);
    try {
      const res = await fetch(`${API}/api/knowledge/candidates/${c.candidate_id}/edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, expected_version: c.version }),
      });
      const body = await this.parseOutcome(res);
      if (body) this.flash('success', 'Updated — conflicts re-checked.');
    } catch {
      this.flash('error', 'Network error — the edit was not saved.');
    } finally {
      this.busyId.set(null);
      await this.load();
    }
  }

  async resolve(c: Candidate, action: 'approve' | 'supersede' | 'keep_both' | 'reject'): Promise<void> {
    this.busyId.set(c.candidate_id);
    try {
      const res = await fetch(`${API}/api/knowledge/candidates/${c.candidate_id}/resolve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, expected_version: c.version }),
      });
      const body = await this.parseOutcome(res);
      if (body) {
        if (body['ingest_error']) {
          this.flash('error', `Couldn’t update the knowledge base: ${body['ingest_error']}`);
        } else {
          const patched = Number(body['patched'] ?? 0);
          const label = action === 'reject' ? 'Rejected'
            : action === 'keep_both' ? 'Added alongside the existing fact'
            : action === 'supersede' ? 'Superseded' : 'Approved';
          this.flash('success', patched > 0
            ? `${label} — ${patched} existing passage${patched === 1 ? '' : 's'} updated.`
            : `${label}.`);
        }
      }
    } catch {
      this.flash('error', 'Network error — the action was not applied.');
    } finally {
      this.busyId.set(null);
      await this.load();
    }
  }
}
