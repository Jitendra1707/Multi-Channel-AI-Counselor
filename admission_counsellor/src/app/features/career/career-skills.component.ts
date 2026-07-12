import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { fmtInt } from '../../shared/util/format';

interface SkillGap { skill: string; gapPct: number; band: 'low' | 'med' | 'high'; course: string; enrolled: number; approved: boolean; }
interface Track { name: string; provider: string; weeks: number; enrolled: number; completion: number; }

@Component({
  selector: 'va-career-skills',
  standalone: true,
  imports: [IconComponent, PageHeaderComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header title="Skills & Upskilling" [subtitle]="subtitle">
      <button class="btn btn-ghost" (click)="toast.info('Exporting skill-gap report')"><va-icon name="download" [size]="16"></va-icon> Export</button>
      <button class="btn btn-primary" (click)="toast.success('Upskilling track submitted for approval')"><va-icon name="plus" [size]="16"></va-icon> Add track</button>
    </va-page-header>

    <div class="kpis">
      @for (k of kpis; track k.label) {
        <div class="tile"><div class="tv t-num" [class.ai]="k.ai" [class.warn]="k.warn">{{ k.value }}</div><div class="tl">{{ k.label }}</div></div>
      }
    </div>

    <div class="body">
      <va-section-card title="Top skill gaps" hint="Vera maps each gap to an approved upskilling course" [flush]="true">
        <span actions class="chip"><va-icon name="brain" [size]="12"></va-icon> {{ gaps.length }} gaps</span>
        <div class="tbl-wrap">
          <table class="va-table">
            <thead><tr><th>Skill</th><th>Gap</th><th>Recommended approved course</th><th>Enrolled</th><th></th></tr></thead>
            <tbody>
              @for (g of gaps; track g.skill) {
                <tr>
                  <td><b>{{ g.skill }}</b></td>
                  <td style="min-width:140px">
                    <div class="gap"><span class="gap-track"><span class="gap-fill" [attr.data-b]="g.band" [style.width.%]="g.gapPct"></span></span><span class="t-cap t-num">{{ g.gapPct }}%</span></div>
                  </td>
                  <td><span class="course"><va-icon name="book-open" [size]="13"></va-icon>{{ g.course }}@if (g.approved) { <va-icon name="shield-check" [size]="12" class="ok"></va-icon> }</span></td>
                  <td class="num">{{ fmt(g.enrolled) }}</td>
                  <td><button class="btn btn-sm btn-subtle" (click)="toast.success('Skill plan created for ' + g.skill)">Create plan</button></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </va-section-card>

      <va-section-card title="Upskilling tracks" hint="Approved providers only">
        <div class="tracks">
          @for (t of tracks; track t.name) {
            <div class="track">
              <div class="track-top">
                <div><div class="track-name">{{ t.name }}</div><div class="t-cap t-muted">{{ t.provider }} · {{ t.weeks }} weeks</div></div>
                <span class="chip">{{ fmt(t.enrolled) }} enrolled</span>
              </div>
              <div class="track-prog"><div class="between t-cap t-muted"><span>Completion</span><span class="t-num">{{ t.completion }}%</span></div>
                <div class="progress success"><span [style.width.%]="t.completion"></span></div>
              </div>
            </div>
          }
        </div>
      </va-section-card>
    </div>
  </div>`,
  styles: [`
    :host { display: block; }
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .tile .tv.ai { background: var(--gradient-career); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
    .tile .tv.warn { color: var(--color-warning); }
    .body { display: grid; grid-template-columns: minmax(0,1.4fr) minmax(0,1fr); gap: 18px; align-items: start; }
    .tbl-wrap { overflow-x: auto; }
    .gap { display: flex; align-items: center; gap: 8px; }
    .gap-track { flex: 1; height: 7px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; min-width: 60px; }
    .gap-fill { display: block; height: 100%; border-radius: 999px; }
    .gap-fill[data-b='high'] { background: var(--color-danger); }
    .gap-fill[data-b='med'] { background: var(--color-warning); }
    .gap-fill[data-b='low'] { background: var(--color-success); }
    .course { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-sm); }
    .course va-icon { color: var(--color-text-muted); }
    .course .ok { color: var(--color-success); }
    .tracks { display: flex; flex-direction: column; gap: 12px; }
    .track { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 14px; }
    .track-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
    .track-name { font-weight: 600; font-size: var(--text-sm); }
    @media (max-width: 1080px) { .body { grid-template-columns: 1fr; } .kpis { grid-template-columns: repeat(2, 1fr); } }
  `],
})
export class CareerSkillsComponent {
  toast = inject(ToastService);
  private auth = inject(AuthService);
  subtitle = `Skill-gap analysis and approved upskilling tracks — ${this.auth.institution().name} · ${this.auth.admissionCycle()}`;
  fmt = fmtInt;

  kpis = [
    { label: 'Skill assessments', value: '1,294', ai: false, warn: false },
    { label: 'Gaps flagged', value: '9', ai: false, warn: true },
    { label: 'Upskilling enrolments', value: '642', ai: false, warn: false },
    { label: 'Completion rate', value: '68%', ai: true, warn: false },
  ];

  gaps: SkillGap[] = [
    { skill: 'Python', gapPct: 62, band: 'high', course: 'Python for Data Science (NPTEL)', enrolled: 184, approved: true },
    { skill: 'SQL & Databases', gapPct: 54, band: 'high', course: 'Databases Essentials', enrolled: 142, approved: true },
    { skill: 'Cloud (AWS/Azure)', gapPct: 48, band: 'med', course: 'Cloud Foundations', enrolled: 96, approved: true },
    { skill: 'Communication', gapPct: 39, band: 'med', course: 'Professional Communication', enrolled: 121, approved: true },
    { skill: 'System Design', gapPct: 33, band: 'med', course: 'Intro to System Design', enrolled: 64, approved: true },
    { skill: 'UX Research', gapPct: 28, band: 'low', course: 'UX Research Methods', enrolled: 47, approved: true },
  ];

  tracks: Track[] = [
    { name: 'Data Science Foundations', provider: 'Approved · NPTEL', weeks: 12, enrolled: 268, completion: 71 },
    { name: 'Full-Stack Web', provider: 'Approved · Internal LMS', weeks: 16, enrolled: 192, completion: 64 },
    { name: 'Cloud & DevOps', provider: 'Approved · AWS Academy', weeks: 10, enrolled: 118, completion: 58 },
    { name: 'Career Readiness & Communication', provider: 'Approved · Internal', weeks: 6, enrolled: 204, completion: 82 },
  ];
}
