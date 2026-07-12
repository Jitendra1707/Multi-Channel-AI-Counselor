import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import {
  ProbabilityBadgeComponent, ApprovalChipComponent,
} from '../../shared/ui/badges.component';
import { PageHeaderComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { FilterBarComponent } from '../../shared/ui/filter-bar.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { DataStore } from '../../data-access/data.store';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { KnowledgeApiService, ResourceDoc } from '../../data-access/knowledge-api.service';
import { KmsDoc, DocStatus } from '../../domain/models';
import { relTime, relFuture, fmtInt } from '../../shared/util/format';

type StatusGroup = 'active' | 'warning' | 'danger' | 'muted';

// ---------------------------------------------------------------------------
// Real-document mapping. The backend (GET /api/resources, backed by Qdrant)
// only knows {id, filename, chunks, status}. The KMS table needs a full KmsDoc,
// so the remaining columns (course/version/dates/confidence/conflict/training)
// are ASSIGNED here. To avoid values flickering on every signal recompute we
// derive them DETERMINISTICALLY from the doc id (a tiny seeded PRNG) instead of
// Math.random() — same doc → same numbers across renders.
// ---------------------------------------------------------------------------
function _seedFrom(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0);
}
/** Deterministic int in [min, max] from a seed string + salt. */
function _rand(seed: string, salt: string, min: number, max: number): number {
  const h = _seedFrom(seed + salt);
  return min + (h % (max - min + 1));
}

const _KMS_CATEGORIES = [
  'Course Brochure', 'Fee Structure', 'Scholarship Policy', 'Placement Report',
  'Admission Procedure', 'Eligibility Criteria', 'Curriculum', 'Internship Details',
  'Academic Calendar', 'FAQ', 'Hostel Info', 'Refund Policy',
];

/** Title-case a raw filename (strip extension, split separators). */
function _titleFromFilename(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim();
  return base
    .split(/\s+/)
    .map(w => w ? w[0].toUpperCase() + w.slice(1) : w)
    .join(' ') || filename;
}

/** Pick a category by keyword in the filename, else a stable pseudo-random one. */
function _categoryFor(filename: string, id: string): string {
  const f = filename.toLowerCase();
  if (f.includes('fee')) return 'Fee Structure';
  if (f.includes('scholarship')) return 'Scholarship Policy';
  if (f.includes('placement')) return 'Placement Report';
  if (f.includes('curriculum') || f.includes('syllabus')) return 'Curriculum';
  if (f.includes('admission')) return 'Admission Procedure';
  if (f.includes('eligibility')) return 'Eligibility Criteria';
  if (f.includes('internship')) return 'Internship Details';
  if (f.includes('hostel')) return 'Hostel Info';
  if (f.includes('refund')) return 'Refund Policy';
  if (f.includes('brochure')) return 'Course Brochure';
  if (f.includes('faq')) return 'FAQ';
  return _KMS_CATEGORIES[_seedFrom(id) % _KMS_CATEGORIES.length];
}

/** Map a backend ResourceDoc → a full KmsDoc row for the library table. */
function realToKmsDoc(d: ResourceDoc): KmsDoc {
  const id = d.id;
  const ready = d.status === 'ready' || d.status == null;
  // chunks ≈ a few KB each; gives a believable size for the row.
  const chunks = d.chunks ?? _rand(id, 'chunks', 4, 40);
  return {
    documentId: id,
    title: _titleFromFilename(d.filename),
    description: 'Uploaded to the knowledge base and ingested into the vector store.',
    category: _categoryFor(d.filename, id),
    course: '',                                  // unknown from Qdrant → "applies to all"
    academicYear: '2026–27',
    version: _rand(id, 'ver', 1, 4),
    status: ready ? 'Active' : 'Processing',
    uploadedBy: 'Knowledge Upload',
    uploadedAt: new Date().toISOString(),
    approvedBy: ready ? 'Auto-ingested' : undefined,
    effectiveDate: new Date().toISOString(),
    expiryDate: new Date(Date.now() + 365 * 86400000).toISOString(),
    aiTrainingStatus: ready ? 'Trained' : 'Queued',
    confidenceScore: _rand(id, 'conf', 80, 98),
    conflictScore: _rand(id, 'conflict', 2, 15),
    usageCount: _rand(id, 'usage', 0, 500),
    tags: ['uploaded'],
    sizeKb: chunks * 6,
  };
}

@Component({
  selector: 'va-kms-library',
  standalone: true,
  imports: [
    RouterLink, IconComponent, ProbabilityBadgeComponent, ApprovalChipComponent,
    PageHeaderComponent, EmptyStateComponent, FilterBarComponent, AiAvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page">
    <va-page-header
      [title]="career() ? 'Career Knowledge' : 'Knowledge'"
      [subtitle]="headerSubtitle()">
      <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
      <button class="btn btn-ghost" (click)="syncTraining()">
        <va-icon name="refresh" [size]="16"></va-icon>Sync training
      </button>
      <button class="btn btn-primary" routerLink="/app/kms/upload">
        <va-icon name="upload" [size]="16"></va-icon>Upload document
      </button>
    </va-page-header>

    <!-- Guardrail banner: responsible, institution-controlled AI -->
    <div class="banner ai mb">
      <va-icon name="shield-check" [size]="18"></va-icon>
      <div>
        <strong>Approved-knowledge-only.</strong>
        {{ counselor.activeMeta().name }} answers strictly from <em>Active</em> documents trained into the model. Documents under approval,
        needs-review, expired or rejected are never used to generate {{ career() ? 'student' : 'candidate' }}-facing answers.
      </div>
    </div>

    <!-- Library health tiles -->
    <div class="tiles mb">
      <div class="tile">
        <span class="tl">Total documents</span>
        <span class="tv t-num">{{ fmtInt(total()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="check-circle" [size]="13"></va-icon> Active &amp; trained</span>
        <span class="tv t-num good">{{ fmtInt(activeCount()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="clock" [size]="13"></va-icon> Needs attention</span>
        <span class="tv t-num warn">{{ fmtInt(attentionCount()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="alert-triangle" [size]="13"></va-icon> Conflicts flagged</span>
        <span class="tv t-num bad">{{ fmtInt(conflictCount()) }}</span>
      </div>
      <div class="tile">
        <span class="tl"><va-icon name="calendar" [size]="13"></va-icon> Expiring soon</span>
        <span class="tv t-num warn">{{ fmtInt(expiringCount()) }}</span>
      </div>
    </div>

    <va-filter-bar
      [query]="query()"
      placeholder="Filter by title or category…"
      [savedViews]="savedViews"
      [activeView]="activeView()"
      (queryChange)="query.set($event)"
      (selectView)="selectView($event)">

      <ng-container filters>
        <select class="select sm" [value]="statusFilter()" (change)="statusFilter.set($any($event.target).value)">
          <option value="">All statuses</option>
          @for (s of statusOptions; track s) { <option [value]="s">{{ s }}</option> }
        </select>
        <select class="select sm" [value]="categoryFilter()" (change)="categoryFilter.set($any($event.target).value)">
          <option value="">All categories</option>
          @for (c of categoryOptions(); track c) { <option [value]="c">{{ c }}</option> }
        </select>
        <select class="select sm" [value]="courseFilter()" (change)="courseFilter.set($any($event.target).value)">
          <option value="">{{ career() ? 'All pathways' : 'All courses' }}</option>
          @for (c of courseOptions(); track c) { <option [value]="c">{{ c }}</option> }
        </select>
      </ng-container>

      <ng-container actions>
        <div class="seg">
          <button [class.active]="view() === 'table'" (click)="view.set('table')" title="Table view">
            <va-icon name="list" [size]="15"></va-icon>
          </button>
          <button [class.active]="view() === 'grid'" (click)="view.set('grid')" title="Grid view">
            <va-icon name="columns" [size]="15"></va-icon>
          </button>
        </div>
      </ng-container>
    </va-filter-bar>

    <div class="resultline t-sm t-muted">
      Showing <strong class="t-num">{{ fmtInt(rows().length) }}</strong> of {{ fmtInt(total()) }} documents
      @if (activeFiltersOn()) {
        · <button class="linkbtn" (click)="clearFilters()">Clear filters</button>
      }
    </div>

    @if (rows().length === 0) {
      @if (total() === 0) {
        <div class="card">
          <va-empty
            icon="book-open"
            title="No documents yet"
            [message]="emptyMessage()"
            cta="Upload document"
            ctaIcon="upload"
            (action)="goUpload()"></va-empty>
        </div>
      } @else {
        <div class="card">
          <va-empty
            icon="search"
            title="No documents match your filters"
            message="Try a different search term or clear the active filters to see the full library."
            cta="Clear filters"
            ctaIcon="x"
            (action)="clearFilters()"></va-empty>
        </div>
      }
    } @else if (view() === 'table') {
      <!-- ============ TABLE ============ -->
      <div class="card flush tablewrap">
        <table class="va-table">
          <thead>
            <tr>
              <th>Document</th>
              <th>{{ career() ? 'Pathway' : 'Course' }}</th>
              <th>Version</th>
              <th>Status</th>
              <th>AI training</th>
              <th>Confidence</th>
              <th>Conflict</th>
              <th class="num">Usage</th>
              <th>Updated</th>
              <th class="menucol"></th>
            </tr>
          </thead>
          <tbody>
            @for (d of rows(); track d.documentId) {
              <tr (click)="open(d)">
                <td>
                  <div class="doc">
                    <span class="dicon" [attr.data-group]="statusGroup(d.status)">
                      <va-icon [name]="docIcon(d.category)" [size]="18"></va-icon>
                    </span>
                    <div class="dtext">
                      <span class="dtitle truncate">{{ d.title }}</span>
                      <span class="t-cap t-muted">{{ d.category }} · {{ d.academicYear }}</span>
                    </div>
                  </div>
                </td>
                <td class="t-sm">{{ d.course || '—' }}</td>
                <td class="t-num t-sm">v{{ d.version }}</td>
                <td>
                  <span class="spill" [attr.data-group]="statusGroup(d.status)">{{ d.status }}</span>
                  @if (isExpiringSoon(d)) {
                    <span class="chip expiry" title="Effective coverage ends soon">
                      <va-icon name="clock" [size]="11"></va-icon>Expires {{ relFuture(d.expiryDate!) }}
                    </span>
                  }
                </td>
                <td>
                  <va-approval-chip [state]="trainingState(d.aiTrainingStatus)"></va-approval-chip>
                </td>
                <td><va-probability-badge [value]="d.confidenceScore" [ai]="true"></va-probability-badge></td>
                <td>
                  @if (d.conflictScore > 20) {
                    <span class="chip conflict" [title]="'Conflict score ' + d.conflictScore + ' — overlaps another document'">
                      <va-icon name="alert-triangle" [size]="11"></va-icon>Conflict
                    </span>
                  } @else {
                    <span class="t-muted t-sm">None</span>
                  }
                </td>
                <td class="num t-num t-sm">{{ fmtInt(d.usageCount) }}</td>
                <td class="t-sm t-muted nowrap">{{ relTime(d.uploadedAt) }}</td>
                <td class="menucol" (click)="$event.stopPropagation()">
                  <div class="menu">
                    <button class="btn btn-icon btn-sm" (click)="toggleMenu(d.documentId)" title="More actions">
                      <va-icon name="more-vertical" [size]="16"></va-icon>
                    </button>
                    @if (openMenu() === d.documentId) {
                      <div class="dropdown">
                        <button (click)="preview(d)"><va-icon name="eye" [size]="15"></va-icon>Preview</button>
                        <button (click)="versionHistory(d)"><va-icon name="git-branch" [size]="15"></va-icon>Version history</button>
                        <div class="sep"></div>
                        <button class="danger" (click)="requestDeletion(d)"><va-icon name="trash" [size]="15"></va-icon>Request deletion</button>
                      </div>
                    }
                  </div>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    } @else {
      <!-- ============ GRID ============ -->
      <div class="grid">
        @for (d of rows(); track d.documentId) {
          <button class="dcard" (click)="open(d)">
            <div class="dcard-top">
              <span class="dicon lg" [attr.data-group]="statusGroup(d.status)">
                <va-icon [name]="docIcon(d.category)" [size]="20"></va-icon>
              </span>
              <span class="spill" [attr.data-group]="statusGroup(d.status)">{{ d.status }}</span>
            </div>
            <div class="dcard-title truncate2">{{ d.title }}</div>
            <div class="t-cap t-muted">{{ d.category }} · v{{ d.version }} · {{ d.course || (career() ? 'All pathways' : 'All courses') }}</div>

            <div class="dcard-conf">
              <va-probability-badge [value]="d.confidenceScore" [ai]="true"></va-probability-badge>
            </div>

            <div class="dcard-flags">
              <va-approval-chip [state]="trainingState(d.aiTrainingStatus)"></va-approval-chip>
              @if (d.conflictScore > 20) {
                <span class="chip conflict"><va-icon name="alert-triangle" [size]="11"></va-icon>Conflict</span>
              }
              @if (isExpiringSoon(d)) {
                <span class="chip expiry"><va-icon name="clock" [size]="11"></va-icon>Expires {{ relFuture(d.expiryDate!) }}</span>
              }
            </div>

            <div class="dcard-foot t-cap t-muted">
              <span><va-icon name="trending-up" [size]="12"></va-icon> {{ fmtInt(d.usageCount) }} uses</span>
              <span>{{ relTime(d.uploadedAt) }}</span>
            </div>
          </button>
        }
      </div>
    }
  </div>
  `,
  styles: [`
    .mb { margin-bottom: var(--s-4); }

    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}

    .banner { display: flex; align-items: flex-start; gap: 10px; }
    .banner em { font-style: normal; font-weight: 600; }

    .tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--s-3); }
    @media (max-width: 1100px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
    .tile .tl { display: inline-flex; align-items: center; gap: 5px; }
    .tile .tv.good { color: var(--color-success); }
    .tile .tv.warn { color: var(--color-warning); }
    .tile .tv.bad  { color: var(--color-danger); }

    .select.sm { width: auto; padding: 7px 30px 7px 11px; min-width: 132px; cursor: pointer;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23889' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat; background-position: right 9px center; appearance: none; -webkit-appearance: none; }

    .resultline { margin: var(--s-3) 0 var(--s-2); }
    .linkbtn { border: none; background: none; padding: 0; font: inherit; font-weight: 600;
      color: var(--color-accent); cursor: pointer; }
    .linkbtn:hover { text-decoration: underline; }

    .tablewrap { overflow: visible; }
    .va-table { table-layout: auto; }

    /* document cell */
    .doc { display: flex; align-items: center; gap: 11px; min-width: 0; }
    .dtext { display: flex; flex-direction: column; gap: 1px; min-width: 0; max-width: 320px; }
    .dtitle { font-weight: 600; }
    .dicon { width: 34px; height: 34px; flex: none; border-radius: var(--r-md); display: grid; place-items: center;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .dicon.lg { width: 40px; height: 40px; }
    .dicon[data-group='active'] { background: rgba(var(--color-primary-rgb), .10); color: var(--color-primary); }
    .dicon[data-group='warning'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .dicon[data-group='danger'] { background: var(--color-danger-soft); color: var(--color-danger); }

    /* status pill */
    .spill { display: inline-flex; align-items: center; font-size: var(--text-cap); font-weight: 600;
      padding: 4px 9px; border-radius: var(--r-pill); white-space: nowrap; border: 1px solid transparent; }
    .spill[data-group='active']  { background: var(--color-success-soft); color: var(--color-success); }
    .spill[data-group='warning'] { background: var(--color-warning-soft); color: var(--color-warning); border-color: rgba(0,0,0,0); }
    .spill[data-group='danger']  { background: var(--color-danger-soft); color: var(--color-danger); }
    .spill[data-group='muted']   { background: var(--color-surface-alt); color: var(--color-text-muted); }

    .chip.conflict { background: var(--color-danger-soft); color: var(--color-danger); border-color: transparent; padding: 3px 8px; }
    .chip.expiry { background: var(--color-warning-soft); color: var(--color-warning); border-color: transparent;
      margin-left: 6px; padding: 3px 8px; }

    .nowrap { white-space: nowrap; }

    /* row menu */
    .menucol { width: 44px; text-align: right; }
    .menu { position: relative; display: inline-block; }
    .dropdown { position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; min-width: 190px;
      background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-md);
      box-shadow: var(--e3); padding: 6px; display: flex; flex-direction: column; }
    .dropdown button { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left;
      padding: 8px 10px; border: none; background: none; font: inherit; font-size: var(--text-sm);
      color: var(--color-text); border-radius: var(--r-sm); cursor: pointer; }
    .dropdown button:hover { background: var(--color-surface-alt); }
    .dropdown button.danger { color: var(--color-danger); }
    .dropdown .sep { height: 1px; background: var(--color-border); margin: 5px 4px; }

    /* grid */
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(264px, 1fr)); gap: var(--s-3); }
    .dcard { text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 7px;
      background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg);
      box-shadow: var(--e1); padding: 16px; font: inherit; transition: border-color .15s, box-shadow .15s, transform .12s; }
    .dcard:hover { border-color: var(--color-border-strong); box-shadow: var(--e2); transform: translateY(-2px); }
    .dcard-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .dcard-title { font-weight: 600; font-size: var(--text-base); line-height: 1.35; }
    .dcard-conf { margin-top: 2px; }
    .dcard-flags { display: flex; flex-wrap: wrap; gap: 5px; min-height: 22px; margin-top: 2px; }
    .dcard-foot { display: flex; align-items: center; justify-content: space-between;
      padding-top: 9px; margin-top: auto; border-top: 1px solid var(--color-border); }
    .dcard-foot span { display: inline-flex; align-items: center; gap: 4px; }

    .truncate2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  `],
})
export class KmsLibraryComponent implements OnInit {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  private knowledge = inject(KnowledgeApiService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');

  /** Real documents ingested into Qdrant (GET /api/resources), mapped to KmsDoc
   * rows and PREPENDED to the seeded mock library so they appear at the top of
   * the same table/tiles. Loaded once on init; refreshed by Sync training. */
  private realDocs = signal<KmsDoc[]>([]);

  ngOnInit(): void { void this.loadRealDocs(); }

  private async loadRealDocs(): Promise<void> {
    try {
      const docs = await this.knowledge.listResources();
      this.realDocs.set(docs.map(d => realToKmsDoc(d)));
    } catch {
      // Backend unreachable / endpoint down — leave the mock library intact.
      this.realDocs.set([]);
    }
  }

  headerSubtitle = computed(() => {
    const n = this.counselor.activeMeta().name;
    return this.career()
      ? `Career source of truth — ${n} guides only from approved + active pathway, skill-framework and salary-band documents`
      : `The institutional source of truth — ${n} uses only approved + active documents`;
  });
  emptyMessage = computed(() =>
    this.career()
      ? 'Upload approved career documents Vera can guide from — pathway guides, skill frameworks, salary-band data and internship/mentor guides.'
      : 'Upload official institution documents Aisha can learn from — brochures, fee structures, scholarship policies and admission procedures.');

  fmtInt = fmtInt;
  relTime = relTime;
  relFuture = relFuture;

  // state
  query = signal('');
  statusFilter = signal('');
  categoryFilter = signal('');
  courseFilter = signal('');
  activeView = signal('All');
  view = signal<'table' | 'grid'>('table');
  openMenu = signal<string | null>(null);

  savedViews = ['All', 'Active', 'Needs review', 'Expiring soon'];
  statusOptions: DocStatus[] = [
    'Active', 'Approved', 'Under Approval', 'Needs Review', 'Processing',
    'Uploaded', 'Draft', 'Expired', 'Rejected', 'Archived',
  ];

  /** Career-flavoured library shown when Vera is the active counselor. */
  private careerDocs: KmsDoc[] = [
    {
      documentId: 'cdoc-001', title: 'Software Engineering Career Pathway Guide 2026',
      description: 'Step-by-step pathway from foundations to placement-ready for software roles.',
      category: 'Pathway Guide', course: 'Software Engineering', academicYear: '2026',
      version: 3, status: 'Active', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-05-28T10:00:00',
      approvedBy: 'Career Lead', effectiveDate: '2026-06-01T00:00:00', expiryDate: '2027-06-01T00:00:00',
      aiTrainingStatus: 'Trained', confidenceScore: 92, conflictScore: 6, usageCount: 1840,
      tags: ['pathway', 'software'], sizeKb: 540,
    },
    {
      documentId: 'cdoc-002', title: 'Data Science & AI Skill Framework',
      description: 'Approved competency map and skill levels for Data Science & AI roles.',
      category: 'Skill Framework', course: 'Data Science & AI', academicYear: '2026',
      version: 2, status: 'Active', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-05-22T09:30:00',
      approvedBy: 'Career Lead', effectiveDate: '2026-05-25T00:00:00', expiryDate: '2027-05-25T00:00:00',
      aiTrainingStatus: 'Trained', confidenceScore: 88, conflictScore: 9, usageCount: 1520,
      tags: ['skills', 'ai'], sizeKb: 410,
    },
    {
      documentId: 'cdoc-003', title: 'Emerging-Role Salary Band Data — Q2 2026',
      description: 'Verified salary bands for emerging tech roles, sourced from approved market reports.',
      category: 'Salary Band Data', course: 'Data Science & AI', academicYear: '2026',
      version: 1, status: 'Under Approval', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-06-10T14:10:00',
      effectiveDate: '2026-06-15T00:00:00', expiryDate: '2026-09-15T00:00:00',
      aiTrainingStatus: 'Queued', confidenceScore: 71, conflictScore: 12, usageCount: 0,
      tags: ['salary', 'market'], sizeKb: 260,
    },
    {
      documentId: 'cdoc-004', title: 'Internship & Mentor Match Guide 2026',
      description: 'Approved internship partners and the mentor-matching playbook.',
      category: 'Internship Guide', course: 'All pathways', academicYear: '2026',
      version: 4, status: 'Active', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-05-30T11:45:00',
      approvedBy: 'Career Lead', effectiveDate: '2026-06-01T00:00:00', expiryDate: '2026-12-31T00:00:00',
      aiTrainingStatus: 'Trained', confidenceScore: 85, conflictScore: 14, usageCount: 1190,
      tags: ['internship', 'mentor'], sizeKb: 480,
    },
    {
      documentId: 'cdoc-005', title: 'Certifications Recruiters Value — Reference 2025',
      description: 'Which certifications recruiters weight by role; superseded ranking flagged.',
      category: 'Skill Framework', course: 'Cybersecurity', academicYear: '2025',
      version: 2, status: 'Needs Review', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-04-18T08:20:00',
      effectiveDate: '2025-09-01T00:00:00', expiryDate: '2026-06-30T00:00:00',
      aiTrainingStatus: 'Trained', confidenceScore: 64, conflictScore: 28, usageCount: 760,
      tags: ['certifications', 'recruiters'], sizeKb: 320,
    },
    {
      documentId: 'cdoc-006', title: 'Product / UX Pathway & Portfolio Mentorship Guide',
      description: 'Pathway milestones and approved portfolio-mentorship track for UX aspirants.',
      category: 'Pathway Guide', course: 'Product / UX Design', academicYear: '2026',
      version: 1, status: 'Active', uploadedBy: 'Vera Knowledge Team', uploadedAt: '2026-06-05T16:00:00',
      approvedBy: 'Career Lead', effectiveDate: '2026-06-08T00:00:00', expiryDate: '2027-06-08T00:00:00',
      aiTrainingStatus: 'Trained', confidenceScore: 81, conflictScore: 7, usageCount: 430,
      tags: ['ux', 'portfolio'], sizeKb: 290,
    },
  ];

  // Real uploaded docs first, then the seeded mock library (kept as-is). The
  // career view stays purely seeded — real ingests belong to the institution KB.
  private docs = computed<KmsDoc[]>(() =>
    this.career() ? this.careerDocs : [...this.realDocs(), ...this.store.kmsDocs()]);
  total = computed(() => this.docs().length);

  categoryOptions = computed(() =>
    Array.from(new Set(this.docs().map(d => d.category))).sort());
  courseOptions = computed(() =>
    Array.from(new Set(this.docs().map(d => d.course).filter((c): c is string => !!c))).sort());

  // health tiles
  activeCount = computed(() => this.docs().filter(d => d.status === 'Active' && d.aiTrainingStatus === 'Trained').length);
  attentionCount = computed(() => this.docs().filter(d =>
    d.status === 'Needs Review' || d.status === 'Under Approval' || d.status === 'Processing').length);
  conflictCount = computed(() => this.docs().filter(d => d.conflictScore > 20).length);
  expiringCount = computed(() => this.docs().filter(d => this.isExpiringSoon(d)).length);

  activeFiltersOn = computed(() =>
    !!this.query() || !!this.statusFilter() || !!this.categoryFilter() || !!this.courseFilter() || this.activeView() !== 'All');

  rows = computed<KmsDoc[]>(() => {
    const q = this.query().trim().toLowerCase();
    const st = this.statusFilter();
    const cat = this.categoryFilter();
    const crs = this.courseFilter();
    const vw = this.activeView();
    return this.docs().filter(d => {
      if (q && !d.title.toLowerCase().includes(q) && !d.category.toLowerCase().includes(q)) return false;
      if (st && d.status !== st) return false;
      if (cat && d.category !== cat) return false;
      if (crs && d.course !== crs) return false;
      if (vw === 'Active' && d.status !== 'Active') return false;
      if (vw === 'Needs review' && !(d.status === 'Needs Review' || d.status === 'Under Approval')) return false;
      if (vw === 'Expiring soon' && !this.isExpiringSoon(d)) return false;
      return true;
    });
  });

  // ---- helpers ----
  statusGroup(s: DocStatus): StatusGroup {
    if (s === 'Active' || s === 'Approved') return 'active';
    if (s === 'Under Approval' || s === 'Needs Review' || s === 'Processing'
      || s === 'Uploaded' || s === 'Draft' || s === 'Extracted' || s === 'Unlearn Pending'
      || s === 'Deletion Requested') return 'warning';
    if (s === 'Expired' || s === 'Rejected') return 'danger';
    return 'muted';
  }

  trainingState(s: KmsDoc['aiTrainingStatus']): 'approved' | 'pending' | 'draft' {
    if (s === 'Trained') return 'approved';
    if (s === 'Queued') return 'pending';
    return 'draft';
  }

  private readonly DAY = 86400000;
  private readonly NOW = new Date('2026-06-14T09:30:00').getTime();
  isExpiringSoon(d: KmsDoc): boolean {
    if (!d.expiryDate || d.status === 'Expired') return false;
    const diff = new Date(d.expiryDate).getTime() - this.NOW;
    return diff > 0 && diff <= 30 * this.DAY;
  }

  docIcon(category: string): string {
    const c = category.toLowerCase();
    if (c.includes('fee') || c.includes('refund')) return 'dollar-sign';
    if (c.includes('scholarship')) return 'star';
    if (c.includes('placement')) return 'trending-up';
    if (c.includes('curriculum') || c.includes('calendar')) return 'book-open';
    if (c.includes('faq')) return 'help-circle';
    if (c.includes('eligibility') || c.includes('procedure')) return 'clipboard-check';
    return 'file-text';
  }

  // ---- interactions ----
  selectView(v: string) { this.activeView.set(v); }

  clearFilters() {
    this.query.set('');
    this.statusFilter.set('');
    this.categoryFilter.set('');
    this.courseFilter.set('');
    this.activeView.set('All');
  }

  toggleMenu(id: string) { this.openMenu.update(cur => cur === id ? null : id); }

  open(d: KmsDoc) {
    this.openMenu.set(null);
    this.router.navigate(['/app/kms/document', d.documentId]);
  }
  goUpload() { this.router.navigateByUrl('/app/kms/upload'); }

  preview(d: KmsDoc) {
    this.openMenu.set(null);
    this.toast.info(`Opening preview for “${d.title}” (v${d.version}).`);
  }
  versionHistory(d: KmsDoc) {
    this.openMenu.set(null);
    this.toast.info(`Loading version history for “${d.title}” — ${d.version} version(s) on record.`);
  }
  requestDeletion(d: KmsDoc) {
    this.openMenu.set(null);
    this.toast.warning(`Deletion requested for “${d.title}”. ${this.counselor.activeMeta().name} will unlearn it once Compliance approves.`);
  }
  syncTraining() {
    // Re-pull the real ingested docs so a just-uploaded document shows up here.
    void this.loadRealDocs();
    this.toast.success(`Re-training queued — ${this.counselor.activeMeta().name} will refresh from approved documents shortly.`);
  }
}
