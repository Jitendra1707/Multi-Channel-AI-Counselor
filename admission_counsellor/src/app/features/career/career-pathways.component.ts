import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { BarListComponent } from '../../shared/ui/charts.component';
import { ProbabilityBadgeComponent, BandChipComponent } from '../../shared/ui/badges.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { fmtInt } from '../../shared/util/format';

interface Pathway {
  name: string; icon: string; courses: string[]; students: number; demand: 'High' | 'Medium' | 'Low'; readiness: number; employers: string[]; band: 'low' | 'med' | 'high';
}

@Component({
  selector: 'va-career-pathways',
  standalone: true,
  imports: [IconComponent, PageHeaderComponent, SectionCardComponent, BarListComponent, ProbabilityBadgeComponent, BandChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header title="Career Pathways" [subtitle]="subtitle">
      <button class="btn btn-ghost" (click)="toast.info('Exporting pathway report')"><va-icon name="download" [size]="16"></va-icon> Export</button>
      <button class="btn btn-primary" (click)="toast.success('New approved pathway submitted for review')"><va-icon name="plus" [size]="16"></va-icon> New pathway</button>
    </va-page-header>

    <div class="kpis">
      @for (k of kpis; track k.label) {
        <div class="tile"><div class="tv t-num" [class.ai]="k.ai">{{ k.value }}</div><div class="tl">{{ k.label }}</div></div>
      }
    </div>

    <div class="body">
      <va-section-card title="Approved career pathways" hint="Each maps to approved courses & outcomes" [flush]="true">
        <span actions class="chip"><va-icon name="shield-check" [size]="12"></va-icon> Approved-knowledge only</span>
        <div class="tbl-wrap">
          <table class="va-table">
            <thead><tr><th>Pathway</th><th>Mapped courses</th><th>Students on track</th><th>Demand</th><th>Avg readiness</th><th>Top employers</th></tr></thead>
            <tbody>
              @for (p of pathways; track p.name) {
                <tr (click)="open(p)">
                  <td><div class="pw"><span class="pw-ic"><va-icon [name]="p.icon" [size]="16"></va-icon></span><b>{{ p.name }}</b></div></td>
                  <td><div class="chips">@for (c of p.courses; track c) { <span class="chip sm">{{ c }}</span> }</div></td>
                  <td class="num">{{ fmt(p.students) }}</td>
                  <td><va-band-chip [band]="p.demand === 'High' ? 'high' : p.demand === 'Medium' ? 'med' : 'low'" [label]="p.demand"></va-band-chip></td>
                  <td style="min-width:150px"><va-probability-badge [value]="p.readiness" [ai]="true"></va-probability-badge></td>
                  <td class="t-sm t-muted">{{ p.employers.join(', ') }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </va-section-card>

      <aside class="rail">
        <va-section-card title="Top career interests" hint="Student demand">
          <va-bar-list [data]="interests()"></va-bar-list>
        </va-section-card>
        <div class="banner ai">
          <va-icon name="compass" [size]="16"></va-icon>
          <span><b>Vera’s tip:</b> pair the Data Science pathway with the approved internship-partner list to lift readiness by ~9%.</span>
        </div>
      </aside>
    </div>
  </div>`,
  styles: [`
    :host { display: block; }
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .tile .tv.ai { background: var(--gradient-career); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
    .body { display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 18px; align-items: start; }
    .rail { display: flex; flex-direction: column; gap: 18px; }
    .tbl-wrap { overflow-x: auto; }
    .pw { display: flex; align-items: center; gap: 10px; }
    .pw-ic { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: rgba(var(--color-career-rgb), .12); color: var(--color-career); flex: none; }
    .chips { display: flex; flex-wrap: wrap; gap: 5px; }
    .chip.sm { font-size: 11px; padding: 2px 7px; }
    tbody tr { cursor: pointer; }
    .banner.ai va-icon { color: var(--color-career); }
    @media (max-width: 1080px) { .body { grid-template-columns: 1fr; } .kpis { grid-template-columns: repeat(2, 1fr); } }
  `],
})
export class CareerPathwaysComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  toast = inject(ToastService);
  private auth = inject(AuthService);
  subtitle = `Vera maps approved pathways and the courses that lead to them — ${this.auth.institution().name} · ${this.auth.admissionCycle()}`;
  fmt = fmtInt;
  interests = this.store.careerInterests;

  kpis = [
    { label: 'Pathways recommended', value: '1,876', ai: true },
    { label: 'Active pathways', value: '12', ai: false },
    { label: 'Avg readiness', value: '74%', ai: true },
    { label: 'Courses mapped', value: '38', ai: false },
  ];

  pathways: Pathway[] = [
    { name: 'Software Engineer', icon: 'route', courses: ['B.Tech CSE', 'B.Tech AI & DS'], students: 612, demand: 'High', readiness: 78, employers: ['Infosys', 'TCS', 'Zoho'], band: 'high' },
    { name: 'Data Scientist', icon: 'bar-chart', courses: ['B.Tech AI & DS', 'B.Sc Data Science'], students: 548, demand: 'High', readiness: 74, employers: ['Fractal', 'Mu Sigma'], band: 'med' },
    { name: 'Product / UX Designer', icon: 'lightbulb', courses: ['B.Des UX'], students: 286, demand: 'Medium', readiness: 69, employers: ['Freshworks', 'Razorpay'], band: 'med' },
    { name: 'Financial Analyst', icon: 'dollar-sign', courses: ['B.Com (Hons)', 'MBA'], students: 241, demand: 'Medium', readiness: 66, employers: ['Deloitte', 'KPMG'], band: 'med' },
    { name: 'Cybersecurity Analyst', icon: 'shield', courses: ['B.Tech CSE', 'M.Tech AI'], students: 198, demand: 'High', readiness: 71, employers: ['Palo Alto', 'Wipro'], band: 'med' },
    { name: 'Product Manager', icon: 'briefcase', courses: ['MBA', 'BBA'], students: 154, demand: 'Medium', readiness: 64, employers: ['Swiggy', 'PhonePe'], band: 'med' },
  ];

  open(p: Pathway) { this.toast.info(`Opening ${p.name} — ${p.students} students on track`); this.router.navigateByUrl('/app/crm'); }
}
