import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { MetricCardComponent } from '../../shared/ui/metric-card.component';
import { FunnelComponent } from '../../shared/ui/funnel.component';
import { BarListComponent, DonutComponent } from '../../shared/ui/charts.component';
import { SectionCardComponent } from '../../shared/ui/layout.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { CounselorService } from '../../core/counselor.service';
import { FunnelStage, Metric } from '../../domain/models';
import { CHANNEL_ICON, relTime } from '../../shared/util/format';

@Component({
  selector: 'va-overview',
  standalone: true,
  imports: [IconComponent, AiAvatarComponent, MetricCardComponent, FunnelComponent, BarListComponent, DonutComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './overview.component.html',
  styleUrl: './overview.component.scss',
})
export class OverviewComponent {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);

  range = signal<'7d' | '30d' | 'cycle'>('30d');
  ranges = [{ k: '7d', l: '7 days' }, { k: '30d', l: '30 days' }, { k: 'cycle', l: 'Full cycle' }] as const;

  career = computed(() => this.counselor.active() === 'career');

  kpis = computed(() => (this.career() ? this.store.careerMetrics() : this.store.metrics()).slice(0, 6));
  funnel = computed(() => this.career() ? this.store.careerFunnel() : this.store.funnel());
  leadSources = this.store.leadSources;
  courseDemand = computed(() => this.career() ? this.store.careerInterests() : this.store.courseDemand());
  probabilityDist = computed(() => this.career() ? this.store.careerReadiness() : this.store.probabilityDist());
  insights = computed(() => (this.career() ? this.store.careerInsights() : this.store.insights()).slice(0, 3));

  // counselor-aware labels
  funnelTitle = computed(() => this.career() ? 'Career pathway funnel' : 'Admissions funnel');
  demandTitle = computed(() => this.career() ? 'Top career interests' : 'Course-wise demand');
  distTitle = computed(() => this.career() ? 'Career-readiness distribution' : 'Conversion-probability distribution');
  distCenter = computed(() => this.career() ? 'students' : 'candidates');
  gapText = computed(() => this.career()
    ? 'Vera flagged 9 career questions (incl. salary bands for emerging AI roles) that need approved guidance before she can answer confidently.'
    : 'Admission Counsellor detected 7 questions that need approved answers before the counselor can respond confidently.');
  activity = computed(() => this.store.activity().slice(0, 8));
  handoffs = computed(() => this.store.escalations().filter(e => e.status !== 'Resolved').slice(0, 4));
  references = computed(() => this.store.references().slice(0, 5));

  upcomingVcons = computed(() =>
    this.store.candidates().filter(c => c.currentStage === 'V-Con Scheduled' || c.currentStage === 'Parent Discussion Required').slice(0, 4));

  regions = [
    { name: 'Telangana', v: 92 }, { name: 'Karnataka', v: 78 }, { name: 'Maharashtra', v: 71 },
    { name: 'Tamil Nadu', v: 64 }, { name: 'Delhi', v: 52 }, { name: 'Kerala', v: 48 },
    { name: 'Gujarat', v: 39 }, { name: 'West Bengal', v: 33 }, { name: 'Rajasthan', v: 28 },
  ];

  relTime = relTime;
  chIcon = (c: any) => (CHANNEL_ICON as any)[c];

  drill(m: Metric) { if (m.drillTo) this.router.navigateByUrl(m.drillTo); }
  drillStage(s: FunnelStage) { this.toast.info(`Opening ${s.label} candidates (${s.count.toLocaleString()})`); this.router.navigateByUrl('/app/crm'); }
  heat(v: number) { return v >= 75 ? 'h3' : v >= 55 ? 'h2' : v >= 35 ? 'h1' : 'h0'; }
  go(url: string) { this.router.navigateByUrl(url); }
  export() { this.toast.success('Dashboard export queued — you’ll be notified when ready.'); }
}
