import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { AvatarComponent } from '../../shared/ui/avatar.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { BarListComponent } from '../../shared/ui/charts.component';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { fmtInt } from '../../shared/util/format';

interface Mentor { name: string; hue: number; expertise: string; type: 'Alumni' | 'Industry' | 'Faculty'; mentees: number; rating: number; }

@Component({
  selector: 'va-career-mentors',
  standalone: true,
  imports: [IconComponent, AvatarComponent, PageHeaderComponent, SectionCardComponent, BarListComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <div class="page page-grid">
    <va-page-header title="Mentors & Placements" [subtitle]="subtitle">
      <button class="btn btn-ghost" (click)="toast.info('Exporting outcomes report')"><va-icon name="download" [size]="16"></va-icon> Export</button>
      <button class="btn btn-primary" (click)="toast.success('Mentor invitation sent')"><va-icon name="plus" [size]="16"></va-icon> Invite mentor</button>
    </va-page-header>

    <div class="kpis">
      @for (k of kpis; track k.label) {
        <div class="tile"><div class="tv t-num" [class.ai]="k.ai">{{ k.value }}</div><div class="tl">{{ k.label }}</div></div>
      }
    </div>

    <div class="body">
      <va-section-card title="Mentors" hint="Approved alumni, industry & faculty mentors" [flush]="true">
        <span actions class="chip"><va-icon name="award" [size]="12"></va-icon> {{ mentors.length }} active</span>
        <div class="tbl-wrap">
          <table class="va-table">
            <thead><tr><th>Mentor</th><th>Expertise</th><th>Type</th><th>Mentees</th><th>Rating</th><th></th></tr></thead>
            <tbody>
              @for (m of mentors; track m.name) {
                <tr>
                  <td><div class="mt"><va-avatar [name]="m.name" [hue]="m.hue" [size]="32"></va-avatar><b>{{ m.name }}</b></div></td>
                  <td class="t-sm">{{ m.expertise }}</td>
                  <td><span class="chip">{{ m.type }}</span></td>
                  <td class="num">{{ m.mentees }}</td>
                  <td><span class="rating"><va-icon name="star" [size]="13"></va-icon>{{ m.rating.toFixed(1) }}</span></td>
                  <td><button class="btn btn-sm btn-subtle" (click)="toast.info('Opening ' + m.name)">View</button></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </va-section-card>

      <aside class="rail">
        <va-section-card title="Placement outcomes" hint="By pathway · this cycle">
          <va-bar-list [data]="outcomes"></va-bar-list>
        </va-section-card>
        <va-section-card title="Partner employers">
          <div class="emps">@for (e of employers; track e) { <span class="chip"><va-icon name="briefcase" [size]="12"></va-icon>{{ e }}</span> }</div>
        </va-section-card>
        <div class="banner ai">
          <va-icon name="compass" [size]="16"></va-icon>
          <span><b>Vera:</b> students with a mentor match reach placement-ready ~3 weeks sooner — and she never guarantees an offer.</span>
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
    .mt { display: flex; align-items: center; gap: 10px; }
    .rating { display: inline-flex; align-items: center; gap: 4px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .rating va-icon { color: var(--color-warning); }
    .emps { display: flex; flex-wrap: wrap; gap: 7px; }
    .banner.ai va-icon { color: var(--color-career); }
    @media (max-width: 1080px) { .body { grid-template-columns: 1fr; } .kpis { grid-template-columns: repeat(2, 1fr); } }
  `],
})
export class CareerMentorsComponent {
  toast = inject(ToastService);
  private auth = inject(AuthService);
  subtitle = `Mentor matches and placement outcomes — ${this.auth.institution().name} · ${this.auth.admissionCycle()}`;

  kpis = [
    { label: 'Mentor matches', value: '196', ai: true },
    { label: 'Placements influenced', value: '311', ai: false },
    { label: 'Partner employers', value: '42', ai: false },
    { label: 'Avg time-to-ready', value: '11 wks', ai: true },
  ];

  mentors: Mentor[] = [
    { name: 'Arjun Mehta', hue: 210, expertise: 'Data Science · ML', type: 'Alumni', mentees: 14, rating: 4.8 },
    { name: 'Sneha Kapoor', hue: 280, expertise: 'Product & UX', type: 'Industry', mentees: 11, rating: 4.7 },
    { name: 'Vikram Rao', hue: 160, expertise: 'Cloud & DevOps', type: 'Industry', mentees: 9, rating: 4.6 },
    { name: 'Dr. Lata Nair', hue: 330, expertise: 'Research & Higher Studies', type: 'Faculty', mentees: 16, rating: 4.9 },
    { name: 'Imran Sheikh', hue: 30, expertise: 'Cybersecurity', type: 'Alumni', mentees: 8, rating: 4.5 },
    { name: 'Priya Menon', hue: 190, expertise: 'Finance & Consulting', type: 'Industry', mentees: 10, rating: 4.6 },
  ];

  outcomes = [
    { label: 'Software Engineer', value: 118, sub: '78% ready' },
    { label: 'Data Scientist', value: 86, sub: '74% ready' },
    { label: 'UX Designer', value: 41, sub: '69% ready' },
    { label: 'Financial Analyst', value: 34, sub: '66% ready' },
    { label: 'Cybersecurity', value: 32, sub: '71% ready' },
  ];
  employers = ['Infosys', 'TCS', 'Zoho', 'Freshworks', 'Razorpay', 'Deloitte', 'Wipro', 'Swiggy', 'PhonePe', 'Fractal'];
}
