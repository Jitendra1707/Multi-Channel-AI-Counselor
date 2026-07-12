import {
  ChangeDetectionStrategy, Component, ElementRef, inject, signal, viewChild,
} from '@angular/core';
import { RouterLink, Router } from '@angular/router';
import * as XLSX from 'xlsx';
import { IconComponent } from '../../shared/ui/icon.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { DataStore } from '../../data-access/data.store';
import { BusinessApiService, UploadLeadsResult } from '../../data-access/business-api.service';
import { fmtInt } from '../../shared/util/format';

const PREVIEW_LIMIT = 10;

@Component({
  selector: 'va-crm-import',
  standalone: true,
  imports: [RouterLink, IconComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">

    <!-- Header -->
    <header class="ci-head">
      <div class="ci-head-text">
        <div class="row gap-3 wrap" style="align-items:center">
          <div class="t-h2">Import candidate list</div>
          <span class="chip ai-chip"><va-icon name="shield-check" [size]="12"></va-icon> Approved-knowledge-only intake</span>
        </div>
        <p class="t-sm t-muted">Upload an Excel of candidates so Aisha can begin governed, consent-aware AI counseling — {{ auth.institution().name }} · {{ auth.admissionCycle() }}</p>
      </div>
      <div class="ci-head-actions">
        <button class="btn btn-ghost btn-sm" routerLink="/app/crm"><va-icon name="arrow-left" [size]="16"></va-icon> Back to CRM</button>
      </div>
    </header>

    <input #fileInput type="file" accept=".xlsx,.xlsm" hidden (change)="onFileChosen($event)" />

    <!-- ============ UPLOAD ============ -->
    @if (phase() === 'upload') {
      <div class="two-col">
        <va-section-card title="Upload your candidate list" hint="Excel .xlsx / .xlsm · up to 20 MB">
          <div class="dropzone" [class.dragging]="dragging()"
            (dragover)="onDragOver($event)" (dragleave)="dragging.set(false)" (drop)="onDrop($event)">
            <div class="dz-ill"><va-icon name="upload" [size]="26"></va-icon></div>
            <div class="t-h4">Drag &amp; drop your file here</div>
            <p class="t-sm t-muted">Admission Counsellor reads the first sheet, de-duplicates by phone, and prepares leads for AI counseling.</p>
            <button class="btn btn-primary" (click)="choose()"><va-icon name="file-text" [size]="16"></va-icon> Choose file</button>
            <div class="dz-formats t-cap t-muted">
              <span class="chip"><va-icon name="file-text" [size]="12"></va-icon> .xlsx</span>
              <span class="chip"><va-icon name="file-text" [size]="12"></va-icon> .xlsm</span>
            </div>
            @if (parseError()) {
              <p class="dz-helper t-cap"><va-icon name="alert-triangle" [size]="13"></va-icon> {{ parseError() }}</p>
            } @else {
              <p class="dz-helper t-cap"><va-icon name="alert-triangle" [size]="13"></va-icon> Keep one header row; first sheet only. Include a Name and a Phone/Mobile column.</p>
            }
          </div>
        </va-section-card>

        <div class="banner ai">
          <va-icon name="shield-check" [size]="18"></va-icon>
          <div><b>Responsible intake.</b> Only leads you confirm consent for are activated for call / WhatsApp / email. Aisha always discloses it is an AI and answers strictly from institution-approved knowledge.</div>
        </div>
      </div>
    }

    <!-- ============ REVIEW & IMPORT ============ -->
    @if (phase() === 'review') {
      <va-section-card title="Review & import" [hint]="rowCount() + ' data row' + (rowCount() === 1 ? '' : 's') + ' detected'">
        <div actions class="row gap-2">
          <span class="chip"><va-icon name="file-check" [size]="12"></va-icon> {{ fileName() }}</span>
          <button class="btn btn-ghost btn-sm" (click)="choose()"><va-icon name="upload" [size]="14"></va-icon> Replace file</button>
        </div>

        <div class="banner info preview-note">
          <va-icon name="eye" [size]="18"></va-icon>
          <span>Preview of the first {{ previewRows().length }} of {{ rowCount() }} rows from <b>{{ fileName() }}</b>. The server validates and de-duplicates every row on import.</span>
        </div>

        <div class="scroll-x preview-wrap">
          <table class="va-table">
            <thead>
              <tr>@for (h of headers(); track $index) { <th>{{ h }}</th> }</tr>
            </thead>
            <tbody>
              @for (r of previewRows(); track $index) {
                <tr class="no-hover">@for (cell of r; track $index) { <td class="truncate cell">{{ cell }}</td> }</tr>
              }
            </tbody>
          </table>
        </div>

        <label class="consent" [class.checked]="consent()">
          <input type="checkbox" [checked]="consent()" (change)="consent.set($any($event.target).checked)" />
          <span class="consent-box"><va-icon name="check" [size]="14"></va-icon></span>
          <span class="consent-text">
            <b>Consent captured for call / WhatsApp / email per policy.</b>
            <span class="t-sm t-muted">I confirm these candidates consented to contact under the institution's data &amp; communication policy.</span>
          </span>
        </label>

        <div class="review-actions">
          <button class="btn btn-ghost" (click)="choose()"><va-icon name="upload" [size]="16"></va-icon> Upload a different file</button>
          <button class="btn btn-accent" [disabled]="!consent() || importing()" (click)="runImport()">
            <va-icon name="rocket" [size]="16"></va-icon> {{ importing() ? 'Importing…' : 'Import ' + rowCount() + ' rows' }}
          </button>
        </div>
      </va-section-card>
    }

    <!-- ============ DONE ============ -->
    @if (phase() === 'done' && result(); as r) {
      <div class="two-col">
        <va-section-card [flush]="true">
          <div class="success">
            <div class="success-ic"><va-icon name="check-circle" [size]="32"></va-icon></div>
            <div class="t-h2">{{ fmtInt(r.inserted) }} of {{ fmtInt(r.rows) }} rows imported</div>
            <p class="t-sm t-muted">{{ fmtInt(r.duplicates) }} duplicate(s) skipped · {{ fmtInt(r.errors) }} error(s). Aisha is ready to begin governed counseling on the new leads.</p>
            <div class="success-actions">
              <button class="btn btn-primary" routerLink="/app/crm"><va-icon name="users" [size]="16"></va-icon> Go to CRM</button>
              <button class="btn btn-ghost" (click)="reset()"><va-icon name="upload" [size]="16"></va-icon> Import another file</button>
            </div>
            <div class="success-pills">
              <span class="spill ok"><va-icon name="check" [size]="13"></va-icon> {{ fmtInt(r.inserted) }} imported</span>
              <span class="spill mute"><va-icon name="git-branch" [size]="13"></va-icon> {{ fmtInt(r.duplicates) }} duplicates</span>
              @if (r.errors) { <span class="spill warn"><va-icon name="alert-triangle" [size]="13"></va-icon> {{ fmtInt(r.errors) }} errors</span> }
            </div>
          </div>
        </va-section-card>

        <va-section-card title="Import summary">
          <dl class="dl batch">
            <dt>File name</dt><dd class="truncate">{{ fileName() }}</dd>
            <dt>Uploaded by</dt><dd>{{ auth.user().name }}</dd>
            <dt>Rows processed</dt><dd class="t-num">{{ fmtInt(r.rows) }}</dd>
            <dt>Imported</dt><dd class="t-num ok">{{ fmtInt(r.inserted) }}</dd>
            <dt>Duplicates</dt><dd class="t-num warn">{{ fmtInt(r.duplicates) }}</dd>
            <dt>Errors</dt><dd class="t-num warn">{{ fmtInt(r.errors) }}</dd>
          </dl>
          @if (r.error_details.length) {
            <div class="err-list">
              <span class="t-cap t-muted block">Error details</span>
              @for (e of r.error_details; track e) {
                <div class="err-row t-cap"><va-icon name="alert-circle" [size]="12"></va-icon> {{ e }}</div>
              }
            </div>
          }
        </va-section-card>
      </div>
    }
  </div>
  `,
  styles: [`
    :host { display: block; }
    .ai-chip { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }

    .ci-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .ci-head-text p { margin-top: 5px; max-width: 64ch; }
    .ci-head-actions { display: flex; gap: 8px; flex-wrap: wrap; }

    .two-col { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 18px; align-items: start; }

    /* dropzone */
    .dropzone { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 8px;
      border: 2px dashed var(--color-border-strong); border-radius: var(--r-lg); padding: 36px 24px;
      background: var(--color-surface-2); transition: border-color .2s, background .2s; }
    .dropzone.dragging { border-color: var(--color-accent); background: rgba(var(--color-accent-rgb), .06); }
    .dz-ill { width: 60px; height: 60px; border-radius: 18px; display: grid; place-items: center; margin-bottom: 4px;
      background: var(--gradient-ai); color: #06121A; box-shadow: var(--e2); }
    .dropzone p { max-width: 44ch; margin: 0; }
    .dropzone .btn { margin-top: 8px; }
    .dz-formats { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; justify-content: center; }
    .dz-helper { display: inline-flex; align-items: center; gap: 6px; color: var(--color-warning); margin-top: 6px; }

    .banner > div { line-height: 1.5; }
    .banner.ai va-icon, .banner.info va-icon { flex: none; }
    .banner.ai va-icon { color: var(--color-accent-2); }
    .banner.info va-icon { color: var(--color-accent); }
    .preview-note { margin-bottom: 14px; }

    /* preview */
    .scroll-x { overflow-x: auto; }
    .preview-wrap { border: 1px solid var(--color-border); border-radius: var(--r-md); max-height: 380px; overflow-y: auto; }
    .va-table tbody tr.no-hover:hover { background: transparent; cursor: default; }
    .cell { max-width: 220px; }

    /* consent */
    .consent { display: flex; align-items: flex-start; gap: 12px; padding: 14px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface-2); cursor: pointer; transition: all .15s; margin-top: 16px; }
    .consent.checked { border-color: color-mix(in srgb, var(--color-success) 45%, var(--color-border)); background: var(--color-success-soft); }
    .consent input { position: absolute; opacity: 0; width: 0; height: 0; }
    .consent-box { width: 22px; height: 22px; border-radius: 6px; border: 1.5px solid var(--color-border-strong); flex: none;
      display: grid; place-items: center; color: transparent; background: var(--color-surface); transition: all .15s; margin-top: 1px; }
    .consent.checked .consent-box { background: var(--color-success); border-color: var(--color-success); color: #fff; }
    .consent-text { display: flex; flex-direction: column; gap: 3px; }

    .review-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 16px; }

    /* success */
    .success { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 8px; padding: 40px 28px; }
    .success-ic { width: 76px; height: 76px; border-radius: 22px; display: grid; place-items: center; margin-bottom: 6px;
      background: var(--color-success-soft); color: var(--color-success); box-shadow: 0 0 0 8px color-mix(in srgb, var(--color-success) 8%, transparent); }
    .success p { max-width: 46ch; margin: 0; }
    .success-actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; justify-content: center; }
    .success-pills { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; justify-content: center; }
    .spill { display: inline-flex; align-items: center; gap: 5px; font-size: var(--text-cap); font-weight: 600; padding: 5px 11px; border-radius: var(--r-pill); }
    .spill.ok { background: var(--color-success-soft); color: var(--color-success); }
    .spill.mute { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .spill.warn { background: var(--color-warning-soft); color: var(--color-warning); }

    .batch dd.ok { color: var(--color-success); }
    .batch dd.warn { color: var(--color-warning); }
    .block { display: block; }
    .err-list { display: flex; flex-direction: column; gap: 6px; margin-top: 14px; }
    .err-row { display: flex; align-items: flex-start; gap: 6px; color: var(--color-danger); }
    .err-row va-icon { flex: none; margin-top: 2px; }

    @media (max-width: 1080px) { .two-col { grid-template-columns: 1fr; } }
  `],
})
export class CrmImportComponent {
  auth = inject(AuthService);
  private toast = inject(ToastService);
  private router = inject(Router);
  private store = inject(DataStore);
  private api = inject(BusinessApiService);

  fmtInt = fmtInt;

  private fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  phase = signal<'upload' | 'review' | 'done'>('upload');
  dragging = signal(false);
  parseError = signal<string | null>(null);

  file = signal<File | null>(null);
  fileName = signal('');
  headers = signal<string[]>([]);
  previewRows = signal<string[][]>([]);
  rowCount = signal(0);

  consent = signal(false);
  importing = signal(false);
  result = signal<UploadLeadsResult | null>(null);

  // ---- file pick / parse ----
  choose(): void { this.fileInput()?.nativeElement.click(); }
  onFileChosen(e: Event): void {
    const input = e.target as HTMLInputElement;
    void this.acceptFile(input.files?.[0]);
    input.value = ''; // allow re-choosing the same file
  }
  onDragOver(e: DragEvent): void { e.preventDefault(); this.dragging.set(true); }
  onDrop(e: DragEvent): void { e.preventDefault(); this.dragging.set(false); void this.acceptFile(e.dataTransfer?.files?.[0]); }

  private async acceptFile(f: File | null | undefined): Promise<void> {
    if (!f) return;
    this.parseError.set(null);
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
      this.parseError.set('Please choose an .xlsx or .xlsm file.');
      this.toast.warning('Please choose an .xlsx or .xlsm file.');
      return;
    }
    try {
      const buf = await f.arrayBuffer();
      const wb = XLSX.read(buf, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(ws, { header: 1, blankrows: false, defval: '' }) as unknown[][];
      if (!rows.length) { this.parseError.set('The sheet appears to be empty.'); return; }

      const headerRow = rows[0] ?? [];
      const headers = headerRow.map((h, i) => (h !== null && h !== undefined && String(h).trim()) ? String(h).trim() : `Column ${i + 1}`);
      const dataRows = rows.slice(1);
      const cell = (v: unknown) => (v === null || v === undefined) ? '' : String(v);
      const preview = dataRows.slice(0, PREVIEW_LIMIT)
        .map(r => headers.map((_, i) => cell((r as unknown[])[i])));

      this.file.set(f);
      this.fileName.set(f.name);
      this.headers.set(headers);
      this.previewRows.set(preview);
      this.rowCount.set(dataRows.length);
      this.consent.set(false);
      this.result.set(null);
      this.phase.set('review');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not read the file.';
      this.parseError.set(`Could not read the Excel file: ${msg}`);
      this.toast.danger('Could not read the Excel file — is it a valid .xlsx?');
    }
  }

  // ---- import ----
  async runImport(): Promise<void> {
    const f = this.file();
    if (!f || !this.consent() || this.importing()) return;
    this.importing.set(true);
    try {
      const res = await this.api.uploadLeads(f);
      this.result.set(res);
      this.phase.set('done');
      void this.store.loadLeads(); // refresh the CRM list
      this.toast.success(
        `${res.inserted} lead(s) imported${res.duplicates ? `, ${res.duplicates} duplicate(s) skipped` : ''}.`,
        'rocket');
    } catch (e) {
      const unavailable = e instanceof Error && e.message === 'LEADS_SERVICE_UNAVAILABLE';
      this.toast.danger(unavailable
        ? 'Leads service unavailable — is BusinessLayer running on :8002?'
        : 'Upload failed — please check the file and try again.');
    } finally {
      this.importing.set(false);
    }
  }

  reset(): void {
    this.phase.set('upload');
    this.file.set(null);
    this.fileName.set('');
    this.headers.set([]);
    this.previewRows.set([]);
    this.rowCount.set(0);
    this.consent.set(false);
    this.result.set(null);
    this.parseError.set(null);
  }
}
